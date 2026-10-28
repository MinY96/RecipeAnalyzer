"""V3.2 FTP 550 재확인·연결 복구·장비별 수집 이력 단위 테스트."""

from ftplib import error_perm
from pathlib import Path

import pandas as pd
import pytest

from src.collectors.ftp import client
from src.collectors.ftp import collector as mod
from src.domain.entities.equipment import Equipment, FTPConnectionInfo


def equipment(model="KLA2139"):
    """테스트용 SMDI06/PUMA FTP 설정을 생성한다."""
    return Equipment(
        "SMDI06",
        "FAB1",
        "DM",
        model,
        FTPConnectionInfo("server", "u", "pw", 21, "cp949", 30, "/remote"),
    )


class RecipeFTP:
    """LIST·RETR 550, CWD 복귀를 모의하는 FTP 서버."""

    def __init__(self, missing="optional", message="550 No such file or directory"):
        """누락 대상 파일과 FTP 응답 메시지를 지정한다."""
        self.stack = []
        self.missing = missing
        self.message = message
        self.retrs = {}

    def pwd(self):
        """원격 현재 디렉터리를 반환한다."""
        return "/" + "/".join(self.stack)

    def cwd(self, path):
        """원격 하위 또는 부모 디렉터리로 이동한다."""
        if path == "..":
            self.stack.pop()
        else:
            self.stack.append(path)

    def retrlines(self, cmd, cb):
        """필수 ins.dat 및 선택 lowds.img를 포함한 목록을 반환한다."""
        assert cmd == "LIST"
        for name in ("ins.dat", "lowds.img"):
            cb(f"-rw-rw-rw- 1 user group 24 Aug 30 2012 {name}")

    def retrbinary(self, command, callback):
        """지정된 파일의 RETR 550 오류 또는 정상 데이터를 모의한다."""
        name = command.split(" ", 1)[1]
        self.retrs[name] = self.retrs.get(name, 0) + 1
        if (
            (self.missing == "optional" and name == "lowds.img")
            or (self.missing == "required" and name == "ins.dat")
            or self.missing == "both"
        ):
            raise error_perm(self.message)
        callback(b"valid recipe contents")


def test_retr_550_optional_file_skipped_but_recipe_committed(tmp_path):
    """lowds.img만 RETR 550이면 2회 확인 후 제외하고 ins.dat는 저장한다."""
    ftp = RecipeFTP(missing="optional")
    dest = tmp_path / "A301_TEST"
    skipped = client.safe_download_recipe(
        ftp, "A301_TEST", dest, "ins.dat", expected_parent="/"
    )
    assert skipped == 1
    assert (dest / "ins.dat").read_bytes() == b"valid recipe contents"
    assert not (dest / "lowds.img").exists()
    assert ftp.retrs["lowds.img"] == 2
    assert ftp.pwd() == "/"


def test_retr_550_required_file_skipped_and_backup_preserved(tmp_path):
    """ins.dat RETR 550 두 번이면 Recipe는 제외하고 기존 백업을 보존한다."""
    ftp = RecipeFTP(missing="required")
    dest = tmp_path / "A301_TEST"
    dest.mkdir()
    (dest / "ins.dat").write_bytes(b"original")
    with pytest.raises(client.FTPRequiredFileMissing, match="RETR 550"):
        client.safe_download_recipe(
            ftp, "A301_TEST", dest, "ins.dat", expected_parent="/"
        )
    assert (dest / "ins.dat").read_bytes() == b"original"
    assert ftp.retrs["ins.dat"] == 2
    assert ftp.pwd() == "/"


def test_retr_550_permission_denied_never_treated_as_missing(tmp_path):
    """FTP 550 권한 오류는 파일 없음으로 무시하지 않는다."""
    ftp = RecipeFTP(missing="required", message="550 Permission denied")
    with pytest.raises(error_perm, match="Permission denied"):
        client.safe_download_recipe(
            ftp, "A301_TEST", tmp_path / "A301_TEST", "ins.dat", expected_parent="/"
        )
    assert ftp.retrs["ins.dat"] == 1


