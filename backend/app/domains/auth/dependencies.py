import secrets

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import CSRF_COOKIE, SESSION_COOKIE, digest
from app.db.models import AuthToken, User, utcnow
from app.db.session import get_db


def find_session(request: Request, db: Session):
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 256:
        return None
    return db.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == digest(token),
            AuthToken.kind == "session",
            AuthToken.used_at.is_(None),
            AuthToken.expires_at > utcnow(),
        )
    )


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    session = find_session(request, db)
    user = db.get(User, session.user_id) if session else None
    if not user:
        raise AppError(401, "unauthenticated", "Необходимо войти в аккаунт")
    if user.status != "active":
        raise AppError(403, "user_blocked", "Аккаунт заблокирован")
    if user.verified_at is None:
        raise AppError(403, "email_unverified", "Подтвердите email")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise AppError(403, "forbidden", "Недостаточно прав")
    return user


def require_csrf(request: Request, db: Session = Depends(get_db)):
    origin = request.headers.get("origin")
    if origin and origin not in settings.allowed_origins:
        raise AppError(403, "csrf_invalid", "Запрос не прошёл проверку безопасности")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("x-csrf-token", "")
    if (
        len(cookie) < 32
        or len(cookie) > 256
        or not secrets.compare_digest(cookie, header)
    ):
        raise AppError(403, "csrf_invalid", "Запрос не прошёл проверку безопасности")
    session = find_session(request, db)
    if session and not secrets.compare_digest(session.csrf_hash or "", digest(header)):
        raise AppError(403, "csrf_invalid", "Запрос не прошёл проверку безопасности")
