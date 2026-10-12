import hashlib
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.audio_models import AudioUsage
from app.db.session import get_engine
from conftest import completed_job, upload


def summary_for(account):
    item = upload(
        account, ("Читатель открыл книгу. Он заметил интересный абзац. " * 12).encode()
    )
    response = account.request(
        "POST",
        f"/api/v1/library/{item['id']}/process",
        json={"idempotency_key": uuid4().hex},
    )
    assert response.status_code == 202
    completed_job(account, response.json()["id"])
    summary = account.request(
        "GET", f"/api/v1/library/{item['id']}/summaries", params={"level": "detailed"}
    ).json()["items"][0]
    return item, summary


def test_audio_queue_cache_acl_ranges_progress_and_deletion(account_factory):
    owner, stranger = account_factory(), account_factory()
    item, summary = summary_for(owner)
    profile = owner.request("GET", "/api/v1/audio/profiles").json()["items"][0]
    payload = {
        "idempotency_key": uuid4().hex,
        "library_item_id": item["id"],
        "summary_id": summary["id"],
        "profile_id": profile["id"],
        "voice": "demo",
    }
    response = owner.request("POST", "/api/v1/audio/generate", json=payload)
    assert response.status_code == 202
    value = response.json()
    asset_id = value["asset_id"]
    completed_job(owner, value["job"]["id"])
    manifest = owner.request("GET", f"/api/v1/audio/{asset_id}/manifest")
    assert manifest.status_code == 200
    assert owner.request("GET", "/api/v1/me/audio-settings").json() == {
        "profile_id": profile["id"],
        "voice": "demo",
    }
    assert stranger.request("GET", "/api/v1/me/audio-settings").json() == {
        "profile_id": None,
        "voice": None,
    }
    data = manifest.json()
    assert data["demo"] and len(data["segments"]) > 1
    assert data["text_hash"] == hashlib.sha256(data["text"].encode()).hexdigest()
    assert all(segment["alignment"] == "words" for segment in data["segments"])
    assert data["duration_ms"] == sum(s["duration_ms"] for s in data["segments"])
    stream = owner.request("GET", f"/api/v1/audio/{asset_id}/stream")
    assert stream.status_code == 200 and stream.content.startswith(b"RIFF")
    partial = owner.client.get(
        f"/api/v1/audio/{asset_id}/stream", headers={"Range": "bytes=0-43"}
    )
    assert partial.status_code == 206 and partial.content == stream.content[:44]
    assert owner.client.head(f"/api/v1/audio/{asset_id}/stream").headers[
        "content-length"
    ] == str(len(stream.content))
    assert (
        owner.client.get(
            f"/api/v1/audio/{asset_id}/stream", headers={"Range": "bytes=999999999-"}
        ).status_code
        == 416
    )
    for path in (
        f"/api/v1/audio/{asset_id}/manifest",
        f"/api/v1/audio/{asset_id}/stream",
        f"/api/v1/me/audio-progress/{asset_id}",
    ):
        assert stranger.request("GET", path).status_code == 404
    progress = {
        "asset_id": asset_id,
        "position_ms": 200,
        "playback_rate": 1.5,
        "revision": 0,
    }
    assert (
        owner.request("PUT", "/api/v1/me/audio-progress", json=progress).status_code
        == 200
    )
    assert (
        owner.request("PUT", "/api/v1/me/audio-progress", json=progress).status_code
        == 409
    )
    assert (
        stranger.request("PUT", "/api/v1/me/audio-progress", json=progress).status_code
        == 404
    )
    assert (
        owner.request("GET", f"/api/v1/me/audio-progress/{asset_id}").json()[
            "playback_rate"
        ]
        == 1.5
    )
    payload["idempotency_key"] = uuid4().hex
    again = owner.request("POST", "/api/v1/audio/generate", json=payload)
    assert (
        again.status_code == 202
        and again.json()["cached"]
        and again.json()["asset_id"] == asset_id
    )
    with Session(get_engine()) as db:
        usage = db.scalars(
            select(AudioUsage).where(AudioUsage.job_id == value["job"]["id"])
        ).all()
        assert len(usage) == len(data["segments"]) and all(
            u.status == "completed" for u in usage
        )
    assert stranger.request("GET", "/api/v1/admin/audio/profiles").status_code == 403
    assert owner.request("DELETE", f"/api/v1/library/{item['id']}").status_code == 204
    assert owner.request("GET", f"/api/v1/audio/{asset_id}/stream").status_code == 404


def test_audio_invalid_scope_voice_and_quota_do_not_start_paid_work(account_factory):
    owner, admin = account_factory(), account_factory(admin=True)
    item, summary = summary_for(owner)
    profile = owner.request("GET", "/api/v1/audio/profiles").json()["items"][0]
    payload = {
        "idempotency_key": uuid4().hex,
        "library_item_id": item["id"],
        "summary_id": summary["id"],
        "profile_id": profile["id"],
        "voice": "not-configured",
    }
    assert (
        owner.request("POST", "/api/v1/audio/generate", json=payload).status_code == 503
    )
    payload["voice"] = "demo"
    assert (
        admin.request(
            "PUT",
            "/api/v1/admin/quotas",
            json={
                "metric": "daily_tts_chars",
                "user_id": owner.user_id,
                "limit_value": 0,
            },
        ).status_code
        == 200
    )
    assert (
        owner.request("POST", "/api/v1/audio/generate", json=payload).status_code == 429
    )
    assert admin.request("GET", "/api/v1/admin/audio/usage").status_code == 200


def test_admin_audio_profile_creation_and_enable_are_protected(account_factory):
    admin, user = account_factory(admin=True), account_factory()
    assert user.request("GET", "/api/v1/admin/audio/voices/genvoice").status_code == 403
    assert (
        admin.request("GET", "/api/v1/admin/audio/voices/genvoice").status_code == 503
    )
    payload = {
        "name": "Second mock",
        "protocol": "mock",
        "model": "signals-v2",
        "voices": ["second"],
    }
    assert (
        user.request("POST", "/api/v1/admin/audio/profiles", json=payload).status_code
        == 403
    )
    created = admin.request("POST", "/api/v1/admin/audio/profiles", json=payload)
    assert created.status_code == 201
    profile_id = created.json()["id"]
    assert "encrypted_secret" not in created.json() and "api_key" not in created.json()
    assert (
        admin.request(
            "PATCH",
            f"/api/v1/admin/audio/profiles/{profile_id}",
            json={"enabled": False},
        ).status_code
        == 200
    )
    assert profile_id not in [
        p["id"] for p in user.request("GET", "/api/v1/audio/profiles").json()["items"]
    ]
    invalid = admin.request(
        "POST",
        "/api/v1/admin/audio/profiles",
        json={**payload, "api_key": "fixture-key"},
    )
    assert invalid.status_code == 422 and "fixture-key" not in invalid.text
