from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
import pandas as pd
import pytest
from src.collectors.ftp import collector as mod
from src.domain.entities.equipment import Equipment, FTPConnectionInfo


def equip():
    """equip 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다."""
    return Equipment(
        "SMDI01",
        "FAB1",
        "DM",
        "KLA2139",
        FTPConnectionInfo("localhost", "u", "p", 21, "cp949", 30, "/remote"),
    )


def test_lookup_and_string_edit_time(tmp_path):
    """test_lookup_and_string_edit_time 사례에서 기대되는 수집·파싱 동작을 검증한다.

    Args:
        tmp_path: 처리에 필요한 입력값."""
    records = pd.DataFrame(
        [{"recipe_key": "SMDI01-A301_TEST", "dt_edit_time": "2025-10-01 15:25"}]
    )
    lookup = mod.build_equipment_lookup(records)
    old = tmp_path / "ins.dat"
    old.write_text("x")
    assert mod.is_file_update(lookup, "SMDI01-A301_TEST", "2025-10-02 11:00", old) == (
        True,
        "update",
    )
    assert mod.is_file_update(lookup, "SMDI01-A301_TEST", "2025-10-01 14:00", old) == (
        False,
        None,
    )
    assert mod.is_file_update(lookup, "SMDI01-A302_TEST", "2025-10-01 14:00", old) == (
        True,
        "new",
    )
    assert len(lookup) == 1


def test_flush_999_and_last_one(monkeypatch):
    """test_flush_999_and_last_one 사례에서 기대되는 수집·파싱 동작을 검증한다.

    Args:
        monkeypatch: 처리에 필요한 입력값."""
    sizes = []
    monkeypatch.setattr(mod, "join_mes_info", lambda records: pd.DataFrame(records))
    monkeypatch.setattr(
        mod, "update_database", lambda e, data, *args, **kw: sizes.append(len(data))
    )
    state = mod.CollectionState(equip())
    for i in range(1000):
        state.append({"recipe_key": f"key_{i}", "status": "new"})
    assert sizes == [999]
    assert len(state.upserts) == 1
    state.flush()
    assert sizes == [999, 1] and state.new_cnt == 1000


def test_failed_flush_keeps_buffer(monkeypatch):
    """test_failed_flush_keeps_buffer 사례에서 기대되는 수집·파싱 동작을 검증한다.

    Args:
        monkeypatch: 처리에 필요한 입력값."""
    monkeypatch.setattr(mod, "join_mes_info", lambda records: pd.DataFrame(records))

    def fail(*args, **kwargs):
        """fail 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            *args: 처리에 필요한 입력값.
            **kwargs: 처리에 필요한 입력값."""
        raise RuntimeError("db failed")

    monkeypatch.setattr(mod, "update_database", fail)
    state = mod.CollectionState(equip(), batch_size=1)
    with pytest.raises(RuntimeError):
        state.append({"recipe_key": "x", "status": "new"})
    assert len(state.upserts) == 1
    assert state.new_cnt == 0


def test_mes_ambiguous_metadata_nulls(monkeypatch):
    """test_mes_ambiguous_metadata_nulls 사례에서 기대되는 수집·파싱 동작을 검증한다.

    Args:
        monkeypatch: 처리에 필요한 입력값."""
    monkeypatch.setattr(
        mod.OracleCRUD,
        "select_query",
        staticmethod(
            lambda *a, **k: pd.DataFrame(
                [
                    {
                        "product": "A301_A",
                        "device": "A301",
                        "device_name": "n",
                        "tech_prod": "X",
                        "tech": "T1",
                        "prod_group": "G",
                    },
                    {
                        "product": "A301_B",
                        "device": "A301",
                        "device_name": "n",
                        "tech_prod": "Y",
                        "tech": "T2",
                        "prod_group": "G",
                    },
                ]
            )
        ),
    )
    recipe = {col: None for col in mod.COLS}
    recipe["device"] = "A301"
    frame = mod.join_mes_info([recipe])
    assert len(frame) == 1
    assert pd.isna(frame.iloc[0]["tech"])


def test_mass_deletion_blocked(monkeypatch, tmp_path):
    """test_mass_deletion_blocked 사례에서 기대되는 수집·파싱 동작을 검증한다.

    Args:
        monkeypatch: 처리에 필요한 입력값.
        tmp_path: 처리에 필요한 입력값."""
    equip_value = equip()
    original = pd.DataFrame(
        {
            "recipe_key": [f"r{i}" for i in range(10)],
            "dt_edit_time": ["2025-01-01 00:00"] * 10,
        }
    )
    monkeypatch.setattr(
        mod.PostgresCRUD, "select_query", staticmethod(lambda *args: original)
    )

    def fake_collect(eq, eqdir, lookup, state):
        """fake_collect 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            eq: 처리에 필요한 입력값.
            eqdir: 처리에 필요한 입력값.
            lookup: 기존 Recipe를 recipe_key로 조회하는 딕셔너리.
            state: 장비 수집 상태 및 저장 버퍼."""
        state.seen_keys.update(["r1", "r2"])

    monkeypatch.setattr(mod, "collect_kla2139", fake_collect)
    with pytest.raises(RuntimeError, match="Removal ratio"):
        mod.collect_equip_data(equip_value, tmp_path)


def test_scan_error_does_not_run_removal(monkeypatch, tmp_path):
    """test_scan_error_does_not_run_removal 사례에서 기대되는 수집·파싱 동작을 검증한다.

    Args:
        monkeypatch: 처리에 필요한 입력값.
        tmp_path: 처리에 필요한 입력값."""
    previous = pd.DataFrame(
        {"recipe_key": ["A301", "A302"], "dt_edit_time": ["2026-01-01 00:00"] * 2}
    )
    monkeypatch.setattr(
        mod.PostgresCRUD, "select_query", staticmethod(lambda *a, **k: previous)
    )

    def failure(eq, eqdir, lookup, state):
        """failure 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            eq: 처리에 필요한 입력값.
            eqdir: 처리에 필요한 입력값.
            lookup: 기존 Recipe를 recipe_key로 조회하는 딕셔너리.
            state: 장비 수집 상태 및 저장 버퍼."""
        state.seen_keys.add("A301")
        raise ConnectionError("FTP LIST failed for remaining folder")

    monkeypatch.setattr(mod, "collect_kla2139", failure)
    committed = []
    monkeypatch.setattr(mod, "update_database", lambda *a, **k: committed.append(1))
    with pytest.raises(ConnectionError):
        mod.collect_equip_data(equip(), tmp_path)
    assert not committed


def test_date_format_preserved_as_string():
    """test_date_format_preserved_as_string 사례에서 기대되는 수집·파싱 동작을 검증한다."""
    assert (
        mod.parse_edit_time("drw-rw-rw- 1 user group 0 Aug 30 2012 A301_RECIPE")
        == "2012-08-30 00:00"
    )
