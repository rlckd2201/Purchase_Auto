# 구매 & 품의 단일 실행 / 품의 본문 미리보기

## 요청
- WMS 장바구니의 `컴퓨존 주문/견적 실행`과 `그룹웨어 품의 상신` 버튼을 하나의 실행 흐름으로 합친다.
- 계정 입력 단계에서 `그룹웨어 품의 본문 미리보기` 체크박스를 받는다.
- 체크 시 Purchase_Auto가 생성한 그룹웨어 품의 본문 HTML을 새 창에 렌더링한다.

## 처리 방향
- WMS는 `구매 & 품의 진행` 버튼 하나로 Purchase_Auto 작업 생성, 컴퓨존 주문/견적, 그룹웨어 품의 상신을 순서대로 실행한다.
- 기존처럼 품절 상품이 발생하면 사용자가 제외/대체를 선택할 수 있고, 제외 후 진행도 품의 상신까지 이어진다.
- Purchase_Auto는 실제 그룹웨어 화면을 열지 않고도 저장된 구매 작업 기준의 품의 제목과 본문 HTML을 반환하는 preview API를 제공한다.

## 변경 파일
- Purchase_Auto
  - `purchase_auto/app.py`
  - `purchase_auto/models.py`
  - `purchase_auto/services.py`
- DS_WMS
  - `backend/routes/purchaseCart.js`
  - `frontend/src/api/api.js`
  - `frontend/src/components/screens/AdminDashboard.js`

## 검증 예정
- Purchase_Auto: `py_compile`, 관련 pytest, `graphify update .`
- DS_WMS: frontend JS syntax check, backend route syntax check, production build
