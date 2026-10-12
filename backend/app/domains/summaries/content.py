"""Strict deterministic validation of provider JSON and same-version source spans."""

import re

from pydantic import ValidationError

from app.domains.summaries.schemas import (
    AggregateOutput,
    DetailedOutput,
    ShortOutput,
    StructureOutput,
    ValidationOutput,
)
from app.integrations.llm import ProviderFailure


def response_model(kind):
    return {
        "structure": StructureOutput,
        "detailed": DetailedOutput,
        "aggregate": AggregateOutput,
        "short": ShortOutput,
        "validate": ValidationOutput,
    }[kind]


def response_schema(kind, payload):
    schema = response_model(kind).model_json_schema()
    if (
        kind == "structure"
        and "core_range" in payload
        and payload["core_range"]["end_offset"] - payload["core_range"]["start_offset"]
        <= 300
    ):
        schema["properties"]["episodes"].update(minItems=1, maxItems=1)
        properties = schema["$defs"]["Episode"]["properties"]
        for field in ("start_offset", "end_offset", "quote"):
            properties[field]["enum"] = [payload["core_range"][field]]
    elif kind in ("detailed", "validate") and len(payload.get("episodes", [])) == 1:
        properties = schema["$defs"]["DetailedNode"]["properties"]
        properties["episode_index"]["enum"] = [0]
        for field in ("text_version_id", "section_id"):
            properties[field]["enum"] = [payload["chunk"][field]]
        for field in ("start_offset", "end_offset", "quote"):
            properties[field]["enum"] = [payload["episodes"][0][field]]
    return schema


def validate_content(kind, content, payload, config):
    reason = "schema"
    try:
        data = response_model(kind).model_validate(content).model_dump()
        if kind == "structure":
            core = payload["chunk"]
            position = core["start_offset"]
            for episode in data["episodes"]:
                reason = "range_partition"
                if (
                    episode["start_offset"] != position
                    or episode["end_offset"] <= position
                    or episode["end_offset"] > core["end_offset"]
                ):
                    raise ValueError()
                reason = "quote_prefix"
                verify_quote(episode, payload)
                position = episode["end_offset"]
            reason = "range_coverage"
            if position != core["end_offset"]:
                raise ValueError()
        elif kind == "detailed":
            reason = "node_contract"
            validate_nodes(data["nodes"], payload, config)
        elif kind in ("aggregate", "short"):
            reason = "source_ids"
            if sorted(data["source_ids"]) != sorted(payload["source_ids"]):
                raise ValueError()
            if kind == "short":
                reason = "sentence_count"
                if any(not 1 <= len(sentence) <= 500 for sentence in data["sentences"]):
                    raise ValueError()
                data["text"] = " ".join(data["sentences"])
                if not 2 <= sentence_count(data["text"]) <= 10:
                    raise ValueError()
            maximum = (
                config["medium_target_chars"] * 2
                if payload.get("level") == "medium"
                else config["aggregate_max_chars"]
            )
            reason = "text_limit"
            if len(data["text"]) > min(maximum, 12000):
                raise ValueError()
        else:
            reason = "validation_contract"
            candidate = payload["candidate"]
            detailed = "nodes" in candidate
            if any(
                finding["node_index"] >= (len(candidate["nodes"]) if detailed else 1)
                for finding in data["findings"]
            ):
                raise ValueError()
            if data["corrected_nodes"] and data["corrected_text"]:
                raise ValueError()
            if detailed:
                if data["corrected_text"]:
                    raise ValueError()
                if data["corrected_nodes"]:
                    validate_nodes(data["corrected_nodes"], payload, config)
            elif data["corrected_nodes"]:
                raise ValueError()
            elif data["corrected_text"]:
                maximum = (
                    config["medium_target_chars"] * 2
                    if payload.get("level") == "medium"
                    else config["aggregate_max_chars"]
                )
                if len(data["corrected_text"]) > min(maximum, 12000):
                    raise ValueError()
                if (
                    payload.get("level") == "short"
                    and not 2 <= sentence_count(data["corrected_text"]) <= 10
                ):
                    raise ValueError()
        return data
    except (ValidationError, ValueError, KeyError, TypeError, IndexError):
        raise ProviderFailure("summary_content_invalid", reason=reason) from None


def sentence_count(text):
    return len([part for part in re.split(r"(?<=[.!?…])\s+", text) if part.strip()])


def verify_quote(span, payload):
    chunk = payload["chunk"]
    start, end = span["start_offset"], span["end_offset"]
    if start < chunk["start_offset"] or end > chunk["end_offset"] or end <= start:
        raise ValueError()
    relative = start - chunk["context_start"]
    if not payload["source_text"][relative : relative + end - start].startswith(
        span["quote"]
    ):
        raise ValueError()


def validate_nodes(nodes, payload, config):
    episodes = payload["episodes"]
    chunk = payload["chunk"]
    if len(nodes) != len(episodes) or [node["episode_index"] for node in nodes] != list(
        range(len(episodes))
    ):
        raise ValueError()
    for node, episode in zip(nodes, episodes, strict=True):
        if (
            node["text_version_id"] != chunk["text_version_id"]
            or node["section_id"] != chunk["section_id"]
            or node["start_offset"] != episode["start_offset"]
            or node["end_offset"] != episode["end_offset"]
            or len(node["text"]) > config["episode_max_chars"]
        ):
            raise ValueError()
        verify_quote(node, payload)
