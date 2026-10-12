"""Small transactions around network I/O; never log provider responses or secrets."""

import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.ai_models import Job, Provider, ProviderModel, UsageEvent
from app.db.models import User, utcnow
from app.db.session import get_engine
from app.domains.jobs.service import TERMINAL, dispatch_due, utc
from app.integrations import llm
from app.workers.celery_app import celery_app


def execute_job(job_id, engine=None):
    engine = engine or get_engine()
    with Session(engine) as db:
        job_type = db.scalar(select(Job.type).where(Job.id == job_id))
    if job_type == "audio":
        from app.domains.audio.service import execute_audio

        execute_audio(job_id, engine)
        return
    if job_type in ("processing", "prompt_test"):
        from app.domains.summaries.pipeline import execute_processing

        execute_processing(job_id, engine)
        return
    with Session(engine) as db:
        # Match user -> job lock ordering used by cancellation/deletion/quota checks.
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
        router = llm.ProviderRouter(job.snapshot)
        plan = router.plan()
        if job.attempts >= len(plan):
            job.status, job.error_code, job.completed_at = (
                "failed",
                "attempts_exhausted",
                utcnow(),
            )
            db.commit()
            return
        config = plan[job.attempts]
        attempt = job.attempts + 1
        model = db.get(ProviderModel, config["id"])
        provider = db.get(Provider, config["provider_id"])
        if not model or not model.enabled or not provider or not provider.enabled:
            job.status, job.error_code, job.completed_at = (
                "failed",
                "ai_unavailable",
                utcnow(),
            )
            db.commit()
            return
        job.status, job.progress, job.started_at, job.attempts = (
            "running",
            25,
            utcnow(),
            attempt,
        )
        usage = UsageEvent(
            user_id=owner,
            job_id=job.id,
            provider_model_id=config["id"],
            attempt=attempt,
        )
        db.add(usage)
        policy = job.snapshot["policy"]
        # Claim before billing; duplicate delivery becomes a no-op.
        db.commit()
        usage_id = usage.id
    started = time.monotonic()
    failure = None
    generation = None
    try:
        generation = llm.get_provider(config).generate_structured(
            llm.DIAGNOSTIC_TEXT,
            llm.DIAGNOSTIC_SCHEMA,
            max_tokens=policy["max_output_tokens"],
            idempotency_key=f"{job_id}-{config['id']}",
        )
    except llm.ProviderFailure as exc:
        failure = exc
    except AppError:
        failure = llm.ProviderFailure("secrets_unavailable")
    except Exception:
        # Never return exception strings or provider data.
        failure = llm.ProviderFailure("provider_internal")
    latency = int((time.monotonic() - started) * 1000)
    if generation is not None:
        # Persist validating state; only validated diagnostic data is published.
        with Session(engine) as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if not job:
                return
            if job.status == "running":
                job.status, job.progress = "validating", 80
                db.commit()
        if (
            generation.content != {"ok": True}
            or type(generation.content.get("ok")) is not bool
        ):
            failure = llm.ProviderFailure(
                "provider_content_invalid", units=generation.units
            )
    with Session(engine) as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job:
            return
        usage = db.get(UsageEvent, usage_id)
        units = generation.units if generation else failure.units
        usage.units, usage.latency_ms = units, latency
        usage.status = "completed" if not failure else "failed"
        usage.error_code = failure.code if failure else None
        pricing = config["pricing"]
        if pricing and {"input_tokens", "output_tokens"} <= units.keys():
            usage.estimated_cost_micros = (
                (
                    units["input_tokens"] * pricing["input_micros_per_million"]
                    + units["output_tokens"] * pricing["output_micros_per_million"]
                )
                + 999999
            ) // 1000000
            usage.currency = pricing["currency"]
        if failure and failure.technical and not failure.safe_retry:
            usage.status = "outcome_unknown"
        if job.status in TERMINAL:
            # Cancellation cannot undo the call; record usage.
            db.commit()
            return
        if not failure:
            job.status, job.progress, job.error_code = "completed", 100, None
            job.result = {
                "ok": True,
                "provider_model_id": config["id"],
                "config_version": job.snapshot["config_version"],
            }
            job.completed_at = utcnow()
        elif router.may_retry(failure, attempt):
            job.status, job.error_code = "queued", failure.code
            job.next_run_at = utcnow() + timedelta(
                seconds=policy["backoff_seconds"] * 2 ** (attempt - 1)
            )
            job.next_dispatch_at = job.next_run_at
        else:
            job.status, job.error_code, job.completed_at = (
                "failed",
                failure.code,
                utcnow(),
            )
        db.commit()


@celery_app.task(name="knigoskop.run_job", ignore_result=True)
def run_job(job_id):
    execute_job(job_id)


@celery_app.task(name="knigoskop.dispatch_jobs", ignore_result=True)
def dispatch_jobs():
    with Session(get_engine()) as db:
        dispatch_due(db)
