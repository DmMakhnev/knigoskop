"""Audio ACL on every request, range streaming and optimistic progress updates."""

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.provider_secrets import encrypt
from app.db.audio_models import (
    AudioProgress,
    AudioSegment,
    AudioSettings,
    AudioUsage,
    TTSProfile,
)
from app.db.models import utcnow
from app.db.session import get_db
from app.domains.audio.schemas import GenerateInput, ProfileInput, ProgressInput
from app.domains.audio.service import create_audio, own_asset
from app.domains.auth.dependencies import require_admin, require_csrf, require_user
from app.domains.jobs.schemas import EnabledInput
from app.domains.jobs.service import publish, serialize_job
from app.integrations.storage import get_storage
from app.integrations.tts import credential

router = APIRouter(prefix="/api/v1", tags=["audio"])


def profile_record(profile):
    ready = True
    try:
        credential(
            {k: getattr(profile, k) for k in ("protocol", "host", "encrypted_secret")}
        )
    except AppError:
        ready = False
    return {
        k: getattr(profile, k)
        for k in (
            "id",
            "name",
            "protocol",
            "model",
            "voices",
            "enabled",
            "price_micros_per_1000_chars",
            "currency",
        )
    } | {"ready": ready, "external": profile.protocol != "mock"}


@router.get("/audio/profiles")
def profiles(user=Depends(require_user), db: Session = Depends(get_db)):
    return {
        "items": [
            profile_record(p)
            for p in db.scalars(
                select(TTSProfile).where(TTSProfile.enabled.is_(True)).limit(100)
            )
        ],
        "fallback": "web_speech",
    }


@router.get("/admin/audio/profiles")
def admin_profiles(admin=Depends(require_admin), db: Session = Depends(get_db)):
    return {
        "items": [profile_record(p) for p in db.scalars(select(TTSProfile).limit(100))]
    }


@router.post(
    "/admin/audio/profiles", status_code=201, dependencies=[Depends(require_csrf)]
)
def add_profile(
    payload: ProfileInput, admin=Depends(require_admin), db: Session = Depends(get_db)
):
    data = payload.model_dump(exclude={"api_key"})
    profile = TTSProfile(
        **data,
        encrypted_secret=encrypt(payload.api_key.get_secret_value())
        if payload.api_key
        else None,
    )
    db.add(profile)
    db.commit()
    return profile_record(profile)


