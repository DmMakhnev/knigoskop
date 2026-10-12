from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmailInput(InputModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.casefold()


class Credentials(EmailInput):
    password: str = Field(min_length=1, max_length=128)


class TokenInput(InputModel):
    token: str = Field(min_length=32, max_length=256)


class ResetInput(TokenInput):
    password: str = Field(min_length=1, max_length=128)


class DeleteInput(InputModel):
    password: str = Field(min_length=1, max_length=128)
    confirmation: Literal["DELETE"]


class StatusInput(InputModel):
    status: Literal["active", "blocked"]


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    role: Literal["user", "admin"]
    status: Literal["active", "blocked"]
    verified_at: datetime | None


class MessageOutput(BaseModel):
    message: str


class UsersOutput(BaseModel):
    items: list[UserOutput]
    total: int
