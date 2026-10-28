"""V3에서 경로 부재·필수 파일 누락을 실패로 집계하지 않는 회귀 테스트."""

from ftplib import error_perm
from pathlib import Path

import pytest

from src.collectors.ftp import client, collector as mod
from src.domain.entities.equipment import Equipment, FTPConnectionInfo


class FakeListingFTP:
    """구형 FTP의 기본 탐색·파일 목록을 모방하는 가짜 연결."""

    def __init__(self, missing_file=False, missing_path=False, permission=False):
        """__init__ 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            self: 현재 클래스 인스턴스.
            missing_file: 처리에 필요한 입력값.
            missing_path: 처리에 필요한 입력값.
            permission: 처리에 필요한 입력값."""
        self.parts = []
        self.missing_file = missing_file
        self.missing_path = missing_path
        self.permission = permission
        self.retr_count = 0

    def pwd(self):
        """pwd 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            self: 현재 클래스 인스턴스."""
        return "/" + "/".join(self.parts)

    def cwd(self, where):
        """cwd 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            self: 현재 클래스 인스턴스.
            where: 처리에 필요한 입력값."""
        if where == "..":
            self.parts.pop()
        elif where.startswith("/"):
            self.parts = [part for part in where.split("/") if part]
        else:
            self.parts.append(where)

    def retrlines(self, command, callback):
        """retrlines 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            self: 현재 클래스 인스턴스.
            command: 처리에 필요한 입력값.
            callback: 처리에 필요한 입력값."""
        assert command == "LIST"
        if self.permission:
            raise error_perm("550 Permission denied")
        if self.missing_path:
            raise error_perm(
                "550 /d:/KT1025/Usr/Ins8/OD42_666562UM/YV17_1565PAD3: "
                "No such file or directory."
            )
        filename = "other.txt" if self.missing_file else "ins.dat"
        callback(f"-rw-rw-rw- 1 user group 24 Aug 30 2012 {filename}")

    def nlst(self):
        """LIST에서 누락된 필수 파일이 NLST에 존재하는지 확인한다."""
        return ["other.txt"] if self.missing_file else ["ins.dat"]

    def retrbinary(self, command, callback):
        """retrbinary 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            self: 현재 클래스 인스턴스.
            command: 처리에 필요한 입력값.
            callback: 처리에 필요한 입력값."""
        self.retr_count += 1
        callback(b"good-data")


def equipment():
    """KLA2139 SMDI06 테스트에 사용할 장비 설정을 반환한다."""
    return Equipment(
        "SMDI06",
        "FAB1",
        "DM",
        "KLA2139",
        FTPConnectionInfo(
            "server", "user", "pw", 21, "cp949", 30, "d:/KT1025/Usr/Ins8"
        ),
    )


def entry(name="YV17_1565PAD3"):
    """실제 LIST 형식의 Recipe 항목을 생성한다."""
    return client.FTPEntry(
        name,
        True,
        f"drw-rw-rw- 1 user group 0 Aug 30 2012 {name}",
    )


def test_missing_required_is_skipped_without_transfer(tmp_path):
    """ins.dat 없는 폴더는 RETR 이전에 제외하고 기존 백업을 보존한다."""
    ftp = FakeListingFTP(missing_file=True)
    dest = tmp_path / "YV17_1565PAD3"
    dest.mkdir()
    (dest / "ins.dat").write_bytes(b"old-backup")
    with pytest.raises(client.FTPRequiredFileMissing):
        client.safe_download_recipe(
            ftp, "YV17_1565PAD3", dest, "ins.dat", expected_parent="/"
        )
    assert ftp.retr_count == 0
    assert ftp.pwd() == "/"
    assert (dest / "ins.dat").read_bytes() == b"old-backup"


def test_missing_required_does_not_increment_failures(tmp_path):
    """ins.dat 누락은 100회 반복되어도 다운로드 실패로 집계하지 않는다."""
    eq = equipment()
    ftp = FakeListingFTP(missing_file=True)
    state = mod.CollectionState(eq)
    for i in range(100):
        item = entry(f"YA01_{i:08d}")
        mod._scan_recipe(
            ftp, item, eq, tmp_path, {}, state, "ins.dat", "YA01", f"{i:08d}", [], "/"
        )
    assert state.skipped_missing_file == 100
    assert state.download_failures == state.consecutive_download_failures == 0
    assert len(state.seen_keys) == 100
    assert len(state.upserts) == 0
    assert ftp.pwd() == "/"


