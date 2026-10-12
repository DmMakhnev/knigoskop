"""Versioned prompts, private processing checkpoints and immutable summaries."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    id: Mapped[str] = identifier()
    task_type: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_prompt_version"),
        UniqueConstraint("template_id", "id", name="uq_prompt_template_id"),
        CheckConstraint(
            "version > 0 AND status IN ('draft','published')", name="ck_prompt_version"
        ),
    )
    id: Mapped[str] = identifier()
    template_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("prompt_templates.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    variables_schema: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    created_by: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class PromptPublication(Base):
    __tablename__ = "prompt_publications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["template_id", "version_id"],
            ["prompt_versions.template_id", "prompt_versions.id"],
            name="fk_publication_version",
        ),
    )
    template_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("prompt_templates.id", ondelete="CASCADE"),
        primary_key=True,
    )
    version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))


class ProcessingStep(Base):
    __tablename__ = "processing_steps"
    __table_args__ = (
        UniqueConstraint("job_id", "order_index", name="uq_processing_step"),
        CheckConstraint(
            "order_index >= 0 AND attempts >= 0 AND "
            "status IN ('pending','running','completed')",
            name="ck_processing_step",
        ),
    )
    id: Mapped[str] = identifier()
    job_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("jobs.id", ondelete="CASCADE")
    )
    order_index: Mapped[int] = mapped_column(Integer)
    task_type: Mapped[str] = mapped_column(String(32))
    parameters: Mapped[dict] = mapped_column(JSON)
    dependencies: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[dict | None] = mapped_column(JSON)
    provenance: Mapped[dict | None] = mapped_column(JSON)


class Summary(Base):
    __tablename__ = "summaries"
    __table_args__ = (
        UniqueConstraint("id", "text_version_id", name="uq_summary_version"),
        UniqueConstraint("generation_id", "level", name="uq_summary_generation_level"),
        ForeignKeyConstraint(
            ["library_item_id", "text_version_id"],
            ["library_items.id", "library_items.text_version_id"],
            ondelete="CASCADE",
            name="fk_summary_item_version",
        ),
        CheckConstraint(
            "level IN ('short','medium','detailed')", name="ck_summary_level"
        ),
    )
    id: Mapped[str] = identifier()
    generation_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    library_item_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    text_version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    source_job_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    level: Mapped[str] = mapped_column(String(16))
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class SummaryNode(Base):
    __tablename__ = "summary_nodes"
    __table_args__ = (
        UniqueConstraint("id", "summary_id", name="uq_summary_node_summary"),
        UniqueConstraint("id", "text_version_id", name="uq_summary_node_version"),
        UniqueConstraint("summary_id", "node_key", name="uq_summary_node_key"),
        ForeignKeyConstraint(
            ["summary_id", "text_version_id"],
            ["summaries.id", "summaries.text_version_id"],
            ondelete="CASCADE",
            name="fk_node_summary_version",
        ),
        ForeignKeyConstraint(
            ["parent_id", "summary_id"],
            ["summary_nodes.id", "summary_nodes.summary_id"],
            ondelete="CASCADE",
            name="fk_summary_node_parent",
        ),
        ForeignKeyConstraint(
            ["source_section_id", "text_version_id"],
            ["text_sections.id", "text_sections.text_version_id"],
            ondelete="CASCADE",
            name="fk_summary_node_section",
        ),
    )
    id: Mapped[str] = identifier()
    summary_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    text_version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    parent_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    source_section_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    node_key: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)
    order_index: Mapped[int] = mapped_column(Integer)
    provenance: Mapped[dict] = mapped_column(JSON)


class SummarySourceSpan(Base):
    __tablename__ = "summary_source_spans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["node_id", "text_version_id"],
            ["summary_nodes.id", "summary_nodes.text_version_id"],
            ondelete="CASCADE",
            name="fk_span_node_version",
        ),
        ForeignKeyConstraint(
            ["section_id", "text_version_id"],
            ["text_sections.id", "text_sections.text_version_id"],
            ondelete="CASCADE",
            name="fk_summary_span_section",
        ),
        CheckConstraint(
            "start_offset >= 0 AND start_offset < end_offset",
            name="ck_summary_span_range",
        ),
    )
    id: Mapped[str] = identifier()
    node_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    text_version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    section_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)


class SummaryValidation(Base):
    __tablename__ = "summary_validations"
    id: Mapped[str] = identifier()
    summary_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("summaries.id", ondelete="CASCADE")
    )
    step_index: Mapped[int] = mapped_column(Integer)
    findings: Mapped[dict] = mapped_column(JSON)
    provenance: Mapped[dict] = mapped_column(JSON)
