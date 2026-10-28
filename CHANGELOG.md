## V3.2 — 2026-10-28
- `RETR ins.dat` / `RETR Sensitivity1.xml`에서 정확한 `550 No such file` 응답을 확인하고 1회 재시도. 두 번 없으면 필수 파일 누락으로 제외.
- `RETR lowds.img`처럼 선택 파일이 사라졌으면 해당 파일만 제외하고 정상 Recipe 백업·UPSERT 진행.
- FTP 목록 조회, Recipe 수집, Device 재탐색 중 타임아웃 시 연결 폐기 후 제한적 재연결·이어 수집.
- 완료된 Recipe 키를 중복 저장하지 않도록 추적. 소켓 장애는 일반 다운로드 실패 횟수에 산입하지 않음.
- 로컬 .part 파일 열기 PermissionError는 제한적 재시도, 지속 실패 시 일반 오류 처리.
- `dm_collection_run_history` 신규 테이블로 running/success/partial/failed를 기록. `dm_update_history`는 정상 완료 시만 업데이트.
- 새 SQL `sql/004_collection_run_history_f1.sql`, 상세 적용 문서 `V3_2_UPDATE.md`, 자동 테스트 보강.

## V3.1 — 2026-10-27
- SMDI06 동일 Recipe 폴더 재귀 탐색 차단 및 단계별 PWD 무결성 확인
- 비정상 재귀 LIST 발견 시 재연결 후 1회 재시도, 이후 별도 제외 처리
- 최대 8단계 탐색 제한 및 회귀 테스트 5개 추가

# RecipeAnalyzer V3 변경내역 (2026-10-27)

- KLA2139 Recipe 경로 `550 No such file or directory` 발생 시 새 FTP 세션으로 1회 재시도.
- 재시도 이후에도 경로가 없는 Recipe는 `skipped_missing_path`로 관리하며 다운로드 실패 수에 포함하지 않음.
- 필수 파일 `ins.dat` 또는 `Sensitivity1.xml` 누락/0바이트인 Recipe는 `skipped_missing_file`로 관리하며 다음 Recipe를 계속 수집.
- LIST의 필수 파일 조회가 누락된 경우에만 NLST 1회 보완 확인하여 정상 파일의 오제외 최소화.
- 원격 디렉터리 복귀를 `CWD ..` 및 `PWD` 검증으로 강화.
- 실제 FTP 통신 장애/권한 오류/파일 전송 실패와 정상 제외 사유를 분리.
- 제외 누적 로그 요약과 대량 반복 로그 억제 추가.
- 전체 애플리케이션/테스트의 함수·메서드 한글 docstring 보완.
- 기존 수집 999건 UPSERT·파싱 배치·DB 저장 방식을 변경하지 않음.

---

# RecipeAnalyzer 변경내역 (운영 안정화 1~6)

- 999건 UPSERT chunk 유지. 실패 시 buffer 보존. 최대 chunk 범위 검증.
- FTP NLST 대신 Unix LIST 사용, 디렉터리 명 누락 대비. 부정확한 응답이면 예외.
- 필수 Recipe 파일을 별도 staging으로 다운로드, 오류 시 기존 Recipe 보존.
- FTP 전체 탐색 실패, 빈 탐색, 다운로드 오류, 위험한 대량 제거 비율이면 삭제/집계 차단.
- 기존 Recipe DB 조회를 pandas 반복 필터링에서 dict lookup으로 변경.
- 문자열 dt_edit_time 유지, lexicographic timestamp comparison.
- 새 Recipe/변경 Recipe UPSERT 시 parse_status=pending. 삭제된 Recipe 복원 시 is_remove=false 반영.
- 파싱 상태 pending/success/failed 관리. 오류 메시지 기록 및 다음 회차 재시도.
- 파싱 큐를 keyset 기준 PARSE_BATCH_SIZE 행으로 읽고 처리.
- 파라미터 전체 교체 + mode 갱신 + parse 성공 마킹을 단일 트랜잭션 처리.
- 0개 parse 결과 DataFrame KeyError 해결.
- Oracle MES bind 인자 사용, Oracle 900 Device 단위 분할, 중복 MES 정보 검증.
- PostgreSQL 삭제/유지 키는 ANY(%s) 파라미터 바인딩.
- 장비 스캔 최종 삭제/유지 상태 갱신 및 update history 기록 원자적 처리.
- 소스만으로 안전하게 추론 가능한 새 필드에 한정한 비파괴 DB migration 및 사전 검증 SQL 동봉.
- 19개 단위 테스트 통과(내부 DB fake). 실제 운영 연결 검증 필요.
