"""DM Recipe FTP equipment scanner with bounded 999-row UPSERT batches."""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from ftplib import FTP, error_temp
from socket import timeout as socket_timeout
from uuid import uuid4
import time
from pathlib import Path
from typing import Any
import logging
import os
import re
import pandas as pd
from dateutil.relativedelta import relativedelta
from src.config.config import RECIPE_BATCH_SIZE
from src.domain.entities.equipment import Equipment, create_equipment
from src.collectors.ftp.client import (
    FTPEntry,
    FTPDirectoryError,
    FTPRecipeUnavailable,
    FTPRequiredFileMissing,
    FTPRecursiveListingError,
    list_entries,
    safe_download_recipe,
)
from src.databases import OracleCRUD, PostgresCRUD

log = logging.getLogger(__name__)
UPSERT_BATCH_SIZE = RECIPE_BATCH_SIZE
COLS = [
    "recipe_key",
    "model",
    "equip",
    "device",
    "recipe",
    "folder_path",
    "file_path",
    "dt_edit_time",
    "backup_folder_path",
    "backup_file_path",
    "dt_backup",
    "status",
    "tech_prod",
    "tech",
    "prod_group",
    "level",
    "layer",
    "is_remove",
    "parse_status",
    "parse_error",
]


def parse_edit_time(line: str) -> str:
    """FTP LIST 수정시각을 YYYY-MM-DD HH:MM 문자열로 변환한다.

    Args:
        line: FTP 또는 Recipe 텍스트 한 행.

    Returns:
        str: 처리 결과를 반환한다."""
    now = datetime.now()
    # Unix FTP: 'Oct 26 14:32' or 'Oct  6 2025' ; no year => current year.
    match = re.search(r"\b([A-Za-z]{3})\s+(\d{1,2})\s+(\d{2}:\d{2}|\d{4})\b", line)
    if match:
        month, day, last = match.groups()
        if ":" in last:
            value = datetime.strptime(
                f"{now.year} {month} {day} {last}", "%Y %b %d %H:%M"
            )
        else:
            value = datetime.strptime(f"{month} {day} {last}", "%b %d %Y")
    else:
        # Localized Korean LIST variants: 8월 30일 2012년 / 8월 30일 14:32
        localized = re.search(r"(\d{1,2})월\s*(\d{1,2})일\s*(\d{4})년?", line)
        local_clock = re.search(r"(\d{1,2})월\s*(\d{1,2})일\s*(\d{1,2}:\d{2})", line)
        if localized:
            m, d, y = localized.groups()
            value = datetime(int(y), int(m), int(d))
        elif local_clock:
            m, d, clock = local_clock.groups()
            hour, minute = map(int, clock.split(":"))
            value = datetime(now.year, int(m), int(d), hour, minute)
        else:
            # Unknown listing format means we cannot reliably compute changes/deletions.
            raise ValueError(f"Unable to parse FTP date: {line!r}")
    if value > now:
        value -= relativedelta(years=1)
    return value.strftime("%Y-%m-%d %H:%M")


def recipe_filter(name: str) -> bool:
    """KLA2139 Recipe 폴더 이름이 지정한 명명 규칙에 맞는지 확인한다.

    Args:
        name: 대상 이름.

    Returns:
        bool: 처리 결과를 반환한다."""
    if name.startswith((".", "_")) or name.endswith(("~", ".inf", ".DAT")):
        return False
    parts = name.split("_")
    return len(parts) == 2 and len(parts[0]) == 4


def is_recipe(entry: FTPEntry) -> bool:
    """FTP 항목이 Recipe 이름 규칙에 맞는 디렉터리인지 확인한다.

    Args:
        entry: FTP 파일/폴더 항목.

    Returns:
        bool: 처리 결과를 반환한다."""
    return entry.is_dir and recipe_filter(entry.name)


def is_recipe_kla2365(entry: FTPEntry, device: str, layer: str) -> bool:
    """KLA2365/PUMA Device·Layer에 맞는 Recipe 폴더만 선택한다.

    Args:
        entry: FTP 파일/폴더 항목.
        device: Device 이름.
        layer: Layer 이름.

    Returns:
        bool: 처리 결과를 반환한다."""
    if not is_recipe(entry):
        return False
    a, b = entry.name.split("_")
    return a == device and b.endswith(layer)


def is_device(entry: FTPEntry) -> bool:
    """디렉터리가 4글자 Device 이름 규칙에 부합하는지 확인한다.

    Args:
        entry: FTP 파일/폴더 항목.

    Returns:
        bool: 처리 결과를 반환한다."""
    return (
        entry.is_dir and len(entry.name) == 4 and not entry.name.startswith((".", "_"))
    )


def is_layer(entry: FTPEntry) -> bool:
    """디렉터리가 Layer 이름 규칙에 부합하는지 확인한다.

    Args:
        entry: FTP 파일/폴더 항목.

    Returns:
        bool: 처리 결과를 반환한다."""
    return is_device(entry)