def test_550_missing_recipe_path_is_skippable(tmp_path):
    """해당 550 경로 부재 오류는 복귀 후 제외할 수 있는 별도 예외로 분류한다."""
    ftp = FakeListingFTP(missing_path=True)
    with pytest.raises(client.FTPRecipeUnavailable, match="550"):
        client.safe_download_recipe(
            ftp, "YV17_1565PAD3", tmp_path / "recipe", "ins.dat", expected_parent="/"
        )
    assert ftp.pwd() == "/"


def test_550_permission_denied_is_real_failure(tmp_path):
    """550 권한 부족은 폴더 부재로 오인하여 무시하면 안 된다."""
    ftp = FakeListingFTP(permission=True)
    with pytest.raises(error_perm, match="Permission denied"):
        client.safe_download_recipe(
            ftp, "YV17_1565PAD3", tmp_path / "recipe", "ins.dat", expected_parent="/"
        )
    assert ftp.pwd() == "/"


def test_path_reconnect_then_success(monkeypatch, tmp_path):
    """첫 FTP 세션에서 경로 550이 나면 재연결한 세션에서 성공해야 한다."""
    eq = equipment()
    state = mod.CollectionState(eq)
    entry_item = entry()
    ftp_sessions = []

    class Fake:
        def __init__(self):
            """__init__ 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

            Args:
                self: 현재 클래스 인스턴스."""
            self.closed = False

        def pwd(self):
            """pwd 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

            Args:
                self: 현재 클래스 인스턴스."""
            return "/root"

        def close(self):
            """close 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

            Args:
                self: 현재 클래스 인스턴스."""
            self.closed = True

    def connect(_):
        """connect 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            _: 처리에 필요한 입력값."""
        obj = Fake()
        ftp_sessions.append(obj)
        return obj

    calls = []

    def fake_scan(ftp, entry, equip, equip_dir, lookup, state, *args, **kw):
        """fake_scan 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            ftp: 연결된 FTP 클라이언트.
            entry: FTP 파일/폴더 항목.
            equip: 수집·파싱 대상 장비 정보.
            equip_dir: 장비별 로컬 백업 폴더.
            lookup: 기존 Recipe를 recipe_key로 조회하는 딕셔너리.
            state: 장비 수집 상태 및 저장 버퍼.
            *args: 처리에 필요한 입력값.
            **kw: 처리에 필요한 입력값."""
        calls.append(kw.get("is_retry", False))
        if len(calls) == 1:
            state.seen_keys.add(f"{eq.name}-{entry.name}")
            raise client.FTPRecipeUnavailable("550 No such file or directory")
        assert kw["is_retry"]
        assert ftp is ftp_sessions[-1]
        state.append({"status": "new", "recipe_key": f"{eq.name}-{entry.name}"})

    monkeypatch.setattr(mod, "_connect_kla2139", connect)
    monkeypatch.setattr(mod, "list_entries", lambda ftp: [entry_item])
    monkeypatch.setattr(mod, "_scan_recipe", fake_scan)
    mod.collect_kla2139(eq, tmp_path, {}, state)
    assert calls == [False, True]
    assert len(ftp_sessions) == 2
    assert all(f.closed for f in ftp_sessions)
    assert state.download_failures == state.skipped_missing_path == 0
    assert len(state.upserts) == 1


def test_path_retry_still_missing_skips_without_failure(monkeypatch, tmp_path):
    """재연결 후에도 같은 550 오류가 나면 해당 Recipe만 제외한다."""
    eq = equipment()
    state = mod.CollectionState(eq)
    sessions = []

    class Fake:
        def pwd(self):
            """pwd 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

            Args:
                self: 현재 클래스 인스턴스."""
            return "/root"

        def close(self):
            """close 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

            Args:
                self: 현재 클래스 인스턴스."""
            pass

    def connect(_):
        """connect 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            _: 처리에 필요한 입력값."""
        sessions.append(Fake())
        return sessions[-1]

    calls = []

    def fake_scan(ftp, entry, equip, equip_dir, lookup, state, *args, **kw):
        """fake_scan 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            ftp: 연결된 FTP 클라이언트.
            entry: FTP 파일/폴더 항목.
            equip: 수집·파싱 대상 장비 정보.
            equip_dir: 장비별 로컬 백업 폴더.
            lookup: 기존 Recipe를 recipe_key로 조회하는 딕셔너리.
            state: 장비 수집 상태 및 저장 버퍼.
            *args: 처리에 필요한 입력값.
            **kw: 처리에 필요한 입력값."""
        state.seen_keys.add(f"{eq.name}-{entry.name}")
        calls.append(kw["is_retry"])
        raise client.FTPRecipeUnavailable("550 No such file or directory")

    monkeypatch.setattr(mod, "_connect_kla2139", connect)
    monkeypatch.setattr(mod, "list_entries", lambda ftp: [entry()])
    monkeypatch.setattr(mod, "_scan_recipe", fake_scan)
    mod.collect_kla2139(eq, tmp_path, {}, state)
    assert calls == [False, True]
    assert len(sessions) == 2
    assert state.skipped_missing_path == 1
    assert state.download_failures == state.consecutive_download_failures == 0


