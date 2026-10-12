"""Phase 5 infrastructure; no book contents in job payloads or telemetry."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, utcnow


def identifier():
    return mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )


class Provider(Base):
    __tablename__ = "providers"
    id: Mapped[str] = identifier()
    name: Mapped[str] = mapped_column(String(100))
    protocol: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(2000))
    host: Mapped[str] = mapped_column(String(253))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ProviderCredential(Base):
    __tablename__ = "provider_credentials"
    id: Mapped[str] = identifier()
    provider_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("providers.id"), unique=True
    )
    encrypted_secret: Mapped[str] = mapped_column(String(4000))
    key_version: Mapped[str] = mapped_column(String(32), default="v1")


class ProviderModel(Base):
    __tablename__ = "provider_models"
    id: Mapped[str] = identifier()
    provider_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("providers.id")
    )
    model_identifier: Mapped[str] = mapped_column(String(100))
    capabilities: Mapped[dict] = mapped_column(JSON)
    pricing: Mapped[dict] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class TaskModelRoute(Base):
    __tablename__ = "task_model_routes"
    __table_args__ = (
        UniqueConstraint("task_type", "config_version", name="uq_route_version"),
    )
    id: Mapped[str] = identifier()
    task_type: Mapped[str] = mapped_column(String(32))
    primary_model_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("provider_models.id")
    )
    fallback_model_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("provider_models.id")
    )
    config_version: Mapped[int] = mapped_column(Integer)
    policy: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class QuotaPolicy(Base):
    __tablename__ = "quota_policies"
    __table_args__ = (CheckConstraint("limit_value >= 0", name="ck_quota_limit"),)
    metric: Mapped[str] = mapped_column(String(32), primary_key=True)
    limit_value: Mapped[int] = mapped_column(Integer)


class QuotaOverride(Base):
    __tablename__ = "quota_overrides"
    __table_args__ = (CheckConstraint("limit_value >= 0", name="ck_override_limit"),)
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    metric: Mapped[str] = mapped_column(String(32), primary_key=True)
    limit_value: Mapped[int] = mapped_column(Integer)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_job_idempotency"),
        Index("ix_jobs_user_status", "user_id", "status"),
        Index("ix_jobs_due", "status", "next_run_at"),
        CheckConstraint(
            "status IN ('queued','running','validating',"
            "'completed','failed','cancelled')",
            name="ck_job_status",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100 AND attempts >= 0",
            name="ck_job_progress",
        ),
    )
    id: Mapped[str] = identifier()
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE")
    )
    library_item_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("library_items.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    route_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("task_model_routes.id")
    )
    snapshot: Mapped[dict] = mapped_column(JSON)
    reservations: Mapped[dict] = mapped_column(JSON)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    next_dispatch_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (UniqueConstraint("job_id", "attempt", name="uq_usage_attempt"),)
    id: Mapped[str] = identifier()
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE")
    )
    job_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("jobs.id", ondelete="CASCADE")
    )
    provider_model_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("provider_models.id")
    )
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="outcome_unknown")
    error_code: Mapped[str | None] = mapped_column(String(64))
    units: Mapped[dict] = mapped_column(JSON, default=dict)
    estimated_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
