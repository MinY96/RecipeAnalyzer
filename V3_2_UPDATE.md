# RecipeAnalyzer V3.2 업데이트 / 적용 가이드

> 기준: 사용자가 실행한 V3의 5개 장비 수집 로그(SMDI01/SMDI02/SMDI03/SMDI08/SMPW06)와 기존 V3.1 재귀 탐색 보호 코드.
> 기존 `dm_recipe_info`, `dm_recipe_para`, `dm_update_history` 구조는 유지합니다.

## 1. 개선 요약

| 운영 사례 | V3.2 처리 |
|---|---|
| SMDI01 `RETR ins.dat` 에서 `550 No such file or directory` | `RETR` 1회 재시도 후 동일한 **명확한 파일 없음** 오류라면 `skipped_missing_file`로 관리. Recipe/기존 백업 보존, 일반 실패 건수 제외 |
| SMDI02 `RETR lowds.img`에서 550 | 선택 파일만 1회 재시도한 후 명확히 없으면 파일 하나를 건너뛰고 나머지 Recipe 저장. `skipped_optional_files`에 기록 |
| SMDI02 `NLST` Timeout 후 `CWD ..`도 실패, 새 FTP 연결 시간 초과 | 손상된 연결을 닫고 여러 차례 재연결. 성공하면 완료 Recipe를 건너뛰고 해당 Recipe/Device에서 재개. 전부 실패하면 `partial/failed`로 종료, 삭제 및 `dm_update_history` 보류 |
| SMPW06 로컬 `Test1.xml.part` PermissionError | 로컬 파일 열기 최대 3회 재시도. 계속 실패하면 일반 다운로드 실패로 기록하여 최종 삭제/정산 보호 |
| V3.1의 재귀 폴더 반복 | V3.1의 재귀 순환 탐지/깊이 제한과 안전한 PWD 복귀 기능 유지 |

**주의:** `550 Permission denied` 및 그 밖의 원인이 명확하지 않은 `550`은 '파일 없음'으로 무시하지 않습니다. 550에서 재시도하는 것은 파일 `RETR` 명령에 한합니다. FTP 세션 오류·폴더 복귀 불일치는 별도의 안전 중단/재연결 정책을 적용합니다.

## 2. DB 작업 (처음 한 번만)

### 반드시 실행할 SQL

`sql/004_collection_run_history_f1.sql`

- `f1.dm_collection_run_history` 테이블과 인덱스만 생성합니다.
- 기존 DB에 이미 `parse_status`, `parse_error`가 있다면 **001은 다시 실행할 필요 없습니다.**
- 실제 `.env`의 `POSTGRES_SCHEMA=f1`이 대상 스키마와 같은지 확인하세요.
- 새 이력 테이블에 INSERT/UPDATE 가능한 DB 계정을 사용하세요.
- 이 테이블이 없으면 V3.2는 시작 이력을 기록할 수 없으므로 해당 장비 수집을 실행하지 않습니다(명시적인 실패). 따라서 **SQL을 먼저 적용**하세요.

### 실행 이력 상태

| scan_status | 시점/의미 | dm_update_history |
|---|---|---|
| `running` | 장비 수집 시작 직후. 프로세스 강제 종료 시 그대로 남을 수 있음 | 기록 전 |
| `success` | 전체 탐색, 다운로드 실패 검증, 삭제/유지 정산 및 기존 집계 완료 | 저장 |
| `partial` | 일부 발견·저장 후 연결 실패 또는 미해결 다운로드 실패/삭제 안전장치 중단 | 신규 집계하지 않음 |
| `failed` | Recipe 탐색/저장 실적 없이 장비 수집 실패 | 신규 집계하지 않음 |

`new_rcp`, `update_rcp`에는 **UPSERT 성공이 확인된 행 수**만 기록합니다. `partial`인 경우 기존 `dm_update_history`를 완료한 것처럼 추가하지 않습니다.

### DB 이력 조회 예시

