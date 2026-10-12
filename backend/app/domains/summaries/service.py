"""Scoped launch, bounded reservations, configuration snapshots and caching."""

import copy
import hashlib
import json
from uuid import uuid4

from sqlalchemy import select

from app.core.errors import AppError
from app.db.ai_models import Job, Provider, TaskModelRoute
from app.db.models import AuditEvent, TextSection, TextVersion, utcnow
from app.db.summary_models import ProcessingStep, Summary
from app.domains.jobs.service import METRICS, model_snapshot, quota_state
from app.domains.library.service import item_for_user, locked_user
from app.domains.reader.structure import ensure_structure
from app.domains.summaries.content import response_model
from app.domains.summaries.plan import PIPELINE, build_plan, parameters
from app.domains.summaries.prompts import snapshot_prompt
from app.domains.summaries.schemas import TASKS
from app.integrations.storage import get_storage


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def existing_job(db, user, key, request_hash):
    job = db.scalar(
        select(Job).where(Job.user_id == user.id, Job.idempotency_key == key)
    )
    if job and job.request_hash != request_hash:
        raise AppError(
            409, "idempotency_conflict", "Ключ уже использован для другого запроса"
        )
    return job


def snapshots(db, tasks=TASKS, *, prompt_override=None):
    # Fixed ordering serializes route reads/config edits, avoiding lock inversions.
    list(db.scalars(select(Provider).order_by(Provider.id).with_for_update()))
    routes, prompts = {}, {}
    for task in tasks:
        route = db.scalar(
            select(TaskModelRoute)
            .where(TaskModelRoute.task_type == task)
            .order_by(TaskModelRoute.config_version.desc())
            .limit(1)
        )
        if not route:
            raise AppError(
                503, "ai_unavailable", "Не настроены маршруты обработки книги"
            )
        routes[task] = {
            "id": route.id,
            "config_version": route.config_version,
            "policy": route.policy,
            "models": [model_snapshot(db, route.primary_model_id)]
            + (
                [model_snapshot(db, route.fallback_model_id)]
                if route.fallback_model_id
                else []
            ),
        }
        prompts[task] = snapshot_prompt(db, task, prompt_override)
    return routes, prompts


def sections_for(db, version):
    ensure_structure(db, version, storage=get_storage())
    return [
        {
            key: getattr(row, key)
            for key in (
                "id",
                "kind",
                "title",
                "parent_id",
                "start_offset",
                "end_offset",
                "text_version_id",
                "order_index",
            )
        }
        for row in db.scalars(
            select(TextSection)
            .where(TextSection.text_version_id == version.id)
            .order_by(TextSection.order_index)
        )
    ]


def output_chars(step, config):
    params = step["parameters"]
    if params["kind"] == "structure":
        return 8 * (120 + 160) + 2048
    if params.get("chunk"):
        return 8 * (config["episode_max_chars"] + 460) + 2048
    if params.get("level") == "medium":
        return min(config["medium_target_chars"] * 2, 12000)
    return config["aggregate_max_chars"]


def input_bound(step, plan, config, prompt):
    params = step["parameters"]
    kind = params["kind"]
    if "test_payload" in params:
        from app.domains.summaries.prompts import render

        return (
            len(
                render(
                    prompt, params["test_payload"], config["aggregate_max_chars"]
                ).encode()
            )
            + len(json.dumps(response_model(kind).model_json_schema()).encode()) * 2
            + 8192
        )
    generation = plan[params["candidate"]] if kind == "validate" else step
    source = generation["parameters"]
    chars = 0
    if "chunk" in source:
        chunk = source["chunk"]
        chars += chunk["context_end"] - chunk["context_start"]
    for index in generation["dependencies"]:
        chars += output_chars(plan[index], config)
    if kind == "validate":
        # Only selected corrections are forwarded, never previous findings.
        chars += output_chars(generation, config)
    schema_bytes = len(json.dumps(response_model(kind).model_json_schema()).encode())
    # Six bytes per character also bounds escaped JSON control characters.
    return len(prompt["body"].encode()) + schema_bytes * 2 + 8192 + chars * 6


