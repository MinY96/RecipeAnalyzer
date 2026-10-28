"""PostgreSQL CRUD, transactionally safe bulk operations, and parse persistence."""

from __future__ import annotations
from contextlib import contextmanager
from threading import Lock
from typing import Any, Iterator, Mapping, Sequence
import re
import pandas as pd
import numpy as np
from psycopg2 import sql
from psycopg2.extras import execute_values
from psycopg2.pool import ThreadedConnectionPool
from src.config import config

Params = Mapping[str, Any] | Sequence[Any] | None
_pool: ThreadedConnectionPool | None = None
_pool_lock = Lock()


def _get_pool():
    """PostgreSQL ThreadedConnectionPool을 지연 초기화한다."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                schema = config.POSTGRES_SCHEMA
                if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
                    raise ValueError("Invalid POSTGRES_SCHEMA")
                _pool = ThreadedConnectionPool(
                    minconn=config.POSTGRES_POOL_MIN_SIZE,
                    maxconn=config.POSTGRES_POOL_MAX_SIZE,
                    host=config.POSTGRES_HOST,
                    port=config.POSTGRES_PORT,
                    database=config.POSTGRES_DATABASE,
                    user=config.POSTGRES_USER,
                    password=config.POSTGRES_PASSWORD,
                    connect_timeout=config.POSTGRES_CONNECT_TIMEOUT,
                    options=f"-c search_path={schema},public",
                )
    return _pool


@contextmanager
def transaction() -> Iterator[tuple[Any, Any]]:
    """커서를 제공하고 성공 시 COMMIT, 오류 시 ROLLBACK한다.

    Returns:
        Iterator[tuple[Any, Any]]: 처리 결과를 반환한다."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cursor:
            yield conn, cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def close_pool() -> None:
    """PostgreSQL 연결 풀을 종료한다.

    Returns:
        None: 처리 결과를 반환한다."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None


def _select_sql(query: str) -> str:
    """SELECT 전용 조회 쿼리의 형식을 확인한다.

    Args:
        query: 실행할 SQL 조회문.

    Returns:
        str: 처리 결과를 반환한다."""
    if not isinstance(query, str) or not re.match(r"^\s*(SELECT|WITH)\b", query, re.I):
        raise ValueError("Expected a SELECT query")
    return query.strip().rstrip(";")


def _table_id(name: str):
    """스키마를 포함한 테이블 이름을 안전한 SQL 식별자로 변환한다.

    Args:
        name: 대상 이름."""
    if not isinstance(name, str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)?", name
    ):
        raise ValueError(f"Invalid table identifier: {name!r}")
    return sql.Identifier(*name.split("."))


def _db_value(value):
    """pandas·numpy 값을 psycopg2가 처리할 수 있는 Python 값으로 변환한다.

    Args:
        value: 변환할 데이터 값."""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.to_pydatetime()
    if isinstance(value, pd.Timedelta):
        return None if pd.isna(value) else value.to_pytimedelta()
    if isinstance(value, np.generic):
        return _db_value(value.item())
    if isinstance(value, (dict, list, tuple, np.ndarray)):
        # Arrays/JSON must be adapted by the caller; never call pd.isna() on vectors.
        return value
    return None if pd.isna(value) else value


def _constraint_key_columns(cursor, name: str, constraint_name: str) -> list[str]:
    """PostgreSQL PRIMARY KEY/UNIQUE 제약조건의 키 컬럼을 조회한다.

    Args:
        cursor: DB 커서.
        name: 대상 이름.
        constraint_name: UPSERT 기준 제약조건 이름.

    Returns:
        list[str]: 처리 결과를 반환한다."""
    cursor.execute(
        """
        SELECT a.attname FROM pg_catalog.pg_constraint c
        JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
        JOIN pg_catalog.pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum
        WHERE c.conrelid=%s::regclass AND c.conname=%s AND c.contype IN ('p','u')
          AND c.condeferrable=false ORDER BY k.ord
    """,
        (name, constraint_name),
    )
    return [row[0] for row in cursor.fetchall()]


def _upsert_on_cursor(
    conn,
    cursor,
    table: str,
    constraint_name: str,
    frame: pd.DataFrame,
    chunk_size: int = 1000,
) -> int:
    """기존 트랜잭션 커서를 이용해 DataFrame을 청크 단위 UPSERT한다.

    Args:
        conn: DB 커넥션.
        cursor: DB 커서.
        table: 대상 PostgreSQL 테이블.
        constraint_name: UPSERT 기준 제약조건 이름.
        frame: 처리 대상 DataFrame.
        chunk_size: 1회 처리할 최대 데이터 건수.

    Returns:
        int: 처리 결과를 반환한다."""
    if frame.empty:
        return 0
    table_id = _table_id(table)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", constraint_name):
        raise ValueError("Invalid constraint identifier")
    columns = list(frame.columns)
    if len(set(columns)) != len(columns) or not all(
        re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", c) for c in columns
    ):
        raise ValueError("Invalid/duplicate dataframe columns")
    keys = _constraint_key_columns(cursor, table_id.as_string(conn), constraint_name)
    if not keys or not set(keys).issubset(columns):
        raise ValueError(
            f"Constraint missing or key mismatch: {table}.{constraint_name}"
        )
    updates = [c for c in columns if c not in keys]
    if updates:
        action = sql.SQL("DO UPDATE SET {}").format(
            sql.SQL(",").join(
                sql.SQL("{}=EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c))
                for c in updates
            )
        )
    else:
        action = sql.SQL("DO NOTHING")
    statement = (
        sql.SQL("INSERT INTO {} ({}) VALUES %s ON CONFLICT ON CONSTRAINT {} {}")
        .format(
            table_id,
            sql.SQL(",").join(map(sql.Identifier, columns)),
            sql.Identifier(constraint_name),
            action,
        )
        .as_string(conn)
    )
    affected = 0
    for start in range(0, len(frame), chunk_size):
        chunk = frame.iloc[start : start + chunk_size]
        complete = chunk[keys].notna().all(axis=1)
        chunk = chunk.loc[~(complete & chunk.duplicated(keys, keep="last"))]
        values = [
            tuple(_db_value(v) for v in row)
            for row in chunk.itertuples(index=False, name=None)
        ]
        if not values:
            continue
        execute_values(cursor, statement, values, page_size=len(values))
        affected += max(cursor.rowcount, 0)
    return affected


class PostgresCRUD:
    @staticmethod
    def select_query(query: str, params: Params = None) -> pd.DataFrame:
        """PostgreSQL 조회 결과를 DataFrame으로 반환한다.

        Args:
            query: 실행할 SQL 조회문.
            params: SQL 바인드 값 또는 Parameter 딕셔너리.

        Returns:
            pd.DataFrame: 처리 결과를 반환한다."""
        with transaction() as (_, cur):
            cur.execute(_select_sql(query), params)
            cols = [desc.name for desc in cur.description]
            rows = cur.fetchall()
        return pd.DataFrame.from_records(rows, columns=cols)

    @staticmethod
    def select_query_by_list(
        query: str, params: Params = None, *, null_value: Any = "NULL"
    ) -> list[Any]:
        """PostgreSQL 조회 결과의 첫 열을 리스트로 반환한다.

        Args:
            query: 실행할 SQL 조회문.
            params: SQL 바인드 값 또는 Parameter 딕셔너리.
            null_value: 처리에 필요한 입력값.

        Returns:
            list[Any]: 처리 결과를 반환한다."""
        with transaction() as (_, cur):
            cur.execute(_select_sql(query), params)
            return [r[0] if r[0] is not None else null_value for r in cur]

    @staticmethod
    def select_one(query: str, params: Params = None) -> dict | None:
        """PostgreSQL에서 첫 행을 딕셔너리 형태로 가져온다.

        Args:
            query: 실행할 SQL 조회문.
            params: SQL 바인드 값 또는 Parameter 딕셔너리.

        Returns:
            dict | None: 처리 결과를 반환한다."""
        with transaction() as (_, cur):
            cur.execute(_select_sql(query), params)
            row = cur.fetchone()
            return dict(zip((d.name for d in cur.description), row)) if row else None

    @staticmethod
    def select_scalar(query: str, params: Params = None, *, default=None):
        """PostgreSQL에서 단일 값을 가져온다.

        Args:
            query: 실행할 SQL 조회문.
            params: SQL 바인드 값 또는 Parameter 딕셔너리.
            default: 조건에 맞는 값이 없을 때 사용할 기본값."""
        with transaction() as (_, cur):
            cur.execute(_select_sql(query), params)
            row = cur.fetchone()
            return row[0] if row else default

    @staticmethod
    def select_query_by_conditions(
        query: str,
        conditions: list[dict],
        *,
        params: Params = None,
        batch_size: int = 1000,
    ) -> pd.DataFrame:
        """여러 조건 딕셔너리를 VALUES 기반 배치 SELECT로 조회한다.

        Args:
            query: 실행할 SQL 조회문.
            conditions: 공통 키를 가진 조회 조건 딕셔너리 목록.
            params: SQL 바인드 값 또는 Parameter 딕셔너리.
            batch_size: 배치 단위 처리 건수.

        Returns:
            pd.DataFrame: 처리 결과를 반환한다."""
        if not conditions:
            return pd.DataFrame()
        keys = list(conditions[0])
        if not keys or any(set(row) != set(keys) for row in conditions):
            raise ValueError("All condition dictionaries must have identical keys")
        if not all(re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", k) for k in keys):
            raise ValueError("Invalid column name")
        rows = list(dict.fromkeys(tuple(d[k] for k in keys) for d in conditions))
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        results = []
        cols = None
        with transaction() as (conn, cur):
            base = (
                cur.mogrify(_select_sql(query), params)
                .decode(conn.encoding)
                .replace("%", "%%")
            )
            for start in range(0, len(rows), batch_size):
                chunk = rows[start : start + batch_size]
                predicate = sql.SQL(" AND ").join(
                    # An all-NULL VALUES column has no useful SQL type. Use IS NULL
                    # instead of a cross-type comparison (e.g. int versus text).
                    (
                        sql.SQL("src.{} IS NULL").format(sql.Identifier(k))
                        if all(row[j] is None for row in chunk)
                        else sql.SQL("src.{} IS NOT DISTINCT FROM cond.{}").format(
                            sql.Identifier(k), sql.Identifier(k)
                        )
                    )
                    for j, k in enumerate(keys)
                )
                q = sql.SQL(
                    "SELECT src.* FROM ({}) src WHERE EXISTS (SELECT 1 FROM (VALUES %s) cond ({}) WHERE {})"
                ).format(
                    sql.SQL(base),
                    sql.SQL(",").join(map(sql.Identifier, keys)),
                    predicate,
                )
                results.extend(
                    execute_values(
                        cur, q.as_string(conn), chunk, fetch=True, page_size=len(chunk)
                    )
                )
                if cols is None:
                    cols = [d.name for d in cur.description]
        return pd.DataFrame.from_records(results, columns=cols)

    @staticmethod
    def upsert(
        table: str,
        constraint_name: str,
        dataframe: pd.DataFrame,
        *,
        chunk_size: int = 1000,
    ) -> int:
        """DataFrame을 지정한 제약조건 기준으로 청크 단위 UPSERT한다.

        Args:
            table: 대상 PostgreSQL 테이블.
            constraint_name: UPSERT 기준 제약조건 이름.
            dataframe: UPSERT 또는 조회용 DataFrame.
            chunk_size: 1회 처리할 최대 데이터 건수.

        Returns:
            int: 처리 결과를 반환한다."""
        if not isinstance(dataframe, pd.DataFrame):
            raise TypeError("dataframe must be DataFrame")
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if dataframe.empty:
            return 0
        with transaction() as (conn, cur):
            return _upsert_on_cursor(
                conn, cur, table, constraint_name, dataframe, chunk_size
            )

    @staticmethod
    def execute(query: str, params: Params = None) -> int:
        """SQL과 바인드 매개변수를 실행하고 영향을 받은 행 수를 반환한다.

        Args:
            query: 실행할 SQL 조회문.
            params: SQL 바인드 값 또는 Parameter 딕셔너리.

        Returns:
            int: 처리 결과를 반환한다."""
        with transaction() as (_, cur):
            cur.execute(query, params)
            return cur.rowcount

    @staticmethod
    def execute_many_values(
        query: str,
        values: Sequence[Sequence[Any]],
        *,
        template: str | None = None,
        page_size: int = 1000,
    ) -> int:
        """여러 VALUES 행을 배치 단위로 실행한다.

        Args:
            query: 실행할 SQL 조회문.
            values: 처리에 필요한 입력값.
            template: 처리에 필요한 입력값.
            page_size: 처리에 필요한 입력값.

        Returns:
            int: 처리 결과를 반환한다."""
        affected = 0
        with transaction() as (_, cur):
            for start in range(0, len(values), page_size):
                batch = values[start : start + page_size]
                execute_values(
                    cur, query, batch, template=template, page_size=len(batch)
                )
                affected += max(cur.rowcount, 0)
        return affected

    @staticmethod
    def start_collection_run(run_id: str, equip_name: str, model: str) -> None:
        """장비 수집 시작을 먼저 DB에 남겨 프로그램 강제 종료도 식별한다.

        Args:
            run_id: 실행을 구분하는 UUID 문자열.
            equip_name: 장비 이름.
            model: 장비 모델.

        Raises:
            Exception: 감사 이력 테이블이 없거나 PostgreSQL 쓰기 실패 시 전파.
        """
        with transaction() as (_, cur):
            cur.execute(
                """
                INSERT INTO dm_collection_run_history
                    (run_id, model, equip, started_at, scan_status)
                VALUES (%s::uuid, %s, %s, NOW(), 'running')
            """,
                (run_id, model, equip_name),
            )

    @staticmethod
    def finish_collection_run(
        run_id: str,
        scan_status: str,
        *,
        new_rcp: int,
        update_rcp: int,
        remove_rcp: int,
        unchanged_rcp: int,
        download_failures: int,
        skipped_missing_file: int,
        skipped_missing_path: int,
        skipped_recursive_listing: int,
        skipped_optional_files: int,
        reconnect_count: int,
        error_message: str | None = None,
    ) -> None:
        """장비 실행 결과를 성공·부분 완료·실패로 확정하고 수치를 저장한다.

        Args:
            run_id: 실행을 구분하는 UUID 문자열.
            scan_status: success, partial, failed 중 하나.
            new_rcp: PostgreSQL UPSERT가 끝난 신규 Recipe 수.
            update_rcp: PostgreSQL UPSERT가 끝난 수정 Recipe 수.
            remove_rcp: 최종 삭제 반영 건수.
            unchanged_rcp: 최종 유지 반영 건수.
            download_failures: 일반 다운로드 오류 수.
            skipped_missing_file: 필수 파일이 없는 Recipe 제외 수.
            skipped_missing_path: 원격 경로가 없는 Recipe 제외 수.
            skipped_recursive_listing: 비정상 재귀 탐색으로 제외한 수.
            skipped_optional_files: RETR 550으로 건너뛴 선택 파일 수.
            reconnect_count: FTP 재연결 성공 횟수.
            error_message: 실패 원인의 문자열 요약.

        Raises:
            ValueError: 유효하지 않은 상태값인 경우.
            Exception: 감사 이력 DB 저장 실패 시 전파.
        """
        if scan_status not in ("success", "partial", "failed"):
            raise ValueError(f"Unsupported collection run status: {scan_status}")
        with transaction() as (_, cur):
            cur.execute(
                """
                UPDATE dm_collection_run_history
                SET finished_at=NOW(), scan_status=%s,
                    new_rcp=%s, update_rcp=%s, remove_rcp=%s,
                    unchanged_rcp=%s, download_failures=%s,
                    skipped_missing_file=%s, skipped_missing_path=%s,
                    skipped_recursive_listing=%s, skipped_optional_files=%s,
                    reconnect_count=%s, error_message=%s
                WHERE run_id=%s::uuid AND scan_status='running'
            """,
                (
                    scan_status,
                    new_rcp,
                    update_rcp,
                    remove_rcp,
                    unchanged_rcp,
                    download_failures,
                    skipped_missing_file,
                    skipped_missing_path,
                    skipped_recursive_listing,
                    skipped_optional_files,
                    reconnect_count,
                    error_message[:2000] if error_message else None,
                    run_id,
                ),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"수집 이력 확정 대상이 1건이 아님: {run_id}")

    @staticmethod
    def finalize_equipment_scan(
        equip_name: str,
        remove_keys: list[str],
        unchanged_keys: list[str],
        history: pd.DataFrame,
        *,
        chunk_size: int = 999,
    ) -> None:
        """삭제 표시·유지 상태 갱신·장비 이력 저장을 단일 트랜잭션으로 처리한다.

        Args:
            equip_name: 처리에 필요한 입력값.
            remove_keys: 삭제 판정 대상 Recipe 키 목록.
            unchanged_keys: 처리에 필요한 입력값.
            history: 처리에 필요한 입력값.
            chunk_size: 1회 처리할 최대 데이터 건수.

        Returns:
            None: 처리 결과를 반환한다."""
        with transaction() as (conn, cur):
            for offset in range(0, len(remove_keys), chunk_size):
                cur.execute(
                    """UPDATE dm_recipe_info SET is_remove=true,status='remove'
                    WHERE equip=%s AND recipe_key=ANY(%s)""",
                    (equip_name, remove_keys[offset : offset + chunk_size]),
                )
            for offset in range(0, len(unchanged_keys), chunk_size):
                cur.execute(
                    """UPDATE dm_recipe_info SET status=NULL
                    WHERE equip=%s AND recipe_key=ANY(%s)""",
                    (equip_name, unchanged_keys[offset : offset + chunk_size]),
                )
            _upsert_on_cursor(
                conn, cur, "dm_update_history", "dm_update_history_pkey", history
            )

    @staticmethod
    def replace_recipe_parameters(results: list[tuple[str, dict]]) -> int:
        """기존 Parameter를 교체하고 mode와 파싱 완료 상태를 원자적으로 저장한다.

        Args:
            results: Recipe별 파싱 결과 목록.

        Returns:
            int: 처리 결과를 반환한다."""
        if not results:
            return 0
        keys = [key for key, _ in results]
        rows = [
            {"recipe_key": key, "para_nm": name, "para_value": value, "matching": None}
            for key, params in results
            for name, value in params.items()
            if value is not None
        ]
        modes = [
            (params.get("mode"), key) for key, params in results if "mode" in params
        ]
        with transaction() as (conn, cur):
            cur.execute(
                "DELETE FROM dm_recipe_para WHERE recipe_key = ANY(%s)", (keys,)
            )
            if rows:
                _upsert_on_cursor(
                    conn, cur, "dm_recipe_para", "recipe_para_pk", pd.DataFrame(rows)
                )
            if modes:
                cur.executemany(
                    "UPDATE dm_recipe_info SET mode = %s WHERE recipe_key = %s", modes
                )
            cur.execute(
                """
                UPDATE dm_recipe_info
                SET parse_status='success', parse_error=NULL
                WHERE recipe_key=ANY(%s)
            """,
                (keys,),
            )
        return len(keys)

    @staticmethod
    def mark_parse_failure(recipe_key: str, message: str) -> None:
        """해당 Recipe의 파싱 상태를 failed로 변경하고 오류 내용을 기록한다.

        Args:
            recipe_key: Recipe 기본키.
            message: 처리에 필요한 입력값.

        Returns:
            None: 처리 결과를 반환한다."""
        PostgresCRUD.execute(
            """
            UPDATE dm_recipe_info
            SET parse_status='failed', parse_error=%s
            WHERE recipe_key=%s
        """,
            (message[:2000], recipe_key),
        )