```sql
SELECT equip, model, started_at, finished_at, scan_status,
       new_rcp, update_rcp, remove_rcp, unchanged_rcp,
       download_failures, skipped_missing_file, skipped_missing_path,
       skipped_recursive_listing, skipped_optional_files, reconnect_count,
       error_message
FROM f1.dm_collection_run_history
ORDER BY started_at DESC
LIMIT 100;
```

이전 V3에서 실패했을 때 누락되었던 `dm_update_history` 기록을 역으로 채워 넣지는 않습니다. V3.2부터 모든 실행을 별도의 테이블에 기록합니다.

## 3. FTP 재연결 정책

`.env` 추가 설정 (미지정 시 기본값 적용):

```dotenv
POSTGRES_SCHEMA=f1
FTP_RECONNECT_ATTEMPTS=3
FTP_RECONNECT_DELAY_SECONDS=2
LOCAL_FILE_OPEN_RETRIES=3
LOCAL_FILE_OPEN_DELAY_SECONDS=0.25
```

- 최초 목록 조회·Recipe 수집·KLA2365/PUMA Device 탐색에서 타임아웃 시 제어 연결을 폐기합니다.
- 새 연결에서 루트 목록 또는 현재 Device를 다시 처리합니다. `completed_keys`로 중복 저장은 방지합니다.
- 재연결 성공 횟수는 `reconnect_count`에 기록합니다.
- 서버가 계속 무응답이면 횟수 제한 이후 해당 장비를 중단하고 **이미 UPSERT한 Recipe는 유지**, 삭제 판정 및 정상 완료 이력 저장을 하지 않습니다.
- 한 번에 999건으로 처리 중인 수집 버퍼는 예외 종료 시 가능한 경우 마지막으로 UPSERT를 시도합니다. 버퍼 저장도 실패하면 성공 건수로 집계하지 않습니다.
- 통신 실패와 파일 없음은 다른 문제입니다. 권한 오류, DB 장애, 지속적인 로컬 PermissionError는 임의로 성공 처리하지 않습니다.

## 4. 기존 기능 유지

- Recipe 신규·수정 수집 **999건 배치 UPSERT** 유지.
- 파싱 `pending/success/failed`, 기본 200건 파싱·재시도, 파라미터 교체 트랜잭션 유지.
- 삭제 후보 비율 25% 보호, FTP 목록 누락으로 인한 대량 Soft Delete 방지 유지.
- SMDI06에 대한 V3.1 FTP 자기참조·재귀 깊이 제한 유지.
- 모든 신규 함수는 한글 docstring을 포함합니다.

## 5. 적용 순서

1. **진행 중인 V3 run.py를 종료할 때는 모든 장비가 끝나도록 기다리거나, 현재 장비가 어디까지 진행했는지 기록하세요.** 실행 중 소스 교체/DB 컬럼 변경은 권장하지 않습니다.
2. 현재 `.env`, `src/config/equipments.yaml`, 수집 파일 디렉터리 및 PostgreSQL DB를 백업합니다.
3. PostgreSQL에 `sql/004_collection_run_history_f1.sql`을 실행하고 계정의 접근 권한 및 `POSTGRES_SCHEMA`를 검증합니다.
4. V3.2 파일로 교체하되 기존 `.env`를 그대로 유지하고, 필요한 설정값만 추가합니다.
5. 아래 명령으로 테스트를 확인합니다.

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
python run.py
```

6. 첫 검증은 한 대 장비로 수행하고 `dm_collection_run_history`의 `scan_status`, `skipped_optional_files`, `reconnect_count`를 확인합니다.

## 6. 시험 범위 및 제약

- 로컬 단위 테스트에서 두 가지 RETR 550, FTP 목록 타임아웃·재연결·이어 수집, 부분/실패/성공 이력, 로컬 파일 잠금, V3.1 보호 조건을 검증했습니다.
- 실장비 FTP/Oracle/PostgreSQL 서버에 연결한 통합 테스트는 수행하지 않았습니다.
- 네트워크 분리가 지속되는 동안에는 자동으로 무한 재시도하지 않습니다.
- 임의의 손상된 권한/경로 오류를 '파일 없음'으로 오분류하지 않도록 보수적으로 설계했습니다.
