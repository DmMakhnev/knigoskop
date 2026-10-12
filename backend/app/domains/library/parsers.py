"""Bounded parsers. Archive members are read in memory and never extracted."""

import io
import json
import posixpath
import resource
import subprocess
import sys
import threading
import unicodedata
import zipfile
from html.parser import HTMLParser
from pathlib import PurePosixPath

from defusedxml import ElementTree as XML
from pypdf import PdfReader

from app.core.config import settings
from app.core.errors import AppError

parse_slots = threading.BoundedSemaphore(1)


def invalid(message="Файл повреждён или его содержимое не соответствует формату"):
    return AppError(422, "invalid_book", message)


def tag(element):
    return element.tag.rsplit("}", 1)[-1]


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, name, attrs):
        if name in ("script", "style", "head"):
            self.hidden += 1
        if not self.hidden and name in ("p", "div", "br", "h1", "h2", "li"):
            self.parts.append("\n")

    def handle_endtag(self, name):
        if name in ("script", "style", "head") and self.hidden:
            self.hidden -= 1
        if not self.hidden and name in ("p", "div", "h1", "h2", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def archive(data):
    result = zipfile.ZipFile(io.BytesIO(data))
    members = result.infolist()
    names = [i.filename for i in members]
    if len(members) > settings.archive_max_entries or len(names) != len(set(names)):
        raise invalid("Слишком много файлов или повторяющиеся пути в архиве")
    total = 0
    for member in members:
        path = PurePosixPath(member.filename)
        total += member.file_size
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in member.filename
            or ":" in member.filename
            or member.flag_bits & 1
            or (member.external_attr >> 16) & 0o170000 == 0o120000
        ):
            raise invalid("Небезопасный архив")
        if total > settings.archive_max_bytes or member.file_size > max(
            1024 * 1024, member.compress_size * 200
        ):
            raise invalid("Архив превышает допустимый размер распаковки")
    return result


def parse_content(data: bytes, fmt: str) -> str:
    if len(data) > settings.upload_max_bytes:
        raise AppError(413, "file_too_large", "Файл превышает допустимый размер")
    if fmt == "txt":
        if (
            data.startswith((b"%PDF", b"PK"))
            or b"\0" in data[:1024]
            and not data.startswith((b"\xff\xfe", b"\xfe\xff"))
        ):
            raise invalid()
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16")
        else:
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = data.decode("cp1251")
        if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
            raise invalid()
    elif fmt == "pdf":
        if not data.startswith(b"%PDF-"):
            raise invalid()
        pdf = PdfReader(io.BytesIO(data), strict=True)
        if pdf.is_encrypted:
            raise invalid("Зашифрованные PDF не поддерживаются")
        if len(pdf.pages) > settings.pdf_max_pages:
            raise invalid("PDF превышает допустимое число страниц")
        text = "\n\n".join(page.extract_text() or "" for page in pdf.pages)
        if not text.strip():
            raise invalid(
                "В PDF нет текстового слоя. OCR в этой версии не поддерживается"
            )
    elif fmt == "fb2":
        root = XML.fromstring(
            data, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
        if tag(root) != "FictionBook":
            raise invalid()
        bodies = [e for e in root if tag(e) == "body"]
        text = "\n".join(
            "".join(e.itertext())
            for body in bodies
            for e in body.iter()
            if tag(e) in ("p", "subtitle", "v")
        )
    elif fmt in ("epub", "docx"):
        if not data.startswith(b"PK"):
            raise invalid()
        with archive(data) as book:
            if fmt == "docx":
                if "[Content_Types].xml" not in book.namelist():
                    raise invalid()
                root = XML.fromstring(book.read("word/document.xml"), forbid_dtd=True)
                if (
                    root.tag
                    != "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document"
                ):
                    raise invalid()
                text = "\n".join(
                    "".join(e.text or "" for e in p.iter() if tag(e) == "t")
                    for p in root.iter()
                    if tag(p) == "p"
                )
            else:
                if book.read("mimetype").strip() != b"application/epub+zip":
                    raise invalid()
                container = XML.fromstring(
                    book.read("META-INF/container.xml"), forbid_dtd=True
                )
                package = next(
                    e.attrib["full-path"]
                    for e in container.iter()
                    if tag(e) == "rootfile"
                )
                root = XML.fromstring(book.read(package), forbid_dtd=True)
                manifest = {
                    e.attrib["id"]: e.attrib["href"]
                    for e in root.iter()
                    if tag(e) == "item"
                    and e.attrib.get("media-type")
                    in ("application/xhtml+xml", "text/html")
                }
                chunks = []
                for entry in root.iter():
                    if tag(entry) != "itemref" or entry.attrib.get("linear") == "no":
                        continue
                    href = manifest[entry.attrib["idref"]]
                    if ":" in href or href.startswith("/"):
                        raise invalid()
                    path = posixpath.normpath(
                        posixpath.join(posixpath.dirname(package), href.split("#")[0])
                    )
                    if path.startswith("../"):
                        raise invalid()
                    content = book.read(path)
                    # Reject DTDs; discard markup, scripts and styles.
                    if (
                        b"<!DOCTYPE" in content.upper()
                        or b"<!ENTITY" in content.upper()
                    ):
                        raise invalid()
                    parser = PlainHTML()
                    parser.feed(content.decode("utf-8-sig"))
                    chunks.append("".join(parser.parts))
                text = "\n\n".join(chunks)
    else:
        raise invalid("Поддерживаются TXT, PDF, EPUB, DOCX и FB2")
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    if not text or len(text.encode("utf-8")) > settings.archive_max_bytes:
        raise invalid("Текст пуст или превышает допустимый размер")
    return text


def parse_book(data: bytes, fmt: str) -> str:
    if not parse_slots.acquire(blocking=False):
        raise AppError(503, "import_busy", "Импорт занят. Попробуйте немного позже")
    try:
        limits = json.dumps(
            {
                "bytes": settings.upload_max_bytes,
                "archive": settings.archive_max_bytes,
                "entries": settings.archive_max_entries,
                "pages": settings.pdf_max_pages,
                "memory": settings.parse_memory_mb,
                "timeout": settings.parse_timeout_seconds,
            }
        )
        result = subprocess.run(
            [sys.executable, "-m", "app.domains.library.parsers", fmt, limits],
            input=data,
            capture_output=True,
            timeout=settings.parse_timeout_seconds,
        )
        if result.returncode:
            raise invalid(
                "Обработка файла прервана: превышен лимит ресурсов или файл повреждён"
            )
        payload = json.loads(result.stdout)
        if "error" in payload:
            raise invalid(payload["error"])
        return payload["text"]
    except subprocess.TimeoutExpired:
        raise invalid("Обработка файла превысила допустимое время") from None
    finally:
        parse_slots.release()


if __name__ == "__main__":
    limits = json.loads(sys.argv[2])
    settings.upload_max_bytes = limits["bytes"]
    settings.archive_max_bytes = limits["archive"]
    settings.archive_max_entries = limits["entries"]
    settings.pdf_max_pages = limits["pages"]
    resource.setrlimit(resource.RLIMIT_AS, (limits["memory"] * 1024**2,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (limits["timeout"],) * 2)
    try:
        output = {
            "text": parse_content(
                sys.stdin.buffer.read(settings.upload_max_bytes + 1), sys.argv[1]
            )
        }
    except AppError as exc:
        output = {"error": exc.message}
    except Exception:
        output = {"error": "Файл повреждён или его содержимое не соответствует формату"}
    print(json.dumps(output, ensure_ascii=False))
