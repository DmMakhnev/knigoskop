from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, settings
from app.core.errors import AppError
from app.core.rate_limit import enforce_rate_limit
from app.integrations.email import SMTPMailBackend
from app.integrations.llm import validate_destination


def production(**overrides):
    return Settings(
        _env_file=None,
        app_env="production",
        public_base_url="https://test.example.com",
        allowed_origins=["https://test.example.com"],
        session_cookie_secure=True,
        mail_backend="disabled",
        registration_enabled=False,
        **overrides,
    )


def test_production_can_start_without_ai_keys_with_closed_registration(monkeypatch):
    for key in ["AI_MASTER_KEY", "KNIGOSKOP_LLM_API_KEY", "KNIGOSKOP_TTS_API_KEY"]:
        monkeypatch.delenv(key, raising=False)
    value = production()
    assert value.app_env == "production" and not value.knigoskop_tts_api_key


@pytest.mark.parametrize(
    "change",
    [
        {"public_base_url": "http://test.example.com"},
        {"session_cookie_secure": False},
        {"allowed_origins": ["*"]},
        {"mail_backend": "file"},
        {"registration_enabled": True},
    ],
)
def test_production_rejects_unsafe_configuration(change):
    data = {
        "app_env": "production",
        "public_base_url": "https://test.example.com",
        "allowed_origins": ["https://test.example.com"],
        "session_cookie_secure": True,
        "mail_backend": "disabled",
        "registration_enabled": False,
    }
    data.update(change)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **data)


def test_smtp_uses_tls_and_masks_provider_failures(monkeypatch):
    import smtplib

    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **kw: smtp)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "sender@example.com")
    monkeypatch.setattr(settings, "smtp_username", "fixture")
    monkeypatch.setattr(settings, "smtp_password", SecretStr("fixture-smtp-key"))
    SMTPMailBackend().send("reader@example.com", "verify_email", "fixture-token")
    assert smtp.starttls.call_count == 1
    assert smtp.login.call_args.args == ("fixture", "fixture-smtp-key")
    message = smtp.send_message.call_args.args[0]
    assert "#token=fixture-token" in message.get_content()
    smtp.send_message.side_effect = smtplib.SMTPException(
        "fixture-token fixture-smtp-key"
    )
    with pytest.raises(AppError) as caught:
        SMTPMailBackend().send("reader@example.com", "verify_email", "fixture-token")
    assert (
        caught.value.code == "mail_unavailable"
        and "fixture-" not in caught.value.message
    )


def test_registration_disabled_before_database_work(client, monkeypatch):
    monkeypatch.setattr(settings, "registration_enabled", False)
    client.get("/api/v1/auth/csrf")
    response = client.post(
        "/api/v1/auth/register",
        headers={"X-CSRF-Token": client.cookies.get("knigoskop_csrf")},
        json={"email": "reader@example.com", "password": "fixture-password-12"},
    )
    assert (
        response.status_code == 403
        and response.json()["code"] == "registration_disabled"
    )


def test_rate_limit_uses_hash_and_is_fail_closed():
    redis = MagicMock()
    pipeline = redis.pipeline.return_value.__enter__.return_value
    pipeline.execute.return_value = [3, True]
    with pytest.raises(AppError) as caught:
        enforce_rate_limit(redis, "login", "fixture@example.com", 2)
    assert caught.value.status == 429
    assert "fixture@example.com" not in pipeline.incr.call_args.args[0]
    from redis.exceptions import ConnectionError

    pipeline.execute.side_effect = ConnectionError("secret-connection-value")
    with pytest.raises(AppError) as caught:
        enforce_rate_limit(redis, "login", "fixture@example.com", 2)
    assert caught.value.status == 503 and "secret-" not in caught.value.message


@pytest.mark.parametrize(
    "url",
    [
        "http://allowed.example/v1",
        "https://allowed.example@127.0.0.1/v1",
        "https://127.0.0.1/v1",
        "https://allowed.example/v1?api_key=secret",
        "https://allowed.example/v1#secret",
    ],
)
def test_provider_url_blocks_unsafe_destinations(url, monkeypatch):
    monkeypatch.setattr(settings, "ai_allowed_hosts", ["allowed.example"])
    with pytest.raises(AppError):
        validate_destination(url, "allowed.example")