def build_equipment_lookup(equip_db: pd.DataFrame) -> dict[str, dict]:
    """기존 Recipe DataFrame을 키 조회가 빠른 딕셔너리로 변환한다.

    Args:
        equip_db: 처리에 필요한 입력값.

    Returns:
        dict[str, dict]: 처리 결과를 반환한다."""
    if equip_db.empty:
        return {}
    return equip_db.set_index("recipe_key").to_dict(orient="index")


def is_file_update(
    equip_lookup: dict[str, dict], rcp_key: str, edit_time: str, backup_file_path: Path
) -> tuple[bool, str | None]:
    """FTP 수정 시각과 기존 백업 상태를 비교해 신규·수정 여부를 판정한다.

    Args:
        equip_lookup: 기존 Recipe 정보를 키로 조회하는 딕셔너리.
        rcp_key: Recipe 기본키.
        edit_time: FTP Recipe 최종 수정시각 문자열.
        backup_file_path: 로컬 필수 파일 백업 경로.

    Returns:
        tuple[bool, str | None]: 처리 결과를 반환한다."""
    info = equip_lookup.get(rcp_key)
    if info is None:
        return True, "new"
    if not backup_file_path.is_file():
        return True, "update"
    return (True, "update") if edit_time > info["dt_edit_time"] else (False, None)


def join_mes_info(upserts: list[dict[str, Any]]) -> pd.DataFrame:
    """Oracle MES 제품 정보를 Recipe 수집 데이터에 바인드 쿼리로 결합한다.

    Args:
        upserts: UPSERT 대상 Recipe 기록.

    Returns:
        pd.DataFrame: 처리 결과를 반환한다."""
    if not upserts:
        return pd.DataFrame(columns=COLS)
    frame = pd.DataFrame.from_records(upserts)
    devices = list(dict.fromkeys(frame["device"].tolist()))
    matches: dict[str, set[tuple]] = {device: set() for device in devices}
    for offset in range(0, len(devices), 900):
        chunk = devices[offset : offset + 900]
        binds = {f"d{i}": device for i, device in enumerate(chunk)}
        holders = ",".join(f":d{i}" for i in range(len(chunk)))
        query = f"""SELECT DISTINCT MAT_ID product, MAT_CMF_21 device,
                    MAT_CMF_5 device_name, MAT_CMF_6 tech_prod,
                    MAT_CMF_7 tech, MAT_CMF_23 prod_group
                    FROM IDBMGR.MWIPMATDEF WHERE FACTORY='AFB1'
                      AND MAT_CMF_21 IN ({holders})"""
        mes = OracleCRUD.select_query(query, binds)
        for row in mes.itertuples(index=False):
            product = getattr(row, "product", None)
            device = str(getattr(row, "device", ""))
            device_name = getattr(row, "device_name", None)
            if device not in matches:
                continue
            if device_name not in (None, "", " ") and (
                not product or device not in str(product)
            ):
                continue
            matches[device].add(
                (
                    getattr(row, "tech_prod", None),
                    getattr(row, "tech", None),
                    getattr(row, "prod_group", None),
                )
            )
    # Do not arbitrary-drop conflicting master metadata. Keep null & log conflict.
    for name, index in (("tech_prod", 0), ("tech", 1), ("prod_group", 2)):
        frame[name] = frame["device"].map(
            lambda dev: (
                next(iter(matches[dev]))[index] if len(matches[dev]) == 1 else None
            )
        )
    for device, variants in matches.items():
        if len(variants) > 1:
            log.warning(
                "MES metadata is ambiguous for device=%s (%d values)",
                device,
                len(variants),
            )
    return frame[COLS]


@dataclass
class CollectionState:
    equip: Equipment
    batch_size: int = UPSERT_BATCH_SIZE
    upserts: list[dict[str, Any]] = field(default_factory=list)
    seen_keys: set[str] = field(default_factory=set)
    unchanged_keys: set[str] = field(default_factory=set)
    new_cnt: int = 0
    update_cnt: int = 0
    download_failures: int = 0
    consecutive_download_failures: int = 0
    skipped_missing_file: int = 0
    skipped_missing_path: int = 0
    skipped_recursive_listing: int = 0
    skipped_optional_files: int = 0
    reconnect_count: int = 0
    completed_keys: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        """수집 UPSERT 배치 크기가 1~999 범위인지 검증한다.

        Args:
            self: 현재 클래스 인스턴스.

        Returns:
            None: 처리 결과를 반환한다."""
        if not 1 <= self.batch_size <= 999:
            raise ValueError("batch_size must be within [1, 999]")

    def append(self, recipe: dict[str, Any]) -> None:
        """수집된 신규·수정 Recipe를 버퍼에 넣고 임계치 도달 시 저장한다.

        Args:
            self: 현재 클래스 인스턴스.
            recipe: Recipe 관련 컬럼을 담은 DB 저장용 딕셔너리.

        Returns:
            None: 처리 결과를 반환한다."""
        self.upserts.append(recipe)
        if len(self.upserts) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        """수집 버퍼를 MES와 결합해 PostgreSQL에 저장하고 성공 시만 초기화한다.

        Args:
            self: 현재 클래스 인스턴스.

        Returns:
            None: 처리 결과를 반환한다."""
        if not self.upserts:
            return
        enriched = join_mes_info(self.upserts)
        update_database(self.equip, enriched, [], [], finalize=False)
        self.new_cnt += sum(x["status"] == "new" for x in self.upserts)
        self.update_cnt += sum(x["status"] == "update" for x in self.upserts)
        log.info("[%s] flushed %s rows", self.equip.name, len(self.upserts))
        self.upserts.clear()