def test_scan_recipe_propagates_timeout_instead_of_counting_failure(
    monkeypatch, tmp_path
):
    """FTP 타임아웃은 연속 다운로드 실패 건수에 포함하지 않고 재연결 대상으로 보낸다."""

    def raise_timeout(*args, **kwargs):
        """타임아웃을 강제로 발생시킨다."""
        raise TimeoutError("timed out")

    monkeypatch.setattr(mod, "safe_download_recipe", raise_timeout)
    state = mod.CollectionState(equipment())
    entry = client.FTPEntry(
        "A301_TEST", True, "drw-rw-rw- 1 user group 0 Aug 30 2012 A301_TEST"
    )
    with pytest.raises(TimeoutError):
        mod._scan_recipe(
            object(),
            entry,
            state.equip,
            tmp_path,
            {},
            state,
            "ins.dat",
            "A301",
            "TEST",
            [],
            "/root",
        )
    assert state.download_failures == 0
    assert state.consecutive_download_failures == 0


def test_kla2139_recovers_timeout_and_resumes_without_duplicate(monkeypatch, tmp_path):
    """중간 Recipe 타임아웃 후 재연결해 미완료 Recipe부터 계속 수집한다."""

    class Session:
        """접속 횟수를 식별하는 모의 세션."""

        def __init__(self, identifier):
            """세션 번호를 기록한다."""
            self.identifier = identifier
            self.closed = False

        def pwd(self):
            """현재 Recipe 루트 경로를 반환한다."""
            return "/root"

        def close(self):
            """연결을 닫는다."""
            self.closed = True

    sessions = []

    def connect(_):
        """새 FTP 세션을 구성한다."""
        ftp = Session(len(sessions))
        sessions.append(ftp)
        return ftp

    entries = [
        client.FTPEntry(
            f"A30{i}_TEST", True, f"drw-rw-rw- 1 user group 0 Aug 30 2012 A30{i}_TEST"
        )
        for i in range(3)
    ]
    monkeypatch.setattr(mod, "_connect_kla2139", connect)
    monkeypatch.setattr(mod, "list_entries", lambda ftp: entries)
    monkeypatch.setenv("FTP_RECONNECT_ATTEMPTS", "2")
    monkeypatch.setenv("FTP_RECONNECT_DELAY_SECONDS", "0")
    calls = []

    def scan(ftp, entry, equip, equip_dir, lookup, state, *args, **kwargs):
        """2번째 항목에서 한 번 타임아웃을 발생시키고 완료 키를 기록한다."""
        calls.append((ftp.identifier, entry.name))
        if entry.name == "A301_TEST" and ftp.identifier == 0:
            state.seen_keys.add(f"{equip.name}-{entry.name}")
            raise TimeoutError("timed out")
        key = f"{equip.name}-{entry.name}"
        state.seen_keys.add(key)
        state.completed_keys.add(key)

    monkeypatch.setattr(mod, "_scan_recipe", scan)
    state = mod.CollectionState(equipment())
    mod.collect_kla2139(state.equip, tmp_path, {}, state)
    assert calls == [
        (0, "A300_TEST"),
        (0, "A301_TEST"),
        (1, "A301_TEST"),
        (1, "A302_TEST"),
    ]
    assert len(state.completed_keys) == 3
    assert state.reconnect_count == 1
    assert state.download_failures == 0
    assert all(ftp.closed for ftp in sessions)


