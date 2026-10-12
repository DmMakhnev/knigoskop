"""Durable outbox, per-user quota reservations, frozen routes and safe usage."""

import hashlib
import json
from datetime import UTC, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select

from app.core.config import settings
from app.core.errors import AppError
from app.core.provider_secrets import cipher, environment_fingerprint
from app.db.ai_models import (
    Job,
    Provider,
    ProviderCredential,
    ProviderModel,
    QuotaOverride,
    QuotaPolicy,
    TaskModelRoute,
)
from app.db.models import AuditEvent, utcnow
from app.domains.library.service import locked_user
from app.integrations.llm import DIAGNOSTIC_TEXT

METRICS = (
    "daily_jobs",
    "daily_requests",
    "daily_input_chars",
    "daily_tokens",
    "active_jobs",
)
TERMINAL = ("completed", "failed", "cancelled")


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def serialize_job(job):
    return {
        key: getattr(job, key)
        for key in (
            "id",
            "type",
            "status",
            "progress",
            "route_id",
            "attempts",
            "error_code",
            "result",
            "created_at",
            "completed_at",
        )
    }


def serialize_provider(db, provider):
    credential = db.scalar(
        select(ProviderCredential).where(ProviderCredential.provider_id == provider.id)
    )
    environment_ready = False
    if provider.protocol != "mock" and not credential:
        try:
            environment_fingerprint(provider.host)
            environment_ready = True
        except AppError:
            pass
    has_key = credential is not None or environment_ready
    return {
        "id": provider.id,
        "name": provider.name,
        "protocol": provider.protocol,
        "host": provider.host,
        "base_url": provider.base_url,
        "enabled": provider.enabled,
        "has_key": has_key,
        "key_mask": "••••••••" if has_key else None,
    }


def model_snapshot(db, model_id):
    model = db.get(ProviderModel, model_id)
    if not model or not model.enabled:
        raise AppError(503, "ai_unavailable", "Модель AI не настроена или отключена")
    provider = db.scalar(
        select(Provider).where(Provider.id == model.provider_id).with_for_update()
    )
    if not provider or not provider.enabled:
        raise AppError(503, "ai_unavailable", "Провайдер AI отключён")
    credential = db.scalar(
        select(ProviderCredential).where(ProviderCredential.provider_id == provider.id)
    )
    fingerprint = None
    if provider.protocol != "mock":
        if credential:
            cipher()  # Stored credentials never silently fall back to environment.
        else:
            fingerprint = environment_fingerprint(provider.host)
    snapshot = {
        "id": model.id,
        "provider_id": provider.id,
        "model_identifier": model.model_identifier,
        "capabilities": model.capabilities,
        "pricing": model.pricing,
        "protocol": provider.protocol,
        "base_url": provider.base_url,
        "host": provider.host,
        "encrypted_secret": credential.encrypted_secret if credential else None,
    }
    if fingerprint:
        snapshot.update(
            credential_source="environment", credential_fingerprint=fingerprint
        )
    return snapshot


def reserve_amounts(snapshot):
    policy = snapshot["policy"]
    calls = len(snapshot["models"]) * policy["attempts_per_model"]
    # Conservative UTF-8 upper bound for the fixed prompt plus protocol/schema overhead.
    input_tokens = len(DIAGNOSTIC_TEXT.encode()) + 256
    tokens = input_tokens + policy["max_output_tokens"]
    costs = []
    for model in snapshot["models"]:
        if tokens > model["capabilities"]["max_context_tokens"]:
            raise AppError(422, "context_limit", "Запрос превышает контекст модели")
        pricing = model["pricing"]
        if policy["max_cost_micros"] is not None:
            if not pricing or pricing["currency"] != policy["currency"]:
                raise AppError(
                    422, "pricing_required", "Для бюджета нужны цены в одной валюте"
                )
            cost = (
                (
                    input_tokens * pricing["input_micros_per_million"]
                    + policy["max_output_tokens"] * pricing["output_micros_per_million"]
                )
                + 999999
            ) // 1000000
            costs.append(cost * policy["attempts_per_model"])
    if policy["max_cost_micros"] is not None and sum(costs) > policy["max_cost_micros"]:
        raise AppError(429, "budget_exceeded", "Маршрут превышает установленный бюджет")
    return {
        "daily_jobs": 1,
        "daily_requests": calls,
        "daily_input_chars": len(DIAGNOSTIC_TEXT) * calls,
        "daily_tokens": tokens * calls,
        "active_jobs": 1,
    }


def quota_state(db, user_id):
    limits = {metric: getattr(settings, "quota_" + metric) for metric in METRICS}
    for policy in db.scalars(select(QuotaPolicy)):
        limits[policy.metric] = policy.limit_value
    for override in db.scalars(
        select(QuotaOverride).where(QuotaOverride.user_id == user_id)
    ):
        limits[override.metric] = override.limit_value
    midnight = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    used = dict.fromkeys(METRICS, 0)
    for job in db.scalars(
        select(Job).where(Job.user_id == user_id, Job.created_at >= midnight)
    ):
        for metric in METRICS[:-1]:
            used[metric] += job.reservations[metric]
    used["active_jobs"] = len(
        list(
            db.scalars(
                select(Job.id).where(
                    Job.user_id == user_id, Job.status.not_in(TERMINAL)
                )
            )
        )
    )
    return {"period": "UTC day", "limits": limits, "reserved": used}


