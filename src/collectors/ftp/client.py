"""구형 검사 장비 FTP 서버의 안전한 탐색, 다운로드 및 디렉터리 복귀 기능."""

from __future__ import annotations

from dataclasses import dataclass
from ftplib import FTP, error_perm
from pathlib import Path, PurePosixPath
import os
import shutil
import uuid
import logging
import time

log = logging.getLogger(__name__)


class FTPListingError(RuntimeError):
    """FTP LIST 응답을 신뢰할 수 없어 탐색을 중단해야 하는 오류."""


class FTPDownloadError(RuntimeError):
    """정상 Recipe 파일의 전송 또는 저장 작업에 실패한 오류."""


class FTPDirectoryError(FTPDownloadError):
    """FTP 현재 폴더가 예상 경로와 달라 장비 탐색을 중단해야 하는 오류."""


class FTPRecipeUnavailable(FTPDownloadError):
    """FTP 서버가 특정 Recipe 경로의 부재(550)를 명확히 응답한 경우."""


class FTPRequiredFileMissing(FTPRecipeUnavailable):
    """Recipe 폴더에 필수 파일이 없거나 크기가 0인 경우."""


class FTPRecursiveListingError(FTPDownloadError):
    """LIST/CWD 오류 또는 순환 디렉터리로 인해 안전한 백업이 불가능한 경우.

    정상 Recipe가 아닌 불완전 백업으로 처리되지 않도록, 전체 Recipe를 제외하고
    기존 백업을 보존한다. 일반 FTP 전송 실패 횟수와는 구분한다.
    """


MAX_FTP_TREE_DEPTH = 8


@dataclass(frozen=True, slots=True)
class FTPEntry:
    """FTP LIST 응답에서 추출한 파일 또는 폴더 정보."""

    name: str
    is_dir: bool
    raw: str


def _is_missing_path_error(exc: Exception) -> bool:
    """550 오류 중 '경로가 존재하지 않음'으로 명시된 경우만 구분한다.

    Args:
        exc: FTP 명령에서 발생한 예외.

    Returns:
        bool: 550 경로 부재로 확인되었을 때만 True.

    Notes:
        550 Permission denied 등은 결코 무시하지 않는다.
    """
    if not isinstance(exc, error_perm):
        return False
    message = str(exc).strip().casefold()
    return message.startswith("550") and any(
        hint in message
        for hint in (
            "no such file",
            "not found",
            "does not exist",
            "cannot find",
            "file unavailable: no such",
        )
    )


def _open_staging_file(path: Path):
    """Windows 임시 파일이 일시 점유되었을 때 제한 횟수만 재시도해 연다.

    Args:
        path: 다운로드용 .part 파일의 절대 경로.

    Returns:
        BinaryIO: 쓰기 모드로 열린 임시 파일 객체.

    Raises:
        PermissionError: 정해진 재시도 이후에도 파일을 열 수 없을 때.
    """
    retries = max(1, int(os.getenv("LOCAL_FILE_OPEN_RETRIES", "3")))
    delay = max(0, float(os.getenv("LOCAL_FILE_OPEN_DELAY_SECONDS", "0.25")))
    for attempt in range(1, retries + 1):
        try:
            return path.open("wb")
        except PermissionError:
            if attempt == retries:
                raise
            log.warning(
                "로컬 임시 파일 점유 재시도 (%d/%d): %s", attempt, retries, path
            )
            time.sleep(delay)


def parse_list_line(line: str) -> FTPEntry | None:
    """Unix 형식의 FTP LIST 한 행을 파일/폴더 정보로 변환한다.

    Args:
        line: LIST 명령에서 받은 한 줄의 원문.

    Returns:
        FTPEntry | None: 일반 항목 정보. 헤더/상대 경로/링크는 None.

    Raises:
        FTPListingError: 형식을 해석할 수 없거나 위험한 경로 이름인 경우.
    """
    line = line.strip()
    if not line or line.startswith("total "):
        return None
    parts = line.split(maxsplit=8)
    if len(parts) < 9 or not parts[0].startswith(("d", "-", "l")):
        raise FTPListingError(f"지원하지 않는 FTP LIST 응답: {line!r}")
    name = parts[8]
    if name in (".", ".."):
        return None
    if "\x00" in name or "/" in name or "\\" in name or name.startswith(".."):
        raise FTPListingError(f"위험한 FTP 경로: {name!r}")
    # 링크는 순환 참조 및 외부 폴더 이동을 막기 위해 탐색하지 않는다.
    if parts[0][0] == "l":
        return None
    return FTPEntry(name, parts[0][0] == "d", line)


