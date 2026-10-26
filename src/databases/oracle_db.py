"""Oracle 11g database helper, SELECT only. Requires python-oracledb Thick mode.

All SQL text is trusted application SQL. Data values are always bound.
Use an Oracle account restricted to SELECT privileges for enforceable read-only access.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any, Iterator, Mapping, Sequence
import re

import oracledb
import pandas as pd

from src.config.config import (
    ORACLE_CLIENT_DIR,
    ORACLE_CONNECT_TIMEOUT,
    ORACLE_USER,
    ORACLE_PASSWORD,
    ORACLE_DSN,
    ORACLE_POOL_MAX_SIZE,
    ORACLE_POOL_MIN_SIZE,
    ORACLE_POOL_INCREMENT,
)

Params = Mapping[str, Any] | Sequence[Any] | None
_pool: oracledb.ConnectionPool | None = None
_pool_lock = Lock()
_identifier_pattern = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*$")


def _get_pool() -> oracledb.ConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                lib_dir = ORACLE_CLIENT_DIR
                if lib_dir and not Path(lib_dir).is_dir():
                    raise FileNotFoundError(
                        f"Oracle Client directory not found: {lib_dir}"
                    )
                # Thick mode must be initialized BEFORE the first Oracle connection.
                if not oracledb.is_thin_mode():
                    pass  # Another module has already initialized Thick mode.
                else:
                    oracledb.init_oracle_client(
                        **({"lib_dir": lib_dir} if lib_dir else {})
                    )
                _pool = oracledb.create_pool(
                    user=ORACLE_USER,
                    password=ORACLE_PASSWORD,
                    dsn=ORACLE_DSN,
                    min=ORACLE_POOL_MIN_SIZE,
                    max=ORACLE_POOL_MAX_SIZE,
                    increment=ORACLE_POOL_INCREMENT,
                    getmode=oracledb.POOL_GETMODE_WAIT,
                    tcp_connect_timeout=ORACLE_CONNECT_TIMEOUT,
                )
    return _pool


@contextmanager
def cursor_scope() -> Iterator[Any]:
    with _get_pool().acquire() as connection:
        with connection.cursor() as cursor:
            yield cursor


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close(force=True)
            _pool = None


def _select_sql(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty SELECT SQL string")
    query = query.strip()
    if query.endswith(";"):
        query = query[:-1].rstrip()
    if not re.match(r"^(?:SELECT|WITH)\b", query, re.IGNORECASE):
        raise ValueError(
            "Only SELECT/WITH queries are accepted (not a security boundary)"
        )
    return query


def _condition_rows(
    conditions: list[dict[str, Any]],
) -> tuple[list[str], list[tuple[Any, ...]]]:
    if not isinstance(conditions, list):
        raise TypeError("conditions must be list[dict]")
    if not conditions:
        return [], []
    if not isinstance(conditions[0], dict) or not conditions[0]:
        raise ValueError("conditions must contain non-empty dictionaries")
    keys = list(conditions[0])
    if any(
        not isinstance(k, str) or not _identifier_pattern.fullmatch(k) for k in keys
    ):
        raise ValueError(
            "Oracle condition columns must be simple, unquoted SQL identifiers"
        )
    if len({k.upper() for k in keys}) != len(keys):
        raise ValueError("Oracle condition columns must be unique ignoring case")
    result = []
    seen = set()
    for condition in conditions:
        if not isinstance(condition, dict) or set(condition) != set(keys):
            raise ValueError("Every condition must contain exactly the same keys")
        values = tuple(condition[k] for k in keys)
        try:
            if values in seen:
                continue
            seen.add(values)
        except TypeError as exc:
            raise TypeError("Condition values must be hashable scalar values") from exc
        result.append(values)
    return keys, result


class OracleCRUD:
    """Oracle SELECT operations returning pandas DataFrames, lists, dicts, or scalars."""

    @staticmethod
    def select_query(
        query: str,
        params: Params = None,
        *,
        arraysize: int = 1000,
        prefetchrows: int = 1000,
    ) -> pd.DataFrame:
        sql = _select_sql(query)
        with cursor_scope() as cursor:
            cursor.arraysize = arraysize
            cursor.prefetchrows = prefetchrows
            cursor.execute(sql, params)
            columns = [desc[0].lower() for desc in cursor.description]
            rows = cursor.fetchall()
        return pd.DataFrame.from_records(rows, columns=columns)

    @staticmethod
    def select_query_by_list(
        query: str,
        params: Params = None,
        *,
        null_value: Any = "NULL",
        arraysize: int = 1000,
    ) -> list[Any]:
        sql = _select_sql(query)
        with cursor_scope() as cursor:
            cursor.arraysize = arraysize
            cursor.execute(sql, params)
            return [row[0] if row[0] is not None else null_value for row in cursor]

    @staticmethod
    def select_one(query: str, params: Params = None) -> dict[str, Any] | None:
        sql = _select_sql(query)
        with cursor_scope() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            if row is None:
                return None
            return dict(zip((desc[0].lower() for desc in cursor.description), row))

    @staticmethod
    def select_scalar(query: str, params: Params = None, *, default: Any = None) -> Any:
        sql = _select_sql(query)
        with cursor_scope() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return row[0] if row is not None else default

    @staticmethod
    def select_query_by_conditions(
        query: str,
        conditions: list[dict[str, Any]],
        *,
        params: Mapping[str, Any] | None = None,
        batch_size: int = 150,
    ) -> pd.DataFrame:
        """Fetch base-query rows matching ANY supplied dict (AND inside each dict).

        Each dict uses the same keys. Duplicate condition dicts are ignored.
        NULL conditions match database NULL via IS NULL.
        Large condition lists are processed in independent bounded SELECT batches.

        `query` must project all filter columns using ordinary, unquoted Oracle names.
        `params` holds named bind variables already present in `query`.
        """
        base_query = _select_sql(query)
        if not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        columns, rows = _condition_rows(conditions)
        if not rows:
            return pd.DataFrame()
        if params is not None and not isinstance(params, Mapping):
            raise TypeError("params must be a named-bind dictionary")
        base_params = dict(params or {})
        all_rows: list[tuple[Any, ...]] = []
        result_columns: list[str] | None = None

        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            disjunction: list[str] = []
            bind_params = dict(base_params)
            for i, values in enumerate(batch):
                conjunction: list[str] = []
                for j, (key, value) in enumerate(zip(columns, values)):
                    if value is None:
                        conjunction.append(f"src.{key} IS NULL")
                    else:
                        bind_key = f"cnd_{i}_{j}"
                        if bind_key in bind_params:
                            raise ValueError(
                                f"Reserved bind name collision: {bind_key}"
                            )
                        conjunction.append(f"src.{key} = :{bind_key}")
                        bind_params[bind_key] = value
                disjunction.append("(" + " AND ".join(conjunction) + ")")
            sql = (
                f"SELECT src.* FROM ({base_query}) src WHERE ("
                + " OR ".join(disjunction)
                + ")"
            )
            with cursor_scope() as cursor:
                cursor.arraysize = 1000
                cursor.execute(sql, bind_params)
                if result_columns is None:
                    result_columns = [desc[0].lower() for desc in cursor.description]
                all_rows.extend(cursor.fetchall())
        return pd.DataFrame.from_records(all_rows, columns=result_columns)
