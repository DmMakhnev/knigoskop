"""Stable sections, personal progress, preferences and bookmarks."""

import sqlalchemy as sa
from alembic import op

revision = "0004_reader"
down_revision = "0003_sources"
branch_labels = None
depends_on = None


def upgrade():
    uuid = sa.Uuid(as_uuid=False)
    with op.batch_alter_table("library_items") as batch:
        batch.create_unique_constraint(
            "uq_library_id_version", ["id", "text_version_id"]
        )
    op.create_table(
        "text_sections",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "text_version_id",
            uuid,
            sa.ForeignKey("text_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parent_id", uuid),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.UniqueConstraint("id", "text_version_id", name="uq_section_id_version"),
        sa.UniqueConstraint(
            "text_version_id", "order_index", name="uq_section_version_order"
        ),
        sa.ForeignKeyConstraint(
            ["parent_id", "text_version_id"],
            ["text_sections.id", "text_sections.text_version_id"],
            ondelete="CASCADE",
            name="fk_section_parent_version",
        ),
        sa.CheckConstraint(
            "start_offset >= 0 AND start_offset < end_offset", name="ck_section_range"
        ),
        sa.CheckConstraint(
            "kind IN ('root', 'part', 'chapter', 'fragment')", name="ck_section_kind"
        ),
    )
    op.create_table(
        "reading_progress",
        sa.Column(
            "user_id",
            uuid,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("library_item_id", uuid, primary_key=True),
        sa.Column("text_version_id", uuid, nullable=False),
        sa.Column("section_id", uuid, nullable=False),
        sa.Column("offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["section_id", "text_version_id"],
            ["text_sections.id", "text_sections.text_version_id"],
            ondelete="CASCADE",
            name="fk_progress_section_version",
        ),
        sa.ForeignKeyConstraint(
            ["library_item_id", "text_version_id"],
            ["library_items.id", "library_items.text_version_id"],
            ondelete="CASCADE",
            name="fk_progress_item_version",
        ),
        sa.CheckConstraint(
            '"offset" >= 0 AND revision > 0 '
            'AND (end_offset IS NULL OR "offset" < end_offset)',
            name="ck_progress_position",
        ),
    )
    op.create_table(
        "reading_settings",
        sa.Column(
            "user_id",
            uuid,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("preferences", sa.JSON(), nullable=False),
    )
    op.create_table(
        "bookmarks",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("library_item_id", uuid, nullable=False),
        sa.Column("text_version_id", uuid, nullable=False),
        sa.Column("section_id", uuid, nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.ForeignKeyConstraint(
            ["section_id", "text_version_id"],
            ["text_sections.id", "text_sections.text_version_id"],
            ondelete="CASCADE",
            name="fk_bookmark_section_version",
        ),
        sa.ForeignKeyConstraint(
            ["library_item_id", "text_version_id"],
            ["library_items.id", "library_items.text_version_id"],
            ondelete="CASCADE",
            name="fk_bookmark_item_version",
        ),
        sa.CheckConstraint(
            "start_offset >= 0 AND start_offset < end_offset", name="ck_bookmark_range"
        ),
    )


def downgrade():
    for table in ("bookmarks", "reading_settings", "reading_progress", "text_sections"):
        op.drop_table(table)
    with op.batch_alter_table("library_items") as batch:
        batch.drop_constraint("uq_library_id_version", type_="unique")
