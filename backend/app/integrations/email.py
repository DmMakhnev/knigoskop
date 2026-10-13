import json
import os
from datetime import UTC, datetime
from uuid import uuid4

from app.core.config import settings
from app.core.errors import AppError


class FileMailBackend:
    """Local-only outbox. Never expose it through an HTTP endpoint."""

    def send(self, recipient: str, kind: str, token: str):
        directory = settings.mail_directory
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = directory / f"{uuid4()}.json"
            route = "verify-email" if kind == "verify_email" else "reset-password"
            message = {
                "recipient": recipient,
                "kind": kind,
                "created_at": datetime.now(UTC).isoformat(),
                "link": f"{settings.public_base_url.rstrip('/')}/{route}#token={token}",
            }
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as output:
                json.dump(message, output, ensure_ascii=False)
        except OSError as exc:
            raise AppError(
                503, "mail_unavailable", "Отправка письма временно недоступна"
            ) from exc

    def purge(self, recipient: str):
        if not settings.mail_directory.exists():
            return
        try:
            for path in settings.mail_directory.glob("*.json"):
                message = json.loads(path.read_text())
                if message.get("recipient") == recipient:
                    path.unlink()
        except (OSError, ValueError) as exc:
            raise AppError(
                503, "mail_unavailable", "Удаление данных временно недоступно"
            ) from exc


def get_mailer():
    return {
        "file": FileMailBackend,
        "smtp": SMTPMailBackend,
        "disabled": DisabledMailBackend,
    }[settings.mail_backend]()


class SMTPMailBackend:
    def send(self, recipient: str, kind: str, token: str):
        import smtplib
        import ssl
        from email.message import EmailMessage

        route = "verify-email" if kind == "verify_email" else "reset-password"
        message = EmailMessage()
        try:
            message["From"] = settings.smtp_from
            message["To"] = recipient
            message["Subject"] = (
                "Книгоскоп: подтверждение адреса"
                if kind == "verify_email"
                else "Книгоскоп: восстановление пароля"
            )
            message.set_content(
                "Откройте ссылку:\n"
                + settings.public_base_url.rstrip("/")
                + "/"
                + route
                + "#token="
                + token
            )
            context = ssl.create_default_context()
            if settings.smtp_tls == "ssl":
                connection = smtplib.SMTP_SSL(
                    settings.smtp_host,
                    settings.smtp_port,
                    timeout=settings.smtp_timeout_seconds,
                    context=context,
                )
            else:
                connection = smtplib.SMTP(
                    settings.smtp_host,
                    settings.smtp_port,
                    timeout=settings.smtp_timeout_seconds,
                )
            with connection as smtp:
                if settings.smtp_tls == "starttls":
                    smtp.ehlo()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                if settings.smtp_username:
                    smtp.login(
                        settings.smtp_username,
                        settings.smtp_password.get_secret_value()
                        if settings.smtp_password
                        else "",
                    )
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError, ValueError):
            raise AppError(
                503, "mail_unavailable", "Отправка письма временно недоступна"
            ) from None

    def purge(self, recipient: str):
        # No local copies; the mail operator controls upstream retention.
        return None


class DisabledMailBackend:
    def send(self, recipient: str, kind: str, token: str):
        raise AppError(
            503, "mail_disabled", "Почта на тестовом сервере ещё не подключена"
        )

    def purge(self, recipient: str):
        return None
