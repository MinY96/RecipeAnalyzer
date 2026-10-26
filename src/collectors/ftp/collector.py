# pylint: disable=broad-exception-caught, W0702
"""FTP collector"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple
from dataclasses import dataclass, field
from pathlib import Path
import re
from datetime import datetime
from dateutil.relativedelta import relativedelta
from ftplib import FTP

import pandas as pd

from src.domain.entities.equipment import Equipment, create_equipment
from src.collectors.ftp.client import ftp_download_tree
from src.databases import OracleCRUD, PostgresCRUD


def parse_edit_time(line: str) -> str:
    """파일 정보 문자 내 최종 수정 일자 파싱"""
    # 연도가 없고 날짜만 있는 경우 : 오늘 월/일보다 뒤에 있을 경우 작년, 오늘 월/일보다 앞에 있을 경우 올해
    pattern_1 = r"(\b[A-Za-z]{3}\s\d{2}\s\d{2}:\d{2}\b)"
    pattern_2 = r"(\b[A-Za-z]{3}\s\s\d{1}\s\d{2}:\d{2}\b)"
    pattern_3 = r"(\b[A-Za-z]{3}\s\d{2}\s\s\d{4}\b)"
    pattern_4 = r"(\b[A-Za-z]{3}\s\s\d{1}\s\s\d{4}\b)"
    pattern_5 = r"(\d+[가-힣]\s\d+[가-힣]\s\d{4}[가-힣])"
    pattern_6 = r"(\d+[가-힣]\s\s\d+[가-힣]\s\d{4}[가-힣])"
    pattern_7 = r"(\d+[가-힣]\s\d+[가-힣]\s\s\d{2}:\d{2}\b)"
    try:
        info = re.findall(pattern_1, line)[0]
        year = datetime.now().year
        edit_time = datetime.strptime(f"{year} {info}", "%Y %b %d %H:%M")
    except:
        try:
            info = re.findall(pattern_2, line)[0]
            year = datetime.now().year
            edit_time = datetime.strptime(f"{year} {info}", "%Y %b  %d %H:%M")
        except:
            try:
                info = re.findall(pattern_3, line)[0]
                edit_time = datetime.strptime(info, "%b %d  %Y")
            except:
                try:
                    info = re.findall(pattern_4, line)[0]
                    edit_time = datetime.strptime(info, "%b  %d  %Y")
                except:
                    try:
                        info = re.findall(pattern_5, line)[0]
                        info = "-".join(
                            [
                                re.match(r"\d+", item).group(0)
                                for item in info.split(" ")
                            ]
                        )
                        edit_time = datetime.strptime(info, "%m-%d-%Y")
                    except:
                        try:
                            info = re.findall(pattern_6, line)[0]
                            info = "-".join(
                                [
                                    re.match(r"\d+", item).group(0)
                                    for item in info.split(" ")
                                    if item is not None and len(item) > 0
                                ]
                            )
                            edit_time = datetime.strptime(info, "%m-%d-%Y")
                        except:
                            try:
                                info = re.findall(pattern_7, line)[0]
                                info = re.sub("[가-힣]", " ", info)
                                edit_time = datetime.strptime(
                                    f"{datetime.now().year} {info}", "%Y %m  %d   %H:%M"
                                )
                            except:
                                print(line)
                                edit_time = datetime.now()
    if edit_time > datetime.now():
        edit_time = edit_time - relativedelta(years=1)
    return edit_time.strftime("%Y-%m-%d %H:%M")


def recipe_filter(file: str) -> bool:
    """Recipe 폴더 필터

    Args:
        file (str): 파일명

    Returns:
        bool: 필터 통과 여부
    """
    # .이나 _로 시작하거나 특정 문자/확장자 로 끝나는 경우 제외
    if file.startswith((".", "_")) or file.endswith(("~", ".inf", ".DAT")):
        return False

    # 언더바(_)로 분할했을 때 정확히 2개여야 하고, 앞부분의 길이가 4여야 함
    parts = file.split("_")
    return len(parts) == 2 and len(parts[0]) == 4


def collect_lines(ftp: FTP) -> List[str]:
    """FTP 폴더 내 파일 정보 수집

    Args:
        ftp (FTP): 접속한 ftp 인스턴스

    Returns:
        List[str]: 폴더/파일 리스트
    """
    lines = []

    def _collect(line):
        lines.append(line)

    ftp.dir(_collect)
    return lines


def is_recipe(line: str) -> bool:
    """Recipe 폴더 필터

    Args:
        line (str): 파일 정보

    Returns:
        bool: 필터 통과 여부
    """
    info = line.split(" ")
    file_name = info[-1].strip()
    # .이나 _로 시작하거나 특정 문자/확장자 로 끝나는 경우 제외
    if file_name.startswith((".", "_")) or file_name.endswith(("~", ".inf", ".DAT")):
        return False

    # 언더바(_)로 분할했을 때 정확히 2개여야 하고, 앞부분의 길이가 4여야 함
    parts = file_name.split("_")
    return len(parts) == 2 and len(parts[0]) == 4


def is_recipe_kla2365(line: str, device: str, layer: str) -> bool:
    """Recipe 폴더 필터

    Args:
        line (str): 파일 정보

    Returns:
        bool: 필터 통과 여부
    """
    info = line.split(" ")
    file_name = info[-1].strip()
    # .이나 _로 시작하거나 특정 문자/확장자 로 끝나는 경우 제외
    if file_name.startswith((".", "_")) or file_name.endswith(("~", ".inf", ".DAT")):
        return False

    # 언더바(_)로 분할했을 때 정확히 2개여야 하고, 앞부분의 길이가 4여야 함
    parts = file_name.split("_")
    if len(parts) != 2:
        return False

    if len(parts[0]) != 4:
        return False

    if parts[0] != device:
        return False

    return parts[1].endswith(layer)


def is_device(line: str) -> bool:
    """Device 폴더 필터

    Args:
        line (str): 파일 정보

    Returns:
        bool: 필터 통과 여부
    """
    info = line.split(" ")
    file_name = info[-1].strip()
    # .이나 _로 시작하거나 특정 문자/확장자 로 끝나는 경우 제외
    if file_name.startswith((".", "_")) or file_name.endswith(("~", ".inf", ".DAT")):
        return False

    # 폴더명이 4글자여야 함
    return len(file_name) == 4


def is_layer(line: str) -> bool:
    """Layer 폴더 필터

    Args:
        line (str): 파일 정보

    Returns:
        bool: 필터 통과 여부
    """
    info = line.split(" ")
    file_name = info[-1].strip()
    # .이나 _로 시작하거나 특정 문자/확장자 로 끝나는 경우 제외
    if file_name.startswith((".", "_")) or file_name.endswith(("~", ".inf", ".DAT")):
        return False

    # 폴더명이 4글자여야 함
    return len(file_name) == 4


def transform_recipe_info(
    line: str, equip: Equipment, equip_dir: Path, recipe_name: str = "ins.dat"
) -> Tuple[str, str, str, str, str, str]:
    """Recipe 정보 추출

    Args:
        line (str): 파일 정보

    Returns:
        Tuple[str, str, str, str, str, str]:
        Recipe Key, Device, Layer, Recipe, Edit Time
    """
    info = line.split(" ")
    file_name = info[-1].strip()
    device, layer = file_name.split("_")
    last_edit_time = parse_edit_time(line)
    recipe_key = f"{equip.name}-{file_name}"
    backup_recipe_dir = equip_dir / file_name
    backup_recipe_path = backup_recipe_dir / recipe_name
    return (
        recipe_key,
        device,
        layer,
        file_name,
        last_edit_time,
        backup_recipe_dir,
        backup_recipe_path,
    )


def transform_folder(line: str) -> str:
    """Device 폴더 추출

    Args:
        line (str): 파일 정보

    Returns:
        str: Device 명
    """
    info = line.split(" ")
    file_name = info[-1].strip()
    return file_name


def is_file_update(
    equip_db: pd.DataFrame, rcp_key: str, edit_time: datetime, backup_file_path: Path
):
    """DB 정보와 파일 최종 수정시간 비교하여 파일 업데이트 상태 최신화"""
    match_info = equip_db[equip_db["recipe_key"] == rcp_key]
    if match_info.empty:
        return True, "new"  # New (신규 Recipe)
    if not backup_file_path.exists():
        return True, "new"
    db_edit_time = match_info.iloc[0]["dt_edit_time"]

    if edit_time > db_edit_time:
        return True, "update"  # Update (수정된 Recipe)
    return False, None


UPSERT_BATCH_SIZE = 999


@dataclass
class CollectionState:
    """장비 단위 공유 상태. upserts는 최대 999건만 누적한다."""

    equip: Equipment
    batch_size: int = UPSERT_BATCH_SIZE
    upserts: list[dict[str, Any]] = field(default_factory=list)
    seen_keys: set[str] = field(default_factory=set)
    unchanged_keys: set[str] = field(default_factory=set)
    new_cnt: int = 0
    update_cnt: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.batch_size <= UPSERT_BATCH_SIZE:
            raise ValueError("batch_size must be between 1 and 999")

    def append(self, recipe_info: dict[str, Any]) -> None:
        self.upserts.append(recipe_info)
        if len(self.upserts) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        """MES 조회 및 DB 저장 성공 시에만 버퍼를 비운다."""
        if not self.upserts:
            return
        enriched = join_mes_info(self.upserts)
        update_database(self.equip, enriched, [], [], finalize=False)
        # Update 실패 시 위 호출에서 예외가 발생하므로 데이터와 카운터는 그대로 남음.
        self.new_cnt += sum(item["status"] == "new" for item in self.upserts)
        self.update_cnt += sum(item["status"] == "update" for item in self.upserts)
        print(
            f"[{self.equip.name}] {len(self.upserts):,}건 UPSERT 완료 "
            f"(누적 신규 {self.new_cnt:,} / 수정 {self.update_cnt:,})"
        )
        self.upserts.clear()


def move_layer_folder(
    ftp: FTP,
    device: str,
    layer: str,
    equip: Equipment,
    equip_dir: Path,
    equip_db: pd.DataFrame,
    state: CollectionState,
) -> None:
    """Layer 하위 Recipe를 수집하며 동일한 upsert 버퍼를 사용한다."""
    current = ftp.pwd()
    try:
        ftp.cwd(layer)
        lines = collect_lines(ftp)
        recipes = (
            transform_recipe_info(line, equip, equip_dir, "Sensitivity1.xml")
            for line in lines
            if is_recipe_kla2365(line, device, layer)
        )
        for (
            rcp_key,
            _,
            total_layer,
            recipe,
            edit_time,
            backup_recipe_dir,
            backup_recipe_path,
        ) in recipes:
            # 다운로드 실패 시에도 FTP에 실제 존재하는 키는 삭제 대상에서 제외.
            state.seen_keys.add(rcp_key)
            is_upsert, file_status = is_file_update(
                equip_db, rcp_key, edit_time, backup_recipe_path
            )
            if not is_upsert:
                state.unchanged_keys.add(rcp_key)
                continue
            try:
                backup_recipe_dir.mkdir(parents=True, exist_ok=True)
                ftp_download_tree(ftp, recipe, backup_recipe_dir)
                backup_time = (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    if backup_recipe_path.exists()
                    else None
                )
            except Exception as exc:
                print(f"[{equip.name}] FTP 다운로드 실패 ({recipe}): {exc}")
                # 기존 장비별 복구 동작과 동일한 위치로 되돌린다.
                ftp.cwd(current)
                ftp.cwd(layer)
                continue
            # 중요: DB 오류를 위 다운로드 except가 삼키지 않도록 try 바깥에서 호출.
            state.append(
                {
                    "recipe_key": rcp_key,
                    "model": equip.model,
                    "equip": equip.name,
                    "device": device,
                    "layer": total_layer,
                    "level": total_layer[:4],
                    "recipe": recipe,
                    "folder_path": f"{equip.ftp_info.remote_directory}/{recipe}",
                    "file_path": f"{equip.ftp_info.remote_directory}/{recipe}/Sensitivity1.xml",
                    "dt_edit_time": edit_time,
                    "backup_folder_path": f"/{equip.name}/{recipe}",
                    "backup_file_path": f"/{equip.name}/{recipe}/Sensitivity1.xml",
                    "dt_backup": backup_time,
                    "status": file_status,
                }
            )
    finally:
        ftp.cwd(current)


def move_device_folder(
    ftp: FTP,
    device: str,
    equip: Equipment,
    equip_dir: Path,
    equip_db: pd.DataFrame,
    state: CollectionState,
) -> None:
    """Device 안의 여러 Layer를 방문하되 중간 리스트를 생성하지 않는다."""
    current = ftp.pwd()
    try:
        ftp.cwd(device)
        lines = collect_lines(ftp)
        layers = [transform_folder(line) for line in lines if is_layer(line)]
        for layer in layers:
            move_layer_folder(ftp, device, layer, equip, equip_dir, equip_db, state)
    finally:
        ftp.cwd(current)


def collect_kla2139(
    equip: Equipment,
    equip_dir: Path,
    equip_db: pd.DataFrame,
    state: CollectionState,
) -> None:
    """KLA2139: 단일 Recipe 폴더 목록 순회 중 999건마다 UPSERT."""
    with FTP(
        timeout=equip.ftp_info.timeout_seconds, encoding=equip.ftp_info.encoding
    ) as ftp:
        ftp.connect(host=equip.ftp_info.host, port=equip.ftp_info.port)
        ftp.login(equip.ftp_info.username, equip.ftp_info.password)
        ftp.cwd(equip.ftp_info.remote_directory)
        lines = collect_lines(ftp)
        recipes = (
            transform_recipe_info(line, equip, equip_dir)
            for line in lines
            if is_recipe(line)
        )
        for (
            rcp_key,
            device,
            layer,
            recipe,
            edit_time,
            backup_recipe_dir,
            backup_recipe_path,
        ) in recipes:
            state.seen_keys.add(rcp_key)
            is_upsert, file_status = is_file_update(
                equip_db, rcp_key, edit_time, backup_recipe_path
            )
            if not is_upsert:
                state.unchanged_keys.add(rcp_key)
                continue
            try:
                backup_recipe_dir.mkdir(parents=True, exist_ok=True)
                ftp_download_tree(ftp, recipe, backup_recipe_dir)
                backup_time = (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    if backup_recipe_path.exists()
                    else None
                )
            except Exception as exc:
                print(f"[{equip.name}] FTP 다운로드 실패 ({recipe}): {exc}")
                ftp.cwd(equip.ftp_info.remote_directory)
                continue
            # 부분 DB UPSERT 오류는 상위 호출자로 전파되어 최종 삭제 정산 중단.
            state.append(
                {
                    "recipe_key": rcp_key,
                    "model": equip.model,
                    "equip": equip.name,
                    "device": device,
                    "layer": layer,
                    "level": layer[:4],
                    "recipe": recipe,
                    "folder_path": f"{equip.ftp_info.remote_directory}/{recipe}",
                    "file_path": f"{equip.ftp_info.remote_directory}/{recipe}/ins.dat",
                    "dt_edit_time": edit_time,
                    "backup_folder_path": f"/{equip.name}/{recipe}",
                    "backup_file_path": f"/{equip.name}/{recipe}/ins.dat",
                    "dt_backup": backup_time,
                    "status": file_status,
                }
            )


def collect_kla2365(
    equip: Equipment,
    equip_dir: Path,
    equip_db: pd.DataFrame,
    state: CollectionState,
) -> None:
    """KLA2365: Device → Layer → Recipe 간 버퍼 공유."""
    with FTP(
        timeout=equip.ftp_info.timeout_seconds, encoding=equip.ftp_info.encoding
    ) as ftp:
        ftp.connect(host=equip.ftp_info.host, port=equip.ftp_info.port)
        ftp.login(equip.ftp_info.username, equip.ftp_info.password)
        lines = collect_lines(ftp)
        devices = [transform_folder(line) for line in lines if is_device(line)]
        for device in devices:
            move_device_folder(ftp, device, equip, equip_dir, equip_db, state)


def collect_puma(
    equip: Equipment,
    equip_dir: Path,
    equip_db: pd.DataFrame,
    state: CollectionState,
) -> None:
    """PUMA: Device → Layer → Recipe 간 버퍼 공유."""
    with FTP(
        timeout=equip.ftp_info.timeout_seconds, encoding=equip.ftp_info.encoding
    ) as ftp:
        ftp.connect(host=equip.ftp_info.host, port=equip.ftp_info.port)
        ftp.login(equip.ftp_info.username, equip.ftp_info.password)
        lines = collect_lines(ftp)
        devices = [transform_folder(line) for line in lines if is_device(line)]
        for device in devices:
            move_device_folder(ftp, device, equip, equip_dir, equip_db, state)


def join_mes_info(upserts: List[Dict[Any]]) -> pd.DataFrame:
    """upsert할 항목에 MES 정보 조인

    Args:
        upserts (List[Dict[Any]]): Insert / Update 할 Recipe 정보 리스트

    Returns:
        pd.DataFrame: MES 정보 조인된 테이블
    """
    cols = [
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
    ]
    if len(upserts) == 0:
        return pd.DataFrame(columns=cols)
    upserts = pd.DataFrame(upserts)
    devices = upserts["device"].unique().tolist()
    device_query = ",".join([f"'{device}'" for device in devices])
    query = f"""
    SELECT DISTINCT
        MAT_ID product, MAT_CMF_21 device, MAT_CMF_5 device_name,
        MAT_CMF_6 tech_prod, MAT_CMF_7 tech, MAT_CMF_23 prod_group
    FROM IDBMGR.MWIPMATDEF
    WHERE FACTORY = 'AFB1'
        AND MAT_CMF_21 IN ({device_query}) 
    ORDER BY MAT_CMF_21
    """
    df_tech = OracleCRUD.select_query(query)
    if df_tech.empty:
        for mes_col in ["tech_prod", "tech", "prod_group"]:
            upserts[mes_col] = None
        return upserts[cols]
    df_tech["result"] = df_tech.apply(
        lambda row: (
            True
            if row["device_name"] in [None, "", " "]
            else row["device"] in row["product"]
        ),
        axis=1,
    )
    df_tech.columns = df_tech.columns.str.lower()
    df_tech = df_tech[df_tech["result"]].drop(
        ["product", "device_name", "result"], axis=1
    )
    df_tech = df_tech.drop_duplicates()
    upserts = upserts.merge(df_tech, how="left", on="device")
    return upserts[cols]


def update_database(
    equip: Equipment,
    upserts: pd.DataFrame,
    remove_keys: List[str],
    unchange_keys: List[str],
    *,
    finalize: bool = True,
    new_cnt: int | None = None,
    update_cnt: int | None = None,
) -> None:
    """부분 UPSERT는 매 배치, 삭제/유지/집계는 장비 끝에서 한 번만."""
    if not upserts.empty:
        PostgresCRUD.upsert("dm_recipe_info", "dm_recipe_info_pkey", upserts)

    if not finalize:
        return

    # 안전한 파라미터 바인딩 및 여러 번에 나누어 UPDATE.
    for start in range(0, len(remove_keys), UPSERT_BATCH_SIZE):
        keys = remove_keys[start : start + UPSERT_BATCH_SIZE]
        PostgresCRUD.execute(
            """
            UPDATE dm_recipe_info
            SET is_remove = true, status = 'remove'
            WHERE equip = %s AND recipe_key = ANY(%s)
            """,
            (equip.name, keys),
        )

    for start in range(0, len(unchange_keys), UPSERT_BATCH_SIZE):
        keys = unchange_keys[start : start + UPSERT_BATCH_SIZE]
        PostgresCRUD.execute(
            """
            UPDATE dm_recipe_info
            SET status = null
            WHERE equip = %s AND recipe_key = ANY(%s)
            """,
            (equip.name, keys),
        )

    if new_cnt is None or update_cnt is None:
        counts = (
            upserts["status"].value_counts()
            if "status" in upserts.columns
            else pd.Series(dtype=int)
        )
        if new_cnt is None:
            new_cnt = int(counts.get("new", 0))
        if update_cnt is None:
            update_cnt = int(counts.get("update", 0))

    PostgresCRUD.upsert(
        "dm_update_history",
        "dm_update_history_pkey",
        pd.DataFrame(
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
        ),
    )
    print(
        f"신규: {new_cnt:,}개, 수정: {update_cnt:,}개, "
        f"삭제: {len(remove_keys):,}개, 유지: {len(unchange_keys):,}개"
    )


def collect_equip_data(equip: Equipment, equip_dir: Path) -> None:
    """장비 단위 수집: 999건 단위 저장 + 수집 완료 후 삭제/이력 정산."""
    equip_db = PostgresCRUD.select_query(
        """
        SELECT recipe_key, dt_edit_time, device, layer, recipe, backup_file_path
        FROM dm_recipe_info
        WHERE equip = %s
            AND is_remove=false""",
        (equip.name,),
    )
    state = CollectionState(equip=equip)
    match equip.model:
        case "KLA2139":
            collect_kla2139(equip, equip_dir, equip_db, state)
        case "KLA2365":
            collect_kla2365(equip, equip_dir, equip_db, state)
        case "PUMA":
            collect_puma(equip, equip_dir, equip_db, state)
        case _:
            raise ValueError(f"지원하지 않는 장비 모델: {equip.model}")

    # 999건 미만으로 남은 마지막 배치 처리.
    state.flush()

    old_keys = set(equip_db["recipe_key"])
    # FTP 빈 목록이 일시적 오류인 경우 대량 오삭제를 막기 위한 방어.
    if old_keys and not state.seen_keys:
        raise RuntimeError(
            f"[{equip.name}] FTP 수집 Recipe가 0건입니다. "
            "일괄 삭제 오판 방지를 위해 최종 삭제/이력 정산을 중단합니다."
        )
    remove_keys = list(old_keys - state.seen_keys)
    update_database(
        equip,
        pd.DataFrame(columns=["status"]),
        remove_keys,
        list(state.unchanged_keys),
        new_cnt=state.new_cnt,
        update_cnt=state.update_cnt,
    )


class Collector:
    """장비 내 Recipe 수집기"""

    def __init__(self, equipments: list[str], dm_dir: Path) -> None:
        self.equipments = equipments
        self.dm_dir = dm_dir

    def run(self) -> None:
        """데이터 수집 실행"""
        for eq_info in self.equipments:
            equip = create_equipment(eq_info)
            equip_dir = self.dm_dir / equip.name
            equip_dir.mkdir(parents=True, exist_ok=True)
            if not equip.enabled:
                continue
            print(f"-----{equip.name}----")
            print("수집 시작")
            collect_equip_data(equip, equip_dir)
            print("수집 종료\n")
