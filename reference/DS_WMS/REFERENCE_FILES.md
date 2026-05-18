# DS_WMS Reference Files

Source repository: `https://github.com/reum0009/DS_WMS`

| Source file | Future integration purpose |
| --- | --- |
| `backend/routes/dashboard.js` | 저재고 추천 조회 |
| `backend/routes/inbound.js` | 구매 완료 후 입고 확정 및 재고 증가 |
| `backend/routes/products.js` | 품목/안전재고 기준 조회 |
| `frontend/src/components/screens/WarehouseLowStock.js` | 후속 구매 버튼 연결 |

1차 구현에서는 WMS를 호출하지 않습니다. 세금계산서 수신 후 `completed` 상태가 안정화된 다음 WMS 입고 API 연동을 추가합니다.
