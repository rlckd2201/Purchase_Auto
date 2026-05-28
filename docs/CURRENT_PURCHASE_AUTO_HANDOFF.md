# Current Purchase_Auto Handoff

Updated: 2026-05-28 KST

이 문서는 컨텍스트 압축 후 바로 이어가기 위한 현재 작업 기록이다.
다음 세션은 전체 대화 기록을 찾기 전에 이 파일부터 읽는다.

민감정보 주의:

- 계정 비밀번호, API 키, 토큰은 이 문서에 저장하지 않는다.
- 실제 값은 서버 환경변수 또는 운영자가 입력한 값만 사용한다.
- 문서에는 경로, 브랜치, 설정 변수명, 작업 순서만 남긴다.

## 빠른 요약

- 목표: WMS 장바구니에서 Compuzone 구매와 그룹웨어 품의 상신을 실행한다.
- Purchase_Auto는 별도 FastAPI 서비스로 실행된다.
- WMS는 Purchase_Auto API를 호출하는 브릿지 역할만 한다.
- Compuzone 주문은 요청 품목 전체를 하나의 주문으로 생성해야 한다.
- 그룹웨어 품의는 해당 주문에 대해 하나의 문서로 상신해야 한다.
- WMS 재고 증가는 세금계산서 수신 후에만 가능하다.

## 로컬/서버 위치

로컬 작업 repo:

- `C:\Users\user\Desktop\개발파일\구매, 품의 자동화`
- GitHub: `https://github.com/rlckd2201/Purchase_Auto`
- Branch: `KGC`

서버 설치 위치:

- Purchase_Auto: `C:\Purchase_Auto\app`
- WMS backend: `C:\Program Files (x86)\WarehousePOS\backend`
- WMS URL: `http://172.16.19.35:5000`
- Purchase_Auto API: `http://127.0.0.1:5008`
- Python: `C:\Python311\python.exe`

관련 저장소:

- WMS: `https://github.com/reum0009/DS_WMS`
- Purchase_Auto: `https://github.com/rlckd2201/Purchase_Auto`

## 프로젝트 규칙

작업 시작 시 읽을 것:

1. `AGENTS.md`
2. `graphify-out/GRAPH_REPORT.md`
3. 필요 시 `graphify-out/graph.json`

코드 변경 후:

```powershell
graphify update .
```

검색은 우선 `rg`를 사용한다.
수동 파일 수정은 `apply_patch`를 사용한다.

## 운영 환경변수

Purchase_Auto 실행 관련:

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

Compuzone/그룹웨어 비밀번호는 문서나 코드에 넣지 않는다.
필요하면 서버 Machine/User 환경변수에만 저장한다.

업데이트 패키지 생성 관련:

- `UPDATE_GIT_REPO=https://github.com/reum0009/DS_WMS.git`
- `UPDATE_GIT_BRANCH=KGC`
- `GIT_SSL_NO_VERIFY=true`

## 서버 반영 명령

Purchase_Auto 변경 반영:

```powershell
cd C:\Purchase_Auto\app
git -c http.sslVerify=false pull origin KGC

Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match "purchase_auto" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

pm2 restart warehouse-pos --update-env
pm2 save
```

WMS 업데이트 패키지는 WMS 화면의 시스템 업데이트에서 생성/적용한다.
GitHub 인증서 문제가 있으면 서버에서 Git SSL 검증 해제 상태를 확인한다.

## 로그 위치

WMS 브릿지 로그:

- `C:\Program Files (x86)\WarehousePOS\backend\logs\purchase-auto-bridge.log`

Purchase_Auto 프로세스 로그:

- `C:\Program Files (x86)\WarehousePOS\backend\logs\purchase-auto-process.log`

WMS 업데이트 로그:

- `C:\Program Files (x86)\WarehousePOS\update.log`

장애 분석 시 우선순위:

1. WMS 화면의 자동화 진단 로그
2. `purchase-auto-bridge.log`
3. `purchase-auto-process.log`
4. Compuzone/그룹웨어 브라우저 화면

## 핵심 사용자 요구

