"""Runtime configuration. Values come from the environment, never from clients."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE) if ENV_FILE.exists() else None,
        extra="ignore",
    )

    database_url: str = Field(min_length=1)
    device_api_token: str = Field(min_length=1)
    dashboard_api_token: str = Field(min_length=1)
    cors_origins: str = "http://localhost:5173"
    port: int = 8001

    @model_validator(mode="after")
    def tokens_are_distinct(self) -> "Settings":
        if self.device_api_token == self.dashboard_api_token:
            raise ValueError("DEVICE_API_TOKEN and DASHBOARD_API_TOKEN must be different")
        return self

    @property
    def cors_origin_list(self) -> List[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
