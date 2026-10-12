"""Persistent defaults and overrides for logical library storage."""

from sqlalchemy import func, select

from app.core.config import settings
from app.core.errors import AppError
from app.db.ai_models import QuotaOverride, QuotaPolicy
from app.db.audio_models import AudioAsset, AudioSegment
from app.db.models import LibraryItem, TextVersion


def limit_for(db, user_id, metric):
    row = db.get(QuotaOverride, (user_id, metric)) or db.get(QuotaPolicy, metric)
    return row.limit_value if row else getattr(settings, "quota_" + metric)


def library_usage(db, user_id):
    books, book_bytes, unknown_books = db.execute(
        select(
            func.count(LibraryItem.id),
            func.coalesce(func.sum(TextVersion.storage_bytes), 0),
            func.count(LibraryItem.id).filter(TextVersion.storage_bytes.is_(None)),
        )
        .join(TextVersion, LibraryItem.text_version_id == TextVersion.id)
        .where(LibraryItem.user_id == user_id)
    ).one()
    audio_bytes, unknown_audio = db.execute(
        select(
            func.coalesce(func.sum(AudioSegment.storage_bytes), 0),
            func.count(AudioSegment.id).filter(
                AudioSegment.storage_key.is_not(None),
                AudioSegment.storage_bytes.is_(None),
            ),
        )
        .join(AudioAsset, AudioSegment.asset_id == AudioAsset.id)
        .where(AudioAsset.user_id == user_id)
    ).one()
    return {
        "books": books,
        "storage_bytes": book_bytes + audio_bytes,
        "unmeasured_files": unknown_books + unknown_audio,
    }


def check_storage(db, user_id, extra_bytes):
    usage = library_usage(db, user_id)
    if usage["unmeasured_files"]:
        raise AppError(
            409,
            "storage_measurement_required",
            "Для старых файлов требуется учёт размера перед новой загрузкой",
        )
    if usage["storage_bytes"] + extra_bytes > limit_for(db, user_id, "storage_bytes"):
        raise AppError(
            429, "storage_quota_exceeded", "Недостаточно места в квоте файлов"
        )