- Compuzone 주문은 모든 요청 품목을 한 주문으로 묶는다.
- 그룹웨어 품의도 한 주문당 한 문서로 상신한다.
- 세금계산서 수신 전에는 WMS 재고를 증가시키지 않는다.
- 품목 중 집기비품/소프트웨어가 하나라도 있으면 자산/노트북형 본문을 사용한다.
- 전결 기준은 총액이 아니라 최고 단가 기준이다.
- 품의 본문 구매내역 표는 단가 내림차순으로 정렬한다.
- 배송비가 0원이면 배송비 행은 넣지 않는다.
- 자산 구매 대상자 표는 대상자 + 품목 라벨 행으로 확장한다.
- 배송지는 사업자/법인 선택과 독립되어야 한다.
- 품의 제목의 공장명은 사업장이 아니라 배송지 선택 기준으로 바뀌면 안 된다.
- 사용자에게 보이는 긴 오류 로그는 줄바꿈되어야 하며 화면 밖으로 튀면 안 된다.

## 최근 해결한 문제

### 일강 그룹웨어 양식 URL 미설정

증상:

- 일강 사업장 품의 상신 시 `일강 그룹웨어 양식 URL이 설정되지 않았습니다.` 오류 발생.

원인:

- `PURCHASE_AUTO_GROUPWARE_FORM_URL_ILGANG` 값이 비어 있으면 즉시 중단했다.

수정:

- 일강 URL이 비어 있으면 `/app/approval/document/new`로 진입한다.
- 기본 작성 화면에서 `일강 - (경영)기안용지` 라벨을 찾아 클릭한다.
- URL이 있는 대승/대승정밀은 기존 직접 URL 방식을 유지한다.

검증:

- `pytest tests\test_state_flow.py -q`: 28 passed
- `py_compile purchase_auto\groupware_approval.py tests\test_state_flow.py`: passed
- `graphify update .`: 완료

최신 커밋:

- `53362f1 Support groupware form fallback by label`

### 업체 직배송 장바구니 개수 처리

증상:

- Compuzone 장바구니에 컴퓨존 배송상품과 업체 직배송상품이 나뉘면 요청 수량과 장바구니 수량 비교가 틀렸다.
- 예: 요청 4건, 장바구니 화면에는 4건인데 컴퓨존 배송상품 3건만 세서 실패.

수정 방향:

- 장바구니 검증 시 컴퓨존 배송상품과 업체 직배송상품을 합산해야 한다.

관련 커밋:

- `bb6ca57 Count direct-delivery cart items`

### Compuzone 장바구니 버튼 감지

증상:

- 상품 상세 페이지에서 실제 장바구니 버튼이 `basket_insert_detail2` 또는 `basket_insert_detail` 형태로 나타났다.
- 기존 선택자가 `a.cart` 또는 `basket_insert_direct`에 과도하게 의존해 실패했다.

수정 방향:

- 구매 영역 내 `basket_insert_detail`, `basket_insert_detail2`, 추천 PC 액션 등을 후보로 감지한다.
- 실패 시 긴 진단 로그는 축약해서 보여준다.

관련 커밋:

- `21dcf5c Handle Compuzone detail basket action`
- `965c28a Wait for Compuzone cart iframe insert`
- `ce15d2a Support Compuzone recommend PC cart action`
- `91e078b Shorten Compuzone cart failure errors`

### Compuzone 자동화 중복 실행

증상:

- 이전 브라우저/프로세스가 남아 `이미 실행 중` 오류가 계속 발생했다.

수정 방향:

- 새 실행 전에 stale 상태를 초기화한다.
- 필요 시 관련 `purchase_auto` 프로세스를 종료하고 다시 시작한다.

관련 커밋:

- `d4a2c3b Guard duplicate Purchase Auto browser runs`
- `755e486 Reset stale Compuzone automation before rerun`

### P4 사업자 선택 오류

증상:

- P4공장으로 구매했는데 P3 사업자번호를 선택하는 문제가 있었다.

수정 방향:

