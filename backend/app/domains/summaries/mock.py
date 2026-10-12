"""Deterministic extractive demonstration; does not simulate factual AI assurance."""

import json
import re


def generate(text):
    payload = json.loads(text.split("INPUT_JSON:\n", 1)[-1])
    operation = payload["operation"]
    config = payload.get("limits", {})
    if operation == "validate":
        return {"findings": [], "corrected_nodes": [], "corrected_text": ""}
    if operation == "structure":
        chunk = payload["chunk"]
        source = payload["source_text"][
            chunk["start_offset"] - chunk["context_start"] : chunk["end_offset"]
            - chunk["context_start"]
        ]
        return {
            "episodes": [
                {
                    "title": source.strip()[:120] or "Эпизод",
                    "start_offset": chunk["start_offset"],
                    "end_offset": chunk["end_offset"],
                    "quote": source[:160],
                }
            ]
        }
    if operation == "detailed":
        chunk = payload["chunk"]
        nodes = []
        for index, episode in enumerate(payload["episodes"]):
            source = payload["source_text"][
                episode["start_offset"] - chunk["context_start"] : episode["end_offset"]
                - chunk["context_start"]
            ]
            nodes.append(
                {
                    "episode_index": index,
                    "text": source.strip()[: config.get("episode_max_chars", 500)]
                    or "Пустой фрагмент.",
                    "text_version_id": chunk["text_version_id"],
                    "section_id": chunk["section_id"],
                    "start_offset": episode["start_offset"],
                    "end_offset": episode["end_offset"],
                    "quote": episode["quote"],
                }
            )
        return {"nodes": nodes}
    combined = " ".join(child["text"] for child in payload["children"])
    if operation == "short":
        sentences = [
            s.strip()[:500] for s in re.split(r"(?<=[.!?])\s+", combined) if s.strip()
        ][:2]
        if len(sentences) < 2:
            sentences = [
                combined[:500] or "Текст книги.",
                "Это демонстрационное извлечение текста, выполненное локальным mock.",
            ]
        sentences = [
            s if s.endswith((".", "!", "?", "…")) else s[:499] + "." for s in sentences
        ]
        return {"sentences": sentences, "source_ids": payload["source_ids"]}
    maximum = (
        config.get("medium_target_chars", 4000) * 2
        if payload.get("level") == "medium"
        else config.get("aggregate_max_chars", 1200)
    )
    # Retain a bounded contribution from every child.
    allowance = max(1, (maximum - len(payload["children"])) // len(payload["children"]))
    return {
        "text": " ".join(child["text"][:allowance] for child in payload["children"]),
        "source_ids": payload["source_ids"],
    }
