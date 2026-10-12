import hashlib
import secrets
from contextlib import contextmanager
from threading import BoundedSemaphore

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import settings
from app.core.errors import AppError

password_hasher = PasswordHasher(type=Type.ID)
password_slots = BoundedSemaphore(settings.password_hash_concurrency)
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))
SESSION_COOKIE = "knigoskop_session"
CSRF_COOKIE = "knigoskop_csrf"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def check_password_policy(password: str):
    if len(password) < settings.password_min_length or len(password) > 128:
        raise AppError(422, "password_policy", "Пароль не соответствует требованиям")


@contextmanager
def password_slot():
    # Argon2 uses 64 MiB per operation; bound concurrency on a small dev VDS.
    if not password_slots.acquire(blocking=False):
        raise AppError(503, "auth_busy", "Авторизация занята. Попробуйте позже")
    try:
        yield
    finally:
        password_slots.release()


def hash_password(password: str) -> str:
    with password_slot():
        return password_hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    with password_slot():
        try:
            return password_hasher.verify(hashed, password)
        except (VerificationError, InvalidHashError):
            return False
