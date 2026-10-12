"""Immutable user-scoped cache; no text in queue payloads, no implicit paid retries."""

import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.ai_models import Job, QuotaOverride, QuotaPolicy
from app.db.audio_models import AudioAsset, AudioSegment, AudioUsage, TTSProfile
from app.db.models import User, utcnow
from app.db.session import get_engine
from app.db.summary_models import Summary, SummaryNode
from app.domains.audio.timings import segments, validated_marks, wav_duration
from app.domains.jobs.service import METRICS, TERMINAL, quota_state
from app.domains.library.service import describe, item_for_user, locked_user
from app.integrations.llm import ProviderFailure
from app.integrations.storage import get_storage
from app.integrations.tts import credential, get_provider


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def selected_text(db, summary_id, node_id):
    rows = db.scalars(
        select(SummaryNode)
        .where(SummaryNode.summary_id == summary_id)
        .order_by(SummaryNode.order_index)
        .limit(5001)
    ).all()
    if len(rows) > 5000:
        raise AppError(422, "audio_text_limit", "Выберите отдельную главу или эпизод")
    selected = {str(node_id)} if node_id else {r.id for r in rows}
    if node_id and str(node_id) not in {r.id for r in rows}:
        raise AppError(404, "summary_node_missing", "Эпизод не найден")
    if node_id:
        while True:
            children = {r.id for r in rows if r.parent_id in selected}
            if children <= selected:
                break
            selected |= children
    parents = {r.parent_id for r in rows if r.id in selected}
    text = "\n\n".join(
        r.text
        for r in rows
        if r.id in selected and r.id not in parents and r.text.strip()
    )
    if not 1 <= len(text) <= settings.audio_max_text_chars:
        raise AppError(
            422, "audio_text_limit", "Резюме пусто или слишком велико; выберите главу"
        )
    return text


def own_asset(db, asset_id, user):
    asset = db.scalar(
        select(AudioAsset).where(
            AudioAsset.id == str(asset_id), AudioAsset.user_id == user.id
        )
    )
    if not asset:
        raise AppError(404, "audio_missing", "Аудио не найдено")
    item_for_user(db, asset.library_item_id, user.id)
    return asset


