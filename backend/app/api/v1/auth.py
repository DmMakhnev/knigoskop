import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.rate_limit import enforce_rate_limit, get_redis
from app.core.security import (
    CSRF_COOKIE,
    DUMMY_HASH,
    SESSION_COOKIE,
    check_password_policy,
    digest,
    hash_password,
    password_hasher,
    verify_password,
)
from app.db.models import AuditEvent, User, utcnow
from app.db.session import get_db
from app.domains.auth.dependencies import (
    find_session,
    require_admin,
    require_csrf,
    require_user,
)
from app.domains.auth.schemas import (
    Credentials,
    DeleteInput,
    EmailInput,
    MessageOutput,
    ResetInput,
    StatusInput,
    TokenInput,
    UserOutput,
    UsersOutput,
)
from app.domains.auth.service import consume_token, issue_token, revoke_tokens
from app.domains.library.service import delete_blobs, locked_user, remove_user_library
from app.integrations.email import get_mailer
from app.integrations.storage import get_storage

router = APIRouter(prefix="/api/v1")
mutations = [Depends(require_csrf)]
EMAIL_MESSAGE = "Если адрес подходит, письмо отправлено. Проверьте почту"


def limit(request: Request, redis, bucket: str, maximum: int, email: str | None = None):
    ip = request.client.host if request.client else "unknown"
    enforce_rate_limit(
        redis, bucket + ":ip", ip, settings.auth_ip_rate_limit if email else maximum
    )
    if email:
        enforce_rate_limit(redis, bucket + ":email", email, maximum)


def set_csrf(response: Response, value: str, ttl: int | None = None):
    response.set_cookie(
        CSRF_COOKIE,
        value,
        max_age=ttl or settings.csrf_ttl_seconds,
        secure=settings.session_cookie_secure,
        httponly=False,
        samesite="lax",
        path="/",
    )


def clear_cookies(response: Response):
    for name in [SESSION_COOKIE, CSRF_COOKIE]:
        response.delete_cookie(
            name,
            path="/",
            secure=settings.session_cookie_secure,
            httponly=name == SESSION_COOKIE,
            samesite="lax",
        )


@router.get("/auth/csrf", response_model=MessageOutput)
def csrf(request: Request, response: Response, db: Session = Depends(get_db)):
    cookie = request.cookies.get(CSRF_COOKIE, "")
    session = find_session(request, db)
    if session:
        if not secrets.compare_digest(session.csrf_hash or "", digest(cookie)):
            raw = secrets.token_urlsafe(32)
            session.csrf_hash = digest(raw)
            db.commit()
        else:
            raw = cookie
    else:
        raw = cookie if 32 <= len(cookie) <= 256 else secrets.token_urlsafe(32)
    set_csrf(response, raw, settings.session_ttl_seconds if session else None)
    response.headers["Cache-Control"] = "no-store"
    return MessageOutput(message="Проверка безопасности подготовлена")


@router.post(
    "/auth/register",
    status_code=202,
    response_model=MessageOutput,
    dependencies=mutations,
)
def register(
    payload: Credentials,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    mailer=Depends(get_mailer),
):
    limit(request, redis, "register", settings.register_rate_limit, payload.email)
    check_password_policy(payload.password)
    # Hash even for duplicates; responses never disclose account existence.
    hashed = hash_password(payload.password)
    if db.scalar(select(User).where(User.email == payload.email)):
        return MessageOutput(message=EMAIL_MESSAGE)
    user = User(email=payload.email, password_hash=hashed)
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return MessageOutput(message=EMAIL_MESSAGE)
    raw = issue_token(db, user, "verify_email")
    mailer.send(user.email, "verify_email", raw)
    db.commit()
    return MessageOutput(message=EMAIL_MESSAGE)


def send_email_token(payload, request, db, redis, mailer, kind):
    limit(request, redis, kind, settings.email_rate_limit, payload.email)
    user = db.scalar(select(User).where(User.email == payload.email).with_for_update())
    eligible = (
        user
        and user.status == "active"
        and (
            user.verified_at is None
            if kind == "verify_email"
            else user.verified_at is not None
        )
    )
    if eligible:
        revoke_tokens(db, user.id, kind)
        raw = issue_token(db, user, kind)
        mailer.send(user.email, kind, raw)
        db.commit()
    return MessageOutput(message=EMAIL_MESSAGE)


@router.post(
    "/auth/request-verification",
    status_code=202,
    response_model=MessageOutput,
    dependencies=mutations,
)
def request_verification(
    payload: EmailInput,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    mailer=Depends(get_mailer),
):
    return send_email_token(payload, request, db, redis, mailer, "verify_email")


