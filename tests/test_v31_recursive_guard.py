"""V3.1 구형 FTP 서버의 동일 폴더 반복 LIST 및 깊이 보호 회귀 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.collectors.ftp import client, collector
from src.domain.entities.equipment import Equipment, FTPConnectionInfo


class RecursiveFTP:
    """Recipe 내부에서 동일 Recipe 폴더를 다시 반환하는 비정상 FTP 서버."""

    def __init__(self, recipe: str = "IL01_920562UM", root_file: bool = True) -> None:
        """테스트할 Recipe 이름, 루트 필수 파일 출력 여부를 설정한다."""
        self.recipe = recipe
        self.root_file = root_file
        self.parts: list[str] = []
        self.longest_depth = 0

    def pwd(self) -> str:
        """현재 서버가 인식하는 작업 폴더를 반환한다."""
        return "/" + "/".join(self.parts)

    def cwd(self, folder: str) -> None:
        """자기참조 목록에서도 계속 진입되는 상황을 모의한다."""
        if folder == "..":
            self.parts.pop()
        else:
            self.parts.append(folder)
            self.longest_depth = max(self.longest_depth, len(self.parts))

    def retrlines(self, command: str, callback) -> None:
        """Recipe 아래에서 같은 폴더와 파일이 반복 보고되는 LIST를 반환한다."""
        assert command == "LIST"
        if self.root_file:
            callback("-rw-rw-rw- 1 user group 20 Aug 30 2012 ins.dat")
        callback("-rw-rw-rw- 1 user group 20 Aug 30 2012 History.Log")
        callback(f"drw-rw-rw- 1 user group 0 Aug 30 2012 {self.recipe}")

    def retrbinary(self, command: str, callback) -> None:
        """필수 파일에 정상 내용을 전송한다."""
        callback(b"valid")

    def nlst(self):
        """LIST 필수 파일 누락 여부 재확인을 지원한다."""
        return ["ins.dat"]


def _equip() -> Equipment:
    """SMDI06 장비 정보를 작성한다."""
    return Equipment(
        "SMDI06",
        "FAB1",
        "DM",
        "KLA2139",
        FTPConnectionInfo("server", "id", "pw", 21, "cp949", 30, "d:/KT1025/Usr/Ins8"),
    )


def _entry(name: str = "IL01_920562UM") -> client.FTPEntry:
    """구형 FTP의 Recipe 디렉터리 LIST 행을 생성한다."""
    return client.FTPEntry(name, True, f"drw-rw-rw- 1 user group 0 Aug 30 2012 {name}")


def test_recursive_self_listing_is_detected_before_windows_path_limit(
    tmp_path: Path,
) -> None:
    """동일 폴더 재등장이 2단계에서 감지되고 기존 백업은 유지되어야 한다."""
    ftp = RecursiveFTP()
    dst = tmp_path / "IL01_920562UM"
    dst.mkdir()
    (dst / "ins.dat").write_bytes(b"original")
    with pytest.raises(client.FTPRecursiveListingError, match="자기참조"):
        client.safe_download_recipe(
            ftp, "IL01_920562UM", dst, "ins.dat", expected_parent="/"
        )
    assert ftp.longest_depth <= 1
    assert ftp.pwd() == "/"
    assert (dst / "ins.dat").read_bytes() == b"original"
    assert not list(tmp_path.glob(".*.staging-*"))


def test_recursive_listing_is_not_general_download_failure(
    monkeypatch, tmp_path: Path
) -> None:
    """재연결 후 동일 오류도 실패 5회 제한이 아닌 별도 제외 카운터로 계산한다."""
    state = collector.CollectionState(_equip())
    sessions = []

    class FakeSession(RecursiveFTP):
        """재접속 흐름 검사용으로 close를 구현한 FTP."""

        def close(self):
            """세션 종료 호출만 기록한다."""
            self.closed = True

    def reconnect(_):
        """새 FTP 연결을 생성하여 세션 목록에 기록한다."""
        instance = FakeSession()
        sessions.append(instance)
        return instance

    monkeypatch.setattr(collector, "_connect_kla2139", reconnect)
    monkeypatch.setattr(collector, "list_entries", lambda ftp: [_entry()])
    collector.collect_kla2139(_equip(), tmp_path, {}, state)
    assert len(sessions) == 2
    assert state.skipped_recursive_listing == 1
    assert state.download_failures == 0
    assert state.consecutive_download_failures == 0
    assert "SMDI06-IL01_920562UM" in state.seen_keys
    assert len(state.upserts) == 0


def test_distinct_nested_folder_download_still_works(tmp_path: Path) -> None:
    """정상적인 서로 다른 이름의 하위 폴더는 계속 다운로드한다."""

    class ValidFTP(RecursiveFTP):
        """Recipe 하위 Logs 폴더를 갖는 정상적인 서버."""

        def retrlines(self, command, callback):
            """Recipe에서는 Logs 디렉터리를, Logs에서는 log 파일을 제공한다."""
            assert command == "LIST"
            if self.parts == ["A301_TEST"]:
                callback("-rw-rw-rw- 1 u g 20 Aug 30 2012 ins.dat")
                callback("drw-rw-rw- 1 u g 0 Aug 30 2012 Logs")
            else:
                callback("-rw-rw-rw- 1 u g 20 Aug 30 2012 History.Log")

    ftp = ValidFTP()
    dst = tmp_path / "A301_TEST"
    client.safe_download_recipe(ftp, "A301_TEST", dst, "ins.dat", expected_parent="/")
    assert (dst / "ins.dat").read_bytes() == b"valid"
    assert (dst / "Logs" / "History.Log").read_bytes() == b"valid"
    assert ftp.pwd() == "/"


def test_depth_limit_blocks_distinct_but_unbounded_tree(tmp_path: Path) -> None:
    """폴더명이 매번 달라도 깊이 상한을 초과하면 작업을 보호한다."""

    class DeepFTP(RecursiveFTP):
        """매 깊이마다 다른 폴더명으로 계속 중첩시키는 비정상 서버."""

        def retrlines(self, command, callback):
            """깊이마다 새 디렉터리 한 개를 계속 보고한다."""
            assert command == "LIST"
            callback("-rw-rw-rw- 1 u g 20 Aug 30 2012 ins.dat")
            callback(f"drw-rw-rw- 1 u g 0 Aug 30 2012 child{len(self.parts)}")

    ftp = DeepFTP()
    with pytest.raises(client.FTPRecursiveListingError, match="깊이 초과"):
        client.safe_download_recipe(ftp, "A301_TEST", tmp_path / "A301_TEST", "ins.dat")
    assert ftp.longest_depth <= client.MAX_FTP_TREE_DEPTH
    assert ftp.pwd() == "/"


def test_directory_cwd_noop_is_classified_as_recursive(tmp_path: Path) -> None:
    """CWD 명령은 성공했지만 PWD가 변경되지 않는 장비를 감지한다."""

    class NoopFTP(RecursiveFTP):
        """CWD 성공을 응답하면서 실제 폴더는 바꾸지 않는 FTP."""

        def cwd(self, folder):
            """CWD .. 및 폴더 이동을 모두 성공으로 응답하고 무시한다."""
            pass

    ftp = NoopFTP()
    with pytest.raises(client.FTPRecursiveListingError, match="PWD"):
        client.safe_download_recipe(ftp, "A301_TEST", tmp_path / "A301_TEST", "ins.dat")
    assert ftp.pwd() == "/"
