"""Real HTTP/PG administrative permissions and live configuration changes."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.ai_models import Job
from app.db.session import get_engine
from conftest import completed_job


@pytest.mark.parametrize(
    "path",
    [
        "/admin/statistics",
        "/admin/audit",
        "/admin/job-monitor",
        "/admin/jobs",
        "/admin/usage",
        "/admin/quotas",
        "/admin/sources",
        "/admin/routes",
        "/admin/audio/routes",
        "/admin/audio/usage",
        "/admin/audio/profiles",
    ],
)
def test_administrative_reads_are_denied_to_ordinary_user(account, path):
    response = account.request("GET", "/api/v1" + path)
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_admin_edits_versions_and_statistics_work_without_restart(account_factory):
    admin = account_factory(admin=True)
    provider = admin.request(
        "POST",
        "/api/v1/admin/providers",
        json={"name": "Administrative fixture", "protocol": "mock"},
    )
    assert provider.status_code == 201
    provider_id = provider.json()["id"]
    edit = admin.request(
        "PUT",
        f"/api/v1/admin/providers/{provider_id}",
        json={"name": "Edited fixture", "base_url": ""},
    )
    assert edit.status_code == 200 and edit.json()["name"] == "Edited fixture"
    model = admin.request(
        "POST",
        "/api/v1/admin/models",
        json={
            "provider_id": provider_id,
            "model_identifier": "admin-mock-v1",
            "pricing": {
                "input_micros_per_million": 1000000,
                "output_micros_per_million": 1000000,
                "currency": "RUB",
            },
        },
    )
    assert model.status_code == 201
    model_id = model.json()["id"]
    route = admin.request(
        "POST",
        "/api/v1/admin/routes",
        json={"task_type": "diagnostic", "primary_model_id": model_id},
    )
    assert route.status_code == 201
    job = admin.request(
        "POST",
        "/api/v1/jobs/diagnostic",
        json={"idempotency_key": uuid4().hex, "route_id": route.json()["id"]},
    )
    assert job.status_code == 202
    job_id = job.json()["id"]
    with Session(get_engine()) as db:
        frozen = db.get(Job, job_id).snapshot
    changed = admin.request(
        "PUT",
        f"/api/v1/admin/models/{model_id}",
        json={
            "model_identifier": "admin-mock-v2",
            "capabilities": {"supports_json_schema": True, "max_context_tokens": 8192},
            "pricing": {
                "input_micros_per_million": 2000000,
                "output_micros_per_million": 2000000,
                "currency": "RUB",
            },
        },
    )
    assert changed.status_code == 200
    completed_job(admin, job_id)
    with Session(get_engine()) as db:
        assert db.get(Job, job_id).snapshot == frozen
    usage = admin.request("GET", "/api/v1/admin/usage?limit=100").json()
    assert any(
        u["job_id"] == job_id and u["estimated_cost_micros"] is not None
        for u in usage["items"]
    )
    stats = admin.request(
        "GET", "/api/v1/admin/statistics", params={"user_id": admin.user_id}
    ).json()
    assert stats["llm"]["calls"] == 1 and stats["llm"]["input_tokens"] > 0
    assert stats["costs"][0]["currency"] == "RUB"
    monitor = admin.request(
        "GET", "/api/v1/admin/job-monitor", params={"user_id": admin.user_id}
    ).json()
    assert (
        monitor["total"] == 1
        and "result" not in monitor["items"][0]
        and "snapshot" not in monitor["items"][0]
    )
    audit = admin.request("GET", "/api/v1/admin/audit?limit=100").json()
    assert {
        "provider_created",
        "provider_updated",
        "provider_model_updated",
        "model_route_created",
    } <= {e["action"] for e in audit["items"]}
    bad = admin.request(
        "PUT",
        f"/api/v1/admin/models/{model_id}",
        json={"model_identifier": "bad", "capabilities": {}, "api_key": "fixture-key"},
    )
    assert bad.status_code == 422 and "fixture-key" not in bad.text


def test_personal_book_and_storage_limits_and_reset_are_enforced(account_factory):
    admin, owner = account_factory(admin=True), account_factory()

    def limit(metric, value):
        response = admin.request(
            "PUT",
            "/api/v1/admin/quotas",
            json={"metric": metric, "limit_value": value, "user_id": owner.user_id},
        )
        assert response.status_code == 200

    def import_book():
        return owner.request(
            "POST",
            "/api/v1/books/upload",
            data={"title": "Quota " + uuid4().hex, "author": "Fixture"},
            files={"file": ("fixture.txt", "Тестовая книга".encode())},
        )

    limit("books", 0)
    assert import_book().status_code == 429
    limit("books", 1)
    limit("storage_bytes", 0)
    assert import_book().status_code == 429
    limit("storage_bytes", 10000)
    assert import_book().status_code == 201
    assert import_book().status_code == 429
    assert owner.request("GET", "/api/v1/me/library-quota").json()["used"]["books"] == 1
    assert (
        admin.request(
            "DELETE", f"/api/v1/admin/quotas/{owner.user_id}/books"
        ).status_code
        == 204
    )
    assert import_book().status_code == 201
    assert (
        admin.request("GET", "/api/v1/admin/quotas").json()["defaults"][
            "daily_tts_chars"
        ]
        >= 0
    )


def test_tts_routing_is_versioned_and_key_input_is_denied_without_cipher(
    account_factory,
):
    admin, user = account_factory(admin=True), account_factory()
    profiles = admin.request("GET", "/api/v1/admin/audio/profiles").json()["items"]
    profile_id = profiles[0]["id"]
    route = {"primary_profile_id": profile_id}
    assert (
        user.request("POST", "/api/v1/admin/audio/routes", json=route).status_code
        == 403
    )
    created = admin.request("POST", "/api/v1/admin/audio/routes", json=route)
    assert created.status_code == 201
    assert created.json()["fallback_policy"] == "manual"
    assert (
        user.request("GET", "/api/v1/audio/profiles").json()["items"][0]["id"]
        == profile_id
    )
    response = admin.request(
        "PUT",
        f"/api/v1/admin/audio/profiles/{profile_id}/credential",
        json={"api_key": "fixture-secret"},
    )
    assert response.status_code == 422 and "fixture-secret" not in response.text
    external = {
        "name": "GenVoice without real key",
        "protocol": "genvoice",
        "base_url": "https://api.genvoice.ru/v1",
        "host": "api.genvoice.ru",
        "voices": [str(uuid4())],
        "api_key": "fixture-secret",
    }
    response = admin.request("POST", "/api/v1/admin/audio/profiles", json=external)
    assert response.status_code == 422 and "fixture-secret" not in response.text


def test_admin_cancellation_is_authorized_csrf_protected_and_audited(account_factory):
    from app.db.models import AuditEvent, utcnow

    admin, owner = account_factory(admin=True), account_factory()
    with Session(get_engine()) as db:
        job = Job(
            user_id=owner.user_id,
            type="diagnostic",
            status="running",
            started_at=utcnow(),
            idempotency_key=uuid4().hex,
            request_hash="a" * 64,
            snapshot={},
            reservations={},
            result={"private": "fixture-private-result"},
        )
        db.add(job)
        db.commit()
        job_id = job.id
    path = f"/api/v1/admin/jobs/{job_id}/cancel"
    assert owner.request("POST", path).status_code == 403
    assert admin.client.post(path).status_code == 403
    cancelled = admin.request("POST", path)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert (
        "fixture-private-result" not in cancelled.text
        and "result" not in cancelled.json()
    )
    assert admin.request("POST", path).json()["status"] == "cancelled"
    with Session(get_engine()) as db:
        events = db.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_id == job_id,
                AuditEvent.action == "job_cancelled_by_admin",
            )
        ).all()
        assert len(events) == 1


def test_source_configuration_and_pagination_are_live_metadata_only(account_factory):
    admin = account_factory(admin=True)
    data = {
        "name": "Metadata fixture",
        "host": "catalog.example.com",
        "catalog_url": "https://catalog.example.com/index.json",
        "enabled": False,
        "approvals": [],
    }
    created = admin.request("POST", "/api/v1/admin/sources", json=data)
    assert created.status_code == 201
    identifier = created.json()["id"]
    data["name"] = "Edited metadata fixture"
    assert (
        admin.request(
            "PUT", f"/api/v1/admin/sources/{identifier}", json=data
        ).status_code
        == 200
    )
    page = admin.request("GET", "/api/v1/admin/sources?offset=0&limit=1").json()
    assert page["total"] >= 1 and len(page["items"]) == 1
    assert (
        admin.request("GET", "/api/v1/admin/sources?offset=9999&limit=1").json()[
            "items"
        ]
        == []
    )
