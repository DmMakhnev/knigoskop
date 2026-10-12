"""Catalog search and explicit, rights-checked import."""

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.models import AuditEvent, Edition, Source, TextVersion, User, Work
from app.db.session import get_db
from app.domains.auth.dependencies import require_admin, require_csrf, require_user
from app.domains.library.parsers import parse_book
from app.domains.library.schemas import Metadata
from app.domains.library.service import (
    commit_import,
    describe,
    identity,
    import_text,
    link_text,
    locked_user,
    rollback_import,
)
from app.integrations import sources
from app.integrations.storage import get_storage

router = APIRouter(prefix="/api/v1", tags=["catalog"])


class SourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    host: str = Field(min_length=1, max_length=253)
    catalog_url: str = Field(max_length=2000)
    enabled: bool = False
    approvals: list[sources.Approval] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_catalog(self):
        if not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", self.host):
            raise ValueError("Invalid host")
        sources.destination(self.catalog_url, self.host)
        if len({item.candidate_id for item in self.approvals}) != len(self.approvals):
            raise ValueError("Duplicate approval")
        return self


class ImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: UUID
    candidate_id: str = Field(min_length=1, max_length=100)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


def serialize_source(source):
    return {
        key: getattr(source, key)
        for key in ("id", "name", "host", "catalog_url", "enabled", "approvals")
    }


