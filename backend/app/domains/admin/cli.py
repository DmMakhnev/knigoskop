"""Explicit metadata-only backfill of old file sizes; dry-run by default."""

import argparse
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.audio_models import AudioSegment
from app.db.models import AuditEvent, TextVersion
from app.db.session import get_engine
from app.integrations.storage import get_storage


def account_storage(db, storage, apply=False):
    measurements = []
    for row in db.scalars(
        select(TextVersion).where(TextVersion.storage_bytes.is_(None))
    ):
        measurements.append(
            (row, storage.size(row.storage_key) + storage.size(row.normalized_text_key))
        )
    for row in db.scalars(
        select(AudioSegment).where(
            AudioSegment.storage_key.is_not(None), AudioSegment.storage_bytes.is_(None)
        )
    ):
        measurements.append((row, storage.size(row.storage_key)))
    if apply:
        for row, size in measurements:
            row.storage_bytes = size
            db.add(
                AuditEvent(actor_id=None, action="storage_accounted", entity_id=row.id)
            )
        db.commit()
    return {
        "objects": len(measurements),
        "bytes": sum(size for _, size in measurements),
        "applied": apply,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Учёт размеров старых файлов без чтения содержимого"
    )
    parser.add_argument("command", choices=["storage-accounting"])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Записать размеры в БД; по умолчанию только проверить",
    )
    args = parser.parse_args()
    with Session(get_engine()) as db:
        try:
            report = account_storage(db, get_storage(), args.apply)
        except AppError:
            parser.exit(
                1, "storage_unavailable: учёт не выполнен; проверьте наличие файлов\n"
            )
        print(json.dumps(report))


if __name__ == "__main__":
    main()
