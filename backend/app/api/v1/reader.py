import hashlib
import threading
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.models import (
    Bookmark,
    LibraryItem,
    ReadingProgress,
    ReadingSettings,
    TextSection,
    TextVersion,
    utcnow,
)
from app.db.session import get_db
from app.domains.auth.dependencies import require_csrf, require_user
from app.domains.library.service import describe, item_for_user, locked_user
from app.domains.reader.structure import ALGORITHM, ensure_structure
from app.integrations.storage import get_storage

router = APIRouter(prefix="/api/v1", tags=["reader"])
mutation = [Depends(require_csrf)]
read_slots = threading.BoundedSemaphore(1)


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: Literal["light", "dark"] = "light"
    font: Literal["serif", "sans", "mono"] = "serif"
    font_size: int = Field(default=20, ge=14, le=36)
    line_height: float = Field(default=1.7, ge=1.2, le=2.5)
    width: int = Field(default=65, ge=40, le=100)


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text_version_id: UUID
    section_id: UUID
    offset: int = Field(ge=0)
    end_offset: int | None = Field(default=None, ge=1)


class ProgressInput(Position):
    library_item_id: UUID
    revision: int = Field(ge=0)


class BookmarkInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text_version_id: UUID
    section_id: UUID
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=100)


def section_dict(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "text_version_id",
            "parent_id",
            "kind",
            "title",
            "order_index",
            "start_offset",
            "end_offset",
        )
    }


def progress_dict(row):
    if row is None:
        return {"revision": 0, "position": None}
    return {
        "revision": row.revision,
        "position": {
            key: getattr(row, key)
            for key in ("text_version_id", "section_id", "offset", "end_offset")
        },
        "updated_at": row.updated_at,
    }


def bookmark_dict(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "text_version_id",
            "section_id",
            "start_offset",
            "end_offset",
            "label",
        )
    }


def authorized_version(db, version_id, user):
    item = db.scalar(
        select(LibraryItem).where(
            LibraryItem.user_id == user.id, LibraryItem.text_version_id == version_id
        )
    )
    if not item:
        raise AppError(404, "text_missing", "Текст не найден")
    return db.get(TextVersion, version_id)


def checked_section(db, version_id, section_id):
    row = db.scalar(
        select(TextSection).where(
            TextSection.id == section_id, TextSection.text_version_id == version_id
        )
    )
    if not row:
        raise AppError(404, "section_missing", "Раздел этой версии не найден")
    return row


def validate_range(row, start, end):
    if (
        start < row.start_offset
        or start >= row.end_offset
        or (end is not None and (end <= start or end > row.end_offset))
    ):
        raise AppError(422, "range_invalid", "Диапазон выходит за границы раздела")


@router.get("/library/{item_id}/reader")
def manifest(
    item_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    user=Depends(require_user),
    storage=Depends(get_storage),
):
    item = item_for_user(db, str(item_id), user.id)
    version = db.get(TextVersion, item.text_version_id)
    root = ensure_structure(db, version, storage=storage)
    db.commit()
    sections = db.scalars(
        select(TextSection)
        .where(TextSection.text_version_id == version.id)
        .order_by(TextSection.order_index)
        .offset(offset)
        .limit(limit)
    ).all()
    first = db.scalar(
        select(TextSection)
        .where(
            TextSection.text_version_id == version.id, TextSection.kind == "fragment"
        )
        .order_by(TextSection.start_offset)
    )
    progress = db.get(ReadingProgress, (user.id, item.id))
    return {
        "item": describe(db, item),
        "text_version_id": version.id,
        "offset_unit": "unicode_code_points",
        "structure_version": ALGORITHM,
        "length": root.end_offset,
        "first_section_id": first.id,
        "sections": [section_dict(row) for row in sections],
        "total": db.scalar(
            select(func.count())
            .select_from(TextSection)
            .where(TextSection.text_version_id == version.id)
        ),
        "progress": progress_dict(progress),
    }


@router.get("/texts/{version_id}/sections/{section_id}")
def read_section(
    version_id: UUID,
    section_id: UUID,
    start: int | None = Query(None, ge=0),
    end: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
    user=Depends(require_user),
    storage=Depends(get_storage),
):
    version = authorized_version(db, str(version_id), user)
    section = checked_section(db, version.id, str(section_id))
    start = section.start_offset if start is None else start
    validate_range(section, start, end)
    leaf = db.scalar(
        select(TextSection).where(
            TextSection.text_version_id == version.id,
            TextSection.kind == "fragment",
            TextSection.start_offset <= start,
            TextSection.end_offset > start,
        )
    )
    if not leaf:
        raise AppError(503, "structure_invalid", "Структура текста недоступна")
    if not read_slots.acquire(blocking=False):
        raise AppError(503, "reader_busy", "Читалка занята; повторите запрос")
    try:
        raw = storage.read(version.normalized_text_key)
        if hashlib.sha256(raw).hexdigest() != version.checksum:
            raise AppError(
                503, "text_integrity", "Не удалось проверить целостность текста"
            )
        text = raw.decode("utf-8")
        # Persisted fragment size stays fixed if runtime settings later change.
        stop = min(leaf.end_offset, leaf.start_offset + 16000)
        if start >= stop:
            raise AppError(503, "structure_invalid", "Структура текста недоступна")
        content = text[leaf.start_offset : stop]
    finally:
        read_slots.release()
    previous = db.scalar(
        select(TextSection.id).where(
            TextSection.text_version_id == version.id,
            TextSection.kind == "fragment",
            TextSection.end_offset == leaf.start_offset,
        )
    )
    following = db.scalar(
        select(TextSection.id).where(
            TextSection.text_version_id == version.id,
            TextSection.kind == "fragment",
            TextSection.start_offset == leaf.end_offset,
        )
    )
    return {
        "text_version_id": version.id,
        "section_id": section.id,
        "fragment": section_dict(leaf),
        "text": content,
        "start_offset": leaf.start_offset,
        "end_offset": stop,
        "highlight": {"start_offset": start, "end_offset": end} if end else None,
        "previous_section_id": previous,
        "next_section_id": following,
    }


