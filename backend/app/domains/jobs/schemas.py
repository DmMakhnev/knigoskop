from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.integrations.llm import validate_destination


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProviderInput(Input):
    name: str = Field(min_length=1, max_length=100)
    protocol: Literal["mock", "openai_chat"]
    base_url: str = Field(default="", max_length=2000)
    host: str = Field(default="", max_length=253)
    api_key: SecretStr | None = None
    credential_source: Literal["encrypted", "environment"] = "encrypted"

    @model_validator(mode="after")
    def validate_provider(self):
        if self.protocol == "mock":
            if self.base_url or self.host or self.api_key:
                raise ValueError("Mock must be offline")
        else:
            validate_destination(self.base_url, self.host)
            if self.credential_source == "environment":
                if self.api_key is not None:
                    raise ValueError("Environment credential must not be submitted")
                return self
            if (
                not self.api_key
                or not 1 <= len(self.api_key.get_secret_value()) <= 2000
            ):
                raise ValueError("Key required")
            if any(c in self.api_key.get_secret_value() for c in "\r\n"):
                raise ValueError("Invalid key")
        return self


class Capabilities(Input):
    supports_json_schema: Literal[True] = True
    max_context_tokens: int = Field(default=4096, ge=512, le=1000000)
    mock_behavior: Literal[
        "success",
        "timeout",
        "rate_limit",
        "unavailable",
        "authentication",
        "invalid_content",
    ] = "success"


class Pricing(Input):
    input_micros_per_million: int = Field(ge=0, le=1000000000000)
    output_micros_per_million: int = Field(ge=0, le=1000000000000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ModelInput(Input):
    provider_id: UUID
    model_identifier: str = Field(min_length=1, max_length=100)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    pricing: Pricing | None = None


class RoutePolicy(Input):
    attempts_per_model: int = Field(default=1, ge=1, le=2)
    backoff_seconds: int = Field(default=2, ge=1, le=30)
    max_output_tokens: int = Field(default=128, ge=16, le=16384)
    max_cost_micros: int | None = Field(default=None, ge=0, le=1000000000)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def validate_budget(self):
        if (self.max_cost_micros is None) != (self.currency is None):
            raise ValueError("Budget currency required")
        return self


class RouteInput(Input):
    task_type: Literal[
        "diagnostic",
        "structure",
        "summary_detailed",
        "summary_short",
        "summary_medium",
        "validation",
    ] = "diagnostic"
    primary_model_id: UUID
    fallback_model_id: UUID | None = None
    policy: RoutePolicy = Field(default_factory=RoutePolicy)


class JobInput(Input):
    idempotency_key: str = Field(
        min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    route_id: UUID | None = None


class EnabledInput(Input):
    enabled: bool


class CredentialInput(Input):
    api_key: SecretStr = Field(min_length=1, max_length=2000)


Metric = Literal[
    "daily_jobs",
    "daily_requests",
    "daily_input_chars",
    "daily_tokens",
    "active_jobs",
    "daily_tts_chars",
    "books",
    "storage_bytes",
]


class QuotaInput(Input):
    metric: Metric
    limit_value: int = Field(ge=0, le=2000000000)
    user_id: UUID | None = None
