from celery import Celery

from app.core.config import Settings

settings = Settings()
celery_app = Celery("knigoskop", broker=settings.redis_url)
celery_app.conf.update(task_serializer="json", accept_content=["json"], result_serializer="json")
