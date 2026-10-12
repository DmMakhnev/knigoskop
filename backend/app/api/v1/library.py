from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.models import Edition, LibraryFolder, LibraryItem, TextVersion, User, Work
from app.db.session import get_db
from app.domains.auth.dependencies import require_csrf, require_user
from app.domains.library.parsers import parse_book
from app.domains.library.schemas import FolderInput, ItemInput, ItemPatch, Metadata
from app.domains.library.service import (
    commit_import,
    delete_blobs,
    describe,
    import_text,
    item_for_user,
    link_text,
    locked_user,
    remove_item,
    rollback_import,
)
from app.integrations.storage import StorageBackend, get_storage

router = APIRouter(prefix="/api/v1", tags=["library"])
mutation = [Depends(require_csrf)]


def folder_owned(db, folder_id, user_id):
    folder = db.scalar(
        select(LibraryFolder).where(
            LibraryFolder.id == folder_id, LibraryFolder.user_id == user_id
        )
    )
    if not folder:
        raise AppError(404, "folder_missing", "Папка не найдена")
    return folder


@router.post("/books/upload", dependencies=mutation, status_code=201)
def upload(
    file: UploadFile = File(),
    title: str = Form(),
    author: str = Form("Неизвестный автор"),
    language: str = Form("ru"),
    edition_label: str = Form("Пользовательское издание"),
    translator: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
):
    try:
        meta = Metadata(
            title=title,
            author=author,
            language=language,
            edition_label=edition_label,
            translator=translator,
        )
    except ValidationError:
        raise AppError(422, "validation_error", "Проверьте сведения о книге") from None
    fmt = Path(file.filename or "").suffix.lower().lstrip(".")
    data = file.file.read(settings.upload_max_bytes + 1)
    if len(data) > settings.upload_max_bytes:
        raise AppError(413, "file_too_large", "Файл превышает допустимый размер")
    text = parse_book(data, fmt)
    locked_user(db, user)
    try:
        version = import_text(db, storage, meta, data, text, fmt, user.id)
        item = link_text(db, user.id, version)
        commit_import(db, storage)
    except Exception:
        rollback_import(db, storage)
        raise
    return describe(db, item)


@router.get("/books/public")
def public_books(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    rows = db.execute(
        select(TextVersion, Edition, Work)
        .join(Edition, TextVersion.edition_id == Edition.id)
        .join(Work, Edition.work_id == Work.id)
        .where(TextVersion.visibility == "public")
        .order_by(Work.title, TextVersion.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "text_version_id": v.id,
                "title": w.title,
                "author": w.author,
                "edition_label": e.edition_label,
                "rights_status": e.rights_status,
                "provenance": e.provenance,
            }
            for v, e, w in rows
        ]
    }


@router.post("/library", dependencies=mutation, status_code=201)
def add_book(
    payload: ItemInput,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        version_id = str(UUID(payload.text_version_id))
    except ValueError:
        raise AppError(404, "book_missing", "Книга не найдена") from None
    locked_user(db, user)
    version = db.get(TextVersion, version_id)
    if not version:
        raise AppError(404, "book_missing", "Книга не найдена")
    item = link_text(db, user.id, version)
    db.commit()
    return describe(db, item)


@router.get("/library")
def library(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    sort: Literal["newest", "oldest", "title", "author"] = "newest",
    favorite: bool | None = None,
    folder_id: UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    query = (
        select(LibraryItem)
        .join(TextVersion)
        .join(Edition)
        .join(Work)
        .where(LibraryItem.user_id == user.id)
    )
    if favorite is not None:
        query = query.where(LibraryItem.favorite == favorite)
    if folder_id:
        folder_owned(db, str(folder_id), user.id)
        query = query.where(LibraryItem.folder_id == str(folder_id))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    ordering = {
        "newest": LibraryItem.added_at.desc(),
        "oldest": LibraryItem.added_at,
        "title": Work.title,
        "author": Work.author,
    }[sort]
    items = db.scalars(
        query.order_by(ordering, LibraryItem.id).offset(offset).limit(limit)
    ).all()
    return {"items": [describe(db, item) for item in items], "total": total}


@router.patch("/library/{item_id}", dependencies=mutation)
def edit_item(
    item_id: UUID,
    payload: ItemPatch,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    locked_user(db, user)
    item = item_for_user(db, str(item_id), user.id)
    if "favorite" in payload.model_fields_set:
        if payload.favorite is None:
            raise AppError(
                422, "validation_error", "Избранное должно быть логическим значением"
            )
        item.favorite = payload.favorite
    if "folder_id" in payload.model_fields_set:
        if payload.folder_id:
            try:
                folder_id = str(UUID(payload.folder_id))
            except ValueError:
                raise AppError(404, "folder_missing", "Папка не найдена") from None
            folder_owned(db, folder_id, user.id)
            item.folder_id = folder_id
        else:
            item.folder_id = None
    db.commit()
    return describe(db, item)


@router.delete("/library/{item_id}", dependencies=mutation, status_code=204)
def delete_item(
    item_id: UUID,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
):
    locked_user(db, user)
    keys = remove_item(db, item_for_user(db, str(item_id), user.id))
    db.commit()
    delete_blobs(storage, keys)


@router.get("/library/{item_id}/original")
def original(
    item_id: UUID,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
):
    item = item_for_user(db, str(item_id), user.id)
    version = db.get(TextVersion, item.text_version_id)
    return Response(
        storage.read(version.storage_key),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="book.{version.format}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/folders")
def folders(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return {
        "items": [
            {"id": f.id, "name": f.name}
            for f in db.scalars(
                select(LibraryFolder)
                .where(LibraryFolder.user_id == user.id)
                .order_by(LibraryFolder.name, LibraryFolder.id)
            )
        ]
    }


@router.post("/folders", dependencies=mutation, status_code=201)
def create_folder(
    payload: FolderInput,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    locked_user(db, user)
    folder = LibraryFolder(user_id=user.id, name=payload.name)
    db.add(folder)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(
            409, "folder_exists", "Папка с таким именем уже существует"
        ) from None
    return {"id": folder.id, "name": folder.name}


@router.delete("/folders/{folder_id}", dependencies=mutation, status_code=204)
def delete_folder(
    folder_id: UUID, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    locked_user(db, user)
    db.delete(folder_owned(db, str(folder_id), user.id))
    db.commit()


@router.get("/library/{item_id}")
def get_item(
    item_id: UUID, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    return describe(db, item_for_user(db, str(item_id), user.id))


@router.patch("/folders/{folder_id}", dependencies=mutation)
def rename_folder(
    folder_id: UUID,
    payload: FolderInput,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    locked_user(db, user)
    folder = folder_owned(db, str(folder_id), user.id)
    folder.name = payload.name
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(
            409, "folder_exists", "Папка с таким именем уже существует"
        ) from None
    return {"id": folder.id, "name": folder.name}