@router.post("/auth/verify-email", response_model=MessageOutput, dependencies=mutations)
def verify_email(
    payload: TokenInput,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
):
    limit(request, redis, "verify", settings.token_rate_limit)
    user = consume_token(db, payload.token, "verify_email")
    user.verified_at = utcnow()
    revoke_tokens(db, user.id, "verify_email")
    db.commit()
    return MessageOutput(message="Email подтверждён. Теперь можно войти")


@router.post("/auth/login", response_model=UserOutput, dependencies=mutations)
def login(
    payload: Credentials,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
):
    limit(request, redis, "login", settings.login_rate_limit, payload.email)
    user = db.scalar(select(User).where(User.email == payload.email).with_for_update())
    valid = verify_password(
        payload.password, user.password_hash if user else DUMMY_HASH
    )
    if not user or not valid:
        raise AppError(401, "invalid_credentials", "Неверный email или пароль")
    if user.status != "active":
        raise AppError(403, "user_blocked", "Аккаунт заблокирован")
    if user.verified_at is None:
        raise AppError(403, "email_unverified", "Подтвердите email перед входом")
    if password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
    previous = find_session(request, db)
    if previous:
        previous.used_at = utcnow()
    csrf_value = secrets.token_urlsafe(32)
    raw = issue_token(db, user, "session", csrf_value)
    db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        raw,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    set_csrf(response, csrf_value, settings.session_ttl_seconds)
    return user


@router.post("/auth/logout", status_code=204, dependencies=mutations)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    session = find_session(request, db)
    if session:
        session.used_at = utcnow()
        db.commit()
    clear_cookies(response)


@router.post(
    "/auth/request-password-reset",
    status_code=202,
    response_model=MessageOutput,
    dependencies=mutations,
)
def request_reset(
    payload: EmailInput,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    mailer=Depends(get_mailer),
):
    return send_email_token(payload, request, db, redis, mailer, "password_reset")


@router.post(
    "/auth/reset-password", response_model=MessageOutput, dependencies=mutations
)
def reset_password(
    payload: ResetInput,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
):
    limit(request, redis, "reset", settings.token_rate_limit)
    check_password_policy(payload.password)
    user = consume_token(db, payload.token, "password_reset")
    user.password_hash = hash_password(payload.password)
    revoke_tokens(db, user.id)
    db.commit()
    clear_cookies(response)
    return MessageOutput(message="Пароль изменён. Войдите с новым паролем")


@router.get("/me", response_model=UserOutput)
def me(user: User = Depends(require_user)):
    return user


@router.delete("/me", status_code=204, dependencies=mutations)
def delete_me(
    payload: DeleteInput,
    request: Request,
    response: Response,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    mailer=Depends(get_mailer),
):
    limit(request, redis, "delete", settings.login_rate_limit, user.email)
    if not verify_password(payload.password, user.password_hash):
        raise AppError(401, "invalid_credentials", "Неверный пароль")
    if user.role == "admin":
        # Serialize admin deletion to preserve at least one active administrator.
        admins = db.scalars(
            select(User)
            .where(User.role == "admin", User.status == "active")
            .order_by(User.id)
            .with_for_update()
        ).all()
        if len(admins) <= 1:
            raise AppError(
                409, "last_admin", "Нельзя удалить последнего администратора"
            )
    locked_user(db, user)
    keys = remove_user_library(db, user.id)
    mailer.purge(user.email)
    db.delete(user)
    db.commit()
    delete_blobs(get_storage(), keys)
    clear_cookies(response)


@router.get("/admin/users", response_model=UsersOutput)
def admin_users(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.scalars(
        select(User).order_by(User.created_at, User.id).offset(offset).limit(limit)
    ).all()
    return UsersOutput(
        items=[UserOutput.model_validate(user) for user in users],
        total=db.scalar(select(func.count()).select_from(User)),
    )


@router.patch(
    "/admin/users/{user_id}", response_model=UserOutput, dependencies=mutations
)
def change_status(
    user_id: UUID,
    payload: StatusInput,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.scalar(select(User).where(User.id == str(user_id)).with_for_update())
    if not target:
        raise AppError(404, "user_not_found", "Пользователь не найден")
    if target.role == "admin":
        raise AppError(409, "admin_protected", "Блокировка администратора недоступна")
    if target.status != payload.status:
        target.status = payload.status
        if payload.status == "blocked":
            revoke_tokens(db, target.id)
        db.add(
            AuditEvent(
                actor_id=admin.id, action=f"user_{payload.status}", entity_id=target.id
            )
        )
    db.commit()
    return target
