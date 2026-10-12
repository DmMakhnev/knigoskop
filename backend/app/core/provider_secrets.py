"""Authenticated encryption; master key is never stored in the database."""

import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.errors import AppError


def cipher():
    try:
        return Fernet(settings.ai_master_key.get_secret_value().encode())
    except (ValueError, AttributeError):
        raise AppError(
            503, "secrets_unavailable", "Ключ шифрования AI не настроен"
        ) from None


def encrypt(secret):
    return cipher().encrypt(secret.encode()).decode()


def decrypt(encrypted):
    try:
        return cipher().decrypt(encrypted.encode()).decode()
    except InvalidToken:
        raise AppError(
            503, "secrets_unavailable", "Ключ шифрования AI недоступен"
        ) from None


def environment_key(host):
    """An operator opt-in binds the raw environment key to one exact hostname."""
    secret = settings.knigoskop_llm_api_key
    value = secret.get_secret_value() if secret else ""
    if (
        not host
        or host != settings.ai_env_provider_host
        or not 1 <= len(value) <= 2000
        or any(c in value for c in "\r\n")
    ):
        raise AppError(503, "secrets_unavailable", "Ключ AI из окружения недоступен")
    return value


def environment_fingerprint(host):
    return hashlib.sha256(environment_key(host).encode()).hexdigest()


def resolve_key(config):
    if config.get("credential_source") != "environment":
        return decrypt(config["encrypted_secret"])
    value = environment_key(config["host"])
    if not hmac.compare_digest(
        hashlib.sha256(value.encode()).hexdigest(), config["credential_fingerprint"]
    ):
        raise AppError(
            503, "secrets_unavailable", "Ключ AI изменён; создайте новую задачу"
        )
    return value
