"""DM Recipe FTP collection followed by recoverable parsing."""

from __future__ import annotations
import logging
import os
from src import create_collector, create_recipe_parser
from src.databases import close_pools


def main() -> None:
    """프로그램 로그를 설정하고 전체 장비 수집, Recipe 파싱, DB 연결 종료를 순서대로 실행한다.

    Returns:
        None: 처리 결과를 반환한다."""
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    try:
        collector = create_collector()
        parser = create_recipe_parser()
        # Each equipment failure is isolated by Collector and RecipeParser.
        # Parser always runs even if the previous collecting pass failed.
        collector.run()
        parser.run()
    finally:
        close_pools()


if __name__ == "__main__":
    main()
