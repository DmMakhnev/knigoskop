"""Deterministic rules-v1 structure. Offsets are Unicode code points, [start,end)."""

import hashlib
import re
from uuid import UUID, uuid5

from sqlalchemy import select

from app.core.config import settings
from app.core.errors import AppError
from app.db.models import TextSection, TextVersion

ALGORITHM = "rules-v1"
HEADINGS = re.compile(
    r"^(?P<title>(?P<kind>часть|книга|part|book|глава|chapter)\s+[^\n]{1,150})$",
    re.I | re.M,
)


def build_sections(version_id: str, text: str):
    if not text:
        raise AppError(422, "text_empty", "В книге нет текста")
    nodes = [
        {
            "kind": "root",
            "title": "Весь текст",
            "start_offset": 0,
            "end_offset": len(text),
            "parent": None,
        }
    ]
    headings = list(HEADINGS.finditer(text))
    if len(headings) > settings.structure_max_sections:
        raise AppError(413, "structure_limit", "Слишком много разделов в книге")
    stack = [0]
    segments = []
    previous = 0
    parent = 0
    for heading in headings:
        start = heading.start()
        if previous < start:
            segments.append((previous, start, parent))
        level = (
            1 if heading["kind"].casefold() in ("часть", "книга", "part", "book") else 2
        )
        while len(stack) > 1 and nodes[stack[-1]]["level"] >= level:
            nodes[stack.pop()]["end_offset"] = start
        nodes.append(
            {
                "kind": "part" if level == 1 else "chapter",
                "title": heading["title"],
                "start_offset": start,
                "end_offset": len(text),
                "parent": stack[-1],
                "level": level,
            }
        )
        stack.append(len(nodes) - 1)
        parent = stack[-1]
        previous = start
    if previous < len(text):
        segments.append((previous, len(text), parent))
    for start, end, parent in segments:
        while start < end:
            stop = min(start + settings.reader_page_chars, end)
            if stop < end:
                boundary = text.rfind(
                    "\n", start + settings.reader_page_chars // 2, stop
                )
                if boundary >= 0:
                    stop = boundary + 1
            nodes.append(
                {
                    "kind": "fragment",
                    "title": f"Фрагмент {start + 1}–{stop}",
                    "start_offset": start,
                    "end_offset": stop,
                    "parent": parent,
                }
            )
            start = stop
            if len(nodes) > settings.structure_max_sections:
                raise AppError(413, "structure_limit", "Слишком много разделов в книге")
    namespace = UUID(version_id)
    for node in nodes:
        node["id"] = str(
            uuid5(
                namespace,
                f"{ALGORITHM}:{node['kind']}:{node['start_offset']}:{node['end_offset']}",
            )
        )
    ordered = sorted(
        nodes,
        key=lambda node: (
            node["start_offset"],
            {"root": 0, "part": 1, "chapter": 2, "fragment": 3}[node["kind"]],
        ),
    )
    return [
        {
            "id": node["id"],
            "text_version_id": version_id,
            "parent_id": nodes[node["parent"]]["id"]
            if node["parent"] is not None
            else None,
            "kind": node["kind"],
            "title": node["title"],
            "order_index": index,
            "start_offset": node["start_offset"],
            "end_offset": node["end_offset"],
        }
        for index, node in enumerate(ordered)
    ]


def ensure_structure(db, version, text=None, storage=None):
    root = db.scalar(
        select(TextSection).where(
            TextSection.text_version_id == version.id, TextSection.kind == "root"
        )
    )
    if root:
        return root
    db.scalar(select(TextVersion).where(TextVersion.id == version.id).with_for_update())
    root = db.scalar(
        select(TextSection).where(
            TextSection.text_version_id == version.id, TextSection.kind == "root"
        )
    )
    if root:
        return root
    if text is None:
        data = storage.read(version.normalized_text_key)
        if hashlib.sha256(data).hexdigest() != version.checksum:
            raise AppError(
                503, "text_integrity", "Не удалось проверить целостность текста"
            )
        text = data.decode("utf-8")
    rows = build_sections(version.id, text)
    # Parent rows must exist before children for immediate FK checks.
    for row in rows:
        db.add(TextSection(**row))
        db.flush()
    return db.get(TextSection, rows[0]["id"])
