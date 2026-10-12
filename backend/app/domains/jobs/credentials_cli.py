"""Bind an explicitly selected provider to a securely injected raw API key."""

import argparse
import os
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.provider_secrets import encrypt
from app.db.ai_models import Provider, ProviderCredential
from app.db.models import AuditEvent
from app.db.session import get_engine
from app.domains.jobs.schemas import CredentialInput


def bind(db, provider_id, raw_key):
    try:
        value = CredentialInput(api_key=raw_key).api_key.get_secret_value()
    except ValidationError:
        raise AppError(422, "credential_invalid", "Неверный формат ключа") from None
    if any(c in value for c in "\r\n"):
        raise AppError(422, "credential_invalid", "Неверный формат ключа")
    provider = db.scalar(
        select(Provider).where(Provider.id == provider_id).with_for_update()
    )
    if not provider or provider.protocol != "openai_chat":
        raise AppError(
            422, "provider_invalid", "Выберите совместимого внешнего провайдера"
        )
    encrypted = encrypt(value)
    row = db.scalar(
        select(ProviderCredential).where(ProviderCredential.provider_id == provider.id)
    )
    if row:
        row.encrypted_secret = encrypted
    else:
        db.add(ProviderCredential(provider_id=provider.id, encrypted_secret=encrypted))
    db.add(AuditEvent(action="provider_credential_cli_updated", entity_id=provider.id))
    db.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-id", type=UUID, required=True)
    args = parser.parse_args()
    raw = os.environ.get("KNIGOSKOP_LLM_API_KEY", "")
    if not raw:
        raise SystemExit(
            "Missing KNIGOSKOP_LLM_API_KEY; enter it in secure environment settings."
        )
    try:
        with Session(get_engine()) as db:
            bind(db, str(args.provider_id), raw)
    except AppError as exc:
        raise SystemExit("Credential was not saved: " + exc.code) from None
    print("Provider credential encrypted and saved; no external request made.")


if __name__ == "__main__":
    main()
