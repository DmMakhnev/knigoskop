"""Reviewed catalog sources and edition provenance."""

import sqlalchemy as sa
from alembic import op

revision = "0003_sources"
down_revision = "0002_library"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("catalog_url", sa.String(2000), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("approvals", sa.JSON(), nullable=False),
    )
    with op.batch_alter_table("editions") as batch:
        batch.add_column(sa.Column("source_id", sa.Uuid(as_uuid=False), nullable=True))
        batch.add_column(
            sa.Column("source_candidate_id", sa.String(100), nullable=True)
        )
        batch.create_foreign_key("fk_edition_source", "sources", ["source_id"], ["id"])


def downgrade():
    with op.batch_alter_table("editions") as batch:
        batch.drop_constraint("fk_edition_source", type_="foreignkey")
        batch.drop_column("source_candidate_id")
        batch.drop_column("source_id")
    op.drop_table("sources")
