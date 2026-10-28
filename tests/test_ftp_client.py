from pathlib import Path
import pytest
from src.collectors.ftp.client import (
    FTPListingError,
    FTPDownloadError,
    parse_list_line,
    list_entries,
    safe_download_recipe,
)


def test_unix_list_directory_and_file():
    """test_unix_list_directory_and_file 사례에서 기대되는 수집·파싱 동작을 검증한다."""
    directory = parse_list_line("drw-rw-rw-    1 user group 0 Aug 30  2012 CP31_BSRPP")
    file = parse_list_line("-rw-rw-rw- 1 user group 123 Aug 30 2012 ins.dat")
    assert directory.name == "CP31_BSRPP" and directory.is_dir
    assert file.name == "ins.dat" and not file.is_dir
    with pytest.raises(FTPListingError):
        parse_list_line("unexpected FTP listing line")


class FakeFTP:
    def __init__(self, fail_retr=False):
        """__init__ 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            self: 현재 클래스 인스턴스.
            fail_retr: 처리에 필요한 입력값."""
        self.parts = []
        self.fail_retr = fail_retr

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
        elif where == "/":
            self.parts = []
        elif where == self.pwd():
            pass
        elif where.startswith("/"):
            self.parts = [v for v in where.split("/") if v]
        else:
            self.parts.append(where)

    def retrlines(self, command, callback):
        """retrlines 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            self: 현재 클래스 인스턴스.
            command: 처리에 필요한 입력값.
            callback: 처리에 필요한 입력값."""
        assert command == "LIST"
        if self.parts == []:
            callback("drw-rw-rw- 1 user group 0 Aug 30 2012 A301_TEST")
        elif self.parts == ["A301_TEST"]:
            callback("-rw-rw-rw- 1 user group 12 Aug 30 2012 ins.dat")

    def retrbinary(self, command, callback):
        """retrbinary 테스트에 사용할 가짜 장비 및 입력 자료를 준비한다.

        Args:
            self: 현재 클래스 인스턴스.
            command: 처리에 필요한 입력값.
            callback: 처리에 필요한 입력값."""
        if self.fail_retr:
            raise OSError("connection lost")
        callback(b"example contents")


def test_list_recognizes_nested_folders():
    """test_list_recognizes_nested_folders 사례에서 기대되는 수집·파싱 동작을 검증한다."""
    ftp = FakeFTP()
    assert list_entries(ftp)[0].name == "A301_TEST"


def test_download_atomic_success(tmp_path: Path):
    """test_download_atomic_success 사례에서 기대되는 수집·파싱 동작을 검증한다.

    Args:
        tmp_path: 처리에 필요한 입력값."""
    ftp = FakeFTP()
    dest = tmp_path / "A301_TEST"
    safe_download_recipe(ftp, "A301_TEST", dest, "ins.dat")
    assert (dest / "ins.dat").read_bytes() == b"example contents"
    assert ftp.pwd() == "/"


def test_failed_download_keeps_old_contents(tmp_path: Path):
    """test_failed_download_keeps_old_contents 사례에서 기대되는 수집·파싱 동작을 검증한다.

    Args:
        tmp_path: 처리에 필요한 입력값."""
    ftp = FakeFTP(fail_retr=True)
    dest = tmp_path / "A301_TEST"
    dest.mkdir()
    (dest / "ins.dat").write_bytes(b"original")
    with pytest.raises(OSError):
        safe_download_recipe(ftp, "A301_TEST", dest, "ins.dat")
    assert (dest / "ins.dat").read_bytes() == b"original"
    assert ftp.pwd() == "/"