def _is_connection_failure(exc: BaseException) -> bool:
    """FTP 타임아웃·소켓 단절을 식별해 사용 불가능한 연결을 재시도 대상으로 분류한다.

    Args:
        exc: FTP 명령 또는 폴더 복귀 과정의 원인 예외.

    Returns:
        bool: FTP 재연결이 필요한 통신 오류이면 True.
    """
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(
            current,
            (
                TimeoutError,
                socket_timeout,
                ConnectionError,
                EOFError,
                BrokenPipeError,
                ConnectionResetError,
            ),
        ):
            return True
        if isinstance(current, OSError) and any(
            marker in str(current).casefold()
            for marker in (
                "timed out",
                "timeout",
                "10060",
                "10054",
                "cannot read from timed out",
            )
        ):
            return True
        if isinstance(current, error_temp) and str(current).startswith("421"):
            return True
        current = current.__cause__ or current.__context__
    return False


def _ftp_reconnect(equip: Equipment, connector, state: CollectionState) -> FTP:
    """손상된 FTP 세션을 버리고 간격을 두어 지정 횟수만큼 재연결한다.

    Args:
        equip: 대상 장비의 FTP 접속 설정.
        connector: 로그인 후 Recipe 탐색 시작 경로로 이동하는 연결 함수.
        state: 연결 재시도 횟수를 기록할 수집 상태.

    Returns:
        FTP: 새로 연결되어 정상 위치에 있는 FTP 세션.

    Raises:
        RuntimeError: 제한된 재연결 시도가 모두 실패했을 때.
    """
    limit = max(1, int(os.getenv("FTP_RECONNECT_ATTEMPTS", "3")))
    delay = max(0.0, float(os.getenv("FTP_RECONNECT_DELAY_SECONDS", "2")))
    last_error = None
    for attempt in range(1, limit + 1):
        try:
            result = connector(equip)
            state.reconnect_count += 1
            log.warning("[%s] FTP 재연결 성공 (%d/%d)", equip.name, attempt, limit)
            return result
        except Exception as exc:
            last_error = exc
            log.warning(
                "[%s] FTP 재연결 실패 (%d/%d): %s", equip.name, attempt, limit, exc
            )
            if attempt < limit:
                time.sleep(delay)
    raise RuntimeError(
        f"[{equip.name}] FTP 재연결 {limit}회 모두 실패; 장비 수집 미완료"
    ) from last_error


def _safe_close(ftp: FTP) -> None:
    """FTP 제어 연결이 타임아웃 난 경우에도 원래 오류를 가리지 않고 소켓을 닫는다.

    Args:
        ftp: 종료할 FTP 세션.
    """
    try:
        ftp.close()
    except Exception:
        pass


def _log_recipe_skip(
    equip: Equipment, recipe_name: str, reason: str, count: int, exc: Exception
) -> None:
    """수집 제외 로그를 처음 10건과 이후 500건마다 기록한다.

    Args:
        equip: 수집 장비 정보.
        recipe_name: 제외된 Recipe 폴더 이름.
        reason: 제외 사유(필수 파일 없음 또는 경로 없음).
        count: 해당 사유로 제외된 누적 Recipe 건수.
        exc: FTP 파일·경로 확인 과정에서 발생한 원인 예외.

    Notes:
        같은 유형의 제외가 수천 건 발생해도 로그 파일 과다 생성을 막는다.
        전체 건수는 장비 처리 종료 시 별도로 출력한다.
    """
    if count <= 10 or count % 500 == 0:
        log.warning(
            "[%s] Recipe 제외(%s) %d건째: %s - %s",
            equip.name,
            reason,
            count,
            recipe_name,
            exc,
        )


def _recipe_info(
    equip: Equipment,
    entry: FTPEntry,
    device: str,
    layer: str,
    path_parts: list[str],
    required: str,
    file_status: str,
) -> dict:
    """FTP Recipe 정보와 로컬 백업 경로를 DB 저장용 딕셔너리로 구성한다.

    Args:
        equip: 수집·파싱 대상 장비 정보.
        entry: FTP 파일/폴더 항목.
        device: Device 이름.
        layer: Layer 이름.
        path_parts: 처리에 필요한 입력값.
        required: 필수 Recipe 파일 이름.
        file_status: 처리에 필요한 입력값.

    Returns:
        dict: 처리 결과를 반환한다."""
    recipe = entry.name
    remote = "/".join(
        [equip.ftp_info.remote_directory.rstrip("/"), *path_parts, recipe]
    )
    return {
        "recipe_key": f"{equip.name}-{recipe}",
        "model": equip.model,
        "equip": equip.name,
        "device": device,
        "layer": layer,
        "level": layer[:4],
        "recipe": recipe,
        "folder_path": remote,
        "file_path": f"{remote}/{required}",
        "dt_edit_time": parse_edit_time(entry.raw),
        "backup_folder_path": f"/{equip.name}/{recipe}",
        "backup_file_path": f"/{equip.name}/{recipe}/{required}",
        "dt_backup": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": file_status,
        "is_remove": False,
        "parse_status": "pending",
        "parse_error": None,
    }