def test_nested_device_recovers_timeout(monkeypatch, tmp_path):
    """KLA2365/PUMA 탐색 오류 후 이전 완료 Recipe를 건너뛰고 Device를 재탐색한다."""

    class Session:
        """FTP 접속 번호를 보관하는 모의 세션."""

        def __init__(self, ident):
            """세션 식별자를 지정한다."""
            self.ident = ident

        def close(self):
            """FTP 연결 종료를 모의한다."""
            return None

    sessions = []

    def connect(_):
        """접속마다 새로운 세션을 반환한다."""
        obj = Session(len(sessions))
        sessions.append(obj)
        return obj

    monkeypatch.setattr(mod, "_connect_nested", connect)
    monkeypatch.setattr(
        mod,
        "list_entries",
        lambda ftp: [
            client.FTPEntry("A301", True, "drw-rw-rw- 1 user group 0 Aug 30 2012 A301")
        ],
    )
    calls = []

    def device_scan(ftp, equip, equip_dir, lookup, state, device):
        """첫 시도에서 네트워크 타임아웃을 발생시킨다."""
        calls.append((ftp.ident, device))
        if ftp.ident == 0:
            state.seen_keys.add(f"{equip.name}-A301_FOO")
            raise TimeoutError("timed out")
        state.completed_keys.add(f"{equip.name}-A301_FOO")

    monkeypatch.setattr(mod, "_scan_nested_device", device_scan)
    monkeypatch.setenv("FTP_RECONNECT_ATTEMPTS", "2")
    monkeypatch.setenv("FTP_RECONNECT_DELAY_SECONDS", "0")
    state = mod.CollectionState(equipment("KLA2365"))
    mod.collect_kla2365(state.equip, tmp_path, {}, state)
    assert calls == [(0, "A301"), (1, "A301")]
    assert state.reconnect_count == 1


def test_collection_run_partial_history_on_timeout(monkeypatch, tmp_path):
    """FTP 중단 후 이미 저장한 건은 유지하면서 부분 완료 이력을 남긴다."""
    begins, ends = [], []
    monkeypatch.setattr(
        mod.PostgresCRUD,
        "start_collection_run",
        staticmethod(lambda *args: begins.append(args)),
    )
    monkeypatch.setattr(
        mod.PostgresCRUD,
        "finish_collection_run",
        staticmethod(lambda *args, **kwargs: ends.append((args, kwargs))),
    )
    monkeypatch.setattr(
        mod.PostgresCRUD, "select_query", staticmethod(lambda *args: pd.DataFrame())
    )

    def partial(equip, equip_dir, lookup, state):
        """일부 성공 후 타임아웃을 발생시킨다."""
        state.seen_keys.add(f"{equip.name}-A301_TEST")
        state.new_cnt = 999
        raise TimeoutError("timed out")

    monkeypatch.setattr(mod, "collect_kla2139", partial)
    finalized = []
    monkeypatch.setattr(
        mod, "update_database", lambda *args, **kwargs: finalized.append(1)
    )
    with pytest.raises(TimeoutError):
        mod.collect_equip_data(equipment(), tmp_path)
    assert len(begins) == 1
    assert len(ends) == 1
    assert ends[0][0][1] == "partial"
    assert ends[0][1]["new_rcp"] == 999
    assert ends[0][1]["remove_rcp"] == 0
    assert not finalized


def test_collection_run_success_keeps_original_update_history(monkeypatch, tmp_path):
    """정상 완료는 기존 집계까지 실행하고 별도의 성공 실행 이력도 남긴다."""
    ends = []
    monkeypatch.setattr(
        mod.PostgresCRUD, "start_collection_run", staticmethod(lambda *args: None)
    )
    monkeypatch.setattr(
        mod.PostgresCRUD,
        "finish_collection_run",
        staticmethod(lambda *args, **kwargs: ends.append((args, kwargs))),
    )
    monkeypatch.setattr(
        mod.PostgresCRUD, "select_query", staticmethod(lambda *args: pd.DataFrame())
    )

    def success(equip, equip_dir, lookup, state):
        """신규 Recipe 2건 처리한 것으로 설정한다."""
        state.seen_keys.update(f"{equip.name}-A30{i}_TEST" for i in range(2))
        state.new_cnt = 2
        state.skipped_optional_files = 3

    monkeypatch.setattr(mod, "collect_kla2139", success)
    history = []
    monkeypatch.setattr(
        mod, "update_database", lambda *args, **kwargs: history.append(1)
    )
    mod.collect_equip_data(equipment(), tmp_path)
    assert history == [1]
    assert ends[0][0][1] == "success"
    assert ends[0][1]["skipped_optional_files"] == 3


