from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings


@lru_cache
def get_engine():
    return create_engine(settings.db_url(), pool_pre_ping=True, hide_parameters=True)


def get_db():
    with Session(get_engine()) as session:
        yield session
