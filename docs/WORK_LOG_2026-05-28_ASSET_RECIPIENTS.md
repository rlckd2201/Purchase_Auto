# 비소모품 품목/모델명 및 지급대상 작업 로그

작성일: 2026-05-28 KST

## 문제

- 품의 구매가격 표에서 `품목`과 `모델명`이 상품명 전체 문자열로 중복 표기되었다.
  - 예: `L14150 완성형 정품무한잉크 복합기 ... : 컴퓨존`이 품목과 모델명에 같이 들어감.
- 집기비품, 컴퓨터소프트웨어, 비용성 항목이 포함된 구매에서 지급대상이 품목별로 입력되지 않고 전역 기본값으로 뭉개졌다.
  - 소모품을 제외한 품목별로 부서, 사용자, 용도를 구매 단계에서 받아야 한다.

## Purchase_Auto 변경

- `PurchaseItem`에 항목별 지급 정보 필드를 추가했다.
  - `asset_department`
  - `asset_user`
  - `asset_purpose`
  - `asset_note`
- 소모품이 아닌 품목인데 지급 정보가 없으면 품의 본문 생성 전에 오류를 낸다.
- 지급대상 입력 예외는 `소모품`만으로 정리했다.
  - `집기비품`, `컴퓨터소프트웨어`, `비용`은 모두 부서, 사용자, 용도 입력을 요구한다.
- 품목/모델 분리 규칙을 보강했다.
  - 복합기, 마이크, 웹캠, 스피커 등 집기비품 분류 추가
  - `L14150`, `SL-M2680N`, `K050`, `BE-GM3`, `H8008R-IGMP` 같은 모델 코드 추출
  - `: 컴퓨존`/`- 컴퓨존` 접미 제거
- 지급대상 비고는 항목별 입력일 때 `품목 / 모델` 형태로 구분한다.
  - 예: `복합기 / L14150`, `마이크 / K050`

## WMS 변경

- 장바구니 구매 설정 화면에 집기비품/소프트웨어 품목별 지급대상 입력 영역을 추가했다.
- 각 대상 품목마다 부서, 사용자, 용도를 필수로 입력하게 했다.
- WMS 브릿지가 Purchase_Auto `POST /api/purchase-jobs` payload의 각 item에 지급 정보를 전달한다.
- 일강 사업장 선택 시 품의 제목/본문의 공장명이 `D1공장`으로 fallback 되지 않도록 사업자번호 기준 공장 라벨을 추가했다.
  - `125-81-51622` -> `일강1공장`
  - `403-85-20895` -> `일강2공장`
- 구매 분류 기준을 실무 기준으로 보정했다.
  - 오래 쓰는 물건: `집기비품`
  - 영구/구매형 라이선스: `컴퓨터소프트웨어`
  - 구독, SaaS, 클라우드, 호스팅, 유지보수, 기술지원: `비용`
  - 쓰면 닳거나 없어지는 물건: `소모품`
- 지급대상 입력 기준은 `소모품`만 제외하고, `집기비품`, `컴퓨터소프트웨어`, `비용`은 모두 입력 대상으로 맞췄다.
- WMS 시작 시 DB 카테고리와 컴퓨존 매핑 룰도 위 기준으로 보정되게 했다.

## 검증

```powershell
& "C:\Users\user\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests\test_state_flow.py -q
& "C:\Users\user\AppData\Local\Programs\Python\Python311\python.exe" -m py_compile purchase_auto\groupware_approval.py tests\test_state_flow.py compuzone_history_parser.py tools\reclassify_compuzone_wms.py
node --check "C:\Users\user\Desktop\개발파일\DS_WMS\backend\routes\purchaseCart.js"
node --check "C:\Users\user\Desktop\개발파일\DS_WMS\backend\server.js"
cd "C:\Users\user\Desktop\개발파일\DS_WMS\frontend"
npm.cmd run build
```

- Purchase_Auto pytest: 35 passed
- Purchase_Auto py_compile: passed
- WMS backend route/server syntax check: passed
- WMS frontend build: passed with existing unrelated eslint warnings
