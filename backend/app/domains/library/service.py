import hashlib
import json
import unicodedata

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import Edition, LibraryItem, TextVersion, User, Work
from app.domains.library.schemas import Metadata
from app.domains.reader.structure import ensure_structure
from app.integrations.storage import StorageBackend


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def normalized(value):
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def locked_user(db: Session, user: User):
    current = db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not current or current.status != "active":
        raise AppError(401, "session_invalid", "Войдите в аккаунт снова")


def unique_row(db, model, key, **values):
    existing = db.scalar(select(model).where(model.identity == key))
    if existing:
        return existing
    try:
        with db.begin_nested():
            row = model(identity=key, **values)
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        row = db.scalar(select(model).where(model.identity == key))
        if row is None:
            raise
        return row


def import_text(
    db: Session,
    storage: StorageBackend,
    meta: Metadata,
    original: bytes,
    text: str,
    fmt: str,
    owner_id: str | None,
    provenance="",
    rights_status="cc0",
    source_id=None,
    source_candidate_id=None,
    source_fingerprint=None,
) -> TextVersion:
    """Caller must commit/rollback; existing blobs are immutable."""
    if owner_id is None and not provenance:
        raise AppError(422, "rights_required", "Укажите основание публикации текста")
    scope = owner_id or "public"
    work = unique_row(
        db,
        Work,
        identity(
            scope,
            normalized(meta.title),
            normalized(meta.author),
            normalized(meta.language),
        ),
        title=meta.title,
        author=meta.author,
        language=meta.language,
    )
    edition = unique_row(
        db,
        Edition,
        identity(
            work.id,
            normalized(meta.edition_label),
            normalized(meta.translator),
            *(
                [source_id, source_candidate_id, source_fingerprint]
                if source_id
                else []
            ),
        ),
        work_id=work.id,
        edition_label=meta.edition_label,
        translator=meta.translator,
        rights_status="private_upload" if owner_id else rights_status,
        source_id=source_id,
        source_candidate_id=source_candidate_id,
        provenance=provenance,
    )
    checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = identity(edition.id, scope, checksum)
    existing = db.scalar(select(TextVersion).where(TextVersion.identity == key))
    if existing:
        ensure_structure(db, existing, text=text)
        return existing
    stored = []
    try:
        stored.append(storage.put(original))
        stored.append(storage.put(text.encode("utf-8")))
        version = unique_row(
            db,
            TextVersion,
            key,
            edition_id=edition.id,
            checksum=checksum,
            original_checksum=hashlib.sha256(original).hexdigest(),
            storage_key=stored[0],
            normalized_text_key=stored[1],
            format=fmt,
            visibility="private" if owner_id else "public",
            owner_user_id=owner_id,
        )
        if version.storage_key != stored[0]:
            for blob in stored:
                storage.delete(blob)
        else:
            # Caller uses this list to clean files if the transaction fails.
            db.info.setdefault("new_blobs", []).extend(stored)
        ensure_structure(db, version, text=text)
        return version
    except Exception:
        for blob in stored:
            storage.delete(blob)
        raise


def rollback_import(db: Session, storage: StorageBackend):
    db.rollback()
    for key in db.info.pop("new_blobs", []):
        storage.delete(key)


def commit_import(db: Session, storage: StorageBackend):
    try:
        db.commit()
        db.info.pop("new_blobs", None)
    except Exception:
        rollback_import(db, storage)
        raise


def link_text(db: Session, user_id: str, version: TextVersion):
    if version.visibility == "private" and version.owner_user_id != user_id:
        raise AppError(404, "book_missing", "Книга не найдена")
    item = db.scalar(
        select(LibraryItem).where(
            LibraryItem.user_id == user_id, LibraryItem.text_version_id == version.id
        )
    )
    if not item:
        item = LibraryItem(user_id=user_id, text_version_id=version.id)
        db.add(item)
        db.flush()
    return item


def item_for_user(db, item_id, user_id):
    item = db.scalar(
        select(LibraryItem).where(
            LibraryItem.id == item_id, LibraryItem.user_id == user_id
        )
    )
    if not item:
        raise AppError(404, "item_missing", "Запись библиотеки не найдена")
    return item


def describe(db, item):
    version = db.get(TextVersion, item.text_version_id)
    edition = db.get(Edition, version.edition_id)
    work = db.get(Work, edition.work_id)
    return {
        "id": item.id,
        "text_version_id": version.id,
        "title": work.title,
        "author": work.author,
        "language": work.language,
        "edition_label": edition.edition_label,
        "translator": edition.translator,
        "visibility": version.visibility,
        "format": version.format,
        "favorite": item.favorite,
        "folder_id": item.folder_id,
        "added_at": item.added_at,
        "rights_status": edition.rights_status,
        "source_id": edition.source_id,
        "provenance": edition.provenance,
    }


def remove_item(db: Session, item: LibraryItem) -> list[str]:
    version = db.get(TextVersion, item.text_version_id)
    from app.domains.audio.service import audio_keys

    keys = audio_keys(db, item.id)
    db.delete(item)
    db.flush()
    if version.visibility == "public":
        return keys
    keys += [version.storage_key, version.normalized_text_key]
    edition = db.get(Edition, version.edition_id)
    work = db.get(Work, edition.work_id)
    db.delete(version)
    db.flush()
    if not db.scalar(
        select(TextVersion.id).where(TextVersion.edition_id == edition.id)
    ):
        db.delete(edition)
        db.flush()
        if not db.scalar(select(Edition.id).where(Edition.work_id == work.id)):
            db.delete(work)
    return keys


def remove_user_library(db, user_id):
    keys = []
    for item in db.scalars(
        select(LibraryItem).where(LibraryItem.user_id == user_id)
    ).all():
        keys.extend(remove_item(db, item))
    return keys


def delete_blobs(storage, keys):
    failed = False
    for key in keys:
        try:
            storage.delete(key)
        except AppError:
            failed = True
    if failed:
        raise AppError(
            503,
            "storage_unavailable",
            "Запись удалена; очистка файлов требует повторной попытки",
        )
