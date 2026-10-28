import pytest
from src.collectors.ftp.collector import parse_edit_time
from src.preprocessing.parsers import KLA2365Parser, PumaParser, KLA2139Parser


def test_unix_ftp_edit_time_format():
    """test_unix_ftp_edit_time_format 사례에서 기대되는 수집·파싱 동작을 검증한다."""
    assert (
        parse_edit_time("drw-rw-rw- 1 user group 0 Aug 30 2012 A301_TEST")
        == "2012-08-30 00:00"
    )


def test_missing_ftp_date_fails_closed():
    """test_missing_ftp_date_fails_closed 사례에서 기대되는 수집·파싱 동작을 검증한다."""
    with pytest.raises(ValueError):
        parse_edit_time("drw-rw-rw- x unexpected date")


def test_kla2139_preserves_numeric_positions():
    """test_kla2139_preserves_numeric_positions 사례에서 기대되는 수집·파싱 동작을 검증한다."""
    assert KLA2139Parser._numeric_values(["5", "", "7", "8"]) == [5, None, 7, 8]


def test_xml_leaf_utilities():
    """test_xml_leaf_utilities 사례에서 기대되는 수집·파싱 동작을 검증한다."""
    assert PumaParser.as_list({"foo": "bar"}) == [{"foo": "bar"}]
    assert KLA2365Parser.four("1  2  3") == ["1", "2", "3", None]
