"""Isolate unit tests from company database credentials and native Oracle client."""

from __future__ import annotations
import sys
import types
import pytest

# The unit tests exercise the scanner, parser and worker logic but not real DB clients.
# A real connection requires dependencies from requirements.txt and live credentials.
fake_db = types.ModuleType("src.databases")


class FakePostgres:
    @staticmethod
    def select_query(*a, **kw):
        """select_query 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            *a: 처리에 필요한 입력값.
            **kw: 처리에 필요한 입력값."""
        raise RuntimeError("Mock DB before calling")

    @staticmethod
    def upsert(*a, **kw):
        """upsert 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            *a: 처리에 필요한 입력값.
            **kw: 처리에 필요한 입력값."""
        raise RuntimeError("Mock DB before calling")

    @staticmethod
    def execute(*a, **kw):
        """execute 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            *a: 처리에 필요한 입력값.
            **kw: 처리에 필요한 입력값."""
        raise RuntimeError("Mock DB before calling")

    @staticmethod
    def replace_recipe_parameters(*a, **kw):
        """replace_recipe_parameters 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            *a: 처리에 필요한 입력값.
            **kw: 처리에 필요한 입력값."""
        raise RuntimeError("Mock DB before calling")

    @staticmethod
    def mark_parse_failure(*a, **kw):
        """mark_parse_failure 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            *a: 처리에 필요한 입력값.
            **kw: 처리에 필요한 입력값."""
        raise RuntimeError("Mock DB before calling")

    @staticmethod
    def start_collection_run(*args, **kwargs):
        """장비 수집 시작 이력을 모의 기록한다."""
        return None

    @staticmethod
    def finish_collection_run(*args, **kwargs):
        """장비 수집 종료 이력을 모의 기록한다."""
        return None


fake_db.PostgresCRUD = FakePostgres
fake_db.OracleCRUD = type(
    "FakeOracle",
    (),
    {"select_query": staticmethod(lambda *a, **kw: __import__("pandas").DataFrame())},
)
fake_db.close_pools = lambda: None
sys.modules["src.databases"] = fake_db