def reserve_plan(plan, config, routes, prompts):
    reservations = dict.fromkeys(METRICS, 0)
    reservations["daily_jobs"] = reservations["active_jobs"] = 1
    budgets = dict.fromkeys(routes, 0)
    for step in plan:
        if step.get("status") == "completed" or step["parameters"]["kind"] == "select":
            continue
        task = step["task_type"]
        route, prompt = routes[task], prompts[task]
        bound = input_bound(step, plan, config, prompt)
        step["parameters"]["input_bound"] = bound
        policy = route["policy"]
        for model in route["models"]:
            if (
                bound + policy["max_output_tokens"]
                > model["capabilities"]["max_context_tokens"]
            ):
                raise AppError(
                    422,
                    "context_limit",
                    "Недостаточный контекст модели: уменьшите чанки "
                    "или выберите модель с большим контекстом",
                )
            attempts = policy["attempts_per_model"]
            reservations["daily_requests"] += attempts
            reservations["daily_input_chars"] += bound * attempts
            reservations["daily_tokens"] += (
                bound + policy["max_output_tokens"]
            ) * attempts
            if policy["max_cost_micros"] is not None:
                pricing = model["pricing"]
                if not pricing or pricing["currency"] != policy["currency"]:
                    raise AppError(
                        422, "pricing_required", "Для бюджета нужны цены в одной валюте"
                    )
                budgets[task] += (
                    (
                        bound * pricing["input_micros_per_million"]
                        + policy["max_output_tokens"]
                        * pricing["output_micros_per_million"]
                        + 999999
                    )
                    // 1000000
                ) * attempts
    if any(
        routes[task]["policy"]["max_cost_micros"] is not None
        and spent > routes[task]["policy"]["max_cost_micros"]
        for task, spent in budgets.items()
    ):
        raise AppError(429, "budget_exceeded", "План превышает бюджет маршрута")
    return reservations


def check_quota(db, user, reservations):
    quotas = quota_state(db, user.id)
    if any(
        quotas["reserved"][metric] + reservations[metric] > quotas["limits"][metric]
        for metric in METRICS
    ):
        raise AppError(
            429, "quota_exceeded", "Квота AI исчерпана; обработка не запущена"
        )


