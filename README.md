# RecipeAnalyzer V3.2 — FTP 장애 복구 및 수집 실행 이력

**V3.2는 V3.1 전체 기능을 유지하면서 FTP `RETR 550`을 필수/선택 파일로 분리하고, 연결 타임아웃 복구·장비별 실행 이력을 추가한 버전입니다.**

- 업데이트 상세: [V3_2_UPDATE.md](V3_2_UPDATE.md)
- **실행 전에 반드시 `sql/004_collection_run_history_f1.sql`을 DB에 적용**하세요.
- `POSTGRES_SCHEMA=f1` 및 기존 `.env` 유지 여부를 확인하세요.
- V3.1의 재귀 탐색 보호 기능은 그대로 포함합니다.

---

# V3.1 FTP 재귀 탐색 핫픽스

SMDI06의 동일 Recipe 디렉터리 반복 `LIST` 오류를 보호합니다. 상세 변경 사항은 [V3_1_FIX.md](V3_1_FIX.md)를 참고하세요.

# RecipeAnalyzer V3 — FTP 수집 안정화 및 한글 문서화

이 프로젝트는 2026-10-26 기준 `MinY96/RecipeAnalyzer`의 GitHub `master` 브랜치에 반영된 **999건 단위 `CollectionState.flush()`** 흐름을 기준으로 개선한 로컬 전체 프로젝트입니다. GitHub 원본에 직접 푸시하지 않았습니다.

> **DB 스키마 참고:** GitHub 원본 `create_db_f1.sql`에서 `f1.dm_recipe_info` / `dm_recipe_para`의 키와 외래키를 확인했습니다. V3는 V2 DB 구조를 유지하며 새 스키마를 요구하지 않습니다. Master 삭제 정책은 운영 시 `fk_master_recipe_key ON DELETE SET NULL`로 별도 적용했는지 확인하세요.

## V3 FTP 오류 보완 (2026-10-27)

자세한 처리 기준은 **[V3_UPDATE.md](V3_UPDATE.md)**를 참고하세요.

- `550 ... No such file or directory`: KLA2139 FTP 새 연결로 1회 재시도, 실패하면 해당 Recipe만 제외.
- `ins.dat` 누락: LIST 검사 → 누락 시에만 NLST 보완 검사 → 실제 없으면 제외.
- 필수 파일 크기 0: 기존 백업 보존, 실패가 아닌 제외 건수에만 집계.
- 실제 전송 실패·권한 오류·FTP 폴더 복귀 불가: 기존의 안전 중단 정책 유지.
- FTP `CWD ..` 복귀와 `PWD` 검사 강화.
- 전체 코드의 함수별 한글 docstring 추가(인자 및 반환 설명 포함).
- **수집 999건 UPSERT / 파싱 200건 / DB 스키마는 V2와 동일.**


## 반영 내역

| 개선 | 주요 변경 | 파일 |
|---|---|---|
| 1. FTP 목록 오류 시 삭제 오판 방지 | `LIST` 기반 구조화 탐색, 탐색 예외 전파, 기존 DB 키와 비교한 삭제율 제한(기본 25%), 미완료 시 최종 정산 차단 | `src/collectors/ftp/client.py`, `collector.py` |
| 1. 다운로드 안전성 | 임시 `.part` 파일로 전송 후 교체, 레시피 전체 스테이징 다운로드 및 필수 파일 존재·비어 있지 않음 확인, 실패 시 기존 로컬 백업 보호 | `client.py` |
| 2. 빈 파싱 결과·파싱 실패 | 빈 DataFrame 컬럼 명시, 비정상 파싱 오류 전파, `parse_status='failed'`와 에러 원인 저장, 모델별 XML 한 개/여러 개 노드 공통 처리 | `worker.py`, `src/preprocessing/parsers/*.py` |
| 3. 파싱 복구 가능 상태 | `parse_status=pending/success/failed`, `parse_error` 도입. Parameter 교체 + mode + 상태 전환을 동일 PostgreSQL 트랜잭션에서 처리 | `postgre_db.py`, `sql/001_recipe_parse_status.sql` |
| 4. Recipe 조회 O(N) 최적화 | 장비 DB 조회 결과를 `recipe_key` 딕셔너리로 한번만 인덱싱. `dt_edit_time` 문자열 유지 | `collector.py` |
| 5. 수집 999건 + 파싱 배치 | 999건 수집 UPSERT 유지, 파싱은 기본 200건씩 `recipe_key` 키셋 페이지 방식으로 처리 | `collector.py`, `worker.py` |
| 6. MES 정합성 / SQL | Oracle IN 목록 바인드 변수 사용(최대 900개씩); MES device가 복수 후보면 **임의 선택하지 않고 NULL + 경고**; PostgreSQL 삭제/유지 키는 `ANY(%s)` 바인드 처리 | `collector.py`, `postgre_db.py` |

## 최초 적용 순서

