import argparse
import getpass
import sys

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import check_password_policy, hash_password
from app.db.models import AuditEvent, User, utcnow
from app.db.session import get_engine
from app.domains.auth.schemas import EmailInput


def create_admin(db: Session, email: str, password: str):
    email = str(EmailInput(email=email).email)
    check_password_policy(password)
    if db.scalar(select(User).where(User.email == email)):
        raise AppError(
            409, "email_exists", "Адрес уже занят; существующий аккаунт не изменён"
        )
    user = User(
        email=email,
        password_hash=hash_password(password),
        role="admin",
        verified_at=utcnow(),
    )
    db.add(user)
    db.flush()
    db.add(AuditEvent(actor_id=user.id, action="admin_created", entity_id=user.id))
    db.commit()


def main():
    parser = argparse.ArgumentParser(description="Создание администратора Книгоскопа")
    parser.add_argument("command", choices=["create-admin"])
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Читать пароль из stdin для автоматизированного запуска",
    )
    args = parser.parse_args()
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Пароль: ")
        if password != getpass.getpass("Повторите пароль: "):
            parser.exit(1, "Пароли не совпадают\n")
    try:
        with Session(get_engine()) as db:
            create_admin(db, args.email, password)
    except (AppError, ValidationError, IntegrityError):
        parser.exit(
            1,
            "Администратор не создан: проверьте email, пароль и существующий аккаунт\n",
        )
    print("Администратор создан")


if __name__ == "__main__":
    main()
