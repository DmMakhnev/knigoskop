"""Real HTTP/SQL/Redis checks, guarded for a fresh validation database only."""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.ai_models import Provider
from app.db.models import User
from app.db.session import get_engine


@pytest.fixture(scope="session", autouse=True)
def validation_only():
    assert os.environ.get("KNIGOSKOP_VALIDATION") == "1"
    assert settings.app_env == "test"
    assert settings.postgres_db == "knigoskop_validation"
    assert settings.postgres_host == "postgres"
    assert not settings.database_url
    assert not settings.ai_master_key
    assert not settings.knigoskop_llm_api_key
    assert not settings.ai_allowed_hosts
    with Session(get_engine()) as db:
        providers = db.scalars(select(Provider)).all()
        assert providers and all(p.protocol == "mock" for p in providers)


@dataclass
class Account:
    client: httpx.Client
    email: str
    password: str
    user_id: str | None = None

    def request(self, method, path, **kwargs):
        headers = {"Origin": "http://frontend:5173"}
        if method.upper() not in ("GET", "HEAD", "OPTIONS"):
            headers["X-CSRF-Token"] = self.client.cookies.get("knigoskop_csrf")
        return self.client.request(method, path, headers=headers, **kwargs)

    def token(self, kind):
        for path in sorted(
            Path(settings.mail_directory).glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            message = json.loads(path.read_text())
            if message["recipient"] == self.email and message["kind"] == kind:
                return parse_qs(urlsplit(message["link"]).fragment)["token"][0]
        raise AssertionError("Validation email not found")

    def login(self):
        assert self.client.get("/api/v1/auth/csrf").status_code == 200
        response = self.request(
            "POST",
            "/api/v1/auth/login",
            json={"email": self.email, "password": self.password},
        )
        assert response.status_code == 200
        self.user_id = response.json()["id"]
        return response


@pytest.fixture
def account_factory():
    clients = []

    def create(verified=True, admin=False):
        client = httpx.Client(base_url="http://api:8000", trust_env=False, timeout=15)
        clients.append(client)
        account = Account(
            client,
            f"validation-{uuid4().hex}@example.com",
            "Validation-fixture-pass-2026",
        )
        assert client.get("/api/v1/auth/csrf").status_code == 200
        response = account.request(
            "POST",
            "/api/v1/auth/register",
            json={"email": account.email, "password": account.password},
        )
        assert response.status_code == 202
        if verified:
            token = account.token("verify_email")
            assert (
                account.request(
                    "POST", "/api/v1/auth/verify-email", json={"token": token}
                ).status_code
                == 200
            )
            account.login()
        if admin:
            with Session(get_engine()) as db:
                user = db.get(User, account.user_id)
                user.role = "admin"
                db.commit()
        return account

    yield create
    for client in clients:
        client.close()


@pytest.fixture
def account(account_factory):
    return account_factory()


def upload(account, data=None, fmt="txt"):
    data = (
        data
        or (
            "Глава 1\n\nЧитатель открыл книгу. За окном шумел дождь. "
            "Он отметил абзац.\n"
        ).encode()
    )
    response = account.request(
        "POST",
        "/api/v1/books/upload",
        data={"title": "Validation " + uuid4().hex, "author": "Fixture"},
        files={"file": ("fixture." + fmt, data)},
    )
    assert response.status_code == 201
    return response.json()


def completed_job(account, job_id):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        response = account.request("GET", f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in ("completed", "failed", "cancelled"):
            assert job["status"] == "completed", job.get("error_code")
            return job
        time.sleep(0.2)
    raise AssertionError("Mock worker job did not complete before deadline")