def _scan_recipe(
    ftp: FTP,
    entry: FTPEntry,
    equip: Equipment,
    equip_dir: Path,
    lookup: dict,
    state: CollectionState,
    required: str,
    device: str,
    layer: str,
    path_parts: list[str],
    expected_parent: str,
    *,
    is_retry: bool = False,
) -> None:
    """Recipe의 갱신 필요성을 판단하고 다운로드·제외·실패를 분류한다.

    Args:
        ftp: 연결된 FTP 클라이언트.
        entry: FTP 파일/폴더 항목.
        equip: 수집·파싱 대상 장비 정보.
        equip_dir: 장비별 로컬 백업 폴더.
        lookup: 기존 Recipe를 recipe_key로 조회하는 딕셔너리.
        state: 장비 수집 상태 및 저장 버퍼.
        required: 필수 Recipe 파일 이름.
        device: Device 이름.
        layer: Layer 이름.
        path_parts: 처리에 필요한 입력값.
        expected_parent: 예상되는 FTP 부모 디렉터리.
        is_retry: FTP 재연결 후 재시도 여부.

    Returns:
        None: 처리 결과를 반환한다."""
    key = f"{equip.name}-{entry.name}"
    if key in state.completed_keys:
        if is_retry:
            return  # 재연결 후 같은 Device를 재탐색할 때 완료 건 중복 저장 방지
        raise RuntimeError(f"Duplicate Recipe key in FTP traversal: {key}")
    if key in state.seen_keys and not is_retry:
        raise RuntimeError(f"Duplicate Recipe key in FTP traversal: {key}")
    state.seen_keys.add(key)
    dest = equip_dir / entry.name
    edit_time = parse_edit_time(entry.raw)
    needs_update, status = is_file_update(lookup, key, edit_time, dest / required)
    if not needs_update:
        state.unchanged_keys.add(key)
        state.completed_keys.add(key)
        return
    try:
        skipped_optional = safe_download_recipe(
            ftp, entry.name, dest, required, expected_parent=expected_parent
        )
    except FTPRequiredFileMissing as exc:
        # 실제 파일이 없는 폴더는 별도 '수집 제외'로 기록한다.
        # 기존 DB/백업은 유지하며 다운로드 실패 건수에는 포함하지 않는다.
        state.skipped_missing_file += 1
        state.consecutive_download_failures = 0
        state.completed_keys.add(key)
        _log_recipe_skip(
            equip, entry.name, "필수 파일 없음", state.skipped_missing_file, exc
        )
        return
    except (FTPRecipeUnavailable, FTPDirectoryError, FTPRecursiveListingError):
        # 비정상 재귀 폴더나 FTP 경로 오류는 새 연결 후 1회 재시도한다.
        # 일시적 오류와 달리 미완성 백업은 정상 UPSERT하지 않는다.
        raise
    except Exception as exc:
        if _is_connection_failure(exc):
            raise  # 타임아웃 난 FTP 제어 연결을 재사용하지 말고 재연결하도록 전파
        state.download_failures += 1
        state.consecutive_download_failures += 1
        log.exception(
            "[%s] Recipe download failed: %s (%d consecutive failures)",
            equip.name,
            entry.name,
            state.consecutive_download_failures,
        )
        if state.consecutive_download_failures >= 5:
            raise RuntimeError(
                f"[{equip.name}] 5 consecutive Recipe downloads failed; "
                "aborting collection to protect integrity. Inspect the first failure."
            )
        return
    state.consecutive_download_failures = 0
    state.append(
        _recipe_info(equip, entry, device, layer, path_parts, required, status)
    )
    state.skipped_optional_files += skipped_optional or 0
    state.completed_keys.add(key)


def _leave_ftp_folder(ftp: FTP, expected_parent: str, folder_name: str) -> None:
    """상대경로로 부모 폴더에 복귀하고 실제 PWD가 일치하는지 확인한다.

    Args:
        ftp: 현재 하위 폴더에 있는 FTP 연결.
        expected_parent: 이동 전 저장한 부모 폴더의 PWD.
        folder_name: 로그에 사용할 이동한 하위 폴더명.

    Raises:
        FTPDirectoryError: CWD ..에 실패했거나 실제 복귀 위치가 다른 경우.
    """
    try:
        ftp.cwd("..")
        restored = ftp.pwd()
    except Exception as exc:
        raise FTPDirectoryError(f"FTP 폴더 복귀 실패: {folder_name!r}") from exc
    if restored.rstrip("/").casefold() != expected_parent.rstrip("/").casefold():
        raise FTPDirectoryError(
            f"FTP 폴더 복귀 위치 불일치: {folder_name!r}, "
            f"expected={expected_parent!r}, actual={restored!r}"
        )


