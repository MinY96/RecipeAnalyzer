"""측정 장비 데이터 수집"""

from __future__ import annotations

from pathlib import Path

from .config.config import load_equipment_config, DATA_BASE_DIR
from .collectors.ftp.collector import Collector
from .workers.dm_recipe.worker import RecipeParser

DATA_BASE_DIR.mkdir(parents=True, exist_ok=True)

EQUIPMENTS = load_equipment_config(Path("src/config/equipments.yaml"))


def create_collector() -> Collector:
    """측정 장비 데이터 수집기 등록

    Returns:
        Collector: 데이터 수집 클래스
    """

    return Collector(equipments=EQUIPMENTS, dm_dir=DATA_BASE_DIR)


def create_recipe_parser() -> RecipeParser:
    """msr 파일 큐 처리기 등록

    Returns:
        QueueWorker: msr 파일 파싱 및 idb 조인 처리기
    """

    return RecipeParser(equipments=EQUIPMENTS, dm_dir=DATA_BASE_DIR)
