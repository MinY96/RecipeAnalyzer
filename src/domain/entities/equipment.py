"""장비 모듈"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FTPConnectionInfo:
    """FTP 접속 정보 클래스"""

    host: str
    username: str
    password: str
    port: int
    encoding: str
    timeout_seconds: int
    remote_directory: str


@dataclass(frozen=True, slots=True)
class Equipment:
    """장비 정보 클래스"""

    name: str
    fab: str
    area: str
    model: str
    ftp_info: FTPConnectionInfo
    enabled: bool = True
    img_save_mode: bool = False


def create_equipment(eq_info: dict[str, str]) -> Equipment:
    """장비 클래스 생성 메소드

    Args:
        eq_info (dict[str,str]): 장비 정보

    Returns:
        Equipment: 파싱된 장비 인스턴스
    """
    fdc_connection = FTPConnectionInfo(
        host=eq_info["connection"]["host"],
        username=eq_info["connection"]["username"],
        password=eq_info["connection"]["password"],
        port=eq_info["connection"]["port"],
        encoding=eq_info["connection"]["encoding"],
        timeout_seconds=eq_info["connection"]["timeout_seconds"],
        remote_directory=eq_info["connection"]["remote_directory"],
    )
    return Equipment(
        name=eq_info["equipment_name"],
        model=eq_info["equipment_model"],
        fab=eq_info["fab"],
        area=eq_info["area"],
        ftp_info=fdc_connection,
        enabled=bool(eq_info["enabled"]),
        img_save_mode=bool(eq_info["img_save_mode"]),
    )
