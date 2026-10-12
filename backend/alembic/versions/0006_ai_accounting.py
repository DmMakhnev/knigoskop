"""Keep configured micro-currency estimates in a 64-bit integer."""

import sqlalchemy as sa
from alembic import op

revision = "0006_ai_accounting"
down_revision = "0005_ai"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("usage_events") as batch:
        batch.alter_column(
            "estimated_cost_micros", existing_type=sa.Integer(), type_=sa.BigInteger()
        )


def downgrade():
    # A downgrade must not silently truncate large accounting values.
    connection = op.get_bind()
    maximum = connection.execute(
        sa.text("SELECT max(estimated_cost_micros) FROM usage_events")
    ).scalar()
    if maximum is not None and maximum > 2147483647:
        raise RuntimeError("Accounting values exceed the previous column capacity")
    with op.batch_alter_table("usage_events") as batch:
        batch.alter_column(
            "estimated_cost_micros", existing_type=sa.BigInteger(), type_=sa.Integer()
        )
