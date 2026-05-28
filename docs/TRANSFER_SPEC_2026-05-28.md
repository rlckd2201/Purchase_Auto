# Purchase_Auto 이관명세서

작성일: 2026-05-28 KST

## 목적

다음 Codex 세션이 Purchase_Auto + WMS 장바구니 구매 자동화 작업을 바로 이어받기 위한 이관 문서다.
전체 대화 로그를 다시 훑기 전에 이 파일과 `docs/CURRENT_PURCHASE_AUTO_HANDOFF.md`를 먼저 읽는다.

민감정보 주의:

- Compuzone/그룹웨어 계정 비밀번호, API 키, 토큰은 코드/문서/커밋에 저장하지 않는다.
- 실제 비밀번호는 서버 환경변수 또는 실행 시 입력값만 사용한다.
- 이 문서에는 경로, 브랜치, 커밋, 환경변수 이름, 작업 상태만 기록한다.

## 저장소와 경로

로컬 작업 저장소:

- `C:\Users\user\Desktop\개발파일\구매, 품의 자동화`
- GitHub: `https://github.com/rlckd2201/Purchase_Auto`
- Branch: `KGC`
- 최신 커밋: `0a6b1a8 Fix Ilgang groupware form fallback`

서버 설치 경로:

- Purchase_Auto: `C:\Purchase_Auto\app`
- WMS backend: `C:\Program Files (x86)\WarehousePOS\backend`
- WMS: `http://172.16.19.35:5000`
- Purchase_Auto API: `http://127.0.0.1:5008`
- Python: `C:\Python311\python.exe`

관련 저장소:

- WMS: `https://github.com/reum0009/DS_WMS`
- Purchase_Auto: `https://github.com/rlckd2201/Purchase_Auto`

## 작업 전 필수 확인

1. `AGENTS.md`
2. `graphify-out/GRAPH_REPORT.md`
3. 필요 시 `graphify-out/graph.json`
4. `docs/CURRENT_PURCHASE_AUTO_HANDOFF.md`
5. 이 파일

코드 변경 후에는 반드시:

```powershell
graphify update .
```

## 서버 반영 명령

Purchase_Auto 변경을 서버에 반영할 때:

```powershell
cd C:\Purchase_Auto\app
git -c http.sslVerify=false pull origin KGC

Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match "purchase_auto" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

pm2 restart warehouse-pos --update-env
pm2 save
```

## 운영 환경변수

Purchase_Auto 실행:

- `PURCHASE_AUTO_PROJECT_DIR=C:\Purchase_Auto\app`
- `PURCHASE_AUTO_PYTHON=C:\Python311\python.exe`
- `PURCHASE_AUTO_HOST=127.0.0.1`
- `PURCHASE_AUTO_PORT=5008`
- `PURCHASE_AUTO_DRY_RUN=0`
- `PURCHASE_AUTO_ENABLE_LIVE_COMPUZONE_ORDER=1`
- `PURCHASE_AUTO_ENABLE_LIVE_GROUPWARE_SUBMIT=1`
- `PURCHASE_AUTO_ALLOW_EXISTING_BROWSER_CDP=0`
- `PURCHASE_AUTO_COMPUZONE_CDP_URL=`
- `PURCHASE_AUTO_GROUPWARE_CDP_URL=`

그룹웨어 양식 URL:

- 대승: `PURCHASE_AUTO_GROUPWARE_FORM_URL_DAESEUNG`
- 대승정밀: `PURCHASE_AUTO_GROUPWARE_FORM_URL_DAESEUNG_PRECISION`
- 일강: `PURCHASE_AUTO_GROUPWARE_FORM_URL_ILGANG`

일강은 URL이 비어 있어도 최신 코드에서 양식 라벨 탐색 fallback을 시도한다.
그래도 안정 운영하려면 운영자가 일강 양식을 한 번 열어 실제 URL을 `PURCHASE_AUTO_GROUPWARE_FORM_URL_ILGANG`에 설정하는 것이 가장 확실하다.

## 핵심 업무 규칙

- Compuzone 주문은 요청 품목 전체를 한 주문으로 생성한다.
- 그룹웨어 품의도 해당 주문에 대해 한 문서만 상신한다.
- WMS 재고 증가는 세금계산서 수신 후에만 한다.
- 집기비품/소프트웨어가 하나라도 있으면 자산/노트북형 본문을 사용한다.
- 전결 기준은 총액이 아니라 최고 단가 기준이다.
- 품의 구매내역 표는 단가 내림차순 정렬이다.
- 배송비가 0원이면 배송비 행은 생략한다.
- 배송지 선택과 사업자/법인 선택은 독립되어야 한다.
- 품의 제목의 공장명은 배송지 선택 때문에 사업장이 바뀌면 안 된다.
- 긴 오류 로그는 UI에서 줄바꿈되어야 하며 화면 밖으로 튀면 안 된다.

## 최근 문제와 처리 상태

### 1. 일강 그룹웨어 양식 URL 미설정

증상:

- 일강 품의 상신 시 `일강 그룹웨어 양식 URL이 비어 있고... 양식을 찾지 못했습니다` 오류.
- `/app/approval/document/new`에서 `결재문서를 열람할 수 없습니다. 일시적인 오류입니다.` 모달이 뜨며 양식 선택이 막힘.

처리:

