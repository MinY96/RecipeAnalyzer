from pathlib import Path
import pytest
from src.collectors.ftp.client import (
    FTPDirectoryError,
    ftp_download_tree,
    safe_download_recipe,
)
from src.collectors.ftp import collector as mod
from src.domain.entities.equipment import Equipment, FTPConnectionInfo


class WinFTP:
    """Legacy server: CWD absolute path is unreliable, CWD .. is supported."""

    def __init__(self):
        """__init__ 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            self: 현재 클래스 인스턴스."""
        self.path = ["d:", "KT1025", "Usr", "Ins8"]
        self.root = tuple(self.path)
        self.existing = {"OD42_666562UM", "YV17_1565PAD3"}
        self.absolute_uses = 0

    def pwd(self):
        """pwd 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            self: 현재 클래스 인스턴스."""
        return "/" + "/".join(self.path)

    def cwd(self, folder):
        """cwd 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            self: 현재 클래스 인스턴스.
            folder: 처리에 필요한 입력값."""
        if folder == "..":
            self.path.pop()
        elif folder.startswith("/"):
            self.absolute_uses += 1
            # Deliberately broken server: absolute CWD stays in current folder
            pass
        elif folder in self.existing:
            if tuple(self.path) != self.root:
                raise FileNotFoundError(f"Wrong parent path: {self.pwd()}/{folder}")
            self.path.append(folder)
        else:
            raise FileNotFoundError(folder)

    def retrlines(self, command, callback):
        """retrlines 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            self: 현재 클래스 인스턴스.
            command: 처리에 필요한 입력값.
            callback: 처리에 필요한 입력값."""
        assert command == "LIST"
        if tuple(self.path) == self.root:
            for name in sorted(self.existing):
                callback(f"drwxrwxrwx 1 u g 0 Oct 26 14:32 {name}")
        else:
            callback("-rw-rw-rw- 1 u g 20 Oct 26 14:32 ins.dat")

    def retrbinary(self, command, callback):
        """retrbinary 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            self: 현재 클래스 인스턴스.
            command: 처리에 필요한 입력값.
            callback: 처리에 필요한 입력값."""
        callback(b"valid content")


def test_sibling_recipe_download_uses_parent_relative_restore(tmp_path):
    """test_sibling_recipe_download_uses_parent_relative_restore 사례에서 기대되는 수집·파싱 동작을 검증한다.

    Args:
        tmp_path: 처리에 필요한 입력값."""
    ftp = WinFTP()
    root = ftp.pwd()
    for recipe in sorted(ftp.existing):
        safe_download_recipe(
            ftp, recipe, tmp_path / recipe, "ins.dat", expected_parent=root
        )
        assert ftp.pwd() == root
    assert ftp.absolute_uses == 0


def test_wrong_parent_is_fatal_before_download(tmp_path):
    """test_wrong_parent_is_fatal_before_download 사례에서 기대되는 수집·파싱 동작을 검증한다.

    Args:
        tmp_path: 처리에 필요한 입력값."""
    ftp = WinFTP()
    ftp.cwd("OD42_666562UM")
    with pytest.raises(FTPDirectoryError, match="폴더가 잘못"):
        safe_download_recipe(
            ftp,
            "YV17_1565PAD3",
            tmp_path / "missing",
            "ins.dat",
            expected_parent="/d:/KT1025/Usr/Ins8",
        )


def test_consecutive_download_failures_abort_early(monkeypatch, tmp_path):
    """test_consecutive_download_failures_abort_early 사례에서 기대되는 수집·파싱 동작을 검증한다.

    Args:
        monkeypatch: 처리에 필요한 입력값.
        tmp_path: 처리에 필요한 입력값."""
    eq = Equipment(
        "SMDI06",
        "FAB1",
        "DM",
        "KLA2139",
        FTPConnectionInfo("host", "user", "pw", 21, "cp949", 30, "d:/KT1025/Usr/Ins8"),
    )

    class BadFTP:
        """수집 중 다운로드 오류를 모의하는 FTP 객체."""

        def pwd(self):
            """현재 장비의 FTP 경로를 반환한다."""
            return "/d:/KT1025/Usr/Ins8"

    entry = mod.FTPEntry(
        "A301_TEST", True, "drw-rw-rw- 1 user group 0 Oct 26 2025 A301_TEST"
    )
    monkeypatch.setattr(
        mod,
        "safe_download_recipe",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("bad file")),
    )
    state = mod.CollectionState(eq)
    for n in range(4):
        state.seen_keys.clear()
        mod._scan_recipe(
            BadFTP(),
            entry,
            eq,
            tmp_path,
            {},
            state,
            "ins.dat",
            "A301",
            "TEST",
            [],
            "/d:/KT1025/Usr/Ins8",
        )
    state.seen_keys.clear()
    with pytest.raises(RuntimeError, match="5 consecutive"):
        mod._scan_recipe(
            BadFTP(),
            entry,
            eq,
            tmp_path,
            {},
            state,
            "ins.dat",
            "A301",
            "TEST",
            [],
            "/d:/KT1025/Usr/Ins8",
        )
