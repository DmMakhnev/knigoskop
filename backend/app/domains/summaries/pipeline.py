"""One network call per delivery; durable checkpoints and atomic publication."""

import copy
import hashlib
import time
from datetime import timedelta
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.ai_models import Job, Provider, ProviderModel, UsageEvent
from app.db.models import TextVersion, User, utcnow
from app.db.summary_models import (
    ProcessingStep,
    Summary,
    SummaryNode,
    SummarySourceSpan,
    SummaryValidation,
)
from app.domains.jobs.service import TERMINAL, publish, utc
from app.domains.summaries.content import response_schema, validate_content
from app.domains.summaries.prompts import render
from app.domains.summaries.service import digest
from app.integrations import llm
from app.integrations.storage import get_storage


def steps_for(db, job_id):
    return list(
        db.scalars(
            select(ProcessingStep)
            .where(ProcessingStep.job_id == job_id)
            .order_by(ProcessingStep.order_index)
        )
    )


def source_text(job):
    if job.type == "prompt_test":
        return job.snapshot["test_text"]
    data = get_storage().read(job.snapshot["storage_key"])
    if hashlib.sha256(data).hexdigest() != job.snapshot["checksum"]:
        raise AppError(503, "text_integrity", "Не удалось проверить целостность текста")
    return data.decode("utf-8")


def plain(content):
    return (
        " ".join(node["text"] for node in content["nodes"])
        if "nodes" in content
        else content["text"]
    )


def corrected(candidate, check):
    result = copy.deepcopy(candidate)
    if check.get("corrected_nodes"):
        result["nodes"] = check["corrected_nodes"]
    if check.get("corrected_text"):
        result["text"] = check["corrected_text"]
    return result


def payload_for(step, steps, job, text):
    params = step.parameters
    kind = params["kind"]
    if "test_payload" in params:
        return params["test_payload"]
    if kind == "validate":
        generation = steps[params["candidate"]]
        payload = payload_for(generation, steps, job, text)
        candidate = generation.content
        if params["round"] == 2:
            candidate = corrected(candidate, steps[params["first"]].content)
        return {
            **payload,
            "operation": "validate",
            "validation_round": params["round"],
            "candidate": candidate,
        }
    if "chunk" in params:
        chunk = params["chunk"]
        payload = {
            "operation": kind,
            "chunk": chunk,
            "source_text": text[chunk["context_start"] : chunk["context_end"]],
            "core_range": {
                "start_offset": chunk["start_offset"],
                "end_offset": chunk["end_offset"],
                "quote": text[chunk["start_offset"] : chunk["end_offset"]][:160],
            },
        }
        if kind != "structure":
            payload["episodes"] = steps[chunk["structure_step"]].content["episodes"]
        return {**payload, "limits": job.snapshot["parameters"]}
    return {
        "operation": kind,
        "level": params["level"],
        "source_ids": params["source_ids"],
        "children": [
            {"id": f"step:{index}", "text": plain(steps[index].content)}
            for index in step.dependencies
        ],
        "limits": job.snapshot["parameters"],
    }


def local_result(step, steps):
    params = step.parameters
    if params["kind"] == "select":
        original, first, second = [
            steps[params[key]] for key in ("candidate", "first", "second")
        ]
        result = corrected(original.content, first.content)
        result = corrected(result, second.content)
        return result, {
            "generation": original.provenance,
            "checks": [first.provenance, second.provenance],
            "mock": bool(original.provenance.get("mock")),
        }
    if params["kind"] == "validate" and params["round"] == 2:
        first = steps[params["first"]].content
        if not first["corrected_nodes"] and not first["corrected_text"]:
            return {
                "findings": [],
                "corrected_nodes": [],
                "corrected_text": "",
                "skipped": True,
            }, {"skipped": True}
    return None