def _scan_nested_device(
    ftp: FTP,
    equip: Equipment,
    equip_dir: Path,
    lookup: dict,
    state: CollectionState,
    device: str,
) -> None:
    """KLA2365/PUMA의 Device → Layer → Recipe 폴더를 순회한다.

    Args:
        ftp: 해당 장비의 로그인 FTP 연결.
        equip: 수집 장비 설정.
        equip_dir: 로컬 Recipe 저장 위치.
        lookup: 기존 Recipe 정보를 조회할 딕셔너리.
        state: 수집 상태 및 UPSERT 버퍼.
        device: 탐색 대상 Device 폴더명.

    Notes:
        Device/Layer에서 빠져나올 때 절대경로 CWD 대신 CWD ..를 사용하며
        복귀 위치를 확인한다. Recipe 경로 없음은 제외, 복귀 실패는 중단한다.
    """
    base = ftp.pwd()
    ftp.cwd(device)
    try:
        device_pwd = ftp.pwd()
        for layer_entry in list_entries(ftp):
            if not is_layer(layer_entry):
                continue
            layer = layer_entry.name
            ftp.cwd(layer)
            try:
                layer_pwd = ftp.pwd()
                for entry in list_entries(ftp):
                    if is_recipe_kla2365(entry, device, layer):
                        key = f"{equip.name}-{entry.name}"
                        if key in state.completed_keys:
                            continue
                        try:
                            _scan_recipe(
                                ftp,
                                entry,
                                equip,
                                equip_dir,
                                lookup,
                                state,
                                "Sensitivity1.xml",
                                device,
                                entry.name.split("_", 1)[1],
                                [device, layer],
                                layer_pwd,
                                is_retry=key in state.seen_keys,
                            )
                        except FTPRecipeUnavailable as exc:
                            # 목록에는 있으나 원격 경로가 실제로 없는 경우 제외.
                            state.skipped_missing_path += 1
                            state.consecutive_download_failures = 0
                            state.completed_keys.add(key)
                            _log_recipe_skip(
                                equip,
                                entry.name,
                                "경로 없음",
                                state.skipped_missing_path,
                                exc,
                            )
                        except FTPRecursiveListingError as exc:
                            # 하위 목록이 자기 자신을 반환하는 구형 FTP 서버 응답은
                            # 미완성 Recipe 백업 대신 별도 제외 건으로 관리한다.
                            state.skipped_recursive_listing += 1
                            state.consecutive_download_failures = 0
                            state.completed_keys.add(key)
                            _log_recipe_skip(
                                equip,
                                entry.name,
                                "재귀 폴더 탐색 이상",
                                state.skipped_recursive_listing,
                                exc,
                            )
            finally:
                _leave_ftp_folder(ftp, device_pwd, layer)
    finally:
        _leave_ftp_folder(ftp, base, device)


def _connect_kla2139(equip: Equipment) -> FTP:
    """KLA2139 장비에 새 FTP 세션을 연결하고 Recipe 루트로 이동한다.

    Args:
        equip: 접속할 장비의 FTP 설정.

    Returns:
        FTP: Recipe 루트 위치까지 이동이 완료된 새 연결.

    Raises:
        Exception: 연결, 인증 또는 루트 폴더 이동에 실패하면 전파.

    Notes:
        구형 FTP 서버의 내부 CWD 상태가 꼬인 경우에만 재연결에 사용한다.
    """
    ftp = FTP(timeout=equip.ftp_info.timeout_seconds, encoding=equip.ftp_info.encoding)
    try:
        ftp.connect(equip.ftp_info.host, equip.ftp_info.port)
        ftp.login(equip.ftp_info.username, equip.ftp_info.password)
        ftp.cwd(equip.ftp_info.remote_directory)
        return ftp
    except Exception:
        ftp.close()
        raise


def _list_root_with_reconnect(
    ftp: FTP, equip: Equipment, connector, state: CollectionState
) -> tuple[FTP, list[FTPEntry]]:
    """최초 Recipe/Device 목록 조회 중 타임아웃도 새 연결로 복구한다.

    Args:
        ftp: 연결된 최초 FTP 세션.
        equip: 장비 접속 설정.
        connector: 복구 후 동일 기준 위치로 이동하는 연결 함수.
        state: 재연결 횟수 등 수집 상태.

    Returns:
        tuple[FTP, list[FTPEntry]]: 사용 가능한 세션과 원격 목록.
    """
    resets = 0
    limit = max(1, int(os.getenv("FTP_RECONNECT_ATTEMPTS", "3")))
    while True:
        try:
            return ftp, list_entries(ftp)
        except Exception as exc:
            if not _is_connection_failure(exc) or resets >= limit:
                raise
            resets += 1
            _safe_close(ftp)
            ftp = _ftp_reconnect(equip, connector, state)