1. **운영 DB와 파일 백업** 후 원본 SQL DDL과 변경 내용을 대조합니다. 테이블 기본키/컬럼 구성, 앱 사용자 DB 권한, 실제 스키마(`public`, `f1`, `f2`)를 확인하세요.
2. 기존 `.env`가 있다면 반드시 유지하고, `.env.example`의 새로운 항목만 추가하세요. `.env`의 비밀번호 등 인증정보는 Git에 올리지 마세요.
3. 운영 대상 PostgreSQL 스키마를 선택하고 `sql/002_validate_schema.sql`을 먼저 검토·실행합니다. `parse_status`, `parse_error`가 없다면 `sql/001_recipe_parse_status.sql`을 **해당 스키마에서 딱 한 번** 실행합니다. `psql`에서는 `SET search_path TO f1, public;` 등을 먼저 지정합니다.
4. 백업 경로를 확인하고 `python -m pip install -r requirements.txt`를 수행합니다. 기존 프로젝트 Python 실행 환경(프로젝트 설정상 3.12 이상)을 확인하세요. Oracle 11g 연결을 위해 oracledb Thick Client가 설치되어야 합니다.
5. `python -m pytest -q`를 실행합니다.
6. 장비 1대만 `enabled: true`로 두고 `python run.py`를 먼저 검증합니다. 모든 장비가 확인되면 나머지 장비를 활성화합니다.

> 자동 삭제 보호 장치: `MAX_AUTO_REMOVAL_RATIO=0.25`면 이전 Recipe 중 25%를 넘는 삭제 후보가 생겼을 때 갱신을 차단합니다. **하지만 25% 이내의 FTP 목록 누락을 완벽하게 검출한다는 의미는 아닙니다.** 중요한 운영 환경에서는 삭제 후보를 재확인/승인하는 별도 운영 절차도 필요합니다. 비율을 올리기 전에 반드시 로그와 원격 폴더를 확인하세요.

## 실행 예시

```powershell
cd RecipeAnalyzer_v3_2
python -m pip install -r requirements.txt
python -m pytest -q
python run.py
```

- 백업 폴더는 `.env`의 `DATA_BASE_DIR`을 사용합니다.
- `RECIPE_BATCH_SIZE=999`: 신규/수정 Recipe 최대 버퍼 크기. **1~999** 범위만 허용합니다.
- `PARSE_BATCH_SIZE=200`: 파싱 큐 1회 조회 Recipe 수 (양의 정수).
- `POSTGRES_SCHEMA=f1` 또는 `f2`: PostgreSQL 스키마 검색 경로 선택. 한 프로세스는 한 스키마를 처리합니다. FAB2는 별도 실행 설정으로 운영하세요.
- `LOG_LEVEL=INFO`: 콘솔 로그 수준.
- `MAX_AUTO_REMOVAL_RATIO=0.25`: 수집 완료 시 한 번에 삭제 처리 가능한 최대 비율.

## 처리 상태와 장애 복구

- 신규/수정 레시피를 성공적으로 다운로드하면 `dm_recipe_info`에 `status='new'/'update'`, `parse_status='pending'`으로 UPSERT합니다.
- 파싱 성공 시 기존 `dm_recipe_para`의 해당 Recipe 파라미터를 대체하고 `mode`, `parse_status='success'`를 한 트랜잭션에서 갱신합니다.
- 파싱에 실패하면 `parse_status='failed'`, `parse_error`에 원인을 기록합니다. 다음 실행에서 재시도합니다. 파서가 예외를 숨기지 않으므로 실패 건수가 추적 가능합니다.
- FTP 통신 장애, 목록 형식 오류, 너무 큰 삭제율, **실제 파일 전송 실패** 등이 발생하면 이미 성공한 부분 UPSERT를 유지하고 장비 최종 삭제·집계를 보류합니다. **단 V3에서는 필수 파일 누락 및 명확한 550 경로 부재를 `수집 제외`로 별도 처리해 다운로드 실패에 포함하지 않습니다.**
- `Collector.run()`은 장비별 오류를 기록하고 다른 장비를 이어 처리합니다. `RecipeParser.run()`은 재처리 가능한 pending/failed 항목을 계속 처리합니다.

## 검증 범위와 주의 사항

- 포함된 테스트는 FTP LIST 형식, 스테이징 다운로드 실패 보호, 999건 버퍼, 문자열 시각 비교·딕셔너리 조회, MES 다중 매칭, 빈 파싱 및 재시도 등 **mock 기반 단위 테스트**입니다.
- 실제 장비의 FTP `LIST` 출력 형식(Unix 스타일), Oracle 11g 서비스, PostgreSQL 운영 DDL과 권한을 사용한 통합 테스트는 진행하지 못했습니다.
- ERP/MES에서 동일 장비 device에 복수 상이한 tech 메타데이터가 있으면 기술 컬럼은 NULL로 남기고 경고를 표시합니다. 정책에 따라 수동 매핑 규칙을 정해야 합니다.
- 새로운 `parse_status`/`parse_error` 컬럼을 직접 추가하지 않으면, 이 버전의 실행은 실패합니다. 마이그레이션은 **기존 테이블 재생성 스크립트가 아닙니다.**
- 파일 이름/경로는 기존 `KLA2139: ins.dat`, `KLA2365/PUMA: Sensitivity1.xml`을 유지했습니다. 모델별 폴더 계층과 필수 파일명을 현장에서 다시 검증해야 합니다.
