from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
        CheckConstraint("status IN ('active', 'blocked')", name="ck_users_status"),
    )
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(16), default="user")
    status: Mapped[str] = mapped_column(String(16), default="active")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('verify_email', 'password_reset', 'session')",
            name="ck_auth_tokens_kind",
        ),
        Index("ix_auth_tokens_user_kind", "user_id", "kind"),
        Index("ix_auth_tokens_expires", "expires_at"),
    )
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    csrf_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    actor_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Work(Base):
    __tablename__ = "works"
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    identity: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    author: Mapped[str] = mapped_column(String(300))
    language: Mapped[str] = mapped_column(String(32))


class Edition(Base):
    __tablename__ = "editions"
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    identity: Mapped[str] = mapped_column(String(64), unique=True)
    work_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey("works.id"))
    edition_label: Mapped[str] = mapped_column(String(300))
    translator: Mapped[str] = mapped_column(String(300))
    rights_status: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("sources.id")
    )
    source_candidate_id: Mapped[str | None] = mapped_column(String(100))
    provenance: Mapped[str] = mapped_column(String(500), default="")


class TextVersion(Base):
    __tablename__ = "text_versions"
    __table_args__ = (
        CheckConstraint(
            "(visibility = 'public' AND owner_user_id IS NULL) OR "
            "(visibility = 'private' AND owner_user_id IS NOT NULL)",
            name="ck_text_visibility_owner",
        ),
    )
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    identity: Mapped[str] = mapped_column(String(64), unique=True)
    edition_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("editions.id")
    )
    checksum: Mapped[str] = mapped_column(String(64))
    original_checksum: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(100), unique=True)
    normalized_text_key: Mapped[str] = mapped_column(String(100), unique=True)
    format: Mapped[str] = mapped_column(String(8))
    visibility: Mapped[str] = mapped_column(String(16))
    owner_user_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class LibraryFolder(Base):
    __tablename__ = "library_folders"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_folder_user_name"),)
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(100))


class LibraryItem(Base):
    __tablename__ = "library_items"
    __table_args__ = (
        UniqueConstraint("user_id", "text_version_id", name="uq_library_user_version"),
        UniqueConstraint("id", "text_version_id", name="uq_library_id_version"),
    )
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE")
    )
    text_version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("text_versions.id", ondelete="CASCADE")
    )
    folder_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("library_folders.id", ondelete="SET NULL")
    )
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(String(100))
    host: Mapped[str] = mapped_column(String(253))
    catalog_url: Mapped[str] = mapped_column(String(2000))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    approvals: Mapped[list] = mapped_column(JSON, default=list)


class TextSection(Base):
    __tablename__ = "text_sections"
    __table_args__ = (
        UniqueConstraint("id", "text_version_id", name="uq_section_id_version"),
        UniqueConstraint(
            "text_version_id", "order_index", name="uq_section_version_order"
        ),
        ForeignKeyConstraint(
            ["parent_id", "text_version_id"],
            ["text_sections.id", "text_sections.text_version_id"],
            ondelete="CASCADE",
            name="fk_section_parent_version",
        ),
        CheckConstraint(
            "start_offset >= 0 AND start_offset < end_offset", name="ck_section_range"
        ),
        CheckConstraint(
            "kind IN ('root', 'part', 'chapter', 'fragment')", name="ck_section_kind"
        ),
    )
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    text_version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("text_versions.id", ondelete="CASCADE")
    )
    parent_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(300))
    order_index: Mapped[int] = mapped_column(Integer)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)


class ReadingProgress(Base):
    __tablename__ = "reading_progress"
    __table_args__ = (
        ForeignKeyConstraint(
            ["section_id", "text_version_id"],
            ["text_sections.id", "text_sections.text_version_id"],
            ondelete="CASCADE",
            name="fk_progress_section_version",
        ),
        ForeignKeyConstraint(
            ["library_item_id", "text_version_id"],
            ["library_items.id", "library_items.text_version_id"],
            ondelete="CASCADE",
            name="fk_progress_item_version",
        ),
        CheckConstraint(
            '"offset" >= 0 AND revision > 0 '
            'AND (end_offset IS NULL OR "offset" < end_offset)',
            name="ck_progress_position",
        ),
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    library_item_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    text_version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    section_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int | None] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ReadingSettings(Base):
    __tablename__ = "reading_settings"
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    preferences: Mapped[dict] = mapped_column(JSON)


class Bookmark(Base):
    __tablename__ = "bookmarks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["section_id", "text_version_id"],
            ["text_sections.id", "text_sections.text_version_id"],
            ondelete="CASCADE",
            name="fk_bookmark_section_version",
        ),
        ForeignKeyConstraint(
            ["library_item_id", "text_version_id"],
            ["library_items.id", "library_items.text_version_id"],
            ondelete="CASCADE",
            name="fk_bookmark_item_version",
        ),
        CheckConstraint(
            "start_offset >= 0 AND start_offset < end_offset", name="ck_bookmark_range"
        ),
    )
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    library_item_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    text_version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    section_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(100))


from app.db import ai_models as ai_models  # noqa: E402,F401
from app.db import audio_models as audio_models  # noqa: E402,F401
from app.db import summary_models as summary_models  # noqa: E402,F401
