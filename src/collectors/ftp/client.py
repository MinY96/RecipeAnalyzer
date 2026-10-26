"""FTP client"""

from ftplib import FTP, all_errors, error_perm
from pathlib import Path, PurePosixPath


def ftp_download_tree(ftp: FTP, remote_dir: str, local_dir: Path) -> None:
    """FTP 디렉터리 트리를 로컬 폴더로 재귀 다운로드한다.

    Args:
        ftp: 입력값 (FTP).
        remote_dir: 입력값 (str).
        local_dir: 입력값 (Path).

    Returns:
        None: 처리 결과.
    """
    local_dir.mkdir(parents=True, exist_ok=True)
    current = ftp.pwd()
    ftp.cwd(remote_dir)
    try:
        names = ftp.nlst()
        for name in names:
            leaf = PurePosixPath(name).name
            if leaf in {".", ".."}:
                continue
            try:
                ftp.cwd(name)
                ftp.cwd("..")
                ftp_download_tree(ftp, name, local_dir / leaf)
            except all_errors:
                if not leaf.endswith("~"):
                    target_file = local_dir / leaf
                    try:
                        with target_file.open("wb") as fp:
                            ftp.retrbinary(f"RETR {name}", fp.write)
                    except Exception as e:
                        print(f"다운로드 실패 ({name}): {e}")
                        if target_file.exists():
                            target_file.unlink()  # 잔여(불완전) 파일 제거
                    # with (local_dir / leaf).open('wb') as fp:
                    #     ftp.retrbinary(f'RETR {name}', fp.write)
    finally:
        ftp.cwd(current)


def ftp_remove_tree(ftp: FTP, remote_dir: str) -> None:
    """FTP 디렉터리 트리를 재귀 삭제한다.

    Args:
        ftp: 입력값 (FTP).
        remote_dir: 입력값 (str).

    Returns:
        None: 처리 결과.
    """
    current = ftp.pwd()
    ftp.cwd(remote_dir)
    try:
        names = ftp.nlst()
        lines: list[str] = []
        ftp.retrlines("LIST", lines.append)
        directory_names: list[str] = []
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            # Unix 형식 LIST 응답에서 d로 시작하면 디렉터리
            if not line.startswith("d"):
                continue
            parts = line.split(maxsplit=8)
            if len(parts) < 9:
                raise RuntimeError(
                    "FTP LIST 결과를 파싱할 수 없습니다. "
                    f"안전한 삭제를 위해 작업을 중단합니다: {raw_line!r}"
                )
            directory_name = parts[8].strip()
            if directory_name not in {".", ".."}:
                directory_names.append(directory_name)

        # NLST 파일 목록 + LIST 디렉터리 목록
        entries = list(dict.fromkeys([*names, *directory_names]))

        for name in entries:
            leaf = PurePosixPath(name).name
            if leaf in {".", ".."}:
                continue
            try:
                ftp.cwd(name)
            except error_perm as exc:
                # CWD 실패가 정말 파일이기 때문인지 확인
                # 일반적으로 파일에 CWD를 실행하면 550 응답
                if not str(exc).startswith("550"):
                    raise
                ftp.delete(name)

            else:
                # 디렉터리 진입에 성공한 경우 원래 위치로 복귀 후 재귀 실행
                ftp.cwd("..")
                ftp_remove_tree(ftp, name)

    finally:
        ftp.cwd(current)
    ftp.rmd(remote_dir)
