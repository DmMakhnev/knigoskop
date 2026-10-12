"""Library and immutable text versions."""

import sqlalchemy as sa
from alembic import op

revision = "0002_library"
down_revision = "0001_auth"
branch_labels = None
depends_on = None


def upgrade():
    uuid = sa.Uuid(as_uuid=False)
    op.create_table(
        "works",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("identity", sa.String(64), nullable=False, unique=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("author", sa.String(300), nullable=False),
        sa.Column("language", sa.String(32), nullable=False),
    )
    op.create_table(
        "editions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("identity", sa.String(64), nullable=False, unique=True),
        sa.Column("work_id", uuid, sa.ForeignKey("works.id"), nullable=False),
        sa.Column("edition_label", sa.String(300), nullable=False),
        sa.Column("translator", sa.String(300), nullable=False),
        sa.Column("rights_status", sa.String(32), nullable=False),
        sa.Column("provenance", sa.String(500), nullable=False),
    )
    op.create_table(
        "text_versions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("identity", sa.String(64), nullable=False, unique=True),
        sa.Column("edition_id", uuid, sa.ForeignKey("editions.id"), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("original_checksum", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(100), nullable=False, unique=True),
        sa.Column("normalized_text_key", sa.String(100), nullable=False, unique=True),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("visibility", sa.String(16), nullable=False),
        sa.Column("owner_user_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(visibility = 'public' AND owner_user_id IS NULL) OR "
            "(visibility = 'private' AND owner_user_id IS NOT NULL)",
            name="ck_text_visibility_owner",
        ),
    )
    op.create_table(
        "library_folders",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "user_id",
            uuid,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="uq_folder_user_name"),
    )
    op.create_table(
        "library_items",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "user_id",
            uuid,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "text_version_id",
            uuid,
            sa.ForeignKey("text_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "folder_id", uuid, sa.ForeignKey("library_folders.id", ondelete="SET NULL")
        ),
        sa.Column("favorite", sa.Boolean(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "text_version_id", name="uq_library_user_version"
        ),
    )


def downgrade():
    for table in (
        "library_items",
        "library_folders",
        "text_versions",
        "editions",
        "works",
    ):
        op.drop_table(table)
