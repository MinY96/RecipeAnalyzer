"""Immutable equipment and FTP configuration."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FTPConnectionInfo:
    host: str
    username: str
    password: str
    port: int
    encoding: str
    timeout_seconds: int
    remote_directory: str


@dataclass(frozen=True, slots=True)
class Equipment:
    name: str
    fab: str
    area: str
    model: str
    ftp_info: FTPConnectionInfo
    enabled: bool = True
    img_save_mode: bool = False


def create_equipment(eq_info: dict[str, Any]) -> Equipment:
    """장비 설정 딕셔너리로부터 FTP 접속 정보와 Equipment 객체를 생성한다.

    Args:
        eq_info: 처리에 필요한 입력값.

    Returns:
        Equipment: 처리 결과를 반환한다."""
    connection = eq_info["connection"]
    return Equipment(
        name=eq_info["equipment_name"],
        model=eq_info["equipment_model"],
        fab=eq_info["fab"],
        area=eq_info["area"],
        ftp_info=FTPConnectionInfo(
            host=connection["host"],
            username=connection["username"],
            password=connection["password"],
            port=int(connection["port"]),
            encoding=connection["encoding"],
            timeout_seconds=int(connection["timeout_seconds"]),
            remote_directory=connection["remote_directory"],
        ),
        enabled=bool(eq_info.get("enabled", True)),
        img_save_mode=bool(eq_info.get("img_save_mode", False)),
    )
