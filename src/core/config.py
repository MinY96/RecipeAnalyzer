"""Application configuration mock."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str
    app_name: str
    log_level: str
    data_base_dir: str


def load_settings(env_file: str = ".env") -> Settings:
    load_dotenv(env_file, override=False)
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        app_name=os.getenv("APP_NAME", "equipment-data-collector"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        data_base_dir=os.getenv("DATA_BASE_DIR", "data"),
    )