- `purchase_auto/groupware_approval.py` 수정.
- 일강 URL이 비어 있으면 기본 작성 화면으로 진입 후 에러 모달을 닫는다.
- `새 결재`, `새결재`, `기안하기`, `결재 작성` 메뉴를 차례로 시도한다.
- `/app/approval/document/new/223` 후보 URL도 시도한다.
- `일강 - (경영)기안용지` 라벨을 찾아 클릭한다.
- 양식 클릭이 새 창/탭으로 열리면 새 페이지를 사용한다.

검증:

- `pytest tests\test_state_flow.py -q`: 28 passed
- `graphify update .`: 완료
- 커밋/푸시: `0a6b1a8 Fix Ilgang groupware form fallback`

남은 확인:

- 서버에서 `git pull origin KGC` 후 일강 품의 상신을 실제로 다시 테스트해야 한다.

### 2. 업체 직배송 장바구니 수량

증상:

- 장바구니 화면에 컴퓨존 배송상품 3개 + 업체 직배송상품 1개가 있는데 자동화가 3개로 세서 실패.

처리:

- 장바구니 수량 검증 시 컴퓨존 배송상품과 업체 직배송상품을 합산하도록 수정됨.
- 관련 커밋: `bb6ca57 Count direct-delivery cart items`

### 3. Compuzone 장바구니 버튼 감지

증상:

- 상세 페이지 장바구니 버튼이 상품별로 `basket_insert_detail`, `basket_insert_detail2`, 추천 PC 액션 등으로 달라 실패.

처리:

- 구매 영역 후보 점수화/iframe 응답 대기/추천 PC 액션 처리 추가.
- 관련 커밋:
  - `ce15d2a Support Compuzone recommend PC cart action`
  - `21dcf5c Handle Compuzone detail basket action`
  - `965c28a Wait for Compuzone cart iframe insert`
  - `91e078b Shorten Compuzone cart failure errors`

남은 리스크:

- Compuzone 사이트 DOM이 자주 바뀌므로, 새 상품군에서 실패하면 `purchase_auto/compuzone_order.py`의 장바구니 후보 탐지 로그부터 본다.

### 4. 자동화 중복 실행

증상:

- 이전 Playwright/자동화 프로세스가 남아 `컴퓨존 주문/견적 자동화가 이미 실행 중입니다` 오류 반복.

처리:

- stale 실행 상태 초기화 로직 추가.
- 서버 반영 시 `purchase_auto` 프로세스를 강제 종료 후 WMS를 재시작하는 절차 사용.

관련 커밋:

- `d4a2c3b Guard duplicate Purchase Auto browser runs`
- `755e486 Reset stale Compuzone automation before rerun`

### 5. P4 사업자 선택 오류

증상:

- P4공장 구매인데 P3 사업자번호를 선택하는 문제.

처리:

- 배송지/사업장 선택 분리.
- P4 선택 시 P4 사업자번호와 담당자 우선 사용.
- 관련 커밋: `b69b249 Fix P4 tax business selection`

### 6. 품의 본문 구매내역 표 오류

증상:

- 품의 본문 표가 제품코드, 수량, 단가, 금액을 잘못 복제하거나 압축.

처리:

- 구매내역 표를 실제 라인 기준으로 다시 구성.
- 단가 내림차순, 행별 수량/단가/금액 분리.
- 관련 커밋: `b18a6a7 Fix approval product line parsing`

남은 확인:

- 일강 품의 본문에서도 사업장/배송지/금액/입금계좌가 맞는지 실제 문서에서 재검증 필요.

## 로그 위치

WMS 브릿지 로그:

- `C:\Program Files (x86)\WarehousePOS\backend\logs\purchase-auto-bridge.log`

Purchase_Auto 프로세스 로그:

- `C:\Program Files (x86)\WarehousePOS\backend\logs\purchase-auto-process.log`

WMS 업데이트 로그:

- `C:\Program Files (x86)\WarehousePOS\update.log`

장애 분석 순서:

1. WMS 장바구니 화면의 자동화 진단 로그
2. `purchase-auto-bridge.log`
3. `purchase-auto-process.log`
4. 실제 Compuzone/그룹웨어 브라우저 화면

## 다음 세션 권장 작업 순서

1. 서버에 최신 Purchase_Auto 반영:
   - `cd C:\Purchase_Auto\app`
   - `git -c http.sslVerify=false pull origin KGC`
   - `purchase_auto` 프로세스 종료
   - `pm2 restart warehouse-pos --update-env`
2. 일강 사업장으로 실제 테스트:
   - 장바구니 생성
   - Compuzone 주문/견적 실행
   - 그룹웨어 품의 상신
3. 실패 시 `자동화 로그` 버튼에서 상세 로그 복사.
4. 일강 양식 fallback이 또 실패하면:
   - 운영자가 그룹웨어에서 `일강 - (경영)기안용지`를 직접 열어 URL 확인
   - 서버에 `PURCHASE_AUTO_GROUPWARE_FORM_URL_ILGANG` 설정
   - 재시작 후 재테스트
5. 성공하면 품의 문서에서 다음을 육안 검증:
   - 법인/사업장
   - 배송지
   - 구매내역 표 품목/모델/수량/단가/금액
   - 입금계좌 정보
   - 주문번호
   - 첨부 견적서

## 현재 작업트리 참고

커밋된 변경:

- `0a6b1a8 Fix Ilgang groupware form fallback`

미추적 파일이 있을 수 있다:

- `.codex/`
- `AGENTS.md`
- `SESSION_STATE.md`
- `compuzone_purchase_history.csv`
- `compuzone_purchase_history.json`
- `docs/NEXT_SESSION_HANDOFF.md`

이 파일들은 현재 이관 작업에서 커밋 대상이 아니다.
