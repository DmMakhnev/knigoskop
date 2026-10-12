from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "knigoskop", broker=settings.redis_url, include=["app.workers.tasks"]
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    broker_connection_retry_on_startup=True,
    worker_concurrency=1,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    task_soft_time_limit=max(45, settings.ai_timeout_seconds + 15),
    task_time_limit=max(60, settings.ai_timeout_seconds + 30),
    broker_connection_timeout=2,
    broker_transport_options={"socket_timeout": 2, "socket_connect_timeout": 2},
    beat_schedule={
        "durable-outbox": {
            "task": "knigoskop.dispatch_jobs",
            "schedule": settings.job_recovery_seconds,
        }
    },
)
