"""Deterministic bounded DAG: chunks, checks, chapters and hierarchical levels."""

from app.core.config import settings
from app.core.errors import AppError

PIPELINE = "summaries-v3"


def parameters():
    return {
        key: getattr(settings, "summary_" + key)
        for key in (
            "chunk_chars",
            "overlap_chars",
            "max_chunks",
            "max_input_chars",
            "fan_in",
            "episode_max_chars",
            "aggregate_max_chars",
            "medium_target_chars",
            "max_steps",
        )
    }


def build_plan(sections, config):
    root = next(row for row in sections if row["kind"] == "root")
    if root["end_offset"] > config["max_input_chars"]:
        raise AppError(413, "summary_text_limit", "Книга превышает лимит AI-обработки")
    plan, chunks, selected = [], [], {}

    def add(task_type, kind, params, dependencies=()):
        index = len(plan)
        plan.append(
            {
                "order_index": index,
                "task_type": task_type,
                "parameters": {"kind": kind, **params},
                "dependencies": list(dependencies),
            }
        )
        if len(plan) > config["max_steps"]:
            raise AppError(413, "summary_step_limit", "Слишком много шагов обработки")
        return index

    def checked(task, kind, params, deps):
        generation = add(task, kind, params, deps)
        first = add(
            "validation",
            "validate",
            {**params, "candidate": generation, "round": 1},
            [generation],
        )
        second = add(
            "validation",
            "validate",
            {**params, "candidate": generation, "first": first, "round": 2},
            [generation, first],
        )
        return add(
            task,
            "select",
            {**params, "candidate": generation, "first": first, "second": second},
            [generation, first, second],
        )

    for section in sections:
        if section["kind"] != "fragment":
            continue
        start = section["start_offset"]
        while start < section["end_offset"]:
            end = min(start + config["chunk_chars"], section["end_offset"])
            chunk = {
                "section_id": section["id"],
                "parent_section_id": section["parent_id"],
                "text_version_id": section["text_version_id"],
                "start_offset": start,
                "end_offset": end,
                "context_start": max(
                    section["start_offset"], start - config["overlap_chars"]
                ),
                "context_end": min(
                    section["end_offset"], end + config["overlap_chars"]
                ),
            }
            structure = add("structure", "structure", {"chunk": chunk})
            chunk["structure_step"] = structure
            final = checked(
                "summary_detailed", "detailed", {"chunk": chunk}, [structure]
            )
            chunks.append((chunk, final))
            start = end
            if len(chunks) > config["max_chunks"]:
                raise AppError(
                    413,
                    "summary_chunk_limit",
                    "Книга требует слишком много чанков; измените лимиты",
                )

    def reduce(task_type, indices, section_id, level):
        current = list(indices)
        while True:
            groups = [
                current[i : i + config["fan_in"]]
                for i in range(0, len(current), config["fan_in"])
            ]
            current = [
                checked(
                    task_type,
                    "aggregate",
                    {
                        "section_id": section_id,
                        "level": level,
                        "source_ids": [f"step:{index}" for index in group],
                    },
                    group,
                )
                for group in groups
            ]
            if len(current) == 1:
                return current[0]

    non_fragments = [row for row in sections if row["kind"] != "fragment"]
    for section in reversed(non_fragments):
        contributions = [
            (chunk["start_offset"], index)
            for chunk, index in chunks
            if chunk["parent_section_id"] == section["id"]
        ]
        contributions += [
            (child["start_offset"], selected[child["id"]])
            for child in non_fragments
            if child["parent_id"] == section["id"]
        ]
        contributions.sort()
        if not contributions:
            raise AppError(
                422, "summary_structure_invalid", "Раздел не содержит текста"
            )
        selected[section["id"]] = reduce(
            "summary_detailed",
            [index for _, index in contributions],
            section["id"],
            "detailed",
        )
    short = checked(
        "summary_short",
        "short",
        {
            "section_id": root["id"],
            "level": "short",
            "source_ids": [f"step:{selected[root['id']]}"],
        },
        [selected[root["id"]]],
    )
    medium = reduce(
        "summary_medium", [index for _, index in chunks], root["id"], "medium"
    )
    return plan, {
        "chunks": chunks,
        "sections": selected,
        "short": short,
        "medium": medium,
        "root": root["id"],
    }
