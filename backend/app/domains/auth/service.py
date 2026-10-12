import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import digest
from app.db.models import AuthToken, User, utcnow


def issue_token(db: Session, user: User, kind: str, csrf: str | None = None) -> str:
    raw = secrets.token_urlsafe(32)
    ttl = (
        settings.session_ttl_seconds
        if kind == "session"
        else settings.email_token_ttl_seconds
    )
    db.add(
        AuthToken(
            user_id=user.id,
            kind=kind,
            token_hash=digest(raw),
            csrf_hash=digest(csrf) if csrf else None,
            expires_at=utcnow() + timedelta(seconds=ttl),
        )
    )
    return raw


def revoke_tokens(db: Session, user_id: str, kind: str | None = None):
    stmt = update(AuthToken).where(
        AuthToken.user_id == user_id, AuthToken.used_at.is_(None)
    )
    if kind:
        stmt = stmt.where(AuthToken.kind == kind)
    db.execute(stmt.values(used_at=utcnow()))


def consume_token(db: Session, raw: str, kind: str) -> User:
    hashed = digest(raw)
    user_id = db.scalar(
        select(AuthToken.user_id).where(
            AuthToken.token_hash == hashed,
            AuthToken.kind == kind,
        )
    )
    # Lock the user before tokens: reset, login and blocking share lock order.
    user = (
        db.scalar(select(User).where(User.id == user_id).with_for_update())
        if user_id
        else None
    )
    claimed = (
        db.scalar(
            update(AuthToken)
            .where(
                AuthToken.token_hash == hashed,
                AuthToken.kind == kind,
                AuthToken.used_at.is_(None),
                AuthToken.expires_at > utcnow(),
            )
            .values(used_at=utcnow())
            .returning(AuthToken.id)
        )
        if user
        else None
    )
    if not claimed or not user or user.status != "active":
        raise AppError(
            400, "token_invalid", "Ссылка недействительна или срок её действия истёк"
        )
    return user
