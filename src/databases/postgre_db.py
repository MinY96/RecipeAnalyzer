"""PostgreSQL helper using psycopg2 ThreadedConnectionPool + execute_values."""

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

from src.config.config import (
    POSTGRES_DATABASE,
    POSTGRES_HOST,
    POSTGRES_USER,
    POSTGRES_PORT,
    POSTGRES_PASSWORD,
    POSTGRES_POOL_MAX_SIZE,
    POSTGRES_POOL_MIN_SIZE,
    POSTGRES_CONNECT_TIMEOUT,
)

Params = Mapping[str, Any] | Sequence[Any] | None
_pool: ThreadedConnectionPool | None = None
_pool_lock = Lock()


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadedConnectionPool(
                    minconn=POSTGRES_POOL_MIN_SIZE,
                    maxconn=POSTGRES_POOL_MAX_SIZE,
                    host=POSTGRES_HOST,
                    port=POSTGRES_PORT,
                    database=POSTGRES_DATABASE,
                    user=POSTGRES_USER,
                    password=POSTGRES_PASSWORD,
                    connect_timeout=POSTGRES_CONNECT_TIMEOUT,
                )
    return _pool


@contextmanager
def transaction() -> Iterator[tuple[Any, Any]]:
    pool = _get_pool()
    connection = pool.getconn()
    try:
        with connection.cursor() as cursor:
            yield connection, cursor
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        pool.putconn(connection)


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
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
    if any(not isinstance(key, str) or not key or "\x00" in key for key in keys):
        raise ValueError("Invalid PostgreSQL condition column")
    rows = []
    seen = set()
    for condition in conditions:
        if not isinstance(condition, dict) or set(condition) != set(keys):
            raise ValueError("Every condition must contain exactly the same keys")
        values = tuple(condition[key] for key in keys)
        try:
            if values in seen:
                continue
            seen.add(values)
        except TypeError as exc:
            raise TypeError("Condition values must be hashable scalar values") from exc
        rows.append(values)
    return keys, rows


def _table_identifier(table: str) -> sql.Identifier:
    """Safely quote table or schema.table names (not arbitrary SQL expressions)."""
    if not isinstance(table, str) or not table.strip():
        raise ValueError("table must be a non-empty table name")
    parts = table.split(".")
    if len(parts) not in (1, 2) or any(
        not part or part != part.strip() or "\x00" in part for part in parts
    ):
        raise ValueError("table must be 'table' or 'schema.table'")
    return sql.Identifier(*parts)


def _db_value(value: Any) -> Any:
    """Convert pandas/numpy scalars to psycopg2-adaptable Python values."""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.to_pydatetime()
    if isinstance(value, pd.Timedelta):
        return None if pd.isna(value) else value.to_pytimedelta()
    if isinstance(value, np.generic):
        return _db_value(value.item())
    if isinstance(value, (list, tuple, dict, np.ndarray)):
        # JSON/arrays require explicit adapters, so preserve the original value.
        return value
    if pd.isna(value):
        return None
    return value