def create_job(db, user, payload, *, model_id=None):
    locked_user(db, user)
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "type": "connection_test" if model_id else "diagnostic",
                "route_id": str(payload.route_id) if payload.route_id else None,
                "model_id": model_id,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    existing = db.scalar(
        select(Job).where(
            Job.user_id == user.id, Job.idempotency_key == payload.idempotency_key
        )
    )
    if existing:
        if existing.request_hash != request_hash:
            raise AppError(
                409, "idempotency_conflict", "Ключ уже использован для другого запроса"
            )
        return existing
    route = None
    if model_id:
        if payload.route_id:
            raise AppError(422, "validation_error", "Для теста модели маршрут не нужен")
        policy = {
            "attempts_per_model": 1,
            "backoff_seconds": 2,
            "max_output_tokens": 128,
            "max_cost_micros": None,
            "currency": None,
        }
        model_ids = [model_id]
    else:
        if payload.route_id:
            route = db.get(TaskModelRoute, str(payload.route_id))
        else:
            route = db.scalar(
                select(TaskModelRoute)
                .where(TaskModelRoute.task_type == "diagnostic")
                .order_by(TaskModelRoute.config_version.desc())
                .limit(1)
            )
        if not route or route.task_type != "diagnostic":
            raise AppError(503, "ai_unavailable", "Маршрут AI не настроен")
        policy = route.policy
        model_ids = [route.primary_model_id] + (
            [route.fallback_model_id] if route.fallback_model_id else []
        )
    # Fixed ordering across concurrent routes avoids provider-lock inversions.
    provider_ids = list(
        db.scalars(
            select(ProviderModel.provider_id).where(ProviderModel.id.in_(model_ids))
        )
    )
    list(
        db.scalars(
            select(Provider)
            .where(Provider.id.in_(provider_ids))
            .order_by(Provider.id)
            .with_for_update()
        )
    )
    snapshot = {
        "config_version": route.config_version if route else None,
        "policy": policy,
        "models": [model_snapshot(db, model_id) for model_id in model_ids],
    }
    reservations = reserve_amounts(snapshot)
    quotas = quota_state(db, user.id)
    if any(
        quotas["reserved"][metric] + reservations[metric] > quotas["limits"][metric]
        for metric in METRICS
    ):
        raise AppError(429, "quota_exceeded", "Квота AI исчерпана; задача не запущена")
    job = Job(
        id=str(uuid4()),
        user_id=user.id,
        type="connection_test" if model_id else "diagnostic",
        idempotency_key=payload.idempotency_key,
        request_hash=request_hash,
        route_id=route.id if route else None,
        snapshot=snapshot,
        reservations=reservations,
    )
    db.add(job)
    db.add(AuditEvent(actor_id=user.id, action="ai_job_created", entity_id=job.id))
    db.commit()  # PostgreSQL is the durable outbox, not Redis.
    return job


def publish(job_id):
    from app.workers.tasks import run_job

    try:
        run_job.apply_async(args=[job_id], task_id=job_id, retry=False)
        return True
    except Exception:
        # Beat retries queued rows. Never include broker credentials in a log.
        return False


def dispatch_due(db):
    now = utcnow()
    jobs = list(
        db.scalars(
            select(Job)
            .where(
                or_(
                    and_(
                        Job.status == "queued",
                        Job.next_run_at <= now,
                        Job.next_dispatch_at <= now,
                    ),
                    and_(
                        Job.status.in_(("running", "validating")),
                        Job.started_at
                        < now - timedelta(seconds=settings.effective_job_lease_seconds),
                    ),
                )
            )
            .order_by(Job.created_at)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    )
    count = 0
    for job in jobs:
        if job.status in ("running", "validating"):
            if job.started_at and utc(job.started_at) < now - timedelta(
                seconds=settings.effective_job_lease_seconds
            ):
                job.status, job.error_code, job.completed_at = (
                    "failed",
                    "provider_outcome_unknown",
                    now,
                )
                # No replay of an in-flight external operation after a crash.
            continue
        if utc(job.next_run_at) <= now:
            if publish(job.id):
                job.next_dispatch_at = now + timedelta(
                    seconds=settings.job_recovery_seconds
                )
                count += 1
    db.commit()
    return count


def own_job(db, job_id, user_id, *, lock=False):
    query = select(Job).where(Job.id == job_id, Job.user_id == user_id)
    job = db.scalar(query.with_for_update() if lock else query)
    if not job:
        raise AppError(404, "job_not_found", "Задача не найдена")
    return job


def usage_record(event):
    return {
        key: getattr(event, key)
        for key in (
            "id",
            "job_id",
            "provider_model_id",
            "attempt",
            "status",
            "error_code",
            "units",
            "estimated_cost_micros",
            "currency",
            "latency_ms",
            "created_at",
        )
    } | {
        "units": {
            k: v
            for k, v in (event.units or {}).items()
            if k in {"input_tokens", "output_tokens", "input_chars", "output_chars"}
            and isinstance(v, int)
            and not isinstance(v, bool)
            and v >= 0
        }
    }
