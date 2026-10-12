import io
import zipfile

import pytest

from app.core.errors import AppError
from app.core.security import CSRF_COOKIE
from app.domains.library.parsers import archive, parse_content
from app.domains.summaries.content import verify_quote


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/me",
        "/api/v1/library",
        "/api/v1/admin/users",
        "/api/v1/admin/providers",
        "/api/v1/admin/models",
        "/api/v1/admin/routes",
        "/api/v1/admin/quotas",
        "/api/v1/admin/ai-status",
        "/api/v1/jobs",
        "/api/v1/audio/profiles",
        "/api/v1/admin/audio/profiles",
        "/api/v1/admin/audio/usage",
        "/api/v1/me/audio-settings",
        "/api/v1/admin/audio/voices/genvoice",
    ],
)
def test_anonymous_requests_are_rejected_before_database_access(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["register", "login", "reset-password"])
def test_auth_mutations_require_csrf(client, path):
    response = client.post(f"/api/v1/auth/{path}", json={})
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_invalid"


def test_csrf_cookie_is_secure_and_untrusted_origin_is_rejected(client):
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
    token = client.cookies.get(CSRF_COOKIE)
    assert len(token) >= 32
    response = client.post(
        "/api/v1/auth/register",
        headers={"Origin": "https://untrusted.example", "X-CSRF-Token": token},
        json={},
    )
    assert response.status_code == 403


def test_validation_response_does_not_echo_credentials(client):
    client.get("/api/v1/auth/csrf")
    secret = "recovery-fixture-password"
    response = client.post(
        "/api/v1/auth/login",
        headers={"X-CSRF-Token": client.cookies.get(CSRF_COOKIE)},
        json={"email": "invalid-address", "password": secret},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert secret not in response.text
    assert "invalid-address" not in response.text


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/escape", "a\\escape"])
def test_archive_rejects_unsafe_paths(name):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as book:
        book.writestr(name, "fixture")
    with pytest.raises(AppError):
        archive(data.getvalue())


def test_txt_import_normalizes_line_endings():
    assert parse_content("Глава\r\nТекст\r\n".encode(), "txt") == "Глава\nТекст"


def test_summary_quote_accepts_matching_source_range():
    verify_quote(
        {"start_offset": 10, "end_offset": 15, "quote": "Hello"},
        {
            "chunk": {"start_offset": 10, "end_offset": 15, "context_start": 10},
            "source_text": "Hello",
        },
    )


@pytest.mark.parametrize(
    "start,end,quote", [(9, 15, "Hello"), (10, 16, "Hello"), (10, 15, "Other")]
)
def test_summary_quote_rejects_outside_ranges_or_wrong_source(start, end, quote):
    payload = {
        "chunk": {"start_offset": 10, "end_offset": 15, "context_start": 10},
        "source_text": "Hello",
    }
    with pytest.raises(ValueError):
        verify_quote(
            {"start_offset": start, "end_offset": end, "quote": quote}, payload
        )
