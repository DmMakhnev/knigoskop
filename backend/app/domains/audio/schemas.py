from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.domains.jobs.schemas import JobInput
from app.integrations.llm import validate_destination


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    protocol: Literal["mock", "genvoice", "yandex_speechkit"]
    base_url: str = Field(default="", max_length=2000)
    host: str = Field(default="", max_length=253)
    model: str = Field(default="provider-default", min_length=1, max_length=100)
    voices: list[str] = Field(min_length=1, max_length=30)
    api_key: SecretStr | None = None
    price_micros_per_1000_chars: int | None = Field(default=None, ge=0, le=1000000000)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def valid(self):
        if len(set(self.voices)) != len(self.voices) or any(
            not 1 <= len(v) <= 100 for v in self.voices
        ):
            raise ValueError("Invalid voices")
        if self.protocol == "mock":
            if self.base_url or self.host or self.api_key:
                raise ValueError("Mock must be offline")
        else:
            validate_destination(self.base_url, self.host)
            if self.protocol == "genvoice":
                for voice in self.voices:
                    UUID(voice)
            if self.api_key and (
                not 1 <= len(self.api_key.get_secret_value()) <= 2000
                or any(c in self.api_key.get_secret_value() for c in "\r\n")
            ):
                raise ValueError("Invalid key")
        if (self.currency is None) != (self.price_micros_per_1000_chars is None):
            raise ValueError("Price and currency must be supplied together")
        return self


class GenerateInput(JobInput):
    library_item_id: UUID
    summary_id: UUID
    node_id: UUID | None = None
    profile_id: UUID
    voice: str = Field(min_length=1, max_length=100)
    consent_external: bool = False
    max_cost_micros: int | None = Field(default=None, ge=0, le=1000000000)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class ProgressInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: UUID
    position_ms: int = Field(ge=0)
    playback_rate: float = Field(ge=0.75, le=2)
    revision: int = Field(ge=0)
