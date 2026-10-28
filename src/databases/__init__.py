from .oracle_db import OracleCRUD, close_pool as close_oracle_pool
from .postgre_db import PostgresCRUD, close_pool as close_postgres_pool

__all__ = ["OracleCRUD", "PostgresCRUD", "close_pools"]


def close_pools():
    """Oracle과 PostgreSQL 연결 풀을 안전하게 종료한다."""
    close_postgres_pool()
    close_oracle_pool()
