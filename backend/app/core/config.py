from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env", extra="ignore"
    )
    app_env: Literal["development", "test", "production"] = "development"
    database_url: SecretStr | None = None
    postgres_user: str = "knigoskop"
    postgres_db: str = "knigoskop"
    postgres_password: SecretStr = SecretStr("")
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    redis_url: str = "redis://localhost:6379/0"
    public_base_url: str = "http://localhost:8080"
    allowed_origins: list[str] = [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    session_cookie_secure: bool = True
    session_ttl_seconds: int = Field(default=86400, ge=60)
    email_token_ttl_seconds: int = Field(default=3600, ge=60)
    csrf_ttl_seconds: int = Field(default=3600, ge=60)
    auth_rate_window_seconds: int = Field(default=900, ge=1)
    auth_ip_rate_limit: int = Field(default=100, ge=1)
    login_rate_limit: int = Field(default=10, ge=1)
    register_rate_limit: int = Field(default=10, ge=1)
    email_rate_limit: int = Field(default=5, ge=1)
    token_rate_limit: int = Field(default=20, ge=1)
    password_hash_concurrency: int = Field(default=2, ge=1, le=4)
    password_min_length: int = Field(default=12, ge=12, le=128)
    storage_directory: Path = Path(".local/files")
    upload_max_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)
    archive_max_bytes: int = Field(default=40 * 1024 * 1024, ge=1024)
    archive_max_entries: int = Field(default=2000, ge=1)
    pdf_max_pages: int = Field(default=1000, ge=1)
    parse_timeout_seconds: int = Field(default=15, ge=1, le=60)
    parse_memory_mb: int = Field(default=192, ge=64, le=512)
    source_timeout_seconds: int = Field(default=5, ge=1, le=15)
    source_catalog_max_bytes: int = Field(default=1024 * 1024, ge=1024)
    source_catalog_max_entries: int = Field(default=1000, ge=1, le=10000)
    source_max_enabled: int = Field(default=3, ge=1, le=10)
    reader_page_chars: int = Field(default=4000, ge=500, le=16000)
    structure_max_sections: int = Field(default=20000, ge=10, le=50000)
    bookmark_max_per_item: int = Field(default=100, ge=1, le=1000)
    summary_chunk_chars: int = Field(default=2000, ge=500, le=10000)
    summary_overlap_chars: int = Field(default=120, ge=0, le=400)
    summary_max_chunks: int = Field(default=200, ge=1, le=1000)
    summary_max_input_chars: int = Field(default=1000000, ge=1000, le=5000000)
    summary_fan_in: int = Field(default=2, ge=2, le=8)
    summary_episode_max_chars: int = Field(default=500, ge=100, le=1000)
    summary_aggregate_max_chars: int = Field(default=1200, ge=500, le=4000)
    summary_medium_target_chars: int = Field(default=4000, ge=1000, le=12000)
    summary_max_steps: int = Field(default=3000, ge=10, le=20000)
    ai_master_key: SecretStr | None = None
    knigoskop_llm_api_key: SecretStr | None = None
    ai_env_provider_host: str = ""
    ai_allowed_hosts: list[str] = []
    ai_https_proxy: SecretStr | None = None
    ai_ca_bundle: Path | None = None
    ai_timeout_seconds: int = Field(default=10, ge=1, le=120)
    ai_response_max_bytes: int = Field(default=65536, ge=1024, le=1048576)
    knigoskop_tts_api_key: SecretStr | None = None
    knigoskop_yandex_tts_api_key: SecretStr | None = None
    yandex_tts_env_provider_host: str = ""
    tts_base_url: str = ""
    tts_env_provider_host: str = ""
    audio_segment_chars: int = Field(default=1000, ge=100, le=3000)
    audio_max_text_chars: int = Field(default=20000, ge=100, le=100000)
    audio_max_bytes: int = Field(default=16 * 1024 * 1024, ge=1024, le=32 * 1024 * 1024)
    quota_daily_tts_chars: int = Field(default=10000, ge=0)
    job_recovery_seconds: int = Field(default=10, ge=2, le=60)
    job_lease_seconds: int = Field(default=120, ge=90, le=600)
    quota_daily_jobs: int = Field(default=20, ge=0)
    quota_daily_requests: int = Field(default=60, ge=0)
    quota_daily_input_chars: int = Field(default=20000, ge=0)
    quota_daily_tokens: int = Field(default=50000, ge=0)
    quota_active_jobs: int = Field(default=2, ge=0)
    mail_directory: Path = Path(".local/mail")

    @model_validator(mode="after")
    def validate_development_backend(self):
        if self.app_env == "production":
            raise ValueError(
                "File email backend is development-only; configure production "
                "email delivery before public deployment."
            )
        return self

    @property
    def effective_job_lease_seconds(self) -> int:
        return max(self.job_lease_seconds, max(60, self.ai_timeout_seconds + 30) + 30)

    def db_url(self) -> str | URL:
        if self.database_url:
            return self.database_url.get_secret_value()
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


settings = Settings()