def collect_kla2139(
    equip: Equipment, equip_dir: Path, lookup: dict, state: CollectionState
) -> None:
    """KLA2139의 Recipe 목록을 한 번 확보하고 연결 장애 후 미완료 Recipe부터 재개한다.

    Args:
        equip: 수집 대상 KLA2139 장비 설정.
        equip_dir: 로컬 Recipe 백업 폴더.
        lookup: 기존 Recipe 정보의 키 기반 딕셔너리.
        state: 장비별 수집 버퍼·진행 및 완료 상태.

    Notes:
        완료된 키는 다시 저장하지 않는다. 550 경로 부재와 통신 타임아웃을
        구분하여 복구하며, 재연결이 끝내 실패하면 상위로 예외를 전달한다.
    """
    ftp = _connect_kla2139(equip)
    try:
        ftp, top_level = _list_root_with_reconnect(ftp, equip, _connect_kla2139, state)
        recipe_root = ftp.pwd()
        recipes = [entry for entry in top_level if is_recipe(entry)]
        reconnect_limit = max(1, int(os.getenv("FTP_RECONNECT_ATTEMPTS", "3")))
        for entry in recipes:
            if f"{equip.name}-{entry.name}" in state.completed_keys:
                continue
            device, layer = entry.name.split("_", 1)
            connection_resets = 0
            path_retry = False
            while True:
                try:
                    _scan_recipe(
                        ftp,
                        entry,
                        equip,
                        equip_dir,
                        lookup,
                        state,
                        "ins.dat",
                        device,
                        layer,
                        [],
                        recipe_root,
                        is_retry=path_retry or connection_resets > 0,
                    )
                    break
                except (
                    FTPRecipeUnavailable,
                    FTPDirectoryError,
                    FTPRecursiveListingError,
                ) as exc:
                    if _is_connection_failure(exc):
                        if connection_resets >= reconnect_limit:
                            raise RuntimeError(
                                f"[{equip.name}] Recipe {entry.name} FTP 연결 복구 횟수 초과"
                            ) from exc
                        connection_resets += 1
                        _safe_close(ftp)
                        ftp = _ftp_reconnect(equip, _connect_kla2139, state)
                        recipe_root = ftp.pwd()
                        continue
                    if not path_retry:
                        log.warning(
                            "[%s] Recipe FTP 경로 이상, 재연결 후 재시도: %s - %s",
                            equip.name,
                            entry.name,
                            exc,
                        )
                        _safe_close(ftp)
                        ftp = _ftp_reconnect(equip, _connect_kla2139, state)
                        recipe_root = ftp.pwd()
                        path_retry = True
                        continue
                    if isinstance(exc, FTPRecipeUnavailable):
                        state.skipped_missing_path += 1
                        state.consecutive_download_failures = 0
                        state.completed_keys.add(f"{equip.name}-{entry.name}")
                        _log_recipe_skip(
                            equip,
                            entry.name,
                            "경로 없음(재시도 완료)",
                            state.skipped_missing_path,
                            exc,
                        )
                        break
                    if isinstance(exc, FTPRecursiveListingError):
                        state.skipped_recursive_listing += 1
                        state.consecutive_download_failures = 0
                        state.completed_keys.add(f"{equip.name}-{entry.name}")
                        _log_recipe_skip(
                            equip,
                            entry.name,
                            "재귀 폴더 탐색 이상(재시도 완료)",
                            state.skipped_recursive_listing,
                            exc,
                        )
                        break
                    raise
                except Exception as exc:
                    if not _is_connection_failure(exc):
                        raise
                    if connection_resets >= reconnect_limit:
                        raise RuntimeError(
                            f"[{equip.name}] Recipe {entry.name} FTP 연결 복구 횟수 초과"
                        ) from exc
                    connection_resets += 1
                    _safe_close(ftp)
                    ftp = _ftp_reconnect(equip, _connect_kla2139, state)
                    recipe_root = ftp.pwd()
    finally:
        _safe_close(ftp)


def _connect_nested(equip: Equipment) -> FTP:
    """KLA2365/PUMA 장비에 로그인하고 기존 V3 탐색 시작 위치를 유지한다.

    Args:
        equip: 장비 FTP 접속 설정.

    Returns:
        FTP: 로그인 완료된 새 세션.
    """
    ftp = FTP(timeout=equip.ftp_info.timeout_seconds, encoding=equip.ftp_info.encoding)
    try:
        ftp.connect(equip.ftp_info.host, equip.ftp_info.port)
        ftp.login(equip.ftp_info.username, equip.ftp_info.password)
        return ftp
    except Exception:
        _safe_close(ftp)
        raise


