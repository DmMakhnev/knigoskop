"""Logical storage accounting and timestamped TTS usage.

Revision ID: 0009_admin
Revises: 0008_audio
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_admin"
down_revision = "0008_audio"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tts_task_routes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "primary_profile_id",
            sa.Uuid(),
            sa.ForeignKey("tts_profiles.id"),
            nullable=False,
        ),
        sa.Column("fallback_profile_id", sa.Uuid(), sa.ForeignKey("tts_profiles.id")),
        sa.Column("version", sa.Integer(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column(
        "text_versions", sa.Column("storage_bytes", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "audio_segments", sa.Column("storage_bytes", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "audio_usage",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.execute(
        "UPDATE audio_usage SET created_at = "
        "(SELECT created_at FROM jobs WHERE jobs.id = audio_usage.job_id)"
    )
    op.create_index("ix_usage_created", "usage_events", ["created_at"])
    op.create_index("ix_audio_usage_created", "audio_usage", ["created_at"])
    op.create_index("ix_audit_created", "audit_events", ["created_at"])


def downgrade():
    op.drop_table("tts_task_routes")
    op.drop_index("ix_audit_created", "audit_events")
    op.drop_index("ix_audio_usage_created", "audio_usage")
    op.drop_index("ix_usage_created", "usage_events")
    op.drop_column("audio_usage", "created_at")
    op.drop_column("audio_segments", "storage_bytes")
    op.drop_column("text_versions", "storage_bytes")
