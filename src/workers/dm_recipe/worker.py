"""Recoverable, bounded Recipe parsing worker."""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Any
import pandas as pd
from src.config.config import PARSE_BATCH_SIZE
from src.databases import PostgresCRUD
from src.domain.entities.equipment import Equipment, create_equipment
from src.preprocessing.parsers import KLA2139Parser, KLA2365Parser, PumaParser, Parser

log = logging.getLogger(__name__)
PARSERS = {"KLA2139": KLA2139Parser, "KLA2365": KLA2365Parser, "PUMA": PumaParser}
PARA_COLS = ["recipe_key", "para_nm", "para_value", "matching"]


def recipe_path(dm_dir: Path, relative: str) -> Path:
    """DB에 저장된 상대 백업 경로를 검증하고 로컬 파일 경로로 변환한다.

    Args:
        dm_dir: Recipe 저장 기본 경로.
        relative: 처리에 필요한 입력값.

    Returns:
        Path: 처리 결과를 반환한다."""
    root = dm_dir.resolve()
    target = (root / relative.lstrip("/\\")).resolve()
    if not target.is_relative_to(root):
        raise ValueError(
            f"Recipe path escapes configured backup directory: {relative!r}"
        )
    return target


def parse_one(
    rcp_key: str, backup_file_path: str, parser: Parser, dm_dir: Path
) -> dict:
    """Recipe 파일을 파싱하고 비어 있지 않은 Parameter 딕셔너리를 검증한다.

    Args:
        rcp_key: Recipe 기본키.
        backup_file_path: 로컬 필수 파일 백업 경로.
        parser: 모델별 Recipe 파서.
        dm_dir: Recipe 저장 기본 경로.

    Returns:
        dict: 처리 결과를 반환한다."""
    path = recipe_path(dm_dir, backup_file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Backed up Recipe missing: {path}")
    params = parser.parse(path)
    if not isinstance(params, dict) or not any(v is not None for v in params.values()):
        raise ValueError(f"Parser returned no parameters: {rcp_key}")
    return params


def parse_queue(queue: pd.DataFrame, parser: Parser, dm_dir: Path) -> pd.DataFrame:
    """소규모 Recipe 큐를 파싱하여 Parameter DataFrame으로 변환한다.

    Args:
        queue: 처리 대상 Recipe 목록 DataFrame.
        parser: 모델별 Recipe 파서.
        dm_dir: Recipe 저장 기본 경로.

    Returns:
        pd.DataFrame: 처리 결과를 반환한다."""
    rows = []
    for item in queue.itertuples(index=False):
        params = parse_one(item.recipe_key, item.backup_file_path, parser, dm_dir)
        rows.extend(
            {
                "recipe_key": item.recipe_key,
                "para_nm": name,
                "para_value": value,
                "matching": None,
            }
            for name, value in params.items()
            if value is not None
        )
    return pd.DataFrame.from_records(rows, columns=PARA_COLS)


def update_database(result: pd.DataFrame) -> None:
    """파싱 결과가 있으면 DB에 Parameter 교체 작업을 요청한다.

    Args:
        result: Recipe 파싱 결과.

    Returns:
        None: 처리 결과를 반환한다."""
    if result.empty:
        return
    parsed = []
    for key, group in result.groupby("recipe_key", sort=False):
        parsed.append((key, dict(zip(group["para_nm"], group["para_value"]))))
    PostgresCRUD.replace_recipe_parameters(parsed)


def parse_recipe(equip: Equipment, parser: Parser, dm_dir: Path) -> None:
    """장비별 미처리 Recipe를 키셋 기반 배치로 조회하고 파싱한다.

    Args:
        equip: 수집·파싱 대상 장비 정보.
        parser: 모델별 Recipe 파서.
        dm_dir: Recipe 저장 기본 경로.

    Returns:
        None: 처리 결과를 반환한다."""
    if PARSE_BATCH_SIZE < 1:
        raise ValueError("PARSE_BATCH_SIZE must be positive")
    # Keyset pagination prevents large in-memory queue and OFFSET skipping on updates.
    cursor_key = ""
    total_success = total_fail = 0
    while True:
        queue = PostgresCRUD.select_query(
            """
            SELECT recipe_key, backup_file_path
            FROM dm_recipe_info
            WHERE equip=%s AND is_remove=false
              AND (parse_status IN ('pending', 'failed')
                   OR (parse_status IS NULL AND status IN ('new','update')))
              AND recipe_key > %s
            ORDER BY recipe_key
            LIMIT %s
        """,
            (equip.name, cursor_key, PARSE_BATCH_SIZE),
        )
        if queue.empty:
            break
        successful: list[tuple[str, dict]] = []
        for item in queue.itertuples(index=False):
            cursor_key = item.recipe_key
            try:
                successful.append(
                    (
                        item.recipe_key,
                        parse_one(
                            item.recipe_key, item.backup_file_path, parser, dm_dir
                        ),
                    )
                )
            except Exception as exc:
                log.exception("[%s] Parser failed for %s", equip.name, item.recipe_key)
                PostgresCRUD.mark_parse_failure(item.recipe_key, str(exc))
                total_fail += 1
        if successful:
            PostgresCRUD.replace_recipe_parameters(successful)
            total_success += len(successful)
        log.info(
            "[%s] parser progress: success=%d failed=%d",
            equip.name,
            total_success,
            total_fail,
        )


class RecipeParser:
    def __init__(self, equipments: list[dict], dm_dir: Path) -> None:
        """Recipe 파싱 대상 장비와 백업 기본 경로를 설정한다.

        Args:
            self: 현재 클래스 인스턴스.
            equipments: 수집할 장비 설정 목록.
            dm_dir: Recipe 저장 기본 경로.

        Returns:
            None: 처리 결과를 반환한다."""
        self.equipments = equipments
        self.dm_dir = dm_dir

    def run(self) -> None:
        """활성화된 장비의 모델에 따라 Parser를 선택하고 파싱을 실행한다.

        Args:
            self: 현재 클래스 인스턴스.

        Returns:
            None: 처리 결과를 반환한다."""
        for eq_info in self.equipments:
            equip = create_equipment(eq_info)
            if not equip.enabled:
                continue
            try:
                factory = PARSERS[equip.model]
                log.info("[%s] Recipe parser started", equip.name)
                parse_recipe(equip, factory(), self.dm_dir)
            except Exception:
                log.exception(
                    "[%s] Recipe parsing failed; next equipment continues", equip.name
                )