def collect_kla2365(
    equip: Equipment, equip_dir: Path, lookup: dict, state: CollectionState
) -> None:
    """KLA2365/PUMA Device를 순회하며 장애 시 해당 Device부터 안전하게 재탐색한다.

    Args:
        equip: 대상 장비 정보.
        equip_dir: 로컬 Recipe 백업 기본 폴더.
        lookup: 기존 Recipe DB 정보 딕셔너리.
        state: 완료된 Recipe 키, 버퍼 및 실패 건수를 유지하는 수집 상태.

    Notes:
        이미 완료한 키는 중복 UPSERT하지 않는다. FTP 재연결 후 해당 Device를
        처음부터 다시 탐색하므로 신규 항목 누락 없이 진행할 수 있다.
    """
    ftp = _connect_nested(equip)
    try:
        ftp, top_level = _list_root_with_reconnect(ftp, equip, _connect_nested, state)
        devices = [entry.name for entry in top_level if is_device(entry)]
        retry_limit = max(1, int(os.getenv("FTP_RECONNECT_ATTEMPTS", "3")))
        for device in devices:
            connection_resets = 0
            while True:
                try:
                    _scan_nested_device(ftp, equip, equip_dir, lookup, state, device)
                    break
                except Exception as exc:
                    if not (
                        _is_connection_failure(exc)
                        or isinstance(exc, FTPDirectoryError)
                    ):
                        raise
                    if connection_resets >= retry_limit:
                        raise RuntimeError(
                            f"[{equip.name}] Device {device} FTP 연결 복구 횟수 초과"
                        ) from exc
                    connection_resets += 1
                    _safe_close(ftp)
                    ftp = _ftp_reconnect(equip, _connect_nested, state)
    finally:
        _safe_close(ftp)


def collect_puma(
    equip: Equipment, equip_dir: Path, lookup: dict, state: CollectionState
) -> None:
    """PUMA 모델을 KLA2365와 동일한 Device/Layer 수집 경로로 처리한다.

    Args:
        equip: 대상 장비 정보.
        equip_dir: 로컬 백업 폴더.
        lookup: 기존 Recipe DB 키 조회 딕셔너리.
        state: 장비 수집 상태.
    """
    collect_kla2365(equip, equip_dir, lookup, state)


