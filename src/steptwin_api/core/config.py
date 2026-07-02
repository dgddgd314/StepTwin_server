from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Steptwin API"
    app_env: Literal["local", "test", "staging", "production"] = "local"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"]
    )

    database_url: str | None = None
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle_seconds: int = 1800
    tmap_app_key: str | None = None
    tmap_base_url: str = "https://apis.openapi.sk.com"
    tmap_transit_path: str = "/transit/routes"
    tmap_timeout_seconds: float = 5
    tmap_use_live: bool = False
    tmap_lang: int = Field(default=0, ge=0, le=1)
    tmap_format: Literal["json", "xml"] = "json"
    tmap_count: int = Field(default=10, ge=1, le=10)
    tmap_search_dttm: str | None = None

    @field_validator("database_url", "tmap_app_key", "tmap_search_dttm", mode="before")
    @classmethod
    def normalize_optional_string(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("tmap_search_dttm")
    @classmethod
    def validate_tmap_search_dttm(cls, value: str | None) -> str | None:
        if value is None:
            return None

        if len(value) != 12 or not value.isdigit():
            raise ValueError("TMAP_SEARCH_DTTM must be yyyymmddhhmi")

        year = int(value[0:4])
        month = int(value[4:6])
        day = int(value[6:8])
        hour = int(value[8:10])
        minute = int(value[10:12])
        if not 1900 <= year <= 9999:
            raise ValueError("TMAP_SEARCH_DTTM year must be between 1900 and 9999")
        if not 1 <= month <= 12:
            raise ValueError("TMAP_SEARCH_DTTM month must be between 01 and 12")
        if not 1 <= day <= 31:
            raise ValueError("TMAP_SEARCH_DTTM day must be between 01 and 31")
        if not 0 <= hour <= 23:
            raise ValueError("TMAP_SEARCH_DTTM hour must be between 00 and 23")
        if not 0 <= minute <= 59:
            raise ValueError("TMAP_SEARCH_DTTM minute must be between 00 and 59")

        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