@router.patch(
    "/admin/audio/profiles/{profile_id}", dependencies=[Depends(require_csrf)]
)
def enable_profile(
    profile_id: UUID,
    payload: EnabledInput,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    profile = db.get(TTSProfile, str(profile_id))
    if not profile:
        raise AppError(404, "tts_missing", "Профиль озвучивания не найден")
    profile.enabled = payload.enabled
    db.commit()
    return profile_record(profile)


@router.get("/admin/audio/usage")
def usage(admin=Depends(require_admin), db: Session = Depends(get_db)):
    return {
        "items": [
            {
                k: getattr(r, k)
                for k in (
                    "id",
                    "job_id",
                    "profile_id",
                    "segment_index",
                    "chars",
                    "duration_ms",
                    "status",
                    "estimated_cost_micros",
                    "currency",
                )
            }
            for r in db.scalars(select(AudioUsage).order_by(AudioUsage.id).limit(100))
        ]
    }


@router.post("/audio/generate", status_code=202, dependencies=[Depends(require_csrf)])
def generate(
    payload: GenerateInput, user=Depends(require_user), db: Session = Depends(get_db)
):
    job, cached = create_audio(db, user, payload)
    row = db.get(AudioSettings, user.id)
    if not row:
        row = AudioSettings(user_id=user.id)
        db.add(row)
    row.profile_id, row.voice = str(payload.profile_id), payload.voice
    db.commit()
    if job.status == "queued":
        publish(job.id)
    return {
        "job": serialize_job(job),
        "asset_id": job.result["asset_id"],
        "cached": cached,
    }


def progress_record(row):
    return {
        "revision": row.revision if row else 0,
        "position_ms": row.position_ms if row else 0,
        "playback_rate": row.playback_rate if row else 1,
    }


@router.get("/audio/{asset_id}/manifest")
def manifest(asset_id: UUID, user=Depends(require_user), db: Session = Depends(get_db)):
    asset = own_asset(db, asset_id, user)
    if asset.status != "completed":
        raise AppError(409, "audio_pending", "Аудио ещё не готово")
    rows = db.scalars(
        select(AudioSegment)
        .where(AudioSegment.asset_id == asset.id)
        .order_by(AudioSegment.order_index)
    ).all()
    start_ms = 0
    records = []
    for s in rows:
        records.append(
            {
                "index": s.order_index,
                "start_ms": start_ms,
                "duration_ms": s.duration_ms,
                "text_start": s.start_offset,
                "text_end": s.end_offset,
                "alignment": s.alignment,
                "timings": s.timings,
                "url": f"/api/v1/audio/{asset.id}/stream?segment={s.order_index}",
            }
        )
        start_ms += s.duration_ms
    return {
        "id": asset.id,
        "text": asset.source_text,
        "text_hash": asset.text_hash,
        "voice": asset.voice,
        "language": asset.language,
        "duration_ms": asset.duration_ms,
        "segments": records,
        "demo": db.get(TTSProfile, asset.profile_id).protocol == "mock",
        "progress": progress_record(db.get(AudioProgress, (user.id, asset.id))),
    }


@router.api_route("/audio/{asset_id}/stream", methods=["GET", "HEAD"])
def stream(
    asset_id: UUID,
    request: Request,
    segment: int = 0,
    user=Depends(require_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    asset = own_asset(db, asset_id, user)
    if asset.status != "completed":
        raise AppError(409, "audio_pending", "Аудио ещё не готово")
    row = db.scalar(
        select(AudioSegment).where(
            AudioSegment.asset_id == asset.id, AudioSegment.order_index == segment
        )
    )
    if not row or not row.storage_key:
        raise AppError(404, "audio_missing", "Аудио не найдено")
    data = storage.read(row.storage_key)
    if hashlib.sha256(data).hexdigest() != row.checksum:
        raise AppError(503, "audio_integrity", "Не удалось проверить целостность аудио")
    start, end, status = 0, len(data) - 1, 200
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    if value := request.headers.get("range"):
        try:
            unit, span = value.split("=", 1)
            first, last = span.split("-", 1)
            if unit != "bytes" or "," in span:
                raise ValueError()
            start = int(first) if first else max(0, len(data) - int(last))
            end = min(int(last), len(data) - 1) if first and last else len(data) - 1
            if not 0 <= start <= end < len(data):
                raise ValueError()
        except ValueError:
            return Response(
                status_code=416, headers={"Content-Range": f"bytes */{len(data)}"}
            )
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
    headers["Content-Length"] = str(end - start + 1)
    return Response(
        b"" if request.method == "HEAD" else data[start : end + 1],
        status_code=status,
        headers=headers,
        media_type="audio/wav",
    )


@router.get("/me/audio-progress/{asset_id}")
def progress(asset_id: UUID, user=Depends(require_user), db: Session = Depends(get_db)):
    asset = own_asset(db, asset_id, user)
    return progress_record(db.get(AudioProgress, (user.id, asset.id)))


@router.put("/me/audio-progress", dependencies=[Depends(require_csrf)])
def save_progress(
    payload: ProgressInput, user=Depends(require_user), db: Session = Depends(get_db)
):
    # Lock owner consistently with deletion and all other user mutations.
    from app.domains.library.service import locked_user

    locked_user(db, user)
    asset = own_asset(db, payload.asset_id, user)
    if asset.status != "completed" or payload.position_ms > asset.duration_ms:
        raise AppError(
            422, "audio_position_invalid", "Позиция выходит за границы аудио"
        )
    row = db.get(AudioProgress, (user.id, asset.id))
    if payload.revision != (row.revision if row else 0):
        raise AppError(
            409, "progress_conflict", "Позиция изменена на другом устройстве"
        )
    if not row:
        row = AudioProgress(user_id=user.id, asset_id=asset.id)
        db.add(row)
    row.position_ms, row.playback_rate, row.revision = (
        payload.position_ms,
        payload.playback_rate,
        payload.revision + 1,
    )
    row.updated_at = utcnow()
    db.commit()
    return progress_record(row)


@router.get("/me/audio-settings")
def audio_settings(user=Depends(require_user), db: Session = Depends(get_db)):
    row = db.get(AudioSettings, user.id)
    return {
        "profile_id": row.profile_id if row else None,
        "voice": row.voice if row else None,
    }


@router.get("/admin/audio/voices/genvoice")
def genvoice_voices(admin=Depends(require_admin)):
    from app.core.config import settings
    from app.integrations.tts import genvoice_public_voices

    return {
        "items": genvoice_public_voices(
            {
                "protocol": "genvoice",
                "base_url": settings.tts_base_url,
                "host": settings.tts_env_provider_host,
                "encrypted_secret": None,
            }
        )
    }
