"""Explicit local CC0 demo seed and recovery of unreferenced storage files."""

import argparse
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import TextVersion
from app.db.session import get_engine
from app.domains.library.schemas import Metadata
from app.domains.library.service import commit_import, import_text
from app.integrations.storage import LocalStorage

DEMO = (
    "Книгоскоп: маленькая история\n\nЧитатель открыл книгу. За окном шумел дождь. "
    "Он отметил любимый абзац и решил вернуться к истории завтра.\n"
)


def main():
    parser = argparse.ArgumentParser(description="Локальные операции библиотеки")
    parser.add_argument("command", choices=["seed-demo", "collect-orphans"])
    args = parser.parse_args()
    storage = LocalStorage(settings.storage_directory)
    with Session(get_engine()) as db:
        if args.command == "seed-demo":
            import_text(
                db,
                storage,
                Metadata(
                    title="Маленькая история",
                    author="Книгоскоп",
                    edition_label="Демонстрационный текст CC0",
                ),
                DEMO.encode(),
                DEMO.strip(),
                "txt",
                None,
                "Self-authored demo dedicated to CC0-1.0; not a third-party book",
            )
            commit_import(db, storage)
            print("Демонстрационный текст CC0 добавлен")
        else:
            keys = set()
            for version in db.scalars(select(TextVersion)):
                keys.update([version.storage_key, version.normalized_text_key])
            removed = 0
            if storage.root.exists():
                for path in storage.root.iterdir():
                    # Ignore new writes to avoid racing an in-flight import.
                    if (
                        path.is_file()
                        and path.name not in keys
                        and time.time() - path.stat().st_mtime > 3600
                    ):
                        storage.delete(path.name)
                        removed += 1
            print(f"Удалено файлов без ссылок: {removed}")


if __name__ == "__main__":
    main()
