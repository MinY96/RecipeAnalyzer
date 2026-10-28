# RecipeAnalyzer V3 — SMDI06 FTP 550 / 필수 파일 누락 대응

## 수정 배경

2026-10-27 SMDI06(KLA2139)에서 발견한 오류는 다음 두 종류입니다.

1. `550 /d:/KT1025/Usr/Ins8/OD42_666562UM/YV17_1565PAD3: No such file or directory.`
   - 이전 Recipe 폴더 아래에 다음 Recipe 경로가 합쳐져 보이는 오류.
   - FTP 서버의 현재 작업 폴더 상태 이상 또는 재귀 탐색 중 복귀 상태가 어긋난 가능성이 있습니다. 로그만으로 장비 서버의 정확한 내부 원인을 확정할 수는 없습니다.
2. `Required recipe file missing/empty: NS31_852562UM/ins.dat`
   - Recipe 이름 규칙에 해당하는 폴더에 실제 파싱 필수 파일이 없거나, 크기가 0인 경우.

기존 V2에서는 두 경우 모두 `download_failures`에 누적돼 수집 후 `collect_equip_data()`의 `RuntimeError`가 발생했고, 결과적으로 다음 장비로 넘어갔습니다.

## V3 처리 원칙

| 상황 | 재시도 | download_failures 증가 | 최종 처리 |
|---|---|---|---|
| 필수 파일 `ins.dat` / `Sensitivity1.xml` 없음 | LIST에 없을 때만 NLST 1회 추가 확인 | 아니요 | 해당 Recipe 제외, 다음 Recipe 처리 |
| 필수 파일 크기 0 | 다운로드 후 검증 | 아니요 | 해당 Recipe 제외, 기존 백업 유지 |
| `550 ... No such file or directory` | KLA2139은 새 FTP 연결 후 1회 재시도 | 아니요 | 2회 실패 시 해당 Recipe만 제외 |
| `550 Permission denied` | 위 예외 대상 아님 | 예 | 일반 다운로드 실패로 처리 |
| 실제 RETR 중단 / 로컬 디스크 오류 | 일반 오류 처리 | 예 | 연속 5건 시 장비 수집 중단 |
| FTP 복귀 PWD 불일치 | KLA2139은 새 연결 후 1회 재시도 | 일반 실패로 집계하지 않지만 안전 중단 가능 | 재연결 후에도 복구 불가능하면 장비 중단 |
| 루트 LIST 형식 또는 통신 장애 | 대상 Recipe 확정 불가 | 별도 치명 오류 | 삭제 오판을 막기 위해 장비 중단 |

> **수집 제외는 정상 백업 성공을 의미하지 않습니다.** 제외된 Recipe는 `dm_recipe_info`에 신규/수정 UPSERT하지 않으며 이전에 존재하던 백업·DB 정보만 보존합니다. FTP 최초 목록에서 관찰한 `recipe_key`는 `seen_keys`로 유지해 이번 회차 삭제 대상으로 처리하지 않습니다.

## 경로 복귀 로직

- `ftp_download_tree()`에서는 진입 전 PWD를 저장하고, 작업 종료 후 `ftp.cwd('..')`로 부모 폴더에 돌아갑니다.
- 실제 PWD가 기대했던 부모 경로와 일치하는지 확인합니다.
- KLA2139에서는 `FTPRecipeUnavailable` 또는 `FTPDirectoryError`가 발생하면 동일 세션을 사용하지 않고 새 연결을 만든 다음 해당 Recipe만 한 번 더 시도합니다.
- KLA2365/PUMA의 Device/Layer 탐색도 절대경로 복귀 대신 상대경로 복귀 및 PWD 확인을 사용합니다.

## 결과 요약 로그

```text
INFO ... [SMDI06] 수집 제외: 필수 파일 없음 45건, 경로 없음 3건
```

- `skipped_missing_file`: 필수 파일이 확인되지 않은 Recipe 수.
- `skipped_missing_path`: 경로 부재 550로 제외된 Recipe 수.
- `download_failures`: 실제 전송·접근권한·로컬 저장 등 기타 실패 수.
- 누적 제외 건수가 아주 많아도 로그를 수천 줄 출력하지 않도록, 개별 경고는 초반 10건과 이후 500건마다 출력합니다.
- 제외는 새로운 `parse_status`를 생성하지 않습니다. 기존 DB에 기록된 대기/실패 상태는 유지됩니다.

## 변경 파일

- `src/collectors/ftp/client.py`: 550 경로 없음 분류, 필수 파일 사전 검증, LIST → NLST 보완 조회, 상대경로 복귀.
- `src/collectors/ftp/collector.py`: KLA2139 재연결 1회 재시도, 제외 상태 분리, Device/Layer 복귀 검증, 집계 로그.
- `tests/test_v3_ftp_skips.py`: 550 재시도/제외, 누락 파일, 권한 오류 비제외, 기존 백업 보호, NLST 보완 조회 테스트.
- 그 밖에 `src/`, `run.py`, 테스트 내 함수/메서드에 한글 docstring을 추가했습니다.

## 현장 적용

1. 기존 소스 및 `.env`를 백업합니다. 새 ZIP으로 **Python 파일만 교체**하되 `.env`, 운영 YAML의 활성화 설정, 기존 백업 폴더는 유지합니다.
2. 기존 V2 DB에 `parse_status`와 `parse_error`가 이미 있다면 마이그레이션을 다시 실행할 필요가 없습니다.
3. `python -m pytest -q`로 로컬 테스트를 실행합니다.
4. 초기에는 SMDI06만 활성화해 `YV17_1565PAD3` 등 인접 Recipe의 다운로드 결과와 `수집 제외` 카운터를 확인합니다.
5. 실제 장비 FTP 서버 연결 시험은 작성 환경에서 수행하지 않았으므로, 운영 전 백업 후 소규모 시험을 권장합니다.

**유의:** 경로 550가 수천 건 발생하면 그 건수는 다운로드 실패에서 제외되지만, 실제로 백업되지 않은 Recipe가 많다는 뜻입니다. `수집 제외` 건수가 비정상적으로 크면 누락을 무시하지 말고 서버 상태와 필수 파일 이름을 반드시 확인해야 합니다.
