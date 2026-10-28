# 전체 Recipe 재파싱 (`parse_all_recipes.py`)

## 목적

FTP 수집이나 `run.py` 실행 없이 `dm_recipe_info`의 `backup_file_path`가 **NULL이 아니고 공백이 아닌 모든 행**을 모델별 Parser로 다시 처리하여 `dm_recipe_para`를 재구성한다.

- 기본값은 `is_remove=true`와 기존 `parse_status='success'`도 **포함**한다. 이미 파싱한 성공 Recipe도 새로 파싱한다.
- DB에 경로가 있지만 실제 백업 파일이 없으면 `parse_status='failed'`, `parse_error`에 오류를 기록한다.
- DB에 `backup_file_path`가 없거나 빈 문자열인 행은 **대상에서 제외**하며 상태도 변경하지 않는다.
- `model`은 DB의 `dm_recipe_info.model` 컬럼을 사용한다. 지원 모델: `KLA2139`, `KLA2365`, `PUMA`.
- `POSTGRES_SCHEMA`는 기존 `.env`에서 사용한다. 예: `f1`. `DATA_BASE_DIR` 역시 `.env` 값이다.
- `dm_collection_run_history`는 사용하지 않는다.

## 실행 방법

프로젝트 **루트**(`run.py`와 동일한 위치)에서 실행한다.

```powershell
# 최초 10건을 대상으로 파싱만 검증하고 DB는 수정하지 않음
python parse_all_recipes.py --dry-run --limit 10

# 특정 장비만 10건 실제 저장해 보기
python parse_all_recipes.py --equip SMDI01 --limit 10

# 백업 경로가 있는 모든 Recipe 재파싱
python parse_all_recipes.py

# 수집 중단 뒤 성공 처리된 Recipe는 건너뛰고 나머지만 재파싱
python parse_all_recipes.py --only-unfinished

# 논리 삭제(is_remove=true) Recipe는 제외
python parse_all_recipes.py --active-only

# 특정 장비의 재처리 대상을 100건씩 저장
python parse_all_recipes.py --equip SMDI08 --batch-size 100
python parse_all_recipes.py --batch-size 800
```

두 개 장비를 동시에 지정할 때는 `--equip SMDI01 --equip SMDI02`를 사용한다.

## DB 처리 방식

1. `recipe_key` 오름차순 **키셋 배치 조회** (기본 200건).
2. 각 행의 `model`에 맞는 V3.2 Parser를 선택하고 백업 파일 확인 및 파싱.
3. 파싱 성공한 Recipe는 V3.2 기존 `PostgresCRUD.replace_recipe_parameters()`를 재사용.
   - `dm_recipe_para`에서 해당 Recipe의 과거 Parameter 삭제.
   - 새로운 Parameter INSERT/UPSERT, `dm_recipe_info.mode` 갱신(파싱 결과에 mode가 있을 때).
   - `dm_recipe_info.parse_status='success'`, `parse_error=NULL`.
   - 위 전체 작업은 **한 트랜잭션**. DB 저장이 실패하면 해당 배치 전체 ROLLBACK 후 건별 저장으로 재시도.
4. 파일 누락, 지원하지 않는 모델, 파싱 실패는 `parse_status='failed'` 및 `parse_error`(2000자 이내) 기록.
5. 실패 상세는 `logs/bulk_parse_failed_YYYYMMDD_HHMMSS.csv`에 저장.

`parse_status`가 `success`인 행까지 기본으로 재파싱하므로 기존 Parameter의 `matching` 값은 새 파싱 결과로 교체될 때 `NULL`로 초기화된다. Recipe/Master Matching은 후속 집계가 필요하다.

## 주의 사항

- **`run.py` 또는 다른 Recipe 동기화·매칭 작업과 동시 실행하지 않는다.**
- 실제 대량 업데이트 전 PostgreSQL 백업을 권장한다.
- 백업 파일이 실제로 존재해야 성공하며, XML/텍스트 Parser가 구조적으로 해석할 수 없는 파일은 `failed`로 남는다.
- 파일 백업 폴더를 삭제하거나 FTP 서버와 연결하지 않는다.
- `dm_recipe_info`의 논리 삭제 상태(`is_remove`)와 수집 상태(`status`)는 변경하지 않는다.
- 오류가 발생한 경우에도 이미 COMMIT된 Recipe는 그대로 남는다. 다시 실행할 때는 `--only-unfinished` 옵션을 활용할 수 있다.
- 모든 대상의 경로는 기존 V3.2 `recipe_path()`로 검증하므로 `DATA_BASE_DIR` 바깥으로 나가는 경로를 차단한다.
- 초기 전체 재파싱 후 `parse_status` 분포를 확인한다.

```sql
SELECT equip, parse_status, COUNT(*) AS cnt
FROM f1.dm_recipe_info
WHERE backup_file_path IS NOT NULL
  AND BTRIM(backup_file_path) <> ''
GROUP BY equip, parse_status
ORDER BY equip, parse_status;
```
