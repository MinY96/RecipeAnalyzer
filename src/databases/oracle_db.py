"""Oracle 11g read-only interface, Thin disabled/Thick required by existing deployment."""

from __future__ import annotations
from contextlib import contextmanager
from threading import Lock
from pathlib import Path
from typing import Any, Mapping, Sequence
import re
import oracledb
import pandas as pd
from src.config import config

_pool = None
_lock = Lock()


def _get_pool():
    """Oracle Thick 모드를 초기화하고 재사용 가능한 연결 풀을 생성한다."""
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                lib_dir = config.ORACLE_CLIENT_DIR
                if lib_dir and not Path(lib_dir).is_dir():
                    raise FileNotFoundError(
                        f"Oracle Client directory missing: {lib_dir}"
                    )
                if oracledb.is_thin_mode():
                    oracledb.init_oracle_client(
                        **({"lib_dir": lib_dir} if lib_dir else {})
                    )
                _pool = oracledb.create_pool(
                    user=config.ORACLE_USER,
                    password=config.ORACLE_PASSWORD,
                    dsn=config.ORACLE_DSN,
                    min=config.ORACLE_POOL_MIN_SIZE,
                    max=config.ORACLE_POOL_MAX_SIZE,
                    increment=config.ORACLE_POOL_INCREMENT,
                    getmode=oracledb.POOL_GETMODE_WAIT,
                    tcp_connect_timeout=config.ORACLE_CONNECT_TIMEOUT,
                )
    return _pool


@contextmanager
def cursor_scope():
    """Oracle 커서를 연결 풀에서 임대하고 작업 종료 시 자동으로 반환한다."""
    with _get_pool().acquire() as conn:
        with conn.cursor() as cursor:
            yield cursor


def close_pool():
    """공유 Oracle 연결 풀을 종료하고 초기화한다."""
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close(force=True)
            _pool = None


def _select_sql(query: str):
    """SQL이 SELECT/WITH 조회문인지 기본 형식을 검증한다.

    Args:
        query: 실행할 SQL 조회문."""
    if not re.match(r"^\s*(SELECT|WITH)\b", query, re.I):
        raise ValueError("SELECT only")
    return query.strip().rstrip(";")


class OracleCRUD:
    @staticmethod
    def select_query(
        query: str, params=None, *, arraysize: int = 1000, prefetchrows: int = 1000
    ):
        """Oracle SELECT 결과를 DataFrame으로 조회한다.

        Args:
            query: 실행할 SQL 조회문.
            params: SQL 바인드 값 또는 Parameter 딕셔너리.
            arraysize: 처리에 필요한 입력값.
            prefetchrows: 처리에 필요한 입력값."""
        with cursor_scope() as cur:
            cur.arraysize = arraysize
            cur.prefetchrows = prefetchrows
            cur.execute(_select_sql(query), params)
            return pd.DataFrame.from_records(
                cur.fetchall(), columns=[d[0].lower() for d in cur.description]
            )

    @staticmethod
    def select_query_by_list(
        query: str, params=None, *, null_value: Any = "NULL", arraysize: int = 1000
    ):
        """Oracle SELECT의 첫 열을 리스트로 조회한다.

        Args:
            query: 실행할 SQL 조회문.
            params: SQL 바인드 값 또는 Parameter 딕셔너리.
            null_value: 처리에 필요한 입력값.
            arraysize: 처리에 필요한 입력값."""
        with cursor_scope() as cur:
            cur.arraysize = arraysize
            cur.execute(_select_sql(query), params)
            return [r[0] if r[0] is not None else null_value for r in cur]

    @staticmethod
    def select_one(query: str, params=None):
        """Oracle SELECT의 첫 번째 행을 딕셔너리로 조회한다.

        Args:
            query: 실행할 SQL 조회문.
            params: SQL 바인드 값 또는 Parameter 딕셔너리."""
        df = OracleCRUD.select_query(query, params)
        return None if df.empty else df.iloc[0].to_dict()

    @staticmethod
    def select_scalar(query: str, params=None, *, default=None):
        """Oracle SELECT의 첫 번째 단일 값을 조회한다.

        Args:
            query: 실행할 SQL 조회문.
            params: SQL 바인드 값 또는 Parameter 딕셔너리.
            default: 조건에 맞는 값이 없을 때 사용할 기본값."""
        with cursor_scope() as cur:
            cur.execute(_select_sql(query), params)
            row = cur.fetchone()
            return row[0] if row else default

    @staticmethod
    def select_query_by_conditions(
        query: str,
        conditions: list[dict],
        *,
        params: Mapping | None = None,
        batch_size: int = 150,
    ):
        """공통 키를 가진 여러 조건을 OR로 묶어 Oracle에서 배치 조회한다.

        Args:
            query: 실행할 SQL 조회문.
            conditions: 공통 키를 가진 조회 조건 딕셔너리 목록.
            params: SQL 바인드 값 또는 Parameter 딕셔너리.
            batch_size: 배치 단위 처리 건수."""
        if not conditions:
            return pd.DataFrame()
        cols = list(conditions[0])
        if not cols or any(set(item) != set(cols) for item in conditions):
            raise ValueError("Inconsistent condition dictionaries")
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]*", col) for col in cols):
            raise ValueError("Invalid condition column")
        rows = list(dict.fromkeys(tuple(item[c] for c in cols) for item in conditions))
        found = []
        result_cols = None
        for start in range(0, len(rows), batch_size):
            chunk = rows[start : start + batch_size]
            predicates = []
            bindings = dict(params or {})
            for i, values in enumerate(chunk):
                conjuncts = []
                for j, (col, value) in enumerate(zip(cols, values)):
                    if value is None:
                        conjuncts.append(f"src.{col} IS NULL")
                    else:
                        key = f"cnd_{i}_{j}"
                        conjuncts.append(f"src.{col}=:{key}")
                        bindings[key] = value
                predicates.append("(" + " AND ".join(conjuncts) + ")")
            statement = (
                "SELECT src.* FROM ("
                + _select_sql(query)
                + ") src WHERE "
                + " OR ".join(predicates)
            )
            with cursor_scope() as cur:
                cur.execute(statement, bindings)
                if result_cols is None:
                    result_cols = [d[0].lower() for d in cur.description]
                found.extend(cur.fetchall())
        return pd.DataFrame.from_records(found, columns=result_cols)
