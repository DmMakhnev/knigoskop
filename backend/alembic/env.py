from alembic import context
from sqlalchemy import create_engine

from app.core.config import settings
from app.db.models import Base


def run_migrations_online():
    engine = create_engine(settings.db_url(), hide_parameters=True)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=Base.metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


def run_migrations_offline():
    context.configure(
        url=settings.db_url(),
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