def finalize(db, job, steps):
    if job.type == "prompt_test":
        job.status, job.progress, job.completed_at = "completed", 100, utcnow()
        job.result = {
            "prompt_version_id": job.snapshot["prompts"][steps[0].task_type]["id"]
        }
        return
    outputs = job.snapshot["outputs"]
    generation_id = str(uuid4())
    section_map = {row["id"]: row for row in job.snapshot["sections"]}
    config = job.snapshot["parameters"]
    checks = [
        step
        for step in steps
        if step.parameters["kind"] == "validate" and not step.content.get("skipped")
    ]
    provenance = {
        "pipeline": job.snapshot["pipeline"],
        "parameters": config,
        "mock": all(
            step.provenance.get("mock", True)
            for step in steps
            if step.parameters["kind"] != "select"
        ),
        "config_hash": job.snapshot["config_hash"],
        "regenerated_chapter_id": job.snapshot["chapter_id"],
        "routes": {
            task: {"id": route["id"], "config_version": route["config_version"]}
            for task, route in job.snapshot["routes"].items()
        },
        "prompt_versions": {
            task: prompt["id"] for task, prompt in job.snapshot["prompts"].items()
        },
        "validation_notice": "AI-проверка не гарантирует отсутствия ошибок",
    }
    for level in ("detailed", "short", "medium"):
        node_specs = []
        if level == "detailed":
            for section in job.snapshot["sections"]:
                if section["kind"] == "fragment":
                    continue
                selected = steps[outputs["sections"][section["id"]]]
                node_specs.append(
                    {
                        "key": "section:" + section["id"],
                        "parent": "section:" + section["parent_id"]
                        if section["parent_id"]
                        else None,
                        "kind": section["kind"],
                        "title": section["title"],
                        "text": selected.content["text"],
                        "section": section,
                        "start": section["start_offset"],
                        "end": section["end_offset"],
                        "provenance": selected.provenance,
                    }
                )
            for chunk, selected_index in outputs["chunks"]:
                selected = steps[selected_index]
                episodes = steps[chunk["structure_step"]].content["episodes"]
                for node in selected.content["nodes"]:
                    node_specs.append(
                        {
                            "key": (
                                f"episode:{chunk['start_offset']}:"
                                f"{node['episode_index']}"
                            ),
                            "parent": "section:" + chunk["parent_section_id"],
                            "kind": "episode",
                            "title": episodes[node["episode_index"]]["title"],
                            "text": node["text"],
                            "section": section_map[node["section_id"]],
                            "start": node["start_offset"],
                            "end": node["end_offset"],
                            "provenance": selected.provenance,
                        }
                    )
        else:
            selected = steps[outputs[level]]
            section = section_map[outputs["root"]]
            node_specs.append(
                {
                    "key": "root",
                    "parent": None,
                    "kind": "root",
                    "title": "Краткое резюме" if level == "short" else "Среднее резюме",
                    "text": selected.content["text"],
                    "section": section,
                    "start": section["start_offset"],
                    "end": section["end_offset"],
                    "provenance": selected.provenance,
                }
            )
        summary = Summary(
            id=str(uuid4()),
            generation_id=generation_id,
            library_item_id=job.library_item_id,
            text_version_id=job.snapshot["text_version_id"],
            source_job_id=job.id,
            level=level,
            config_hash=job.snapshot["config_hash"],
            content_hash=digest(node_specs),
            provenance=provenance,
        )
        db.add(summary)
        db.flush()
        # Parents precede children. Display ordering follows original ranges.
        ids = {
            spec["key"]: str(uuid5(UUID(summary.id), spec["key"]))
            for spec in node_specs
        }
        display_order = {
            spec["key"]: index
            for index, spec in enumerate(
                sorted(
                    node_specs, key=lambda row: (row["start"], row["kind"] == "episode")
                )
            )
        }
        for spec in node_specs:
            node = SummaryNode(
                id=ids[spec["key"]],
                summary_id=summary.id,
                text_version_id=summary.text_version_id,
                parent_id=ids[spec["parent"]] if spec["parent"] else None,
                source_section_id=spec["section"]["id"],
                node_key=spec["key"],
                kind=spec["kind"],
                title=spec["title"],
                text=spec["text"],
                order_index=display_order[spec["key"]],
                provenance=spec["provenance"],
            )
            db.add(node)
            db.flush()
            db.add(
                SummarySourceSpan(
                    node_id=node.id,
                    text_version_id=summary.text_version_id,
                    section_id=node.source_section_id,
                    start_offset=spec["start"],
                    end_offset=spec["end"],
                )
            )
        for check in checks:
            db.add(
                SummaryValidation(
                    summary_id=summary.id,
                    step_index=check.order_index,
                    findings=check.content,
                    provenance=check.provenance,
                )
            )
    job.status, job.progress, job.completed_at = "completed", 100, utcnow()
    job.result = {"generation_id": generation_id, "cached": False}


