from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")
    app_name: str = "Книгоскоп"
    app_env: str = "development"
    cors_origins: list[str] = ["http://localhost:5173"]
    redis_url: str = "redis://localhost:6379/0"