@router.get("/library/{item_id}/reading-progress")
def get_progress(
    item_id: UUID, db: Session = Depends(get_db), user=Depends(require_user)
):
    item = item_for_user(db, str(item_id), user.id)
    return progress_dict(db.get(ReadingProgress, (user.id, item.id)))


@router.put("/me/reading-progress", dependencies=mutation)
def save_progress(
    data: ProgressInput, db: Session = Depends(get_db), user=Depends(require_user)
):
    locked_user(db, user)
    item = item_for_user(db, str(data.library_item_id), user.id)
    db.scalar(select(LibraryItem).where(LibraryItem.id == item.id).with_for_update())
    if item.text_version_id != str(data.text_version_id):
        raise AppError(422, "version_mismatch", "Позиция относится к другой версии")
    section = checked_section(db, item.text_version_id, str(data.section_id))
    validate_range(section, data.offset, data.end_offset)
    if section.kind != "fragment":
        raise AppError(
            422, "position_fragment", "Сохраните позицию в конкретном фрагменте"
        )
    row = db.get(ReadingProgress, (user.id, item.id))
    if data.revision != (row.revision if row else 0):
        raise AppError(
            409,
            "progress_conflict",
            "Позиция изменена на другом устройстве; обновите читалку",
        )
    if row is None:
        row = ReadingProgress(user_id=user.id, library_item_id=item.id)
        db.add(row)
    row.text_version_id = item.text_version_id
    row.section_id = section.id
    row.offset = data.offset
    row.end_offset = data.end_offset
    row.revision = data.revision + 1
    row.updated_at = utcnow()
    db.commit()
    return progress_dict(row)


@router.get("/me/reading-settings")
def get_preferences(db: Session = Depends(get_db), user=Depends(require_user)):
    row = db.get(ReadingSettings, user.id)
    return Preferences.model_validate(row.preferences if row else {}).model_dump()


@router.put("/me/reading-settings", dependencies=mutation)
def save_preferences(
    data: Preferences, db: Session = Depends(get_db), user=Depends(require_user)
):
    locked_user(db, user)
    row = db.get(ReadingSettings, user.id)
    if row is None:
        row = ReadingSettings(user_id=user.id)
        db.add(row)
    row.preferences = data.model_dump()
    db.commit()
    return row.preferences


@router.get("/library/{item_id}/bookmarks")
def list_bookmarks(
    item_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    item = item_for_user(db, str(item_id), user.id)
    rows = db.scalars(
        select(Bookmark)
        .where(Bookmark.library_item_id == item.id)
        .order_by(Bookmark.start_offset, Bookmark.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [bookmark_dict(row) for row in rows],
        "total": db.scalar(
            select(func.count())
            .select_from(Bookmark)
            .where(Bookmark.library_item_id == item.id)
        ),
    }


@router.post("/library/{item_id}/bookmarks", status_code=201, dependencies=mutation)
def add_bookmark(
    item_id: UUID,
    data: BookmarkInput,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    locked_user(db, user)
    item = item_for_user(db, str(item_id), user.id)
    if item.text_version_id != str(data.text_version_id):
        raise AppError(422, "version_mismatch", "Закладка относится к другой версии")
    section = checked_section(db, item.text_version_id, str(data.section_id))
    validate_range(section, data.start_offset, data.end_offset)
    if (
        db.scalar(
            select(func.count())
            .select_from(Bookmark)
            .where(Bookmark.library_item_id == item.id)
        )
        >= settings.bookmark_max_per_item
    ):
        raise AppError(409, "bookmark_limit", "Достигнут лимит закладок")
    row = Bookmark(
        library_item_id=item.id,
        text_version_id=item.text_version_id,
        section_id=section.id,
        start_offset=data.start_offset,
        end_offset=data.end_offset,
        label=data.label,
    )
    db.add(row)
    db.commit()
    return bookmark_dict(row)


@router.delete(
    "/library/{item_id}/bookmarks/{bookmark_id}", status_code=204, dependencies=mutation
)
def delete_bookmark(
    item_id: UUID,
    bookmark_id: UUID,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    locked_user(db, user)
    item = item_for_user(db, str(item_id), user.id)
    row = db.scalar(
        select(Bookmark).where(
            Bookmark.id == str(bookmark_id), Bookmark.library_item_id == item.id
        )
    )
    if not row:
        raise AppError(404, "bookmark_missing", "Закладка не найдена")
    db.delete(row)
    db.commit()