class PostgresCRUD:
    """Class name preserves user-requested spelling: PostgesCRUD."""

    @staticmethod
    def select_query(query: str, params: Params = None) -> pd.DataFrame:
        with transaction() as (_, cursor):
            cursor.execute(_select_sql(query), params)
            columns = [desc.name for desc in cursor.description]
            rows = cursor.fetchall()
        return pd.DataFrame.from_records(rows, columns=columns)

    @staticmethod
    def select_query_by_list(
        query: str, params: Params = None, *, null_value: Any = "NULL"
    ) -> list[Any]:
        with transaction() as (_, cursor):
            cursor.execute(_select_sql(query), params)
            return [row[0] if row[0] is not None else null_value for row in cursor]

    @staticmethod
    def select_one(query: str, params: Params = None) -> dict[str, Any] | None:
        with transaction() as (_, cursor):
            cursor.execute(_select_sql(query), params)
            row = cursor.fetchone()
            return (
                dict(zip((desc.name for desc in cursor.description), row))
                if row is not None
                else None
            )

    @staticmethod
    def select_scalar(query: str, params: Params = None, *, default: Any = None) -> Any:
        with transaction() as (_, cursor):
            cursor.execute(_select_sql(query), params)
            row = cursor.fetchone()
            return row[0] if row is not None else default

    @staticmethod
    def select_query_by_conditions(
        query: str,
        conditions: list[dict[str, Any]],
        *,
        params: Params = None,
        batch_size: int = 1000,
    ) -> pd.DataFrame:
        """Bulk composite-key SELECT using execute_values() + correlated EXISTS.

        Values are bound via psycopg2.mogrify; column names quoted with sql.Identifier.
        NULL-safe match is implemented with IS NOT DISTINCT FROM.
        Query is trusted application SQL, not untrusted end-user SQL.
        Each batch is a single SELECT. Identical conditions are deduplicated.
        """
        base_query = _select_sql(query)
        if not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        keys, rows = _condition_rows(conditions)
        if not rows:
            return pd.DataFrame()
        all_rows: list[tuple[Any, ...]] = []
        result_columns: list[str] | None = None

        with transaction() as (connection, cursor):
            # mogrify safely binds optional parameters in the caller's base query.
            rendered_query = cursor.mogrify(base_query, params).decode(
                connection.encoding
            )
            # execute_values parses every percent sign. Escape any literal %
            # remaining in the already-bound base SELECT before inserting VALUES %s.
            rendered_query = rendered_query.replace("%", "%%")
            for start in range(0, len(rows), batch_size):
                batch = rows[start : start + batch_size]
                value_columns = sql.SQL(", ").join(map(sql.Identifier, keys))
                predicates = []
                for j, key in enumerate(keys):
                    lhs = sql.SQL("src.{}").format(sql.Identifier(key))
                    # If this batch only contains NULL in a column, omit its
                    # VALUES comparison to prevent PostgreSQL inferring TEXT
                    # for an entirely-NULL integer/date/boolean column.
                    if all(record[j] is None for record in batch):
                        predicates.append(sql.SQL("{} IS NULL").format(lhs))
                    else:
                        rhs = sql.SQL("cond.{}").format(sql.Identifier(key))
                        predicates.append(
                            sql.SQL("{} IS NOT DISTINCT FROM {}").format(lhs, rhs)
                        )
                statement = sql.SQL("""
                    SELECT src.*
                    FROM ({base_query}) AS src
                    WHERE EXISTS (
                        SELECT 1 FROM (VALUES %s) AS cond ({columns})
                        WHERE {predicates}
                    )
                """).format(
                    base_query=sql.SQL(rendered_query),
                    columns=value_columns,
                    predicates=sql.SQL(" AND ").join(predicates),
                )
                fetched = execute_values(
                    cursor,
                    statement.as_string(connection),
                    batch,
                    page_size=len(batch),
                    fetch=True,
                )
                if result_columns is None:
                    result_columns = [desc.name for desc in cursor.description]
                all_rows.extend(fetched)

        return pd.DataFrame.from_records(all_rows, columns=result_columns)

    @staticmethod
    def upsert(
        table: str,
        constraint_name: str,
        dataframe: pd.DataFrame,
        *,
        chunk_size: int = 1000,
    ) -> int:
        """Chunked DataFrame UPSERT with one atomic PostgreSQL transaction.

        INSERT ... ON CONFLICT ON CONSTRAINT ... DO UPDATE SET non-key columns.
        Returns total number of inserted + updated rows. When only key columns
        are supplied, ON CONFLICT DO NOTHING is used instead.

        Duplicate non-NULL conflict keys within a chunk are collapsed (keep last)
        to avoid PostgreSQL's "cannot affect row a second time" error.
        Across chunks, the later row naturally wins.
        """
        if not isinstance(dataframe, pd.DataFrame):
            raise TypeError("dataframe must be a pandas.DataFrame")
        if (
            not isinstance(chunk_size, int)
            or isinstance(chunk_size, bool)
            or chunk_size < 1
        ):
            raise ValueError("chunk_size must be a positive integer")
        table_id = _table_identifier(table)
        if (
            not isinstance(constraint_name, str)
            or not constraint_name
            or "\x00" in constraint_name
        ):
            raise ValueError("constraint_name must be a non-empty constraint name")
        columns = list(dataframe.columns)
        if not columns or any(
            not isinstance(col, str) or not col or "\x00" in col for col in columns
        ):
            raise ValueError("DataFrame must have valid, non-empty column names")
        if len(columns) != len(set(columns)):
            raise ValueError("DataFrame column names must be unique")
        if dataframe.empty:
            return 0

        with transaction() as (connection, cursor):
            # Verify the supplied constraint belongs to the target table and is
            # a PRIMARY KEY / UNIQUE constraint. Discover its key columns so
            # they will not be needlessly updated during conflicts.
            table_regclass = table_id.as_string(connection)
            cursor.execute(
                """
                SELECT a.attname, c.condeferrable
                FROM pg_catalog.pg_constraint AS c
                JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                    ON TRUE
                JOIN pg_catalog.pg_attribute AS a
                  ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                WHERE c.conrelid = %s::regclass
                  AND c.conname = %s
                  AND c.contype IN ('p', 'u')
                ORDER BY k.ord
                """,
                (table_regclass, constraint_name),
            )
            constraints = cursor.fetchall()
            if not constraints:
                raise ValueError(
                    f"PRIMARY KEY/UNIQUE constraint {constraint_name!r} "
                    f"was not found on {table!r}"
                )
            if any(deferrable for _, deferrable in constraints):
                raise ValueError("ON CONFLICT does not support deferrable constraints")
            key_columns = [name for name, _ in constraints]
            missing = set(key_columns) - set(columns)
            if missing:
                raise ValueError(
                    f"DataFrame is missing constraint columns: {sorted(missing)}"
                )
            updates = [column for column in columns if column not in key_columns]

            if updates:
                assignments = sql.SQL(", ").join(
                    sql.SQL("{} = EXCLUDED.{}").format(
                        sql.Identifier(column), sql.Identifier(column)
                    )
                    for column in updates
                )
                conflict_action = sql.SQL("DO UPDATE SET {}").format(assignments)
            else:
                conflict_action = sql.SQL("DO NOTHING")

            statement = (
                sql.SQL(
                    "INSERT INTO {} ({}) VALUES %s " "ON CONFLICT ON CONSTRAINT {} {}"
                )
                .format(
                    table_id,
                    sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                    sql.Identifier(constraint_name),
                    conflict_action,
                )
                .as_string(connection)
            )

            affected = 0
            for start in range(0, len(dataframe), chunk_size):
                chunk = dataframe.iloc[start : start + chunk_size]
                # PostgreSQL rejects duplicate arbiter keys within one INSERT.
                # Preserve NULL-bearing rows: by default UNIQUE constraints
                # consider NULL values distinct, unlike pandas drop_duplicates.
                complete = chunk[key_columns].notna().all(axis=1)
                duplicated = chunk.duplicated(subset=key_columns, keep="last")
                chunk = chunk.loc[~(complete & duplicated)]
                if chunk.empty:
                    continue
                values = [
                    tuple(_db_value(value) for value in row)
                    for row in chunk.itertuples(index=False, name=None)
                ]
                # Exactly one VALUES page per chunk. The enclosing transaction
                # rolls all chunks back if any page fails.
                execute_values(
                    cursor,
                    statement,
                    values,
                    page_size=len(values),
                )
                if cursor.rowcount >= 0:
                    affected += cursor.rowcount
        return affected

    @staticmethod
    def execute(query: str, params: Params = None) -> int:
        with transaction() as (_, cursor):
            cursor.execute(query, params)
            return cursor.rowcount

    @staticmethod
    def execute_many_values(
        query: str,
        values: Sequence[Sequence[Any]],
        *,
        template: str | None = None,
        page_size: int = 1000,
    ) -> int:
        """INSERT ... VALUES %s; returns sum of affected rows across pages."""
        if not isinstance(page_size, int) or page_size < 1:
            raise ValueError("page_size must be a positive integer")
        rows = list(values)
        if not rows:
            return 0
        affected = 0
        with transaction() as (_, cursor):
            for start in range(0, len(rows), page_size):
                batch = rows[start : start + page_size]
                execute_values(
                    cursor, query, batch, template=template, page_size=len(batch)
                )
                if cursor.rowcount >= 0:
                    affected += cursor.rowcount
        return affected