def create_audio(db, user, payload):
    locked_user(db, user)
    item = item_for_user(db, str(payload.library_item_id), user.id)
    summary = db.get(Summary, str(payload.summary_id))
    if not summary or summary.library_item_id != item.id:
        raise AppError(404, "summary_missing", "Резюме не найдено")
    request_hash = digest(payload.model_dump(mode="json", exclude={"idempotency_key"}))
    previous = db.scalar(
        select(Job).where(
            Job.user_id == user.id, Job.idempotency_key == payload.idempotency_key
        )
    )
    if previous:
        if previous.type != "audio" or previous.request_hash != request_hash:
            raise AppError(
                409, "idempotency_conflict", "Ключ использован другим запросом"
            )
        return previous, previous.status == "completed"
    profile = db.get(TTSProfile, str(payload.profile_id))
    if not profile or not profile.enabled or payload.voice not in profile.voices:
        raise AppError(
            503, "tts_unavailable", "Модель или голос озвучивания недоступны"
        )
    snapshot = {
        k: getattr(profile, k)
        for k in (
            "id",
            "protocol",
            "base_url",
            "host",
            "model",
            "encrypted_secret",
            "price_micros_per_1000_chars",
            "currency",
        )
    }
    text = selected_text(db, summary.id, payload.node_id)
    language = describe(db, item)["language"]
    cache_key = digest(
        {
            "owner": user.id,
            "summary": summary.id,
            "text": text,
            "language": language,
            "voice": payload.voice,
            "profile": {
                k: snapshot[k] for k in ("id", "protocol", "base_url", "host", "model")
            },
            "format": "wav_24000",
            "adapter_version": 1,
        }
    )
    cached = db.scalar(select(AudioAsset).where(AudioAsset.cache_key == cache_key))
    if cached:
        job = db.scalar(
            select(Job)
            .where(
                Job.type == "audio",
                Job.user_id == user.id,
                Job.result["asset_id"].as_string() == cached.id,
            )
            .order_by(Job.created_at.desc())
        )
        if job and job.status in ("queued", "running", "completed"):
            return job, job.status == "completed"
        raise AppError(
            409,
            "audio_previous_failed",
            "Предыдущая генерация прервана; повтор требует проверки расходов",
        )
    if profile.protocol != "mock":
        if not payload.consent_external:
            raise AppError(
                422,
                "external_consent_required",
                "Подтвердите передачу резюме провайдеру озвучивания",
            )
        key = credential(snapshot)
        snapshot["key_fingerprint"] = hashlib.sha256(key.encode()).hexdigest()
        if (
            profile.price_micros_per_1000_chars is None
            or payload.max_cost_micros is None
            or payload.currency != profile.currency
        ):
            raise AppError(
                422,
                "pricing_required",
                "Укажите цену, валюту и максимальный бюджет озвучивания",
            )
    ranges = segments(text, settings.audio_segment_chars)
    from app.domains.admin.limits import check_storage

    check_storage(db, user.id, settings.audio_max_bytes)
    # No paid retry until TTS idempotency is documented; one attempt per segment.
    if profile.price_micros_per_1000_chars is not None:
        cost = sum(
            ((end - start) * profile.price_micros_per_1000_chars + 999) // 1000
            for start, end in ranges
        )
        if payload.max_cost_micros is not None and cost > payload.max_cost_micros:
            raise AppError(
                429, "budget_exceeded", "Озвучивание превышает указанный бюджет"
            )
    reservations = dict.fromkeys(METRICS, 0)
    reservations.update(
        daily_jobs=1,
        daily_requests=len(ranges),
        daily_input_chars=len(text),
        active_jobs=1,
    )
    quotas = quota_state(db, user.id)
    if any(
        quotas["reserved"][m] + reservations[m] > quotas["limits"][m] for m in METRICS
    ):
        raise AppError(429, "quota_exceeded", "Квота озвучивания исчерпана")
    limit = settings.quota_daily_tts_chars
    for policy in (
        db.get(QuotaPolicy, "daily_tts_chars"),
        db.get(QuotaOverride, (user.id, "daily_tts_chars")),
    ):
        if policy:
            limit = policy.limit_value
    midnight = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    used = sum(
        j.reservations.get("daily_input_chars", 0)
        for j in db.scalars(
            select(Job).where(
                Job.user_id == user.id, Job.type == "audio", Job.created_at >= midnight
            )
        )
    )
    if used + len(text) > limit:
        raise AppError(
            429, "tts_quota_exceeded", "Дневной лимит символов озвучивания исчерпан"
        )
    asset = AudioAsset(
        user_id=user.id,
        library_item_id=item.id,
        summary_id=summary.id,
        profile_id=profile.id,
        cache_key=cache_key,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
        source_text=text,
        voice=payload.voice,
        language=language,
    )
    db.add(asset)
    db.flush()
    for index, (start, end) in enumerate(ranges):
        db.add(
            AudioSegment(
                asset_id=asset.id, order_index=index, start_offset=start, end_offset=end
            )
        )
    job = Job(
        user_id=user.id,
        library_item_id=item.id,
        type="audio",
        idempotency_key=payload.idempotency_key,
        request_hash=request_hash,
        snapshot={"asset_id": asset.id, "profile": snapshot},
        reservations=reservations,
        result={"asset_id": asset.id},
    )
    db.add(job)
    db.commit()
    return job, False