@router.get("/admin/sources")
def list_sources(
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    rows = db.scalars(select(Source).order_by(Source.id)).all()
    return {
        "items": [serialize_source(row) for row in rows[offset : offset + limit]],
        "total": len(rows),
    }


def save_source(db, admin, data, source=None):
    # Lock all administrator rows in a stable order across concurrent editors.
    db.scalars(
        select(User).where(User.role == "admin").order_by(User.id).with_for_update()
    ).all()
    locked_user(db, admin)
    enabled = db.scalars(select(Source).where(Source.enabled.is_(True))).all()
    if (
        data.enabled
        and sum(row.id != (source.id if source else None) for row in enabled)
        >= settings.source_max_enabled
    ):
        raise AppError(409, "source_limit", "Достигнут лимит подключённых источников")
    if source is None:
        source = Source()
        db.add(source)
    for key, value in data.model_dump().items():
        setattr(source, key, value)
    db.flush()
    db.add(
        AuditEvent(actor_id=admin.id, action="source_configured", entity_id=source.id)
    )
    db.commit()
    return serialize_source(source)


@router.post("/admin/sources", status_code=201, dependencies=[Depends(require_csrf)])
def create_source(
    data: SourceInput, db: Session = Depends(get_db), admin=Depends(require_admin)
):
    return save_source(db, admin, data)


@router.put("/admin/sources/{source_id}", dependencies=[Depends(require_csrf)])
def update_source(
    source_id: UUID,
    data: SourceInput,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    source = db.get(Source, str(source_id))
    if not source:
        raise AppError(404, "source_missing", "Источник не найден")
    return save_source(db, admin, data, source)


def external_results(source, query):
    try:
        candidates = sources.get_connector(source).search(query)
        approvals = {item["candidate_id"]: item for item in source.approvals}
        result = []
        for candidate in candidates:
            token = sources.fingerprint(candidate)
            approval = approvals.get(candidate.id)
            allowed = bool(approval and approval["fingerprint"] == token)
            result.append(
                {
                    **candidate.model_dump(exclude={"id", "url"}),
                    "candidate_id": candidate.id,
                    "fingerprint": token,
                    "source_id": source.id,
                    "source_name": source.name,
                    "rights_status": approval["rights_status"]
                    if allowed
                    else "unverified",
                    "provenance": approval["provenance"]
                    if allowed
                    else "Права не подтверждены",
                    "can_import": allowed,
                }
            )
        return result, None
    except Exception:
        # Third-party responses and exceptions are never echoed or logged.
        return [], {"source_id": source.id, "message": "Источник временно недоступен"}


@router.get("/books/search")
def search(
    q: str = Query(min_length=1, max_length=200),
    scope: Literal["local", "external"] = "local",
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    words = q.casefold().split()
    if not words:
        raise AppError(422, "query_empty", "Введите название или автора")
    if scope == "local":
        rows = db.execute(
            select(TextVersion, Edition, Work)
            .join(Edition, TextVersion.edition_id == Edition.id)
            .join(Work, Edition.work_id == Work.id)
            .where(
                or_(
                    TextVersion.visibility == "public",
                    TextVersion.owner_user_id == user.id,
                )
            )
            .order_by(Work.title, Edition.id, TextVersion.id)
        )
        items = []
        for version, edition, work in rows:
            haystack = " ".join(
                [work.title, work.author, edition.edition_label, edition.translator]
            ).casefold()
            if all(word in haystack for word in words):
                source = (
                    db.get(Source, edition.source_id) if edition.source_id else None
                )
                items.append(
                    {
                        "text_version_id": version.id,
                        "title": work.title,
                        "author": work.author,
                        "language": work.language,
                        "edition_label": edition.edition_label,
                        "translator": edition.translator,
                        "format": version.format,
                        "rights_status": edition.rights_status,
                        "provenance": edition.provenance,
                        "source_name": source.name if source else "Локальный каталог",
                        "can_import": True,
                    }
                )
        errors = []
    else:
        configured = db.scalars(
            select(Source)
            .where(Source.enabled.is_(True))
            .order_by(Source.id)
            .limit(settings.source_max_enabled)
        ).all()
        for source in configured:
            db.expunge(source)
        db.rollback()  # No open database transaction during external I/O.
        with ThreadPoolExecutor(max_workers=settings.source_max_enabled) as pool:
            results = list(
                pool.map(lambda source: external_results(source, q), configured)
            )
        items = [item for result, error in results for item in result]
        errors = [error for result, error in results if error]
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "source_errors": errors,
    }


@router.post("/books/import", status_code=201, dependencies=[Depends(require_csrf)])
def import_candidate(
    data: ImportInput,
    db: Session = Depends(get_db),
    user=Depends(require_user),
    storage=Depends(get_storage),
):
    source = db.get(Source, str(data.source_id))
    if not source or not source.enabled:
        raise AppError(403, "source_disabled", "Источник не разрешён")
    approval = next(
        (
            item
            for item in source.approvals
            if item["candidate_id"] == data.candidate_id
            and item["fingerprint"] == data.fingerprint
        ),
        None,
    )
    if not approval:
        raise AppError(403, "rights_unverified", "Права на это издание не подтверждены")
    db.expunge(source)
    db.rollback()  # Retain the review snapshot; revalidate under lock before commit.
    try:
        connector = sources.get_connector(source)
        candidate = next(
            (
                item
                for item in connector.search("")
                if item.id == data.candidate_id
                and sources.fingerprint(item) == data.fingerprint
            ),
            None,
        )
        if candidate is None:
            raise AppError(
                409, "candidate_changed", "Издание изменилось; повторите поиск"
            )
        original = connector.fetch(candidate)
    except AppError:
        raise
    except Exception:
        raise sources.unavailable() from None
    if hashlib.sha256(original).hexdigest() != approval["checksum"]:
        raise AppError(
            409,
            "source_checksum_mismatch",
            "Текст изменился; требуется повторная проверка прав",
        )
    text = parse_book(original, candidate.format)
    try:
        locked_user(db, user)
        # Refresh under lock after network/parsing so revocation wins.
        current = db.scalar(
            select(Source)
            .where(Source.id == source.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not current.enabled or approval not in current.approvals:
            raise AppError(403, "rights_unverified", "Разрешение на импорт отозвано")
        version = import_text(
            db,
            storage,
            Metadata.model_validate(
                candidate.model_dump(include=set(Metadata.model_fields))
            ),
            original,
            text,
            candidate.format,
            None,
            provenance=approval["provenance"],
            rights_status=approval["rights_status"],
            source_id=source.id,
            source_candidate_id=candidate.id,
            source_fingerprint=identity(
                data.fingerprint,
                approval["checksum"],
                approval["rights_status"],
                approval["provenance"],
            ),
        )
        item = link_text(db, user.id, version)
        result = describe(db, item)
        commit_import(db, storage)
        return result
    except Exception:
        rollback_import(db, storage)
        raise