def test_collection_run_failed_before_discovery(monkeypatch, tmp_path):
    """탐색 시작 전 FTP 연결 실패는 failed로 기록한다."""
    ends = []
    monkeypatch.setattr(
        mod.PostgresCRUD, "start_collection_run", staticmethod(lambda *args: None)
    )
    monkeypatch.setattr(
        mod.PostgresCRUD,
        "finish_collection_run",
        staticmethod(lambda *args, **kwargs: ends.append((args, kwargs))),
    )
    monkeypatch.setattr(
        mod.PostgresCRUD, "select_query", staticmethod(lambda *args: pd.DataFrame())
    )

    def fail(*args):
        """FTP 연결 실패를 발생시킨다."""
        raise ConnectionError("FTP unreachable")

    monkeypatch.setattr(mod, "collect_kla2139", fail)
    with pytest.raises(ConnectionError):
        mod.collect_equip_data(equipment(), tmp_path)
    assert ends[0][0][1] == "failed"


def test_root_listing_timeout_reconnect(monkeypatch):
    """최초 장비 Recipe 목록 조회가 끊겨도 새 FTP 연결로 목록을 다시 읽는다."""

    class FTP:
        """첫 번째 LIST가 실패하는 FTP 세션."""

        def __init__(self, number):
            """세션 번호를 설정한다."""
            self.number = number
            self.closed = False

        def close(self):
            """FTP 제어 채널 종료 여부를 저장한다."""
            self.closed = True

    sessions = [FTP(0)]

    def connect(_):
        """새 FTP 세션을 반환한다."""
        result = FTP(len(sessions))
        sessions.append(result)
        return result

    def listing(ftp):
        """첫 연결의 LIST만 TimeoutError로 실패시킨다."""
        if ftp.number == 0:
            raise TimeoutError("timed out")
        return [client.FTPEntry("A301_TEST", True, "")]

    monkeypatch.setattr(mod, "list_entries", listing)
    monkeypatch.setenv("FTP_RECONNECT_ATTEMPTS", "2")
    monkeypatch.setenv("FTP_RECONNECT_DELAY_SECONDS", "0")
    state = mod.CollectionState(equipment())
    result, entries = mod._list_root_with_reconnect(
        sessions[0], state.equip, connect, state
    )
    assert result.number == 1
    assert [e.name for e in entries] == ["A301_TEST"]
    assert sessions[0].closed
    assert state.reconnect_count == 1


def test_transient_local_permission_retry(monkeypatch, tmp_path):
    """Windows의 일시적인 part 파일 접근 거부는 재시도 후 저장한다."""
    original_open = Path.open
    calls = []

    def permission_once(path, *args, **kwargs):
        """part 파일을 처음 열 때만 PermissionError를 발생시킨다."""
        if path.name.endswith(".part"):
            calls.append(path)
            if len(calls) == 1:
                raise PermissionError("File temporarily locked")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", permission_once)
    monkeypatch.setenv("LOCAL_FILE_OPEN_RETRIES", "3")
    monkeypatch.setenv("LOCAL_FILE_OPEN_DELAY_SECONDS", "0")
    dest = tmp_path / "A301_TEST"
    skipped = client.safe_download_recipe(
        RecipeFTP(missing="none"), "A301_TEST", dest, "ins.dat", expected_parent="/"
    )
    assert skipped == 0
    assert len(calls) >= 2
    assert (dest / "ins.dat").is_file()


def test_local_permission_persistent_failure_is_not_skipped(monkeypatch, tmp_path):
    """계속 발생하는 로컬 PermissionError는 다운로드 실패로 남긴다."""
    original_open = Path.open

    def deny_part(path, *args, **kwargs):
        """모든 part 파일 접근을 거부한다."""
        if path.name.endswith(".part"):
            raise PermissionError("Access denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_part)
    monkeypatch.setenv("LOCAL_FILE_OPEN_RETRIES", "2")
    monkeypatch.setenv("LOCAL_FILE_OPEN_DELAY_SECONDS", "0")
    with pytest.raises(PermissionError):
        client.safe_download_recipe(
            RecipeFTP(missing="none"),
            "A301_TEST",
            tmp_path / "A301_TEST",
            "ins.dat",
            expected_parent="/",
        )
