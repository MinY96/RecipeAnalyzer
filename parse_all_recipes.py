"""V3.2: FTP 수집 없이 DB에 등록된 모든 Recipe의 백업 파일을 재파싱한다.

기본 대상: dm_recipe_info.backup_file_path가 NULL/공백이 아닌 모든 행.
기본적으로 is_remove=true 및 parse_status=success인 행도 다시 파싱한다.
기존 Parameter의 삭제와 삽입, parse_status='success' 변경은 기존 V3.2
PostgresCRUD.replace_recipe_parameters()의 단일 트랜잭션을 재사용한다.

실행 예시:
    python parse_all_recipes.py
    python parse_all_recipes.py --dry-run --limit 10
    python parse_all_recipes.py --equip SMDI01 --batch-size 100
    python parse_all_recipes.py --only-unfinished --active-only
"""

from __future__ import annotations

import argparse
import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config.config import DATA_BASE_DIR, POSTGRES_SCHEMA
from src.databases import PostgresCRUD, close_pools
from src.workers.dm_recipe.worker import PARSERS, parse_one

log = logging.getLogger("bulk_recipe_parser")


@dataclass
class Summary:
    """일괄 파싱 실행에서 조회·성공·실패한 Recipe 수를 기록한다."""

    scanned: int = 0
    success: int = 0
    failed: int = 0
    parse_failed: int = 0
    db_failed: int = 0


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """일괄 파싱 실행 옵션을 해석한다.

    Args:
        argv: 명령행 인자 목록. None이면 실제 실행 인자를 사용한다.

    Returns:
        argparse.Namespace: 검증된 실행 옵션.
    """
    parser = argparse.ArgumentParser(
        description="RecipeAnalyzer V3.2 - DB의 백업 경로가 있는 Recipe 전체 재파싱"
    )
    parser.add_argument(
        "--equip",
        action="append",
        default=[],
        help="지정한 장비만 처리. 여러 장비는 --equip을 반복 지정",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="DB 조회 및 Parameter 저장 배치 크기(기본 200, 최대 999)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="최대 처리 Recipe 수(시험 실행에 사용)"
    )
    parser.add_argument(
        "--start-after", default="", help="지정한 recipe_key 이후부터 순서대로 처리"
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="is_remove=true인 논리적 삭제 Recipe는 제외",
    )
    parser.add_argument(
        "--only-unfinished",
        action="store_true",
        help="parse_status='success'인 Recipe는 건너뛰기(재개용)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="파일 파싱까지 검증하되 PostgreSQL 변경하지 않음",
    )
    parser.add_argument(
        "--data-base-dir",
        type=Path,
        default=DATA_BASE_DIR,
        help="백업 파일 기준 경로(기본 DATA_BASE_DIR 환경변수)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="로그 출력 수준(기본 INFO)",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.batch_size <= 999:
        parser.error("--batch-size는 1부터 999 사이여야 합니다.")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit는 1 이상의 정수여야 합니다.")
    return args


def select_recipe_batch(cursor_key: str, args: argparse.Namespace):
    """백업 경로가 있는 Recipe를 recipe_key 순서로 제한 조회한다.

    Args:
        cursor_key: 이전 배치에서 마지막으로 처리한 recipe_key.
        args: 장비 및 처리 범위 조건이 담긴 실행 옵션.

    Returns:
        pandas.DataFrame: recipe_key, equip, model, backup_file_path 조회 결과.

    Notes:
        상태·장비 필터는 파라미터 바인딩을 사용하며 OFFSET 대신 키셋 방식을 사용한다.
    """
    clauses = [
        "backup_file_path IS NOT NULL",
        "BTRIM(backup_file_path) <> ''",
        "recipe_key > %s",
    ]
    params: list[Any] = [cursor_key]
    if args.equip:
        clauses.append("equip = ANY(%s)")
        params.append(args.equip)
    if args.active_only:
        clauses.append("COALESCE(is_remove, FALSE) = FALSE")
    if args.only_unfinished:
        clauses.append("parse_status IS DISTINCT FROM 'success'")
    sql_query = (
        "SELECT recipe_key, equip, model, backup_file_path "
        "FROM dm_recipe_info WHERE "
        + " AND ".join(clauses)
        + " ORDER BY recipe_key LIMIT %s"
    )
    params.append(args.batch_size)
    return PostgresCRUD.select_query(sql_query, tuple(params))


def parse_item(item: Any, dm_dir: Path, cache: dict[str, Any]) -> dict:
    """DB Recipe 한 건을 모델별 Parser로 파싱한다.

    Args:
        item: recipe_key, model, backup_file_path가 있는 조회 결과 행.
        dm_dir: 백업 파일 최상위 경로.
        cache: 모델별 Parser 인스턴스 재사용을 위한 딕셔너리.

    Returns:
        dict: Parameter 이름과 값을 매핑한 파싱 결과.

    Raises:
        ValueError: 미지원 모델 또는 유효한 Parameter가 없는 경우.
        FileNotFoundError: 백업 파일이 없는 경우.
    """
    model = str(item.model).strip().upper() if item.model is not None else ""
    factory = PARSERS.get(model)
    if factory is None:
        raise ValueError(
            f"지원하지 않는 장비 모델: {item.model!r} (equip={item.equip})"
        )
    if model not in cache:
        cache[model] = factory()
    return parse_one(
        str(item.recipe_key), str(item.backup_file_path), cache[model], dm_dir
    )


def save_success_batch(
    successful: list[tuple[str, dict]],
    *,
    dry_run: bool,
    summary: Summary,
    on_failure,
) -> None:
    """성공적으로 파싱한 Recipe의 Parameter를 배치로 교체한다.

    Args:
        successful: (recipe_key, Parameter 딕셔너리) 목록.
        dry_run: True이면 DB 쓰기 생략.
        summary: 성공·실패 건수 누적 정보.
        on_failure: 개별 Recipe 실패 처리 콜백.

    Notes:
        배치 DB 오류 시 전체 트랜잭션은 기존 CRUD에서 ROLLBACK된다.
        이후 한 건씩 재시도하여 특정 Recipe 문제로 나머지 Recipe까지
        저장되지 않는 상황을 최소화한다. DB 자체가 장애인 경우에는
        실패 상태 기록에서도 예외가 발생하므로 안전하게 실행을 종료한다.
    """
    if not successful:
        return
    if dry_run:
        summary.success += len(successful)
        return
    try:
        PostgresCRUD.replace_recipe_parameters(successful)
        summary.success += len(successful)
        return
    except Exception:
        log.exception(
            "Parameter 배치 저장 실패(%d건). 개별 저장으로 재시도", len(successful)
        )

    for key, params in successful:
        try:
            PostgresCRUD.replace_recipe_parameters([(key, params)])
            summary.success += 1
        except Exception as exc:
            log.exception("[%s] Parameter 개별 저장 실패", key)
            on_failure(key, "db_save", exc)
            summary.db_failed += 1


def run_bulk_parse(args: argparse.Namespace) -> Summary:
    """DB의 전체 대상 Recipe를 제한된 크기로 읽어 순차 재파싱한다.

    Args:
        args: DB 조회 필터, 배치 크기, 백업 경로 및 실행 옵션.

    Returns:
        Summary: 조회 및 파싱 성공/실패 건수.

    Raises:
        Exception: DB 접속 실패 또는 실패 상태 기록을 할 수 없는 경우.
    """
    root = args.data_base_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"DATA_BASE_DIR가 존재하지 않습니다: {root}")

    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    error_csv = log_dir / f"bulk_parse_failed_{datetime.now():%Y%m%d_%H%M%S}.csv"
    summary = Summary()
    cursor_key = args.start_after
    parsers: dict[str, Any] = {}

    log.info(
        "일괄 재파싱 시작: schema=%s, backup=%s, batch=%d, dry_run=%s",
        POSTGRES_SCHEMA,
        root,
        args.batch_size,
        args.dry_run,
    )
    log.info(
        "대상 범위: equip=%s, active_only=%s, only_unfinished=%s",
        args.equip or "전체",
        args.active_only,
        args.only_unfinished,
    )

    with error_csv.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(["recipe_key", "stage", "error"])

        def record_failure(key: str, stage: str, exc: Exception) -> None:
            """실패한 Recipe의 DB 상태와 CSV 오류 이력을 갱신한다."""
            message = f"{type(exc).__name__}: {exc}"
            if not args.dry_run:
                PostgresCRUD.mark_parse_failure(key, message)
            writer.writerow([key, stage, message[:2000]])
            fp.flush()
            summary.failed += 1

        while True:
            queue = select_recipe_batch(cursor_key, args)
            if queue.empty:
                break
            successful: list[tuple[str, dict]] = []
            for item in queue.itertuples(index=False):
                if args.limit is not None and summary.scanned >= args.limit:
                    break
                cursor_key = str(item.recipe_key)
                summary.scanned += 1
                try:
                    params = parse_item(item, root, parsers)
                    successful.append((cursor_key, params))
                except Exception as exc:
                    log.error(
                        "[%s] 파싱 실패: %s: %s", cursor_key, type(exc).__name__, exc
                    )
                    record_failure(cursor_key, "parse", exc)
                    summary.parse_failed += 1

            save_success_batch(
                successful,
                dry_run=args.dry_run,
                summary=summary,
                on_failure=record_failure,
            )
            log.info(
                "진행: 조회=%d, 성공=%d, 실패=%d, 마지막=%s",
                summary.scanned,
                summary.success,
                summary.failed,
                cursor_key,
            )
            if args.limit is not None and summary.scanned >= args.limit:
                break

    log.info(
        "전체 완료: 조회=%d, 성공=%d, 실패=%d (파싱=%d, DB=%d)",
        summary.scanned,
        summary.success,
        summary.failed,
        summary.parse_failed,
        summary.db_failed,
    )
    if summary.failed:
        log.warning("실패 상세 CSV: %s", error_csv)
    else:
        error_csv.unlink(missing_ok=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    """로그와 DB 연결을 관리하며 독립 Recipe 재파싱을 실행한다.

    Args:
        argv: 명령행 인자 목록.

    Returns:
        int: 정상 0, 개별 실패 포함 2, 실행 중 치명적 오류 1.
    """
    args = parse_arguments(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    try:
        result = run_bulk_parse(args)
        return 2 if result.failed else 0
    except Exception:
        log.exception("일괄 재파싱 작업 중단")
        return 1
    finally:
        close_pools()


if __name__ == "__main__":
    raise SystemExit(main())