def execute_processing(job_id, engine):
    with Session(engine) as db:
        owner = db.scalar(select(Job.user_id).where(Job.id == job_id))
        if not owner:
            return
        user = db.scalar(select(User).where(User.id == owner).with_for_update())
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if (
            not user
            or not job
            or job.status != "queued"
            or utc(job.next_run_at) > utcnow()
        ):
            return
        if user.status != "active":
            job.status, job.error_code, job.completed_at = (
                "cancelled",
                "user_blocked",
                utcnow(),
            )
            db.commit()
            return
        steps = steps_for(db, job_id)
        try:
            for step in steps:
                if step.status == "completed":
                    continue
                if any(
                    steps[index].status != "completed" for index in step.dependencies
                ):
                    raise AppError(
                        500, "summary_checkpoint_invalid", "Шаги обработки повреждены"
                    )
                local = local_result(step, steps)
                if not local:
                    break
                step.content, step.provenance = local
                step.status = "completed"
                db.flush()
            else:
                finalize(db, job, steps)
                db.commit()
                return
            route = job.snapshot["routes"][step.task_type]
            router = llm.ProviderRouter(route)
            plan = router.plan()
            if step.attempts >= len(plan):
                raise AppError(503, "attempts_exhausted", "Число попыток исчерпано")
            model_config = plan[step.attempts]
            model, provider = (
                db.get(ProviderModel, model_config["id"]),
                db.get(Provider, model_config["provider_id"]),
            )
            if not model or not model.enabled or not provider or not provider.enabled:
                raise AppError(503, "ai_unavailable", "Модель отключена")
            if job.type == "processing":
                version = db.get(TextVersion, job.snapshot["text_version_id"])
                if not version or (
                    version.visibility == "private" and version.owner_user_id != user.id
                ):
                    raise AppError(404, "text_missing", "Текст недоступен")
            text = source_text(job)
            payload = payload_for(step, steps, job, text)
            config = job.snapshot["parameters"]
            prompt = job.snapshot["prompts"][step.task_type]
            target_chars = (
                config["medium_target_chars"]
                if step.parameters.get("level") == "medium"
                else config["aggregate_max_chars"]
            )
            schema = response_schema(step.parameters["kind"], payload)
            # Some compatible gateways do not expose response_format constraints
            # to the model. Include the trusted schema in the prompt as well.
            rendered = render(
                prompt, {**payload, "output_schema": schema}, target_chars
            )
            kind = step.parameters["kind"]
            if (
                len(rendered.encode()) + len(str(schema).encode())
                > step.parameters["input_bound"]
            ):
                raise AppError(
                    422, "context_limit", "Запрос превышает зарезервированный контекст"
                )
            job.attempts += 1
            step.attempts += 1
            step.status = "running"
            job.status = "validating" if step.task_type == "validation" else "running"
            job.started_at = utcnow()
            job.progress = int(
                95 * sum(row.status == "completed" for row in steps) / len(steps)
            )
            usage = UsageEvent(
                user_id=owner,
                job_id=job.id,
                provider_model_id=model_config["id"],
                attempt=job.attempts,
            )
            db.add(usage)
            db.commit()
            usage_id, step_id = usage.id, step.id
        except AppError as exc:
            job.status, job.error_code, job.completed_at = "failed", exc.code, utcnow()
            db.commit()
            return
    start = time.monotonic()
    generation, failure, content = None, None, None
    try:
        generation = llm.get_provider(model_config).generate_structured(
            rendered,
            schema,
            max_tokens=route["policy"]["max_output_tokens"],
            idempotency_key=f"{job_id}-{step_id}-{model_config['id']}",
        )
        content = validate_content(kind, generation.content, payload, config)
    except llm.ProviderFailure as exc:
        failure = exc
    except AppError:
        failure = llm.ProviderFailure("secrets_unavailable")
    except Exception:
        failure = llm.ProviderFailure("provider_internal")
    next_delivery = False
    with Session(engine) as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job:
            return
        usage = db.get(UsageEvent, usage_id)
        step = db.get(ProcessingStep, step_id)
        units = generation.units if generation else failure.units
        usage.units, usage.latency_ms = units, int((time.monotonic() - start) * 1000)
        usage.status, usage.error_code = (
            ("failed", failure.code) if failure else ("completed", None)
        )
        if failure and failure.technical and not failure.safe_retry:
            usage.status = "outcome_unknown"
        pricing = model_config["pricing"]
        if pricing and {"input_tokens", "output_tokens"} <= units.keys():
            usage.estimated_cost_micros = (
                units["input_tokens"] * pricing["input_micros_per_million"]
                + units["output_tokens"] * pricing["output_micros_per_million"]
                + 999999
            ) // 1000000
            usage.currency = pricing["currency"]
        if job.status in TERMINAL:
            db.commit()
            return
        if not failure:
            step.content, step.status = content, "completed"
            step.provenance = {
                "provider_model_id": model_config["id"],
                "model_identifier": model_config["model_identifier"],
                "route_id": route["id"],
                "config_version": route["config_version"],
                "prompt_version_id": prompt["id"],
                "prompt_version": prompt["version"],
                "prompt_body_hash": prompt["body_hash"],
                "mock": model_config["protocol"] == "mock",
            }
            if step.task_type == "validation":
                step.provenance = {
                    **step.provenance,
                    "validation_round": step.parameters.get("round", 1),
                }
            if step.task_type == "validation":
                has_errors = any(
                    finding["severity"] == "error" for finding in content["findings"]
                )
                has_corrections = bool(
                    content["corrected_nodes"] or content["corrected_text"]
                )
                if (has_errors and not has_corrections) or (
                    step.parameters.get("round", 1) == 2
                    and (has_errors or has_corrections)
                ):
                    failure = llm.ProviderFailure("summary_validation_failed")
            if not failure:
                job.status, job.error_code, job.next_run_at = "queued", None, utcnow()
                job.next_dispatch_at = job.next_run_at
                next_delivery = True
        if failure:
            if failure.reason:
                step.provenance = {"rejection_reason": failure.reason}
            if router.may_retry(failure, step.attempts):
                step.status = "pending"
                job.status, job.error_code = "queued", failure.code
                job.next_run_at = utcnow() + timedelta(
                    seconds=route["policy"]["backoff_seconds"]
                    * 2 ** (step.attempts - 1)
                )
                job.next_dispatch_at = job.next_run_at
            else:
                job.status, job.error_code, job.completed_at = (
                    "failed",
                    failure.code,
                    utcnow(),
                )
        db.commit()
    if next_delivery:
        publish(job_id)