def list_entries(ftp: FTP) -> list[FTPEntry]:
    """LIST로 현재 폴더의 모든 파일/디렉터리를 조회한다.

    Args:
        ftp: 로그인 및 폴더 이동이 완료된 FTP 연결.

    Returns:
        list[FTPEntry]: 현재 폴더의 파일/폴더 목록.

    Raises:
        FTPListingError: 응답 형식이 올바르지 않을 때.
        ftplib.all_errors: FTP 명령 수행 중 통신 장애가 발생할 때.

    Notes:
        일부 구형 장비의 NLST는 폴더만 있는 경우 빈 목록을 반환하여
        디렉터리 여부 확인은 LIST를 유지한다.
    """
    lines: list[str] = []
    ftp.retrlines("LIST", lines.append)
    result = []
    for line in lines:
        item = parse_list_line(line)
        if item is not None:
            result.append(item)
    return result


def ftp_download_tree(
    ftp: FTP,
    remote_dir: str,
    local_dir: Path,
    *,
    expected_parent: str | None = None,
    required_file: str | None = None,
    _ancestors: tuple[str, ...] = (),
    _pwd_stack: tuple[str, ...] = (),
) -> int:
    """원격 Recipe 폴더를 재귀 다운로드하고 원래 FTP 폴더로 복귀한다.

    Args:
        ftp: 활성 FTP 연결.
        remote_dir: 현재 FTP 폴더 기준 다운로드할 하위 폴더명.
        local_dir: 다운로드할 로컬 저장 폴더.
        expected_parent: 다운로드 시작 전에 기대하는 FTP 부모 경로.
        required_file: 최상위 Recipe 폴더의 필수 파일명(존재 여부 사전 확인).
        _ancestors: 상위 디렉터리의 이름. 재귀 순환 감지용 내부 인자.
        _pwd_stack: 탐색 중 거친 원격 FTP 절대경로. 순환 감지용 내부 인자.

    Raises:
        FTPDirectoryError: 현재 폴더가 잘못되었거나 복귀할 수 없을 때.
        FTPRecipeUnavailable: 폴더 진입/LIST 시 명확한 550 경로 부재 응답.
        FTPRequiredFileMissing: 폴더 LIST에 필수 파일이 없을 때.
        FTPRecursiveListingError: 상위와 동일한 이름·경로가 반복되거나 깊이 초과.
        FTPDownloadError: 기타 다운로드 실패.

    Notes:
        LIST에서 필수 파일이 발견되면 추가 NLST 요청 없이 다운로드한다.
        LIST에서만 누락된 경우 NLST를 1회 보조 조회한다.
        하위 폴더 진입 후에는 CWD ..로 복귀하고 PWD로 위치를 검증한다.
    """
    current = ftp.pwd()
    current_normalized = current.rstrip("/").casefold()
    remote_normalized = remote_dir.casefold()
    # 일부 구형 서버는 하위 LIST에도 부모 Recipe 이름을 재표시한다.
    # 부모 이름으로 CWD가 성공해도 다시 같은 LIST가 반환되어 무한 재귀할 수 있다.
    if remote_normalized in (name.casefold() for name in _ancestors):
        path = "/".join((*_ancestors, remote_dir))
        raise FTPRecursiveListingError(
            f"비정상 FTP 자기참조 폴더 반복: {path} (PWD={current})"
        )
    if len(_ancestors) >= MAX_FTP_TREE_DEPTH:
        raise FTPRecursiveListingError(
            f"FTP 재귀 탐색 깊이 초과({MAX_FTP_TREE_DEPTH}): "
            f'{"/".join((*_ancestors, remote_dir))}'
        )
    if current_normalized in _pwd_stack:
        raise FTPRecursiveListingError(f"FTP PWD가 이전 탐색 경로로 순환: {current!r}")
    if (
        expected_parent is not None
        and current_normalized != expected_parent.rstrip("/").casefold()
    ):
        raise FTPDirectoryError(
            f"FTP 폴더가 잘못되었습니다: recipe={remote_dir!r}, "
            f"expected={expected_parent!r}, actual={current!r}"
        )
    try:
        ftp.cwd(remote_dir)
    except error_perm as exc:
        if _is_missing_path_error(exc):
            raise FTPRecipeUnavailable(
                f"FTP Recipe 폴더 없음: {remote_dir}: {exc}"
            ) from exc
        raise

    try:
        entered = ftp.pwd()
        if (
            entered.rstrip("/").casefold() == current_normalized
            or entered.rstrip("/").casefold() in _pwd_stack
        ):
            raise FTPRecursiveListingError(
                f"FTP CWD 이동 후 PWD가 갱신되지 않거나 순환함: "
                f"from={current!r}, to={entered!r}, dir={remote_dir!r}"
            )
        try:
            entries = list_entries(ftp)
        except error_perm as exc:
            if _is_missing_path_error(exc):
                raise FTPRecipeUnavailable(
                    f"FTP Recipe 목록 경로 없음: {remote_dir}: {exc}"
                ) from exc
            raise

        # KLA2139 등은 폴더명만 맞지만 실제 Recipe 파일이 없는 폴더가 존재한다.
        # 필수 파일이 없다면 이미 전송한 파일을 손상하지 않고 곧바로 제외한다.
        if required_file is not None:
            exists = any(
                not item.is_dir and item.name.casefold() == required_file.casefold()
                for item in entries
            )
            if not exists:
                # 일부 장비에서 LIST 파일 항목이 빠지더라도 NLST에서는
                # 실제 파일이 조회될 수 있다. 필수 파일이 누락됐을 때에만
                # NLST를 추가로 호출해 불필요한 재다운로드/오탐을 줄인다.
                try:
                    names = ftp.nlst()
                except error_perm as exc:
                    if _is_missing_path_error(exc):
                        raise FTPRecipeUnavailable(
                            f"FTP Recipe NLST 경로 없음: {remote_dir}: {exc}"
                        ) from exc
                    raise
                for raw_name in names:
                    leaf = PurePosixPath(str(raw_name).replace("\\", "/")).name
                    if leaf.casefold() == required_file.casefold():
                        entries.append(
                            FTPEntry(leaf, False, f"NLST fallback: {raw_name}")
                        )
                        exists = True
                        break
            if not exists:
                raise FTPRequiredFileMissing(
                    f"필수 Recipe 파일 없음: {remote_dir}/{required_file}"
                )

        local_dir.mkdir(parents=True, exist_ok=True)
        skipped_optional = 0
        for entry in entries:
            target = local_dir / entry.name
            if entry.is_dir:
                skipped_optional += ftp_download_tree(
                    ftp,
                    entry.name,
                    target,
                    _ancestors=(*_ancestors, remote_dir),
                    _pwd_stack=(*_pwd_stack, current_normalized),
                )
            elif not entry.name.endswith("~"):
                part = target.with_name(target.name + ".part")
                is_required = (
                    required_file is not None
                    and entry.name.casefold() == required_file.casefold()
                )
                for attempt in (1, 2):
                    try:
                        with _open_staging_file(part) as stream:
                            ftp.retrbinary(f"RETR {entry.name}", stream.write)
                        os.replace(part, target)
                        break
                    except error_perm as exc:
                        if not _is_missing_path_error(exc):
                            raise  # 550 Permission denied 등은 일반 실패 처리
                        # FTP 경로가 잘못 바뀐 경우는 단순 파일 누락으로 분류하지 않는다.
                        if (
                            ftp.pwd().rstrip("/").casefold()
                            != entered.rstrip("/").casefold()
                        ):
                            raise FTPDirectoryError(
                                f"RETR 중 FTP 폴더가 변경됨: {entry.name!r}"
                            ) from exc
                        if attempt == 1:
                            log.warning(
                                "FTP RETR 550 파일 없음, 한 번 재시도: %s/%s",
                                remote_dir,
                                entry.name,
                            )
                            continue
                        if is_required:
                            raise FTPRequiredFileMissing(
                                f"필수 Recipe 파일 RETR 550: {remote_dir}/{entry.name}"
                            ) from exc
                        # 선택 파일(로그·이미지 등)의 확정된 550은 Recipe 전체 실패로 보지 않는다.
                        skipped_optional += 1
                        log.warning(
                            "FTP 선택 파일 RETR 550, 제외: %s/%s",
                            remote_dir,
                            entry.name,
                        )
                    finally:
                        part.unlink(missing_ok=True)
    finally:
        try:
            ftp.cwd("..")
            restored = ftp.pwd()
        except Exception as exc:
            raise FTPDirectoryError(
                f"FTP 부모 폴더 복귀 실패: remote={remote_dir!r}, parent={current!r}"
            ) from exc
        if restored.rstrip("/").casefold() != current.rstrip("/").casefold():
            raise FTPDirectoryError(
                f"FTP 복귀 위치 불일치: remote={remote_dir!r}, "
                f"expected={current!r}, actual={restored!r}"
            )
    return skipped_optional


