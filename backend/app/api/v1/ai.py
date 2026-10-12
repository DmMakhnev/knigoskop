"""Provider configuration and diagnostics; content is served through owner APIs."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.provider_secrets import cipher, encrypt, environment_key
from app.db.ai_models import (
    Job,
    Provider,
    ProviderCredential,
    ProviderModel,
    QuotaOverride,
    QuotaPolicy,
    TaskModelRoute,
    UsageEvent,
)
from app.db.models import AuditEvent, User, utcnow
from app.db.session import get_db
from app.domains.auth.dependencies import require_admin, require_csrf, require_user
from app.domains.jobs import service as jobs_service
from app.domains.jobs.schemas import (
    CredentialInput,
    EnabledInput,
    JobInput,
    ModelInput,
    ProviderInput,
    QuotaInput,
    RouteInput,
)
from app.domains.jobs.service import (
    TERMINAL,
    create_job,
    model_snapshot,
    own_job,
    quota_state,
    serialize_job,
    serialize_provider,
    usage_record,
)
from app.domains.library.service import locked_user

router = APIRouter(prefix="/api/v1", tags=["ai-infrastructure"])


def paged(db, query, serializer, offset, limit):
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    return {
        "items": [
            serializer(row) for row in db.scalars(query.offset(offset).limit(limit))
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def audit(db, actor, action, entity_id):
    db.add(AuditEvent(actor_id=actor.id, action=action, entity_id=entity_id))


def required(db, model, object_id):
    row = db.get(model, str(object_id))
    if not row:
        raise AppError(404, "ai_config_not_found", "Настройка AI не найдена")
    return row


def serialize_model(model):
    return {
        key: getattr(model, key)
        for key in (
            "id",
            "provider_id",
            "model_identifier",
            "capabilities",
            "pricing",
            "enabled",
        )
    }


def serialize_route(route):
    return {
        key: getattr(route, key)
        for key in (
            "id",
            "task_type",
            "primary_model_id",
            "fallback_model_id",
            "config_version",
            "policy",
            "created_at",
        )
    }


@router.get("/admin/ai-status")
def status(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        cipher()
        secrets_ready = True
    except AppError:
        secrets_ready = False
    from app.db.summary_models import PromptPublication, PromptTemplate
    from app.domains.summaries.schemas import TASKS

    configured = set(db.scalars(select(TaskModelRoute.task_type)))
    published = set(
        db.scalars(select(PromptTemplate.task_type).join(PromptPublication))
    )
    return {
        "master_key_configured": secrets_ready,
        "environment_key_configured": bool(
            settings.ai_env_provider_host
            and settings.knigoskop_llm_api_key
            and settings.knigoskop_llm_api_key.get_secret_value()
        ),
        "allowed_hosts": settings.ai_allowed_hosts,
        "route_configured": db.scalar(select(TaskModelRoute.id).limit(1)) is not None,
        "book_processing_available": set(TASKS) <= configured & published,
        "task_types": ["diagnostic", *TASKS],
    }


@router.get("/admin/providers")
def providers(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return paged(
        db,
        select(Provider).order_by(Provider.id),
        lambda row: serialize_provider(db, row),
        offset,
        limit,
    )


@router.post("/admin/providers", status_code=201, dependencies=[Depends(require_csrf)])
def add_provider(
    payload: ProviderInput,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.protocol != "mock" and payload.credential_source == "environment":
        environment_key(payload.host)
    provider = Provider(**payload.model_dump(exclude={"api_key", "credential_source"}))
    db.add(provider)
    db.flush()
    if payload.api_key:
        db.add(
            ProviderCredential(
                provider_id=provider.id,
                encrypted_secret=encrypt(payload.api_key.get_secret_value()),
            )
        )
    audit(db, admin, "provider_created", provider.id)
    db.commit()
    return serialize_provider(db, provider)


@router.patch("/admin/providers/{provider_id}", dependencies=[Depends(require_csrf)])
def enable_provider(
    provider_id: UUID,
    payload: EnabledInput,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    provider = db.scalar(
        select(Provider).where(Provider.id == str(provider_id)).with_for_update()
    )
    if not provider:
        raise AppError(404, "ai_config_not_found", "Провайдер не найден")
    provider.enabled = payload.enabled
    audit(db, admin, "provider_enabled_changed", provider.id)
    db.commit()
    return serialize_provider(db, provider)


@router.put(
    "/admin/providers/{provider_id}/credential", dependencies=[Depends(require_csrf)]
)
def set_credential(
    provider_id: UUID,
    payload: CredentialInput,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    provider = db.scalar(
        select(Provider).where(Provider.id == str(provider_id)).with_for_update()
    )
    secret = payload.api_key.get_secret_value()
    if not provider:
        raise AppError(404, "ai_config_not_found", "Провайдер не найден")
    if provider.protocol == "mock" or any(c in secret for c in "\r\n"):
        raise AppError(422, "validation_error", "Ключ не подходит провайдеру")
    credential = db.scalar(
        select(ProviderCredential).where(ProviderCredential.provider_id == provider.id)
    )
    if not credential:
        credential = ProviderCredential(provider_id=provider.id)
        db.add(credential)
    credential.encrypted_secret = encrypt(secret)
    audit(db, admin, "provider_credential_changed", provider.id)
    db.commit()
    return serialize_provider(db, provider)


@router.get("/admin/models")
def models(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return paged(
        db,
        select(ProviderModel).order_by(ProviderModel.id),
        serialize_model,
        offset,
        limit,
    )


@router.post("/admin/models", status_code=201, dependencies=[Depends(require_csrf)])
def add_model(
    payload: ModelInput,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    provider = required(db, Provider, payload.provider_id)
    if provider.protocol != "mock" and payload.capabilities.mock_behavior != "success":
        raise AppError(
            422,
            "validation_error",
            "Mock-поведение доступно только локальному адаптеру",
        )
    model = ProviderModel(
        provider_id=provider.id,
        model_identifier=payload.model_identifier,
        capabilities=payload.capabilities.model_dump(),
        pricing=payload.pricing.model_dump() if payload.pricing else {},
    )
    db.add(model)
    db.flush()
    audit(db, admin, "provider_model_created", model.id)
    db.commit()
    return serialize_model(model)


@router.patch("/admin/models/{model_id}", dependencies=[Depends(require_csrf)])
def enable_model(
    model_id: UUID,
    payload: EnabledInput,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    model = required(db, ProviderModel, model_id)
    model.enabled = payload.enabled
    audit(db, admin, "provider_model_enabled_changed", model.id)
    db.commit()
    return serialize_model(model)


@router.post(
    "/admin/models/{model_id}/test",
    status_code=202,
    dependencies=[Depends(require_csrf)],
)
def test_model(
    model_id: UUID,
    payload: JobInput,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    job = create_job(db, admin, payload, model_id=str(model_id))
    jobs_service.publish(job.id)
    return serialize_job(job)


@router.get("/admin/routes")
def routes(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return paged(
        db,
        select(TaskModelRoute).order_by(TaskModelRoute.config_version.desc()),
        serialize_route,
        offset,
        limit,
    )


@router.post("/admin/routes", status_code=201, dependencies=[Depends(require_csrf)])
def add_route(
    payload: RouteInput,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    # Serialize version allocation against stable existing provider rows.
    list(db.scalars(select(Provider).order_by(Provider.id).with_for_update()))
    for model_id in (payload.primary_model_id, payload.fallback_model_id):
        if model_id:
            model_snapshot(db, str(model_id))
    if payload.primary_model_id == payload.fallback_model_id:
        raise AppError(
            422, "validation_error", "Основная и резервная модели должны различаться"
        )
    version = (
        db.scalar(
            select(func.max(TaskModelRoute.config_version)).where(
                TaskModelRoute.task_type == payload.task_type
            )
        )
        or 0
    ) + 1
    route = TaskModelRoute(
        task_type=payload.task_type,
        primary_model_id=str(payload.primary_model_id),
        fallback_model_id=str(payload.fallback_model_id)
        if payload.fallback_model_id
        else None,
        config_version=version,
        policy=payload.policy.model_dump(),
    )
    db.add(route)
    db.flush()
    audit(db, admin, "model_route_created", route.id)
    db.commit()
    return serialize_route(route)


@router.post("/jobs/diagnostic", status_code=202, dependencies=[Depends(require_csrf)])
def diagnostic(
    payload: JobInput, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    job = create_job(db, user, payload)
    jobs_service.publish(job.id)
    return serialize_job(job)


@router.get("/jobs")
def jobs(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return paged(
        db,
        select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc()),
        serialize_job,
        offset,
        limit,
    )


@router.get("/jobs/{job_id}")
def job_status(
    job_id: UUID, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    return serialize_job(own_job(db, str(job_id), user.id))


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_csrf)])
def cancel_job(
    job_id: UUID, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    locked_user(db, user)
    job = own_job(db, str(job_id), user.id, lock=True)
    if job.status not in TERMINAL:
        job.status, job.error_code, job.completed_at = (
            "cancelled",
            "user_cancelled",
            utcnow(),
        )
        db.commit()
    return serialize_job(job)


@router.get("/me/ai-quota")
def quota(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return quota_state(db, user.id)


@router.get("/admin/quotas")
def quotas(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    # Explicit defaults plus paginated overrides, no secret or book fields.
    defaults = quota_state(db, admin.id)["limits"]
    for metric in defaults:
        policy = db.get(QuotaPolicy, metric)
        defaults[metric] = (
            policy.limit_value if policy else getattr(settings, "quota_" + metric)
        )
    overrides = paged(
        db,
        select(QuotaOverride).order_by(QuotaOverride.user_id, QuotaOverride.metric),
        lambda row: {
            "user_id": row.user_id,
            "metric": row.metric,
            "limit_value": row.limit_value,
        },
        offset,
        limit,
    )
    return {"defaults": defaults, "overrides": overrides}


@router.put("/admin/quotas", dependencies=[Depends(require_csrf)])
def set_quota(
    payload: QuotaInput,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.user_id:
        locked_user(db, required(db, User, payload.user_id))
        key = (str(payload.user_id), payload.metric)
        row = db.get(QuotaOverride, key)
        if not row:
            row = QuotaOverride(user_id=str(payload.user_id), metric=payload.metric)
            db.add(row)
    else:
        # New limits apply to subsequent launches; reservations stay frozen.
        row = db.get(QuotaPolicy, payload.metric)
        if not row:
            row = QuotaPolicy(metric=payload.metric)
            db.add(row)
    row.limit_value = payload.limit_value
    audit(db, admin, "ai_quota_changed", str(payload.user_id or admin.id))
    db.commit()
    return payload.model_dump(mode="json")


@router.get("/admin/usage")
def usage(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return paged(
        db,
        select(UsageEvent).order_by(UsageEvent.created_at.desc()),
        usage_record,
        offset,
        limit,
    )


@router.get("/admin/jobs")
def admin_jobs(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return paged(
        db, select(Job).order_by(Job.created_at.desc()), serialize_job, offset, limit
    )