- 사업장 선택과 배송지 선택을 분리한다.
- P4 선택 시 P4 사업자번호/담당자를 우선 사용한다.

관련 커밋:

- `b69b249 Fix P4 tax business selection`

### 품의 본문 구매내역 표 파싱 오류

증상:

- 그룹웨어 본문 구매내역이 `제품코드`, 수량, 금액을 잘못 압축하거나 복제했다.
- 품목/모델/수량/단가/금액이 서로 뒤섞였다.

수정 방향:

- 견적서/주문 데이터에서 실제 품목명, 제조사, 모델, 수량, 단가, 금액을 분리해 본문 표에 넣어야 한다.
- 총액은 행별 금액 합산과 주문 총액이 맞아야 한다.

관련 커밋:

- `b18a6a7 Fix approval product line parsing`

## 최근 커밋 흐름

```text
53362f1 Support groupware form fallback by label
bb6ca57 Count direct-delivery cart items
91e078b Shorten Compuzone cart failure errors
f8a5268 Fix Compuzone cart product verification
b18a6a7 Fix approval product line parsing
b69b249 Fix P4 tax business selection
3122d72 Add Compuzone order progress logging
755e486 Reset stale Compuzone automation before rerun
ce15d2a Support Compuzone recommend PC cart action
d4a2c3b Guard duplicate Purchase Auto browser runs
965c28a Wait for Compuzone cart iframe insert
21dcf5c Handle Compuzone detail basket action
```

## 주요 파일

Purchase_Auto API:

- `purchase_auto/app.py`
- `purchase_auto/services.py`
- `purchase_auto/models.py`
- `purchase_auto/db.py`
- `purchase_auto/config.py`

Compuzone:

- `purchase_auto/compuzone_order.py`

Groupware:

- `purchase_auto/groupware_approval.py`
- `purchase_auto/corps.py`

Tests:

- `tests/test_state_flow.py`

WMS integration side:

- WMS repo `reum0009/DS_WMS`
- 주요 변경은 WMS `backend/routes/purchaseCart.js` 및 프론트 장바구니 화면에서 이루어짐.

## 다음 세션에서 우선 확인할 것

1. 서버가 최신 `KGC` 커밋인지 확인한다.
   ```powershell
   cd C:\Purchase_Auto\app
   git log --oneline -5
   ```

2. Purchase_Auto API가 live 상태인지 확인한다.
   ```powershell
   Invoke-RestMethod http://127.0.0.1:5008/health
   ```

3. WMS 브릿지 로그에서 최신 요청 body를 확인한다.
   ```powershell
   Get-Content "C:\Program Files (x86)\WarehousePOS\backend\logs\purchase-auto-bridge.log" -Tail 80
   ```

4. Compuzone 장바구니 실패가 나오면 화면의 실제 상품 개수와 `컴퓨존 배송상품`, `업체 직배송상품` 그룹을 함께 센다.

5. 그룹웨어 품의 본문이 이상하면 먼저 실제 `approval_document_url`을 열어 구매내역 표와 주문번호/금액/첨부를 확인한다.

## 서버에 새 패치 올리는 기본 순서

로컬에서 수정:

```powershell
pytest tests\test_state_flow.py -q
graphify update .
git status --short
git add <changed-files>
git commit -m "<message>"
git push origin KGC
```

서버에서 반영:

```powershell
cd C:\Purchase_Auto\app
git -c http.sslVerify=false pull origin KGC

Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match "purchase_auto" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

pm2 restart warehouse-pos --update-env
pm2 save
```

## 하지 말 것

- 비밀번호/API 키를 문서나 코드에 저장하지 말 것.
- 사용자가 만든 unrelated 파일을 되돌리지 말 것.
- Compuzone 주문과 그룹웨어 품의를 여러 건으로 쪼개지 말 것.
- 세금계산서 수신 전 WMS 재고 증가 처리하지 말 것.
- 품절 상품을 조용히 삭제하지 말 것. 사용자에게 제외/대체 선택을 보여줘야 한다.
- 긴 오류 로그를 화면 오른쪽으로 밀어내지 말 것.