def safe_download_recipe(
    ftp: FTP,
    recipe: str,
    dest: Path,
    required_file: str,
    *,
    expected_parent: str | None = None,
) -> int:
    """Recipe를 임시 폴더에 내려받아 검증 후 기존 백업과 교체한다.

    Args:
        ftp: 활성 FTP 연결.
        recipe: 다운로드 대상 Recipe 폴더명.
        dest: 최종 백업 로컬 경로.
        required_file: 모델별 필수 파일명(ins.dat 또는 Sensitivity1.xml).
        expected_parent: Recipe 폴더로 이동하기 전 확인할 원격 부모 경로.

    Raises:
        FTPRequiredFileMissing: 필수 파일이 없거나 크기가 0일 때.
        FTPRecipeUnavailable: 원격 Recipe 경로가 존재하지 않을 때.
        FTPDirectoryError: 작업 중 FTP 현재 경로가 잘못되었을 때.
        Exception: 실제 파일 전송 또는 로컬 저장 장애가 발생할 때.

    Notes:
        교체는 다운로드/검증 성공 후에만 실시하며 실패 시 기존 백업을 유지한다.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    suffix = uuid.uuid4().hex
    stage = dest.with_name(f".{dest.name}.staging-{suffix}")
    previous = dest.with_name(f".{dest.name}.previous-{suffix}")
    try:
        skipped_optional = ftp_download_tree(
            ftp,
            recipe,
            stage,
            expected_parent=expected_parent,
            required_file=required_file,
        )
        expected = stage / required_file
        # 실제 서버에 파일명이 대문자로 기록된 경우에도 Windows 경로 호환.
        if not expected.is_file():
            alternatives = [
                path
                for path in stage.iterdir()
                if path.is_file() and path.name.casefold() == required_file.casefold()
            ]
            if len(alternatives) == 1:
                alternatives[0].rename(expected)
        if not expected.is_file() or expected.stat().st_size == 0:
            raise FTPRequiredFileMissing(
                f"필수 Recipe 파일 없음/빈 파일: {recipe}/{required_file}"
            )
        if dest.exists():
            dest.rename(previous)
        try:
            stage.rename(dest)
        except Exception:
            if previous.exists() and not dest.exists():
                previous.rename(dest)
            raise
        if previous.exists():
            shutil.rmtree(previous)
        return skipped_optional
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def ftp_remove_tree(ftp: FTP, remote_dir: str) -> None:
    """FTP 서버의 지정한 디렉터리 트리를 재귀 삭제한다.

    Args:
        ftp: FTP 연결.
        remote_dir: 현재 폴더 기준 삭제 대상 디렉터리.

    Notes:
        Recipe 수집에서는 호출하지 않는 별도의 관리 기능이다.
    """
    current = ftp.pwd()
    ftp.cwd(remote_dir)
    try:
        entries = list_entries(ftp)
        for entry in entries:
            if entry.is_dir:
                ftp_remove_tree(ftp, entry.name)
            else:
                ftp.delete(entry.name)
    finally:
        ftp.cwd("..")
        restored = ftp.pwd()
        if restored.rstrip("/").casefold() != current.rstrip("/").casefold():
            raise FTPDirectoryError(f"FTP 삭제 후 부모 복귀 실패: {remote_dir!r}")
    ftp.rmd(remote_dir)
