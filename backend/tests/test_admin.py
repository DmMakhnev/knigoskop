"""Isolated SQL tests of secret handling, statistics and administration metadata."""

from datetime import timedelta
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.ai_models import (
    Job,
    Provider,
    ProviderCredential,
    ProviderModel,
    UsageEvent,
)
from app.db.audio_models import AudioUsage, TTSProfile
from app.db.models import AuditEvent, Base, User, utcnow
from app.db.session import get_db
from app.domains.auth.dependencies import require_admin
from app.main import app


@pytest.fixture
def administrator(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    user = User(
        email="admin-fixture@example.com",
        password_hash="unused",
        role="admin",
        status="active",
        verified_at=utcnow(),
    )
    db.add(user)
    db.commit()
    monkeypatch.setattr(
        settings, "ai_master_key", SecretStr(Fernet.generate_key().decode())
    )
    monkeypatch.setattr(
        settings, "ai_allowed_hosts", ["api.example.com", "api.genvoice.ru"]
    )
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_admin] = lambda: user
    try:
        with TestClient(app, base_url="https://testserver") as client:
            client.get("/api/v1/auth/csrf")
            client.headers["X-CSRF-Token"] = client.cookies.get("knigoskop_csrf")
            yield client, db, user
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        db.close()
        engine.dispose()


def test_encrypted_llm_and_tts_keys_never_return_or_enter_audit(administrator):
    client, db, _ = administrator
    key = "fixture-private-provider-key"
    response = client.post(
        "/api/v1/admin/providers",
        json={
            "name": "Fixture",
            "protocol": "openai_chat",
            "base_url": "https://api.example.com/v1",
            "host": "api.example.com",
            "api_key": key,
        },
    )
    assert response.status_code == 201 and key not in response.text
    provider = response.json()["id"]
    encrypted = db.scalar(
        select(ProviderCredential).where(ProviderCredential.provider_id == provider)
    ).encrypted_secret
    assert (
        encrypted != key
        and Fernet(settings.ai_master_key.get_secret_value().encode())
        .decrypt(encrypted.encode())
        .decode()
        == key
    )
    assert (
        client.put(
            f"/api/v1/admin/providers/{provider}/credential",
            json={"api_key": "replacement-fixture-key"},
        ).status_code
        == 200
    )
    bad = client.put(
        f"/api/v1/admin/providers/{provider}",
        json={"name": "Changed", "base_url": "https://another.example.com/v1"},
    )
    assert bad.status_code == 422
    profile = client.post(
        "/api/v1/admin/audio/profiles",
        json={
            "name": "GenVoice fixture",
            "protocol": "genvoice",
            "base_url": "https://api.genvoice.ru/v1",
            "host": "api.genvoice.ru",
            "voices": [str(uuid4())],
            "api_key": key,
        },
    )
    assert profile.status_code == 201 and key not in profile.text
    profile_id = profile.json()["id"]
    assert db.get(TTSProfile, profile_id).encrypted_secret != key
    rotated = client.put(
        f"/api/v1/admin/audio/profiles/{profile_id}/credential",
        json={"api_key": "tts-replacement-fixture"},
    )
    assert rotated.status_code == 200 and "tts-replacement-fixture" not in rotated.text
    assert key not in client.get("/api/v1/admin/audio/profiles").text
    audit = client.get("/api/v1/admin/audit").json()
    assert {
        "provider_created",
        "provider_credential_changed",
        "tts_profile_created",
        "tts_credential_changed",
    } <= {r["action"] for r in audit["items"]}
    assert key not in str(audit) and "encrypted_secret" not in str(audit)
    assert all(
        set(r) == {"id", "actor_id", "action", "entity_id", "created_at"}
        for r in audit["items"]
    )


