# RecipeAnalyzer V3.1 — KLA2139 동일 Recipe 재귀 탐색 오류 수정

## 원인

SMDI06에서 `IL01_920562UM` 하위 `LIST`가 또다시 `IL01_920562UM`을 디렉터리로 반환하여, V3의 `ftp_download_tree()`가 제한 없이 같은 폴더명을 계속 재귀 방문했습니다. 그 결과 Windows 임시 백업 경로가 매우 길어지고 `History.Log.part` 쓰기에서 `FileNotFoundError`가 발생했습니다. 실제 서버의 `LIST` 또는 `CWD`가 왜 이런 응답을 보냈는지는 서버 로그/원본 LIST 자료가 없으므로 확정할 수 없습니다.

## 개선 사항

1. `FTPRecursiveListingError` 신설: 진입하려는 디렉터리명이 상위 경로에서 이미 방문된 이름이면 **재귀 호출 전에** 탐색 중단.
2. 최대 원격 디렉터리 탐색 깊이를 `MAX_FTP_TREE_DEPTH = 8`로 제한. 실제로 8단계를 초과하는 정상 폴더 구조가 있다면 설정 변경이 필요할 수 있습니다.
3. FTP `CWD` 후 `PWD`가 변하지 않거나 이미 방문한 절대경로로 되돌아온 경우 탐색 이상으로 판단.
4. KLA2139에서 해당 오류 발생 시 **FTP 새 연결로 같은 Recipe 1회 재시도**. 재발하면 `skipped_recursive_listing`으로 집계하고 다음 Recipe로 이동. 일반 `download_failures` 및 연속 5건 제한에 포함하지 않음.
5. KLA2365/PUMA에서도 비정상 재귀 폴더를 별도 제외하되, 복구 불가능한 원격 경로 오류나 실제 전송/저장 실패는 예외로 유지.
6. 실패 Recipe는 `seen_keys`에 유지하지만 정상 UPSERT를 수행하지 않습니다. **기존 로컬 백업을 손상하지 않고, 기존 DB Recipe를 삭제 대상으로 오판하지 않습니다.**

## 처리 구분

| 사례 | 처리 |
|---|---|
| 동일 Recipe 폴더가 자기 자신을 자식으로 재보고 | 안전 중단 → KLA2139 재연결 1회 → 계속 비정상이면 별도 제외 |
| 상위 폴더명 반복 또는 8단계 깊이 초과 | 위와 동일 |
| 정상 하위 폴더(`Logs` 등) | 기존과 같이 재귀 다운로드 |
| `ins.dat` 없음 | 기존 V3대로 필수 파일 누락 제외 |
| RETR 실패·로컬 디스크 오류 | 기존 V3대로 일반 다운로드 실패에 포함 |
| `PWD` 복귀 자체 실패 | 기존 V3대로 장비 안전 중단 가능 |

> **중요:** 별도 제외 처리는 해당 Recipe의 백업 성공을 의미하지 않습니다. 정상 Recipe가 중첩된 동일 이름의 폴더를 실제로 포함하고 있다면, 보수적으로 제외될 수 있습니다. `skipped_recursive_listing`이 증가하면 해당 Recipe의 FTP `LIST` 출력 및 폴더 구조를 직접 확인해야 합니다.

## 변경 파일

- `src/collectors/ftp/client.py`: 무한 재귀 및 CWD/PWD 순환 가드 추가.
- `src/collectors/ftp/collector.py`: 오류 재시도·별도 제외 건수 및 요약 로그.
- `tests/test_v31_recursive_guard.py`: 반복 `LIST`, 재접속 후 재발, 정상 하위 폴더, 최대 깊이, CWD 무효 응답 테스트.

기존 999건 단위 UPSERT, MES 매칭, 200건 파싱 배치, DB 스키마와 SQL은 변경하지 않았습니다.

## 검증

- `python -m pytest -q` → **37 passed** (모의 FTP 포함)
- `python -m compileall -q src tests run.py` → 성공
- 현장 구형 FTP 서버 실제 연결은 테스트하지 않았습니다.

## 적용

1. V3 실행 중이면 종료합니다.
2. 현재 프로젝트를 별도 폴더에 백업합니다.
3. ZIP의 `src/collectors/ftp/client.py`, `src/collectors/ftp/collector.py`를 기존 V3 프로젝트에 덮어씁니다. 전체 ZIP으로 교체해도 되지만, 기존 `.env`, `equipments.yaml`, 백업 폴더는 유지하세요.
4. `python -m pytest -q` 실행.
5. SMDI06 한 장비로 실행하여 `IL01_920562UM`의 `재귀 폴더 탐색 이상` 제외 로그와 그 다음 Recipe로 넘어가는지 확인합니다.
