# 수량별 지급대상 입력 작업 로그

작성일: 2026-05-29 KST

## 문제

- 비소모품을 여러 개 구매해도 지급대상 입력이 품목당 1개만 표시됐다.
- 예: 모니터 수량이 4개면 부서/사용자/용도도 4명 또는 4부서로 나뉠 수 있는데 1줄만 입력 가능했다.

## 변경

- WMS 장바구니 구매 화면에서 비소모품 지급대상 입력을 품목 수량만큼 펼친다.
  - 예: `모니터 1/4`, `모니터 2/4`, `모니터 3/4`, `모니터 4/4`
- WMS 백엔드는 수량별 입력값을 `asset_recipients` 배열로 Purchase_Auto에 전달한다.
- Purchase_Auto는 `asset_recipients`를 받아 품의서 지급대상 표를 수량만큼 생성한다.
- 모델명 추출 규칙에 숫자로 시작하는 모니터 모델 코드를 추가했다.
  - 예: `27FD100SB 모니터` -> `27FD100SB`

## 검증

```powershell
& "C:\Users\user\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests\test_state_flow.py -q
& "C:\Users\user\AppData\Local\Programs\Python\Python311\python.exe" -m py_compile purchase_auto\models.py purchase_auto\groupware_approval.py tests\test_state_flow.py
node --check "C:\Users\user\Desktop\개발파일\DS_WMS\backend\routes\purchaseCart.js"
node "C:\Users\user\Desktop\개발파일\DS_WMS\frontend\node_modules\react-scripts\bin\react-scripts.js" build
```

- Purchase_Auto pytest: 37 passed
- Purchase_Auto py_compile: passed
- WMS backend route syntax check: passed
- WMS frontend build: passed with existing unrelated eslint warnings
