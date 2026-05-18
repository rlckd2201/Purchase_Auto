# Migration Notes

## ERP_Auto_Web에서 참고한 구조

- `web_v1/backend/compuzone_quote.py`
  - 기존 역할은 주문번호 기반 컴퓨존 견적서 PDF 확보입니다.
  - `Purchase_Auto`에서는 이 흐름을 주문 생성 이후 단계로 흡수했습니다.

- `web_v1/backend/purchase_analysis.py`
  - 기존 구매 증빙 분석과 문서세트 상태 관리 관점을 참고했습니다.
  - 1차 구현은 품목 분석보다 URL/수량 입력과 상태 전이를 우선합니다.

- `web_v1/backend/approval_fetcher.py`
  - 기존 역할은 이미 존재하는 그룹웨어 품의 PDF 확보입니다.
  - `Purchase_Auto`에서는 반대로 신규 품의를 작성/상신하는 worker를 둡니다.

- Agent/queue 구조
  - 담당자 PC의 로그인된 브라우저 세션을 사용하는 방향은 유지합니다.
  - v1은 API 호출 즉시 동기 실행하는 단순 구조로 두고, 운영 안정화 후 큐/Agent로 분리합니다.

## DS_WMS에서 후속 연동에 참고할 지점

- `backend/routes/dashboard.js`
  - `/api/dashboard/alerts/low-stock`를 통해 저재고 추천 후보를 가져올 수 있습니다.

- `backend/routes/inbound.js`
  - `POST /api/inbound`가 입고 확정과 재고 증가의 기준점입니다.

- `frontend/src/components/screens/WarehouseLowStock.js`
  - 후속 단계에서 `발주` 버튼을 `Purchase_Auto` 작업 생성 API로 연결합니다.

## 보안 메모

원본 저장소의 운영 계정, 비밀번호, 내부 DB 접속 정보는 새 저장소에 복사하지 않습니다. 필요한 설정값은 `.env.example`의 환경변수 이름으로만 남깁니다.
