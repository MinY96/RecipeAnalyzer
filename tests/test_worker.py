from pathlib import Path
import pandas as pd
from unittest.mock import MagicMock
from src.workers.dm_recipe import worker as mod


def test_empty_parsing_returns_columns_and_no_upsert(monkeypatch, tmp_path):
    """test_empty_parsing_returns_columns_and_no_upsert 사례에서 기대되는 수집·파싱 동작을 검증한다.

    Args:
        monkeypatch: 처리에 필요한 입력값.
        tmp_path: 처리에 필요한 입력값."""
    called = []
    monkeypatch.setattr(
        mod.PostgresCRUD,
        "replace_recipe_parameters",
        staticmethod(lambda items: called.append(items)),
    )

    class Parser:
        def parse(self, path):
            """parse 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

            Args:
                self: 현재 클래스 인스턴스.
                path: 파일 또는 폴더 경로."""
            return {"pixel": "1.0"}

    result = mod.parse_queue(
        pd.DataFrame(columns=["recipe_key", "backup_file_path"]), Parser(), tmp_path
    )
    assert list(result.columns) == mod.PARA_COLS
    mod.update_database(result)
    assert not called


def test_worker_retries_failed_and_marks_success(monkeypatch, tmp_path):
    """test_worker_retries_failed_and_marks_success 사례에서 기대되는 수집·파싱 동작을 검증한다.

    Args:
        monkeypatch: 처리에 필요한 입력값.
        tmp_path: 처리에 필요한 입력값."""
    key = "SMDI01-A301_TEST"
    (tmp_path / "SMDI01").mkdir()
    (tmp_path / "SMDI01" / "ins.dat").write_text("dummy")
    statuses = []
    queried = []

    def select(q, params):
        """select 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            q: 처리에 필요한 입력값.
            params: SQL 바인드 값 또는 Parameter 딕셔너리."""
        queried.append(params)
        if params[1]:
            return pd.DataFrame(columns=["recipe_key", "backup_file_path"])
        return pd.DataFrame(
            [{"recipe_key": key, "backup_file_path": "/SMDI01/ins.dat"}]
        )

    monkeypatch.setattr(mod.PostgresCRUD, "select_query", staticmethod(select))
    monkeypatch.setattr(
        mod.PostgresCRUD,
        "replace_recipe_parameters",
        staticmethod(lambda rows: statuses.extend(rows)),
    )
    monkeypatch.setattr(
        mod.PostgresCRUD, "mark_parse_failure", staticmethod(lambda *x: None)
    )

    class Parser:
        def parse(self, path):
            """parse 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

            Args:
                self: 현재 클래스 인스턴스.
                path: 파일 또는 폴더 경로."""
            return {"pixel": "0.62", "mode": "AUTO"}

    from src.domain.entities.equipment import Equipment, FTPConnectionInfo

    equip = Equipment(
        "SMDI01",
        "FAB1",
        "DM",
        "KLA2139",
        FTPConnectionInfo("h", "u", "p", 21, "cp949", 30, "/remote"),
    )
    mod.parse_recipe(equip, Parser(), tmp_path)
    assert statuses == [(key, {"pixel": "0.62", "mode": "AUTO"})]
    assert len(queried) == 2


def test_missing_file_marks_failed(monkeypatch, tmp_path):
    """test_missing_file_marks_failed 사례에서 기대되는 수집·파싱 동작을 검증한다.

    Args:
        monkeypatch: 처리에 필요한 입력값.
        tmp_path: 처리에 필요한 입력값."""
    marks = []
    monkeypatch.setattr(
        mod.PostgresCRUD,
        "mark_parse_failure",
        staticmethod(lambda *args: marks.append(args)),
    )
    from src.domain.entities.equipment import Equipment, FTPConnectionInfo

    equip = Equipment(
        "SMDI01",
        "FAB1",
        "DM",
        "KLA2139",
        FTPConnectionInfo("h", "u", "p", 21, "cp949", 30, "/remote"),
    )

    def select(q, params):
        """select 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            q: 처리에 필요한 입력값.
            params: SQL 바인드 값 또는 Parameter 딕셔너리."""
        if params[1]:
            return pd.DataFrame(columns=["recipe_key", "backup_file_path"])
        return pd.DataFrame(
            [{"recipe_key": "SMDI01-TEST", "backup_file_path": "/not-exist.dat"}]
        )

    monkeypatch.setattr(mod.PostgresCRUD, "select_query", staticmethod(select))

    class Parser:
        """테스트용 고정 파싱 결과를 반환하는 클래스."""

        def parse(self, *a):
            """테스트에서 사용할 pixel Parameter를 반환한다."""
            return {"pixel": 1}

    mod.parse_recipe(equip, Parser(), tmp_path)
    assert marks[0][0] == "SMDI01-TEST"


def test_no_path_escape(tmp_path):
    """test_no_path_escape 사례에서 기대되는 수집·파싱 동작을 검증한다.

    Args:
        tmp_path: 처리에 필요한 입력값."""
    import pytest

    with pytest.raises(ValueError):
        mod.recipe_path(tmp_path, "../../secret")
