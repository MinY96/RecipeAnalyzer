"""Centralized environment configuration for RecipeAnalyzer."""

from __future__ import annotations
import os
from pathlib import Path
from typing import Any
import yaml
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


def _env_int(key: str, default: int) -> int:
    """환경변수의 정수값을 읽고 설정값이 없으면 기본값을 반환한다.

    Args:
        key: 조회 키.
        default: 조건에 맞는 값이 없을 때 사용할 기본값.

    Returns:
        int: 처리 결과를 반환한다."""
    return int(os.getenv(key, str(default)))


POSTGRES_DATABASE = os.getenv("POSTGRES_DATABASE", "dm_rms")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_USER = os.getenv("POSTGRES_USERNAME", "")
POSTGRES_PORT = _env_int("POSTGRES_PORT", 5432)
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_CONNECT_TIMEOUT = _env_int("POSTGRES_CONNECT_TIMEOUT", 10)
POSTGRES_POOL_MIN_SIZE = _env_int("POSTGRES_POOL_MIN", 1)
POSTGRES_POOL_MAX_SIZE = _env_int("POSTGRES_POOL_MAX", 5)
POSTGRES_SCHEMA = os.getenv("POSTGRES_SCHEMA", "public")
ORACLE_CLIENT_DIR = os.getenv("ORACLE_CLIENT_DIR", "")
ORACLE_DATABASE = os.getenv("ORACLE_DATABASE", "")
ORACLE_HOST = os.getenv("ORACLE_HOST", "")
ORACLE_SERVICE_NAME = os.getenv("ORACLE_SERVICE_NAME", "")
ORACLE_USER = os.getenv("ORACLE_USERNAME", "")
ORACLE_PORT = _env_int("ORACLE_PORT", 1521)
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD", "")
ORACLE_CONNECT_TIMEOUT = _env_int("ORACLE_CONNECT_TIMEOUT", 10)
ORACLE_POOL_MIN_SIZE = _env_int("ORACLE_POOL_MIN", 1)
ORACLE_POOL_MAX_SIZE = _env_int("ORACLE_POOL_MAX", 4)
ORACLE_POOL_INCREMENT = _env_int("ORACLE_POOL_INCREMENT", 1)
ORACLE_DSN = f"{ORACLE_HOST}:{ORACLE_PORT}/{ORACLE_SERVICE_NAME}"
DATA_BASE_DIR = Path(os.getenv("DATA_BASE_DIR", r"D:\IQC_DATA"))
RECIPE_BATCH_SIZE = _env_int("RECIPE_BATCH_SIZE", 999)
PARSE_BATCH_SIZE = _env_int("PARSE_BATCH_SIZE", 200)


def load_equipment_config(yaml_path: Path) -> list[dict[str, Any]]:
    """장비 YAML을 읽고 각 FTP 호스트·계정 정보를 환경변수에서 주입한다.

    Args:
        yaml_path: 장비 설정 YAML 파일 경로.

    Returns:
        list[dict[str, Any]]: 처리 결과를 반환한다."""
    with yaml_path.open(encoding="utf-8") as file:
        equipments = yaml.safe_load(file).get("equipments", [])
    for equipment in equipments:
        connection = equipment["connection"]
        for prop in ("host", "username", "password"):
            env_key = connection.get(f"{prop}_env")
            connection[prop] = (
                os.getenv(env_key, "") if env_key else connection.get(prop, "")
            )
    return equipments
