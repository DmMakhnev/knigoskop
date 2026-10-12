"""Private storage; keys are generated internally, never derived from filenames."""

import os
import re
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.core.config import settings
from app.core.errors import AppError


class StorageBackend(Protocol):
    def put(self, content: bytes) -> str: ...
    def read(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root

    def path(self, key: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", key):
            raise AppError(400, "invalid_storage_key", "Некорректный ключ файла")
        return self.root / key

    def put(self, content: bytes) -> str:
        key = uuid4().hex
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.path(key).open("xb") as stream:
                os.chmod(stream.name, 0o600)
                stream.write(content)
        except OSError:
            self.path(key).unlink(missing_ok=True)
            raise AppError(
                503, "storage_unavailable", "Хранилище временно недоступно"
            ) from None
        return key

    def read(self, key: str) -> bytes:
        try:
            return self.path(key).read_bytes()
        except OSError:
            raise AppError(
                503, "storage_unavailable", "Файл временно недоступен"
            ) from None

    def delete(self, key: str) -> None:
        try:
            self.path(key).unlink(missing_ok=True)
        except OSError:
            raise AppError(
                503, "storage_unavailable", "Не удалось удалить файл"
            ) from None


def get_storage() -> StorageBackend:
    return LocalStorage(settings.storage_directory)
