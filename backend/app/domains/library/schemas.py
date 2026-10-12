from pydantic import BaseModel, ConfigDict, Field, field_validator


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    author: str = Field(default="Неизвестный автор", min_length=1, max_length=300)
    language: str = Field(default="ru", min_length=2, max_length=32)
    edition_label: str = Field(
        default="Пользовательское издание", min_length=1, max_length=300
    )
    translator: str = Field(default="", max_length=300)

    @field_validator(
        "title", "author", "language", "edition_label", "translator", mode="before"
    )
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class FolderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class ItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text_version_id: str = Field(min_length=36, max_length=36)


class ItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    favorite: bool | None = None
    folder_id: str | None = Field(default=None, min_length=36, max_length=36)
