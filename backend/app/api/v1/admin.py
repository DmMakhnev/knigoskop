"""Administrative metadata only: no private content, snapshots or credentials."""

from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.ai import audit, paged, required, serialize_model
from app.core.errors import AppError
from app.db.ai_models import (
    Job,
    Provider,
    ProviderModel,
    QuotaOverride,
    UsageEvent,
)
from app.db.audio_models import AudioUsage
from app.db.models import AuditEvent, User, utcnow
from app.db.session import get_db
from app.domains.admin.limits import library_usage, limit_for
from app.domains.auth.dependencies import require_admin, require_csrf, require_user
from app.domains.jobs.schemas import Capabilities, Input, Metric, Pricing
from app.domains.jobs.service import TERMINAL, serialize_provider
from app.integrations.llm import validate_destination

router = APIRouter(prefix="/api/v1", tags=["administration"])


class ProviderEdit(Input):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(default="", max_length=2000)


class ModelEdit(Input):
    model_identifier: str = Field(min_length=1, max_length=100)
    capabilities: Capabilities
    pricing: Pricing | None = None


@router.put("/admin/providers/{provider_id}", dependencies=[Depends(require_csrf)])
def edit_provider(
    provider_id: UUID,
    payload: ProviderEdit,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.scalar(
        select(Provider).where(Provider.id == str(provider_id)).with_for_update()
    )
    if not row:
        raise AppError(404, "ai_config_not_found", "Провайдер не найден")
    if row.protocol == "mock":
        if payload.base_url:
            raise AppError(422, "validation_error", "Mock работает без сети")
    else:
        # Credentials stay bound to this host; changing hosts requires a new provider.
        validate_destination(payload.base_url, row.host)
    row.name, row.base_url = payload.name, payload.base_url
    audit(db, admin, "provider_updated", row.id)
    db.commit()
    return serialize_provider(db, row)


@router.put("/admin/models/{model_id}", dependencies=[Depends(require_csrf)])
def edit_model(
    model_id: UUID,
    payload: ModelEdit,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    # Updating metadata must not block the worker's FK KEY SHARE lock on this model.
    row = db.scalar(
        select(ProviderModel)
        .where(ProviderModel.id == str(model_id))
        .with_for_update(key_share=True)
    )
    if not row:
        raise AppError(404, "ai_config_not_found", "Модель не найдена")
    provider = required(db, Provider, row.provider_id)
    if provider.protocol != "mock" and payload.capabilities.mock_behavior != "success":
        raise AppError(
            422, "validation_error", "Mock-поведение доступно только локально"
        )
    row.model_identifier = payload.model_identifier
    row.capabilities = payload.capabilities.model_dump()
    row.pricing = payload.pricing.model_dump() if payload.pricing else {}
    audit(db, admin, "provider_model_updated", row.id)
    db.commit()
    return serialize_model(row)


def administrative_job(row):
    return {
        k: getattr(row, k)
        for k in (
            "id",
            "user_id",
            "type",
            "status",
            "progress",
            "attempts",
            "error_code",
            "created_at",
            "started_at",
            "completed_at",
        )
    }


@router.get("/admin/job-monitor")
def job_monitor(
    status: Literal[
        "queued", "running", "validating", "completed", "failed", "cancelled"
    ]
    | None = None,
    errors_only: bool = False,
    user_id: UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = select(Job)
    if status:
        query = query.where(Job.status == status)
    if errors_only:
        query = query.where(Job.error_code.is_not(None))
    if user_id:
        query = query.where(Job.user_id == str(user_id))
    return paged(
        db,
        query.order_by(Job.created_at.desc(), Job.id),
        administrative_job,
        offset,
        limit,
    )


@router.post("/admin/jobs/{job_id}/cancel", dependencies=[Depends(require_csrf)])
def cancel_job(
    job_id: UUID, admin=Depends(require_admin), db: Session = Depends(get_db)
):
    found = required(db, Job, job_id)
    owner = required(db, User, found.user_id)
    # Lock owner before job; blocked owners can still be cancelled.
    db.scalar(select(User).where(User.id == owner.id).with_for_update())
    row = db.scalar(
        select(Job)
        .where(Job.id == found.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not row:
        raise AppError(404, "job_missing", "Задача не найдена")
    if row.status not in TERMINAL:
        row.status, row.error_code, row.completed_at = (
            "cancelled",
            "admin_cancelled",
            utcnow(),
        )
        audit(db, admin, "job_cancelled_by_admin", row.id)
        db.commit()
    return administrative_job(row)


@router.get("/admin/audit")
def audit_events(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return paged(
        db,
        select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id),
        lambda r: {
            k: getattr(r, k)
            for k in ("id", "actor_id", "action", "entity_id", "created_at")
        },
        offset,
        limit,
    )


@router.delete(
    "/admin/quotas/{user_id}/{metric}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
def reset_override(
    user_id: UUID,
    metric: Metric,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    required(db, User, user_id)
    db.scalar(select(User).where(User.id == str(user_id)).with_for_update())
    row = db.get(QuotaOverride, (str(user_id), metric))
    if row:
        db.delete(row)
        audit(db, admin, "quota_override_removed", str(user_id))
        db.commit()


@router.get("/me/library-quota")
def own_limits(user=Depends(require_user), db: Session = Depends(get_db)):
    return {
        "used": library_usage(db, user.id),
        "limits": {m: limit_for(db, user.id, m) for m in ("books", "storage_bytes")},
    }


@router.get("/admin/statistics")
def statistics(
    days: int = Query(7, ge=1, le=90),
    user_id: UUID | None = None,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    since = utcnow() - timedelta(days=days)
    job_filter = [Job.created_at >= since]
    llm_filter = [UsageEvent.created_at >= since]
    tts_filter = [AudioUsage.created_at >= since]
    if user_id:
        job_filter.append(Job.user_id == str(user_id))
        llm_filter.append(UsageEvent.user_id == str(user_id))
        tts_filter.append(AudioUsage.user_id == str(user_id))
    job_counts = {
        status: count
        for status, count in db.execute(
            select(Job.status, func.count()).where(*job_filter).group_by(Job.status)
        )
    }
    llm = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(UsageEvent.units["input_tokens"].as_integer()), 0),
            func.coalesce(func.sum(UsageEvent.units["output_tokens"].as_integer()), 0),
            func.avg(UsageEvent.latency_ms),
        ).where(*llm_filter)
    ).one()
    tts = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(AudioUsage.chars), 0),
            func.coalesce(func.sum(AudioUsage.duration_ms), 0),
        ).where(*tts_filter)
    ).one()
    costs = {}
    for model, criteria in ((UsageEvent, llm_filter), (AudioUsage, tts_filter)):
        for currency, amount, unknown, total in db.execute(
            select(
                model.currency,
                func.coalesce(func.sum(model.estimated_cost_micros), 0),
                func.count().filter(model.estimated_cost_micros.is_(None)),
                func.count(),
            )
            .where(*criteria)
            .group_by(model.currency)
        ):
            key = currency or "UNSPECIFIED"
            record = costs.setdefault(
                key,
                {
                    "currency": currency,
                    "estimated_cost_micros": 0,
                    "unknown_cost_calls": 0,
                    "calls": 0,
                },
            )
            record["estimated_cost_micros"] += amount
            record["unknown_cost_calls"] += unknown
            record["calls"] += total
    llm_models = [
        {"model_id": model, "calls": count}
        for model, count in db.execute(
            select(UsageEvent.provider_model_id, func.count())
            .where(*llm_filter)
            .group_by(UsageEvent.provider_model_id)
        )
    ]
    tts_profiles = [
        {"profile_id": profile, "calls": count}
        for profile, count in db.execute(
            select(AudioUsage.profile_id, func.count())
            .where(*tts_filter)
            .group_by(AudioUsage.profile_id)
        )
    ]
    users = db.scalar(select(func.count()).select_from(User))
    return {
        "days": days,
        "since": since,
        "user_id": str(user_id) if user_id else None,
        "users_total": users,
        "jobs": job_counts,
        "llm": {
            "calls": llm[0],
            "input_tokens": llm[1],
            "output_tokens": llm[2],
            "mean_latency_ms": round(llm[3]) if llm[3] is not None else None,
        },
        "tts": {"calls": tts[0], "chars": tts[1], "duration_ms": tts[2]},
        "costs": list(costs.values()),
        "models": llm_models,
        "tts_profiles": tts_profiles,
        "cost_is_estimate": True,
    }


class AudioRouteInput(Input):
    primary_profile_id: UUID
    fallback_profile_id: UUID | None = None


@router.get("/admin/audio/routes")
def audio_routes(admin=Depends(require_admin), db: Session = Depends(get_db)):
    from app.db.audio_models import TTSTaskRoute

    rows = db.scalars(
        select(TTSTaskRoute).order_by(TTSTaskRoute.version.desc()).limit(100)
    )
    return {
        "items": [
            {
                k: getattr(row, k)
                for k in (
                    "id",
                    "primary_profile_id",
                    "fallback_profile_id",
                    "version",
                    "created_at",
                )
            }
            | {"fallback_policy": "manual"}
            for row in rows
        ]
    }


@router.post(
    "/admin/audio/routes", status_code=201, dependencies=[Depends(require_csrf)]
)
def add_audio_route(
    payload: AudioRouteInput,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.db.audio_models import TTSProfile, TTSTaskRoute

    db.scalars(
        select(User).where(User.role == "admin").order_by(User.id).with_for_update()
    ).all()
    if payload.primary_profile_id == payload.fallback_profile_id:
        raise AppError(
            422, "validation_error", "Основной и резервный профили должны различаться"
        )
    for identifier in (payload.primary_profile_id, payload.fallback_profile_id):
        if identifier and not required(db, TTSProfile, identifier).enabled:
            raise AppError(422, "tts_unavailable", "Выбранный профиль отключён")
    version = (db.scalar(select(func.max(TTSTaskRoute.version))) or 0) + 1
    row = TTSTaskRoute(
        primary_profile_id=str(payload.primary_profile_id),
        fallback_profile_id=str(payload.fallback_profile_id)
        if payload.fallback_profile_id
        else None,
        version=version,
    )
    db.add(row)
    db.flush()
    audit(db, admin, "tts_route_created", row.id)
    db.commit()
    return {"id": row.id, "version": row.version, "fallback_policy": "manual"}


def archive_config(db, admin, model, identifier, action):
    row = required(db, model, identifier)
    row.enabled = False
    audit(db, admin, action, row.id)
    db.commit()


@router.delete(
    "/admin/providers/{provider_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
def archive_provider(
    provider_id: UUID, admin=Depends(require_admin), db: Session = Depends(get_db)
):
    archive_config(db, admin, Provider, provider_id, "provider_archived")


@router.delete(
    "/admin/models/{model_id}", status_code=204, dependencies=[Depends(require_csrf)]
)
def archive_model(
    model_id: UUID, admin=Depends(require_admin), db: Session = Depends(get_db)
):
    archive_config(db, admin, ProviderModel, model_id, "provider_model_archived")