def test_stats_preserve_currency_unknown_costs_and_hide_content(administrator):
    client, db, user = administrator
    provider = Provider(name="offline", protocol="mock", host="", base_url="")
    db.add(provider)
    db.flush()
    model = ProviderModel(
        provider_id=provider.id, model_identifier="fixture", capabilities={}, pricing={}
    )
    tts = TTSProfile(name="signals", protocol="mock", model="signals", voices=["demo"])
    db.add_all([model, tts])
    db.flush()
    job = Job(
        user_id=user.id,
        type="diagnostic",
        idempotency_key="fixture",
        request_hash="a" * 64,
        snapshot={"api_key": "fixture-hidden-key"},
        reservations={},
        status="completed",
        result={"text": "fixture-private-text"},
    )
    db.add(job)
    db.flush()
    db.add_all(
        [
            UsageEvent(
                user_id=user.id,
                job_id=job.id,
                provider_model_id=model.id,
                attempt=1,
                status="completed",
                units={
                    "input_tokens": 12,
                    "output_tokens": 3,
                    "private": "fixture-private-text",
                },
                estimated_cost_micros=123456,
                currency="RUB",
                latency_ms=100,
            ),
            UsageEvent(
                user_id=user.id,
                job_id=job.id,
                provider_model_id=model.id,
                attempt=2,
                status="completed",
                units={"input_tokens": 4, "output_tokens": 2},
                estimated_cost_micros=700,
                currency="USD",
                latency_ms=300,
            ),
            UsageEvent(
                user_id=user.id,
                job_id=job.id,
                provider_model_id=model.id,
                attempt=3,
                status="outcome_unknown",
                units={},
                estimated_cost_micros=None,
                currency="RUB",
            ),
            AudioUsage(
                user_id=user.id,
                job_id=job.id,
                profile_id=tts.id,
                segment_index=0,
                chars=50,
                duration_ms=2000,
                status="completed",
                estimated_cost_micros=1000000,
                currency="RUB",
            ),
            UsageEvent(
                user_id=user.id,
                job_id=job.id,
                provider_model_id=model.id,
                attempt=4,
                status="completed",
                units={"input_tokens": 1000},
                estimated_cost_micros=1000000,
                currency="RUB",
                created_at=utcnow() - timedelta(days=100),
            ),
        ]
    )
    db.commit()
    stats = client.get("/api/v1/admin/statistics?days=7").json()
    assert stats["llm"] == {
        "calls": 3,
        "input_tokens": 16,
        "output_tokens": 5,
        "mean_latency_ms": 200,
    }
    assert stats["tts"] == {"calls": 1, "chars": 50, "duration_ms": 2000}
    costs = {r["currency"]: r for r in stats["costs"]}
    assert (
        costs["RUB"]["estimated_cost_micros"] == 1123456
        and costs["RUB"]["unknown_cost_calls"] == 1
    )
    assert costs["USD"]["estimated_cost_micros"] == 700
    for endpoint in (
        "/admin/jobs",
        "/admin/job-monitor",
        "/admin/statistics",
        "/admin/usage",
    ):
        response = client.get("/api/v1" + endpoint)
        assert response.status_code == 200
        assert (
            "fixture-hidden-key" not in response.text
            and "fixture-private-text" not in response.text
        )
    assert (
        client.get("/api/v1/admin/statistics?user_id=" + str(uuid4())).json()["llm"][
            "calls"
        ]
        == 0
    )
    assert client.get("/api/v1/admin/statistics?days=91").status_code == 422


def test_admin_writes_require_csrf_and_validation_masks_key(administrator):
    client, _, _ = administrator
    client.headers.pop("X-CSRF-Token")
    response = client.post(
        "/api/v1/admin/providers", json={"name": "offline", "protocol": "mock"}
    )
    assert response.status_code == 403
    client.headers["X-CSRF-Token"] = client.cookies.get("knigoskop_csrf")
    response = client.post(
        "/api/v1/admin/audio/voices/genvoice",
        json={"api_key": "fixture-secret\r\ninvalid"},
    )
    assert response.status_code == 422 and "fixture-secret" not in response.text


def test_legacy_storage_accounting_is_dry_by_default(
    administrator, tmp_path, monkeypatch
):
    from app.db.models import TextVersion
    from app.domains.admin.cli import account_storage
    from app.domains.library.schemas import Metadata
    from app.domains.library.service import import_text
    from app.integrations.storage import LocalStorage

    _, db, user = administrator
    storage = LocalStorage(tmp_path)
    version = import_text(
        db,
        storage,
        Metadata(title="Accounting fixture", author="Fixture"),
        b"Original",
        "Normalized",
        "txt",
        user.id,
    )
    version.storage_bytes = None
    db.commit()
    monkeypatch.setattr(
        storage, "read", lambda key: pytest.fail("Accounting must not read content")
    )
    report = account_storage(db, storage)
    assert report == {"objects": 1, "bytes": 18, "applied": False}
    assert db.get(TextVersion, version.id).storage_bytes is None
    assert account_storage(db, storage, apply=True)["applied"]
    assert db.get(TextVersion, version.id).storage_bytes == 18
    assert account_storage(db, storage)["objects"] == 0


def test_archiving_keeps_configuration_identity_and_audit(administrator):
    client, db, _ = administrator
    provider = client.post(
        "/api/v1/admin/providers", json={"name": "Archive fixture", "protocol": "mock"}
    ).json()
    model = client.post(
        "/api/v1/admin/models",
        json={"provider_id": provider["id"], "model_identifier": "archive-model"},
    ).json()
    assert client.delete("/api/v1/admin/models/" + model["id"]).status_code == 204
    assert not db.get(ProviderModel, model["id"]).enabled
    assert (
        client.patch(
            "/api/v1/admin/models/" + model["id"], json={"enabled": True}
        ).status_code
        == 200
    )
    assert client.delete("/api/v1/admin/providers/" + provider["id"]).status_code == 204
    assert not db.get(Provider, provider["id"]).enabled
    assert (
        client.patch(
            "/api/v1/admin/providers/" + provider["id"], json={"enabled": True}
        ).status_code
        == 200
    )
    assert {"provider_archived", "provider_model_archived"} <= {
        r.action for r in db.scalars(select(AuditEvent))
    }


def test_admin_can_manage_limits_of_blocked_users(administrator):
    client, db, _ = administrator
    blocked = User(
        email="blocked@example.com",
        password_hash="unused",
        role="user",
        status="blocked",
        verified_at=utcnow(),
    )
    db.add(blocked)
    db.commit()
    assert (
        client.put(
            "/api/v1/admin/quotas",
            json={"metric": "books", "limit_value": 2, "user_id": blocked.id},
        ).status_code
        == 200
    )
    assert (
        client.delete("/api/v1/admin/quotas/" + blocked.id + "/books").status_code
        == 204
    )
