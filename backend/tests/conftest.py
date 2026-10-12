"""Recovery checks use no live database, Redis, storage or paid providers."""

import os

import pytest

# Configure before importing the recovered module-level Settings instance.
os.environ["APP_ENV"] = "test"
os.environ["AI_ALLOWED_HOSTS"] = "[]"
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"
os.environ["SESSION_COOKIE_SECURE"] = "true"
for name in ("AI_MASTER_KEY", "KNIGOSKOP_LLM_API_KEY", "KNIGOSKOP_TTS_API_KEY"):
    os.environ.pop(name, None)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.db.session import get_db
    from app.main import app

    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError(f"Anonymous request attempted database access: {name}")

    def database():
        yield NoDatabase()

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app, base_url="https://testserver") as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
