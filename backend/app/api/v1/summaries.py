"""Owner-scoped results and versioned administrative prompt configuration."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.ai import audit, paged, required
from app.core.errors import AppError
from app.db.ai_models import Job
from app.db.models import User
from app.db.session import get_db
from app.db.summary_models import (
    ProcessingStep,
    PromptPublication,
    PromptTemplate,
    PromptVersion,
    Summary,
    SummaryNode,
    SummarySourceSpan,
    SummaryValidation,
)
from app.domains.auth.dependencies import require_admin, require_csrf, require_user
from app.domains.jobs import service as jobs
from app.domains.jobs.service import own_job, serialize_job
from app.domains.library.service import item_for_user, locked_user
from app.domains.summaries import service
from app.domains.summaries.plan import PIPELINE, parameters
from app.domains.summaries.prompts import validate_template
from app.domains.summaries.schemas import (
    ProcessInput,
    PromptInput,
    PromptTemplateInput,
    PromptTestInput,
    RollbackInput,
)

router = APIRouter(prefix="/api/v1", tags=["summaries"])


def fields(row, keys):
    return {key: getattr(row, key) for key in keys.split()}


def prompt_version(row):
    return fields(row, "id template_id version body variables_schema status created_at")


def template_record(db, row):
    active = db.get(PromptPublication, row.id)
    return {
        **fields(row, "id task_type name"),
        "active_version_id": active.version_id if active else None,
    }


@router.get("/admin/prompts")
def templates(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return paged(
        db,
        select(PromptTemplate).order_by(PromptTemplate.task_type),
        lambda r: template_record(db, r),
        offset,
        limit,
    )


@router.post("/admin/prompts", status_code=201, dependencies=[Depends(require_csrf)])
def create_template(
    payload: PromptTemplateInput,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    locked_user(db, user)
    if db.scalar(
        select(PromptTemplate).where(PromptTemplate.task_type == payload.task_type)
    ):
        raise AppError(409, "prompt_exists", "Промпт этой задачи уже существует")
    row = PromptTemplate(**payload.model_dump())
    db.add(row)
    db.flush()
    audit(db, user, "prompt_template_created", row.id)
    db.commit()
    return template_record(db, row)


@router.get("/admin/prompts/{template_id}/versions")
def versions(
    template_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    required(db, PromptTemplate, template_id)
    return paged(
        db,
        select(PromptVersion)
        .where(PromptVersion.template_id == str(template_id))
        .order_by(PromptVersion.version.desc()),
        prompt_version,
        offset,
        limit,
    )


@router.post(
    "/admin/prompts/{template_id}/versions",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
def create_version(
    template_id: UUID,
    payload: PromptInput,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    locked_user(db, user)
    template = db.scalar(
        select(PromptTemplate)
        .where(PromptTemplate.id == str(template_id))
        .with_for_update()
    )
    if not template:
        raise AppError(404, "prompt_missing", "Промпт не найден")
    validate_template(payload.body, payload.variables_schema)
    number = (
        db.scalar(
            select(func.max(PromptVersion.version)).where(
                PromptVersion.template_id == template.id
            )
        )
        or 0
    )
    row = PromptVersion(
        template_id=template.id,
        version=number + 1,
        created_by=user.id,
        **payload.model_dump(),
    )
    db.add(row)
    db.flush()
    audit(db, user, "prompt_draft_created", row.id)
    db.commit()
    return prompt_version(row)


@router.patch(
    "/admin/prompt-versions/{version_id}", dependencies=[Depends(require_csrf)]
)
def edit_version(
    version_id: UUID,
    payload: PromptInput,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    locked_user(db, user)
    row = db.scalar(
        select(PromptVersion)
        .where(PromptVersion.id == str(version_id))
        .with_for_update()
    )
    if not row:
        raise AppError(404, "prompt_missing", "Версия промпта не найдена")
    if row.status != "draft":
        raise AppError(
            409,
            "prompt_immutable",
            "Опубликованную версию нельзя редактировать; создайте новую",
        )
    validate_template(payload.body, payload.variables_schema)
    row.body, row.variables_schema = payload.body, payload.variables_schema
    audit(db, user, "prompt_draft_updated", row.id)
    db.commit()
    return prompt_version(row)


def activate(db, user, row, rollback=False):
    db.scalar(
        select(PromptTemplate)
        .where(PromptTemplate.id == row.template_id)
        .with_for_update()
    )
    if rollback and row.status != "published":
        raise AppError(
            422, "prompt_not_published", "Откат доступен только к опубликованной версии"
        )
    row.status = "published"
    active = db.get(PromptPublication, row.template_id)
    if active:
        active.version_id = row.id
    else:
        db.add(PromptPublication(template_id=row.template_id, version_id=row.id))
    audit(db, user, "prompt_rollback" if rollback else "prompt_published", row.id)
    db.commit()
    return prompt_version(row)


@router.post(
    "/admin/prompt-versions/{version_id}/publish", dependencies=[Depends(require_csrf)]
)
def publish_version(
    version_id: UUID, user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    locked_user(db, user)
    return activate(db, user, required(db, PromptVersion, version_id))


@router.post(
    "/admin/prompts/{template_id}/rollback", dependencies=[Depends(require_csrf)]
)
def rollback(
    template_id: UUID,
    payload: RollbackInput,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    locked_user(db, user)
    row = required(db, PromptVersion, payload.version_id)
    if row.template_id != str(template_id):
        raise AppError(404, "prompt_missing", "Версия не принадлежит этому промпту")
    return activate(db, user, row, True)


@router.post(
    "/admin/prompt-versions/{version_id}/test",
    status_code=202,
    dependencies=[Depends(require_csrf)],
)
def test_prompt(
    version_id: UUID,
    payload: PromptTestInput,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    locked_user(db, user)
    version = required(db, PromptVersion, version_id)
    template = required(db, PromptTemplate, version.template_id)
    if payload.route_id:
        raise AppError(422, "validation_error", "Используется маршрут задачи промпта")
    request_hash = service.digest(
        {
            "version": version.id,
            **payload.model_dump(mode="json", exclude={"idempotency_key"}),
        }
    )
    old = service.existing_job(db, user, payload.idempotency_key, request_hash)
    if old:
        return serialize_job(old)
    routes, prompts = service.snapshots(
        db, [template.task_type], prompt_override=version.id
    )
    external = any(
        m["protocol"] != "mock" for route in routes.values() for m in route["models"]
    )
    if external and not payload.consent_external:
        raise AppError(
            422,
            "external_consent_required",
            "Подтвердите передачу тестового текста AI-провайдеру",
        )
    config = parameters()
    chunk = {
        "start_offset": 0,
        "end_offset": len(payload.text),
        "context_start": 0,
        "context_end": len(payload.text),
        "text_version_id": str(uuid4()),
        "section_id": str(uuid4()),
    }
    episode = {
        "title": "Тест",
        "start_offset": 0,
        "end_offset": len(payload.text),
        "quote": payload.text[:160],
    }
    kind = {
        "structure": "structure",
        "summary_detailed": "detailed",
        "summary_short": "short",
        "summary_medium": "aggregate",
        "validation": "validate",
    }[template.task_type]
    test_payload = {"operation": kind, "limits": config}
    if kind in ("structure", "detailed", "validate"):
        test_payload.update(chunk=chunk, source_text=payload.text, episodes=[episode])
    else:
        test_payload.update(
            source_ids=["test"],
            children=[{"id": "test", "text": payload.text}],
            level="medium" if kind == "aggregate" else "short",
        )
    if kind == "validate":
        test_payload["candidate"] = {
            "nodes": [
                {
                    "episode_index": 0,
                    "text": payload.text[: config["episode_max_chars"]],
                    "text_version_id": chunk["text_version_id"],
                    "section_id": chunk["section_id"],
                    **{k: episode[k] for k in ("start_offset", "end_offset", "quote")},
                }
            ]
        }
    plan = [
        {
            "order_index": 0,
            "task_type": template.task_type,
            "parameters": {"kind": kind, "round": 1, "test_payload": test_payload},
            "dependencies": [],
        }
    ]
    reservations = service.reserve_plan(plan, config, routes, prompts)
    service.check_quota(db, user, reservations)
    job = Job(
        user_id=user.id,
        type="prompt_test",
        idempotency_key=payload.idempotency_key,
        request_hash=request_hash,
        reservations=reservations,
        snapshot={
            "pipeline": PIPELINE,
            "parameters": config,
            "routes": routes,
            "prompts": prompts,
            "test_text": payload.text,
        },
    )
    db.add(job)
    db.flush()
    db.add(ProcessingStep(job_id=job.id, **plan[0]))
    audit(db, user, "prompt_test_created", job.id)
    db.commit()
    jobs.publish(job.id)
    return serialize_job(job)


@router.get("/jobs/{job_id}/prompt-result")
def prompt_result(
    job_id: UUID, user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    job = own_job(db, str(job_id), user.id)
    if job.type != "prompt_test" or job.status != "completed":
        raise AppError(409, "result_unavailable", "Тест ещё не завершён")
    step = db.scalar(select(ProcessingStep).where(ProcessingStep.job_id == job.id))
    return {"content": step.content, "provenance": step.provenance}


@router.post(
    "/library/{item_id}/process", status_code=202, dependencies=[Depends(require_csrf)]
)
def process(
    item_id: UUID,
    payload: ProcessInput,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    job = service.create_processing(db, user, str(item_id), payload)
    if job.status == "queued":
        jobs.publish(job.id)
    return serialize_job(job)


@router.get("/library/{item_id}/processing-config")
def processing_config(
    item_id: UUID, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    return service.configuration_status(db, str(item_id), user)


@router.get("/library/{item_id}/summaries")
def summaries(
    item_id: UUID,
    level: str = Query("detailed", pattern="^(short|medium|detailed)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    item_for_user(db, str(item_id), user.id)
    return paged(
        db,
        select(Summary)
        .where(Summary.library_item_id == str(item_id), Summary.level == level)
        .order_by(Summary.created_at.desc()),
        lambda r: fields(
            r,
            "id generation_id text_version_id source_job_id level "
            "content_hash provenance created_at",
        ),
        offset,
        limit,
    )


def own_summary(db, item_id, summary_id, user):
    item_for_user(db, str(item_id), user.id)
    summary = db.get(Summary, str(summary_id))
    if not summary or summary.library_item_id != str(item_id):
        raise AppError(404, "summary_missing", "Резюме не найдено")
    return summary


@router.get("/library/{item_id}/summaries/{summary_id}/nodes")
def nodes(
    item_id: UUID,
    summary_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    summary = own_summary(db, item_id, summary_id, user)

    def record(row):
        spans = db.scalars(
            select(SummarySourceSpan).where(SummarySourceSpan.node_id == row.id)
        )
        return {
            **fields(row, "id parent_id kind title text order_index provenance"),
            "source_spans": [
                fields(span, "text_version_id section_id start_offset end_offset")
                for span in spans
            ],
        }

    return paged(
        db,
        select(SummaryNode)
        .where(SummaryNode.summary_id == summary.id)
        .order_by(SummaryNode.order_index),
        record,
        offset,
        limit,
    )


@router.get("/library/{item_id}/summaries/{summary_id}/validations")
def validations(
    item_id: UUID,
    summary_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    summary = own_summary(db, item_id, summary_id, user)
    return paged(
        db,
        select(SummaryValidation)
        .where(SummaryValidation.summary_id == summary.id)
        .order_by(SummaryValidation.step_index),
        lambda r: fields(r, "step_index findings provenance"),
        offset,
        limit,
    )


@router.get("/jobs/{job_id}/validation-findings")
def job_findings(
    job_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    job = own_job(db, str(job_id), user.id)
    return paged(
        db,
        select(ProcessingStep)
        .where(
            ProcessingStep.job_id == job.id,
            ProcessingStep.task_type == "validation",
            ProcessingStep.content.is_not(None),
        )
        .order_by(ProcessingStep.order_index),
        lambda r: {
            "step_index": r.order_index,
            "findings": r.content,
            "provenance": r.provenance,
        },
        offset,
        limit,
    )
