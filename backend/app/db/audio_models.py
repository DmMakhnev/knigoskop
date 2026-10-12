"""Owner-scoped immutable audio cache, segments, progress and usage."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, utcnow


def identifier():
    return mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )


class TTSProfile(Base):
    __tablename__ = "tts_profiles"
    id: Mapped[str] = identifier()
    name: Mapped[str] = mapped_column(String(100))
    protocol: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(2000), default="")
    host: Mapped[str] = mapped_column(String(253), default="")
    model: Mapped[str] = mapped_column(String(100))
    voices: Mapped[list] = mapped_column(JSON)
    encrypted_secret: Mapped[str | None] = mapped_column(String(4000))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    price_micros_per_1000_chars: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(3))


class AudioAsset(Base):
    __tablename__ = "audio_assets"
    __table_args__ = (UniqueConstraint("cache_key", name="uq_audio_cache"),)
    id: Mapped[str] = identifier()
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE")
    )
    library_item_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("library_items.id", ondelete="CASCADE")
    )
    summary_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("summaries.id", ondelete="CASCADE")
    )
    profile_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("tts_profiles.id")
    )
    cache_key: Mapped[str] = mapped_column(String(64))
    text_hash: Mapped[str] = mapped_column(String(64))
    source_text: Mapped[str] = mapped_column(Text)
    voice: Mapped[str] = mapped_column(String(100))
    language: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class AudioSegment(Base):
    __tablename__ = "audio_segments"
    __table_args__ = (
        UniqueConstraint("asset_id", "order_index", name="uq_audio_segment"),
        CheckConstraint(
            "start_offset >= 0 AND end_offset > start_offset AND duration_ms >= 0",
            name="ck_audio_segment_range",
        ),
    )
    id: Mapped[str] = identifier()
    asset_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("audio_assets.id", ondelete="CASCADE")
    )
    order_index: Mapped[int] = mapped_column(Integer)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str | None] = mapped_column(String(32))
    checksum: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    timings: Mapped[list] = mapped_column(JSON, default=list)
    alignment: Mapped[str] = mapped_column(String(16), default="none")


class AudioProgress(Base):
    __tablename__ = "audio_progress"
    __table_args__ = (
        CheckConstraint(
            "position_ms >= 0 AND playback_rate >= 0.75 AND playback_rate <= 2",
            name="ck_audio_progress",
        ),
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    asset_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("audio_assets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position_ms: Mapped[int] = mapped_column(Integer)
    playback_rate: Mapped[float]
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class AudioUsage(Base):
    __tablename__ = "audio_usage"
    __table_args__ = (
        UniqueConstraint("job_id", "segment_index", name="uq_audio_usage_segment"),
    )
    id: Mapped[str] = identifier()
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE")
    )
    job_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("jobs.id", ondelete="CASCADE")
    )
    profile_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("tts_profiles.id")
    )
    segment_index: Mapped[int] = mapped_column(Integer)
    chars: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="outcome_unknown")
    estimated_cost_micros: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(3))


class AudioSettings(Base):
    __tablename__ = "audio_settings"
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    profile_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("tts_profiles.id", ondelete="SET NULL")
    )
    voice: Mapped[str | None] = mapped_column(String(100))