def update_database(
    equip: Equipment,
    upserts: pd.DataFrame,
    remove_keys: list[str],
    unchange_keys: list[str],
    *,
    finalize: bool = True,
    new_cnt: int | None = None,
    update_cnt: int | None = None,
) -> None:
    """수집 배치를 UPSERT하고 장비 최종 변경 이력을 확정한다.

    Args:
        equip: 수집·파싱 대상 장비 정보.
        upserts: UPSERT 대상 Recipe 기록.
        remove_keys: 삭제 판정 대상 Recipe 키 목록.
        unchange_keys: 변경되지 않은 Recipe 키 목록.
        finalize: 장비 단위 삭제·유지·이력 정산 여부.
        new_cnt: 신규 Recipe 건수.
        update_cnt: 수정 Recipe 건수.

    Returns:
        None: 처리 결과를 반환한다."""
    if not upserts.empty:
        PostgresCRUD.upsert("dm_recipe_info", "dm_recipe_info_pkey", upserts)
    if not finalize:
        return
    if new_cnt is None:
        new_cnt = int(upserts["status"].eq("new").sum()) if "status" in upserts else 0
    if update_cnt is None:
        update_cnt = (
            int(upserts["status"].eq("update").sum()) if "status" in upserts else 0
        )
    history = pd.DataFrame(
        [
            {
                "model": equip.model,
                "equip": equip.name,
                "new_rcp": new_cnt,
                "update_rcp": update_cnt,
                "remove_rcp": len(remove_keys),
                "aggregation_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        ]
    )
    PostgresCRUD.finalize_equipment_scan(
        equip.name, remove_keys, unchange_keys, history, chunk_size=UPSERT_BATCH_SIZE
    )
    log.info(
        "[%s] new=%d update=%d removed=%d unchanged=%d",
        equip.name,
        new_cnt,
        update_cnt,
        len(remove_keys),
        len(unchange_keys),
    )


def collect_equip_data(equip: Equipment, equip_dir: Path) -> None:
    """장비 한 대를 수집하고 정상·부분 완료·실패 이력을 빠짐없이 기록한다.

    Args:
        equip: 수집·파싱 대상 장비 설정.
        equip_dir: 로컬 백업 위치.

    Raises:
        RuntimeError: 비정상 FTP 탐색, 삭제 비율 초과 또는 다운로드 오류.
        Exception: 그 밖의 수집·DB 저장 오류.

    Notes:
        신규·수정 데이터는 999건 단위로 먼저 UPSERT한다.
        최종 삭제·유지/기존 dm_update_history는 완전한 장비 수집일 때만 실행한다.
        부분 완료·실패 수량은 dm_collection_run_history에 별도로 남긴다.
    """
    run_id = str(uuid4())
    state = CollectionState(equip=equip)
    # 수집 시작 이력을 먼저 적재: 비정상 프로세스 종료 시 running으로 남는다.
    PostgresCRUD.start_collection_run(run_id, equip.name, equip.model)
    remove_keys: list[str] = []
    try:
        equip_db = PostgresCRUD.select_query(
            """SELECT recipe_key, dt_edit_time, device,
            layer, recipe, backup_file_path FROM dm_recipe_info
            WHERE equip=%s AND is_remove=false""",
            (equip.name,),
        )
        lookup = build_equipment_lookup(equip_db)
        del equip_db
        match equip.model:
            case "KLA2139":
                collect_kla2139(equip, equip_dir, lookup, state)
            case "KLA2365":
                collect_kla2365(equip, equip_dir, lookup, state)
            case "PUMA":
                collect_puma(equip, equip_dir, lookup, state)
            case _:
                raise ValueError(f"Unsupported equipment model: {equip.model}")
        state.flush()
        log.info(
            "[%s] 수집 제외: 필수 파일 없음 %d건, 경로 없음 %d건, "
            "재귀 탐색 이상 %d건, 선택 파일 없음 %d건, FTP 재연결 %d회",
            equip.name,
            state.skipped_missing_file,
            state.skipped_missing_path,
            state.skipped_recursive_listing,
            state.skipped_optional_files,
            state.reconnect_count,
        )
        old_keys = set(lookup)
        if old_keys and not state.seen_keys:
            raise RuntimeError(
                f"[{equip.name}] FTP returned no Recipe; deletion blocked"
            )
        remove_keys = list(old_keys - state.seen_keys)
        allowed_fraction = float(os.getenv("MAX_AUTO_REMOVAL_RATIO", "0.25"))
        if old_keys and (len(remove_keys) / len(old_keys)) > allowed_fraction:
            raise RuntimeError(
                f"[{equip.name}] Removal ratio {len(remove_keys)}/{len(old_keys)} "
                f"exceeds {allowed_fraction:.0%}; deletion blocked"
            )
        if state.download_failures:
            raise RuntimeError(
                f"[{equip.name}] {state.download_failures} downloads failed; "
                "delete/update-history skipped; pending parses remain available"
            )
        update_database(
            equip,
            pd.DataFrame(columns=["status"]),
            remove_keys,
            list(state.unchanged_keys),
            new_cnt=state.new_cnt,
            update_cnt=state.update_cnt,
        )
        PostgresCRUD.finish_collection_run(
            run_id,
            "success",
            new_rcp=state.new_cnt,
            update_rcp=state.update_cnt,
            remove_rcp=len(remove_keys),
            unchanged_rcp=len(state.unchanged_keys),
            download_failures=state.download_failures,
            skipped_missing_file=state.skipped_missing_file,
            skipped_missing_path=state.skipped_missing_path,
            skipped_recursive_listing=state.skipped_recursive_listing,
            skipped_optional_files=state.skipped_optional_files,
            reconnect_count=state.reconnect_count,
        )
        log.info("[%s] 장비 수집 완료: run_id=%s status=success", equip.name, run_id)
    except Exception as exc:
        # FTP 중단 시 현재 999 미만의 정상 다운로드도 최선을 다해 저장한다.
        # DB 저장 자체가 실패한 경우 버퍼는 유지하며 성공으로 허위 집계하지 않는다.
        if state.upserts:
            try:
                state.flush()
            except Exception:
                log.exception("[%s] 부분 수집 버퍼 DB 저장 실패", equip.name)
        scan_status = (
            "partial"
            if state.seen_keys or state.new_cnt or state.update_cnt
            else "failed"
        )
        try:
            PostgresCRUD.finish_collection_run(
                run_id,
                scan_status,
                new_rcp=state.new_cnt,
                update_rcp=state.update_cnt,
                remove_rcp=0,
                unchanged_rcp=0,
                download_failures=state.download_failures,
                skipped_missing_file=state.skipped_missing_file,
                skipped_missing_path=state.skipped_missing_path,
                skipped_recursive_listing=state.skipped_recursive_listing,
                skipped_optional_files=state.skipped_optional_files,
                reconnect_count=state.reconnect_count,
                error_message=f"{type(exc).__name__}: {exc}",
            )
            log.warning(
                "[%s] 수집 %s 이력 기록: run_id=%s, new=%d, update=%d",
                equip.name,
                scan_status,
                run_id,
                state.new_cnt,
                state.update_cnt,
            )
        except Exception:
            log.exception(
                "[%s] 수집 실행 이력 확정 실패: run_id=%s", equip.name, run_id
            )
        raise


class Collector:
    def __init__(self, equipments: list[dict], dm_dir: Path) -> None:
        """Collector의 장비 목록과 백업 기본 경로를 초기화한다.

        Args:
            self: 현재 클래스 인스턴스.
            equipments: 수집할 장비 설정 목록.
            dm_dir: Recipe 저장 기본 경로.

        Returns:
            None: 처리 결과를 반환한다."""
        self.equipments = equipments
        self.dm_dir = dm_dir

    def run(self) -> None:
        """활성화된 모든 장비를 순회하고 장비별 실패를 격리한다.

        Args:
            self: 현재 클래스 인스턴스.

        Returns:
            None: 처리 결과를 반환한다."""
        failures = []
        for eq_info in self.equipments:
            equip = create_equipment(eq_info)
            if not equip.enabled:
                continue
            equip_dir = self.dm_dir / equip.name
            equip_dir.mkdir(parents=True, exist_ok=True)
            try:
                log.info("[%s] FTP collection started", equip.name)
                collect_equip_data(equip, equip_dir)
            except Exception:
                failures.append(equip.name)
                log.exception(
                    "[%s] FTP collection failed; other equipment continues", equip.name
                )
        if failures:
            log.error("FTP collection failed for: %s", ", ".join(failures))