def create_processing(db, user, item_id, payload):
    locked_user(db, user)
    item = item_for_user(db, item_id, user.id)
    if payload.route_id:
        raise AppError(422, "validation_error", "Маршруты задаются отдельно по задачам")
    if bool(payload.chapter_id) != bool(payload.base_summary_id):
        raise AppError(422, "validation_error", "Для главы нужна базовая версия резюме")
    request_hash = digest(
        {
            "item_id": item.id,
            "type": "processing",
            **payload.model_dump(mode="json", exclude={"idempotency_key"}),
        }
    )
    existing = existing_job(db, user, payload.idempotency_key, request_hash)
    if existing:
        return existing
    version = db.get(TextVersion, item.text_version_id)
    sections = sections_for(db, version)
    config = parameters()
    routes, prompts = snapshots(db)
    external = any(
        model["protocol"] != "mock"
        for route in routes.values()
        for model in route["models"]
    )
    if external and not payload.consent_external:
        raise AppError(
            422,
            "external_consent_required",
            "Подтвердите передачу текста выбранным AI-провайдерам",
        )
    if payload.chapter_id:
        base = db.get(Summary, str(payload.base_summary_id))
        if (
            not base
            or base.library_item_id != item.id
            or base.text_version_id != version.id
            or base.level != "detailed"
            or not base.source_job_id
        ):
            raise AppError(404, "summary_missing", "Базовая версия резюме не найдена")
        chapter = next(
            (
                s
                for s in sections
                if s["id"] == str(payload.chapter_id) and s["kind"] == "chapter"
            ),
            None,
        )
        if not chapter:
            raise AppError(
                422, "chapter_invalid", "Глава не принадлежит этой версии текста"
            )
        base_job = db.get(Job, base.source_job_id)
        config = base_job.snapshot["parameters"]
        plan = [
            {
                "order_index": step.order_index,
                "task_type": step.task_type,
                "parameters": copy.deepcopy(step.parameters),
                "dependencies": step.dependencies,
                "status": "completed",
                "content": step.content,
                "provenance": step.provenance,
            }
            for step in db.scalars(
                select(ProcessingStep)
                .where(ProcessingStep.job_id == base_job.id)
                .order_by(ProcessingStep.order_index)
            )
        ]
        changed = set()
        for step in plan:
            chunk = step["parameters"].get("chunk")
            if (
                chunk
                and chapter["start_offset"]
                <= chunk["start_offset"]
                < chapter["end_offset"]
            ) or any(index in changed for index in step["dependencies"]):
                changed.add(step["order_index"])
                step.update(status="pending", content=None, provenance=None)
        outputs = base_job.snapshot["outputs"]
    else:
        plan, outputs = build_plan(sections, config)
    config_hash = digest(
        {
            "version_id": version.id,
            "routes": {task: r["id"] for task, r in routes.items()},
            "prompts": {task: p["id"] for task, p in prompts.items()},
            "parameters": config,
            "pipeline": PIPELINE,
            "base_summary_id": str(payload.base_summary_id)
            if payload.base_summary_id
            else None,
            "chapter_id": str(payload.chapter_id) if payload.chapter_id else None,
        }
    )
    cached = (
        None
        if payload.force or payload.chapter_id
        else db.scalar(
            select(Summary)
            .where(
                Summary.library_item_id == item.id,
                Summary.config_hash == config_hash,
                Summary.level == "detailed",
            )
            .order_by(Summary.created_at.desc())
            .limit(1)
        )
    )
    reservations = (
        dict.fromkeys(METRICS, 0)
        if cached
        else reserve_plan(plan, config, routes, prompts)
    )
    if not cached:
        check_quota(db, user, reservations)
    job = Job(
        id=str(uuid4()),
        user_id=user.id,
        library_item_id=item.id,
        type="processing",
        idempotency_key=payload.idempotency_key,
        request_hash=request_hash,
        snapshot={
            "pipeline": PIPELINE,
            "parameters": config,
            "sections": sections,
            "outputs": outputs,
            "routes": routes,
            "prompts": prompts,
            "text_version_id": version.id,
            "checksum": version.checksum,
            "storage_key": version.normalized_text_key,
            "config_hash": config_hash,
            "external": external,
            "chapter_id": str(payload.chapter_id) if payload.chapter_id else None,
        },
        reservations=reservations,
    )
    if cached:
        job.status, job.progress, job.completed_at = "completed", 100, utcnow()
        job.result = {"generation_id": cached.generation_id, "cached": True}
    db.add(job)
    db.flush()
    if not cached:
        for row in plan:
            db.add(ProcessingStep(job_id=job.id, **row))
    db.add(
        AuditEvent(actor_id=user.id, action="book_processing_created", entity_id=job.id)
    )
    db.commit()
    return job


def configuration_status(db, item_id, user):
    item = item_for_user(db, item_id, user.id)
    missing = []
    provider_names = set()
    external = False
    for task in TASKS:
        route = db.scalar(
            select(TaskModelRoute)
            .where(TaskModelRoute.task_type == task)
            .order_by(TaskModelRoute.config_version.desc())
            .limit(1)
        )
        if not route:
            missing.append(task)
            continue
        try:
            snap = snapshots(db, [task])
            for model in snap[0][task]["models"]:
                provider_names.add(model["protocol"] + ": " + model["model_identifier"])
                external |= model["protocol"] != "mock"
        except AppError:
            missing.append(task)
    latest = db.scalar(
        select(Job)
        .where(Job.library_item_id == item.id, Job.user_id == user.id)
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return {
        "ready": not missing,
        "missing_tasks": missing,
        "external": external,
        "models": sorted(provider_names),
        "latest_job_id": latest.id if latest else None,
    }