def execute_audio(job_id, engine=None):
    engine = engine or get_engine()
    storage = get_storage()
    with Session(engine) as db:
        job = db.get(Job, job_id)
        if not job:
            return
        owner_id = job.user_id
        user = db.scalar(select(User).where(User.id == owner_id).with_for_update())
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job.status != "queued":
            return
        asset = db.get(AudioAsset, job.snapshot["asset_id"])
        profile = db.get(TTSProfile, asset.profile_id) if asset else None
        if (
            not user
            or user.status != "active"
            or not asset
            or not profile
            or not profile.enabled
        ):
            job.status, job.error_code, job.completed_at = (
                "failed",
                "tts_unavailable",
                utcnow(),
            )
            db.commit()
            return
        job.status, job.started_at, job.attempts = "running", utcnow(), job.attempts + 1
        asset.status = "running"
        frozen = job.snapshot["profile"]
        asset_id = asset.id
        rows = [
            (s.id, s.order_index, asset.source_text[s.start_offset : s.end_offset])
            for s in db.scalars(
                select(AudioSegment)
                .where(
                    AudioSegment.asset_id == asset.id,
                    AudioSegment.storage_key.is_(None),
                )
                .order_by(AudioSegment.order_index)
                .limit(1)
            )
        ]
        voice = asset.voice
        language = asset.language
        total_segments = db.scalar(
            select(func.count())
            .select_from(AudioSegment)
            .where(AudioSegment.asset_id == asset.id)
        )
        db.commit()
    try:
        key = credential(frozen)
        if (
            frozen.get("key_fingerprint")
            and hashlib.sha256(key.encode()).hexdigest() != frozen["key_fingerprint"]
        ):
            raise ProviderFailure("tts_key_changed")
        provider = get_provider(frozen)
        for segment_id, index, source in rows:
            with Session(engine) as db:
                job = db.get(Job, job_id)
                if not job or job.status in TERMINAL:
                    return
                from app.domains.admin.limits import check_storage

                check_storage(db, owner_id, settings.audio_max_bytes)
                usage = AudioUsage(
                    user_id=owner_id,
                    job_id=job_id,
                    profile_id=frozen["id"],
                    segment_index=index,
                    chars=len(source),
                )
                db.add(usage)
                db.commit()
                usage_id = usage.id
            speech = provider.synthesize(source, voice, key, language=language)
            if len(speech.audio) > settings.audio_max_bytes:
                raise ProviderFailure("audio_size_limit")
            duration = wav_duration(speech.audio)
            marks = (
                validated_marks(source, speech.marks, duration)
                if speech.alignment == "words"
                else []
            )
            stored = storage.put(speech.audio)
            try:
                with Session(engine) as db:
                    owner = db.get(User, owner_id)
                    if not owner:
                        storage.delete(stored)
                        return
                    locked_user(db, owner)
                    job = db.scalar(
                        select(Job).where(Job.id == job_id).with_for_update()
                    )
                    segment = db.get(AudioSegment, segment_id)
                    if not job or job.status != "running" or not segment:
                        storage.delete(stored)
                        return
                    check_storage(db, owner_id, len(speech.audio))
                    segment.storage_bytes = len(speech.audio)
                    segment.storage_key, segment.checksum = (
                        stored,
                        hashlib.sha256(speech.audio).hexdigest(),
                    )
                    segment.duration_ms, segment.timings, segment.alignment = (
                        duration,
                        marks,
                        speech.alignment,
                    )
                    event = db.get(AudioUsage, usage_id)
                    event.duration_ms, event.status = duration, "completed"
                    if frozen["price_micros_per_1000_chars"] is not None:
                        event.estimated_cost_micros = (
                            len(source) * frozen["price_micros_per_1000_chars"] + 999
                        ) // 1000
                        event.currency = frozen["currency"]
                    job.progress = (index + 1) * 100 // total_segments
                    db.commit()
            except Exception:
                storage.delete(stored)
                raise
        with Session(engine) as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job and job.status == "running":
                asset = db.get(AudioAsset, asset_id)
                asset.duration_ms = (
                    db.scalar(
                        select(func.sum(AudioSegment.duration_ms)).where(
                            AudioSegment.asset_id == asset_id
                        )
                    )
                    or 0
                )
                remaining = db.scalar(
                    select(func.count())
                    .select_from(AudioSegment)
                    .where(
                        AudioSegment.asset_id == asset_id,
                        AudioSegment.storage_key.is_(None),
                    )
                )
                if remaining:
                    job.status = "queued"
                    job.next_run_at = job.next_dispatch_at = utcnow()
                else:
                    asset.status = "completed"
                    job.status, job.progress, job.completed_at = (
                        "completed",
                        100,
                        utcnow(),
                    )
                db.commit()
    except Exception as exc:
        code = (
            exc.code
            if isinstance(exc, (ProviderFailure, AppError))
            else "audio_internal"
        )
        with Session(engine) as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job and job.status not in TERMINAL:
                job.status, job.error_code, job.completed_at = "failed", code, utcnow()
                asset = db.get(AudioAsset, asset_id)
                if asset:
                    asset.status = "failed"
                db.commit()


def audio_keys(db, item_id):
    return list(
        db.scalars(
            select(AudioSegment.storage_key)
            .join(AudioAsset)
            .where(
                AudioAsset.library_item_id == item_id,
                AudioSegment.storage_key.is_not(None),
            )
        )
    )
