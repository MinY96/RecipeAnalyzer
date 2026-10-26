# pylint: disable=bare-except
"""Queue Worker"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.domain.entities.equipment import create_equipment, Equipment
from src.databases import PostgresCRUD
from src.preprocessing.parsers import KLA2139Parser, KLA2365Parser, PumaParser, Parser


def parse_queue(queue: pd.DataFrame, parser: Parser, dm_dir: Path) -> pd.DataFrame:
    """Recipe 리스트 파싱 및 결과 DataFrame 반환

    Args:
        queue (pd.DataFrame): Recipe 큐
        parser (Parser): Recipe 파서
        dm_dir (Path): Recipe 저장 Parent 경로

    Returns:
        pd.DataFrame: 파싱 결과
    """
    para_infos = []
    for item in queue.itertuples():
        rcp_key = item.recipe_key
        recipe_path = dm_dir / item.backup_file_path[1:]
        if recipe_path.exists():
            params = parser.parse(recipe_path)
            infos = [
                {
                    "recipe_key": rcp_key,
                    "para_nm": para_nm,
                    "para_value": para_value,
                    "matching": None,
                }
                for para_nm, para_value in params.items()
                if para_value is not None
            ]
            para_infos += infos
    return pd.DataFrame(para_infos)


def update_database(result: pd.DataFrame) -> None:
    """장비 단위 Recipe 파싱 정보 DB 업데이트

    Args:
        result (pd.DataFrame): Recipe 파싱 정보
    """
    # 1. 신규/수정 Recipe 정보 Upsert
    if not result.empty:
        PostgresCRUD.upsert("dm_recipe_para", "recipe_para_pk", result)
    # 2. 'Mode' Parameter 존재하는 경우 dm_recipe_info 업데이트
    modes = result[result["para_nm"] == "mode"][["recipe_key", "para_value"]]
    modes = modes.rename(columns={"para_value": "mode"})
    if not modes.empty:
        PostgresCRUD.upsert("dm_recipe_info", "dm_recipe_info_pkey", modes)


def parse_recipe(equip: Equipment, parser: Parser, dm_dir: Path) -> None:
    """장비 별 Recipe 파싱 함수

    Args:
        equip (Equipment): 장비 정보
        parser (Parser): Recipe 파서
        dm_dir (Path): DM Recipe 폴더
    """
    # 1. Equip 단위 파싱할 Recipe 조회
    # queue = PostgresCRUD.select_query(
    #     """
    #     SELECT dri.recipe_key, dri.backup_file_path
    #     FROM dm_recipe_info dri
    #     WHERE dri.equip = %s
    #         AND dri.is_remove = false
    #     """,
    #     (equip.name,),
    # )
    queue = PostgresCRUD.select_query(
        """
        SELECT dri.recipe_key, dri.backup_file_path
        FROM dm_recipe_info dri
        WHERE dri.equip = %s
            AND dri.is_remove = false
            AND dri.status IN ('new', 'update')
        """,
        (equip.name,),
    )
    # 2. Recipe 파싱
    result = parse_queue(queue, parser, dm_dir)
    # 3. Database 업데이트
    update_database(result)


class RecipeParser:
    """큐에 수집된 msr 폴더 처리 클래스"""

    def __init__(self, equipments: list[str], dm_dir: Path) -> None:
        self.equipments = equipments
        self.dm_dir = dm_dir

    def run(self) -> None:
        """데이터 수집 실행"""
        parser = None
        for eq_info in self.equipments:
            equip = create_equipment(eq_info)
            if not equip.enabled:
                continue
            print(f"-----{equip.name}----")
            match equip.model:
                case "KLA2139":
                    parser = KLA2139Parser()
                case "KLA2365":
                    parser = KLA2365Parser()
                case "PUMA":
                    parser = PumaParser()

            print("Recipe 파싱 시작")
            parse_recipe(equip, parser, self.dm_dir)
            print("Recipe 파싱 종료\n")
