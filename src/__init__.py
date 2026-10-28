"""DM RecipeAnalyzer factories."""

from pathlib import Path
from .config.config import DATA_BASE_DIR, load_equipment_config
from .collectors.ftp.collector import Collector
from .workers.dm_recipe.worker import RecipeParser


def _equipments():
    """YAML에 등록된 장비 정보를 불러와 수집·파싱기에 전달한다."""
    return load_equipment_config(Path(__file__).parent / "config" / "equipments.yaml")


def create_collector() -> Collector:
    """장비 설정과 백업 기준 경로로 FTP 수집기 인스턴스를 생성한다.

    Returns:
        Collector: 처리 결과를 반환한다."""
    DATA_BASE_DIR.mkdir(parents=True, exist_ok=True)
    return Collector(equipments=_equipments(), dm_dir=DATA_BASE_DIR)


def create_recipe_parser() -> RecipeParser:
    """장비 설정과 백업 경로로 Recipe 파싱 작업기를 생성한다.

    Returns:
        RecipeParser: 처리 결과를 반환한다."""
    DATA_BASE_DIR.mkdir(parents=True, exist_ok=True)
    return RecipeParser(equipments=_equipments(), dm_dir=DATA_BASE_DIR)
