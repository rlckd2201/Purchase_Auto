# ERP_Auto_Web Reference Files

Source repository: `https://github.com/rlckd2201/ERP_Auto_Web`

Current reference commit: `e2f2fc7 Add Chrome job notifications`

| Source file | Purchase_Auto replacement |
| --- | --- |
| `web_v1/backend/compuzone_quote.py` | `purchase_auto/compuzone_order.py` |
| `web_v1/backend/purchase_analysis.py` | `purchase_auto/services.py`, `purchase_auto/models.py` |
| `web_v1/backend/approval_fetcher.py` | `purchase_auto/groupware_approval.py` |
| `web_v1/backend/app.py` | `purchase_auto/app.py` |
| `web_v1/backend/worker.py` | 후속 Agent/queue 분리 시 참고 |
| `web_v1/frontend/app.js` | 후속 화면 구현 시 참고 |

원본 파일은 일부 운영 계정/환경 의존 정보가 섞일 수 있어 새 공개 저장소에 그대로 복사하지 않았습니다.