def test_invalid_directory_restore_still_fatal(monkeypatch, tmp_path):
    """새 연결에서도 FTP 복귀가 불가능하면 데이터 정합성을 위해 중단한다."""
    eq = equipment()
    state = mod.CollectionState(eq)

    class Fake:
        """복구할 수 없는 원격 경로 오류를 시뮬레이션한다."""

        def pwd(self):
            """현재 FTP 원격 경로를 반환한다."""
            return "/root"

        def close(self):
            """가짜 FTP 연결을 종료한다."""
            pass

    monkeypatch.setattr(mod, "_connect_kla2139", lambda _: Fake())
    monkeypatch.setattr(mod, "list_entries", lambda _: [entry()])

    def fail(*args, **kwargs):
        """fail 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            *args: 처리에 필요한 입력값.
            **kwargs: 처리에 필요한 입력값."""
        raise client.FTPDirectoryError("RESTORE FAILED")

    monkeypatch.setattr(mod, "_scan_recipe", fail)
    with pytest.raises(client.FTPDirectoryError):
        mod.collect_kla2139(eq, tmp_path, {}, state)
    assert state.download_failures == 0


def test_nlst_fallback_recovers_required_file(tmp_path):
    """LIST에서 ins.dat가 빠져도 NLST에 있으면 다운로드에 성공한다."""

    class FallbackFTP(FakeListingFTP):
        """LIST가 필수 파일을 빼먹지만 NLST는 정확한 FTP 서버."""

        def __init__(self):
            """누락된 LIST 응답을 모의할 FTP 객체를 초기화한다."""
            super().__init__(missing_file=True)

        def nlst(self):
            """NLST에서는 존재하는 ins.dat를 반환한다."""
            return ["ins.dat"]

    ftp = FallbackFTP()
    destination = tmp_path / "YV17_1565PAD3"
    client.safe_download_recipe(
        ftp, "YV17_1565PAD3", destination, "ins.dat", expected_parent="/"
    )
    assert (destination / "ins.dat").read_bytes() == b"good-data"
    assert ftp.retr_count == 2  # LIST에 있던 other.txt + NLST ins.dat


def test_zero_byte_required_file_is_skipped_and_preserves_backup(tmp_path):
    """필수 파일이 0바이트이면 기존 백업을 건드리지 않고 제외한다."""

    class EmptyRequiredFTP(FakeListingFTP):
        """LIST에는 파일이 있지만 RETR 결과는 0바이트인 FTP 서버."""

        def retrbinary(self, command, callback):
            """0바이트 파일을 전송하는 상황을 재현한다."""
            self.retr_count += 1
            callback(b"")

    ftp = EmptyRequiredFTP()
    destination = tmp_path / "A301_TEST"
    destination.mkdir()
    (destination / "ins.dat").write_bytes(b"original-valid-file")
    with pytest.raises(client.FTPRequiredFileMissing, match="빈 파일"):
        client.safe_download_recipe(
            ftp, "A301_TEST", destination, "ins.dat", expected_parent="/"
        )
    assert (destination / "ins.dat").read_bytes() == b"original-valid-file"
    assert ftp.pwd() == "/"


def test_excluded_recipes_do_not_abort_equipment_finalization(monkeypatch, tmp_path):
    """필수 파일 누락 건수가 많아도 실제 다운로드 실패가 없으면 장비 정산을 수행한다."""
    import pandas as pd

    eq = equipment()
    original = pd.DataFrame(
        {
            "recipe_key": ["SMDI06-A301_TEST", "SMDI06-A302_TEST"],
            "dt_edit_time": ["2025-01-01 00:00", "2025-01-01 00:00"],
        }
    )
    monkeypatch.setattr(
        mod.PostgresCRUD, "select_query", staticmethod(lambda *args: original)
    )

    def simulate_missing_required(equip, equip_dir, lookup, state):
        """실패하지 않은 수집 제외 건수와 발견한 원격 키를 구성한다."""
        state.seen_keys.update(original["recipe_key"])
        state.skipped_missing_file = 8340

    monkeypatch.setattr(mod, "collect_kla2139", simulate_missing_required)
    finalized = []
    monkeypatch.setattr(
        mod, "update_database", lambda *args, **kwargs: finalized.append((args, kwargs))
    )
    mod.collect_equip_data(eq, tmp_path)
    assert len(finalized) == 1
    assert finalized[0][0][2] == []
