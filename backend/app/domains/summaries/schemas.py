from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.jobs.schemas import JobInput

TASKS = (
    "structure",
    "summary_detailed",
    "summary_short",
    "summary_medium",
    "validation",
)
TaskType = Literal[
    "structure", "summary_detailed", "summary_short", "summary_medium", "validation"
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Episode(Strict):
    title: str = Field(min_length=1, max_length=120)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=160)


class StructureOutput(Strict):
    episodes: list[Episode] = Field(min_length=1, max_length=8)


class DetailedNode(Strict):
    episode_index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=1000)
    text_version_id: str
    section_id: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=160)


class DetailedOutput(Strict):
    nodes: list[DetailedNode] = Field(min_length=1, max_length=8)


class AggregateOutput(Strict):
    text: str = Field(min_length=1, max_length=12000)
    source_ids: list[str] = Field(min_length=1, max_length=8)


class ShortOutput(Strict):
    sentences: list[str] = Field(min_length=2, max_length=10)
    source_ids: list[str] = Field(min_length=1, max_length=8)


class Finding(Strict):
    node_index: int = Field(ge=0)
    severity: Literal["warning", "error"]
    message: str = Field(min_length=1, max_length=500)


class ValidationOutput(Strict):
    findings: list[Finding] = Field(max_length=30)
    corrected_nodes: list[DetailedNode] = Field(max_length=8)
    corrected_text: str = Field(max_length=12000)


class ProcessInput(JobInput):
    chapter_id: UUID | None = None
    base_summary_id: UUID | None = None
    force: bool = False
    consent_external: bool = False


class PromptTemplateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_type: TaskType
    name: str = Field(min_length=1, max_length=100)


class PromptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=8000)
    variables_schema: dict


class RollbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: UUID


class PromptTestInput(JobInput):
    text: str = Field(min_length=1, max_length=3000)
    consent_external: bool = False
