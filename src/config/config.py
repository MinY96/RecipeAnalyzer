"""FAB1 API 서버 설정.

운영 환경별 값은 소스 수정 대신 ``DM_RMS_*`` 환경변수로 주입한다.
환경변수가 없으면 기존 FAB1 서버에서 사용하던 기본값을 사용한다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final, Any

import yaml

from dotenv import load_dotenv
load_dotenv(".env", override=False)

# PostgreSQL 연결 정보
POSTGRES_DATABASE: Final[str] = os.getenv("POSTGRES_DATABASE")
POSTGRES_HOST: Final[str] = os.getenv("POSTGRES_HOST")
POSTGRES_USER: Final[str] = os.getenv("POSTGRES_USERNAME")
POSTGRES_PORT: Final[int] = int(os.getenv("POSTGRES_PORT"))
POSTGRES_PASSWORD: Final[str] = os.getenv("POSTGRES_PASSWORD")
POSTGRES_CONNECT_TIMEOUT: Final[int] = int(
    os.getenv("POSTGRES_CONNECT_TIMEOUT")
)
POSTGRES_POOL_MIN_SIZE: Final[int] = int(os.getenv("POSTGRES_POOL_MIN"))
POSTGRES_POOL_MAX_SIZE: Final[int] = int(os.getenv("POSTGRES_POOL_MAX"))

# Oracle 연결 정보
ORACLE_CLIENT_DIR: Final[str] = os.getenv("ORACLE_CLIENT_DIR")
ORACLE_DATABASE: Final[str] = os.getenv("ORACLE_DATABASE")
ORACLE_HOST: Final[str] = os.getenv("ORACLE_HOST")
ORACLE_SERVICE_NAME: Final[str] = os.getenv("ORACLE_SERVICE_NAME")
ORACLE_USER: Final[str] = os.getenv("ORACLE_USERNAME")
ORACLE_PORT: Final[int] = int(os.getenv("ORACLE_PORT"))
ORACLE_PASSWORD: Final[str] = os.getenv("ORACLE_PASSWORD")
ORACLE_CONNECT_TIMEOUT: Final[int] = int(
    os.getenv("ORACLE_CONNECT_TIMEOUT")
)
ORACLE_POOL_MIN_SIZE: Final[int] = int(os.getenv("ORACLE_POOL_MIN"))
ORACLE_POOL_MAX_SIZE: Final[int] = int(os.getenv("ORACLE_POOL_MAX"))
ORACLE_POOL_INCREMENT: Final[int] = int(os.getenv("ORACLE_POOL_INCREMENT"))
ORACLE_DSN: Final[str] = f"{ORACLE_HOST}:{ORACLE_PORT}/{ORACLE_SERVICE_NAME}"

# 파일 백업 경로
DATA_BASE_DIR: Final[Path] = Path(
    os.getenv("DATA_BASE_DIR", r"D:\IQC_DATA")
)

# 장비 정보
def load_equipment_config(yaml_path: Path) -> list[dict[str, Any]]:
    """환경변수와 장비 yaml을 읽어 정보를 반환

    Args:
        yaml_path (Path): 장비 yaml 경로
        env_path (Path): 환경변수 경로

    Returns:
        list[dict[str, Any]]: 장비 별 ftp 접속 정보0
    """
    with yaml_path.open(mode="r", encoding="utf-8",) as file:
        config = yaml.safe_load(file)
        equipments = config.get("equipments", [])
        for equipment in equipments:
            connection = equipment["connection"]
            host_env = connection.get("host_env")
            username_env = connection.get("username_env")
            password_env = connection.get("password_env")
            connection["host"] = os.getenv(host_env)
            connection["username"] = os.getenv(username_env)
            connection["password"] = os.getenv(password_env)
    return equipments
