# Purchase_Auto

`Purchase_Auto` 1차 버전은 WMS 연동보다 먼저 안정화해야 하는 핵심 흐름에 집중합니다.

목표 흐름:

1. 구매 담당자가 법인과 `컴퓨존 상품 URL + 수량` 목록을 입력합니다.
2. 담당자 PC의 로그인된 컴퓨존 세션으로 상품을 장바구니에 담고 수량을 맞춥니다.
3. 결제수단을 `무통장입금`으로 선택해 주문을 생성합니다.
4. 주문번호 기반으로 컴퓨존 견적서 PDF를 저장합니다.
5. 법인별 그룹웨어 양식으로 품의를 작성하고 견적서 PDF를 첨부합니다.
6. 결재 정보의 참조자 개인 그룹에 법인별 재정팀 그룹을 추가한 뒤 `결재요청`까지 진행합니다.
7. 세금계산서 메일 수신이 확인되면 구매 완료로 처리합니다.

WMS 저재고 추천, 구매 버튼, 입고 확정 및 재고 증가는 이 흐름이 검증된 뒤 붙이는 후속 단계입니다.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m purchase_auto
```

기본값은 `PURCHASE_AUTO_DRY_RUN=1`입니다. 이 상태에서는 실제 컴퓨존 주문이나 그룹웨어 상신 없이 상태 흐름과 PDF 저장만 검증합니다.

실제 주문/상신은 담당자 PC 브라우저 세션 확인 후 아래 값을 명시적으로 켜야 합니다.

```powershell
$env:PURCHASE_AUTO_DRY_RUN="0"
$env:PURCHASE_AUTO_ENABLE_LIVE_COMPUZONE_ORDER="1"
$env:PURCHASE_AUTO_ENABLE_LIVE_GROUPWARE_SUBMIT="1"
```

## Public APIs

- `POST /api/purchase-jobs`
- `GET /api/purchase-jobs`
- `GET /api/purchase-jobs/{job_id}`
- `POST /api/purchase-jobs/{job_id}/run-compuzone-order`
- `POST /api/purchase-jobs/{job_id}/submit-approval`
- `POST /api/purchase-jobs/{job_id}/mark-tax-invoice-received`

예시:

```powershell
$body = @{
  corp = "대승"
  title = "컴퓨존 비품 구매 품의"
  requester = "구매담당자"
  memo = "관리총괄 비품 보충"
  items = @(
    @{ url = "https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=123456"; quantity = 2 }
  )
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5008/api/purchase-jobs -ContentType application/json -Body $body
```

## Status

- `created`
- `cart_ready`
- `order_submitted_pending_payment`
- `quote_saved`
- `approval_submitted`
- `waiting_tax_invoice`
- `tax_invoice_received`
- `completed`
- `failed`

## Legal Entity Defaults

| 법인 | 그룹웨어 양식 | 참조자 개인 그룹 |
| --- | --- | --- |
| 대승 | 대승 - (관리총괄)기안용지(관리직) | 재정_대승 |
| 대승정밀 | 대승정밀 - (관리총괄)기안용지(관리직) | 재정_대승정밀 |
| 일강 | 일강 - (경영)기안용지 | 재정_일강 |

## Notes

- 운영 계정, 비밀번호, 세션 프로필, 내부 URL은 `.env`로 관리하고 Git에 올리지 않습니다.
- 그룹웨어 세션이 만료되어 로그인 화면이 보이면 자동 로그인하지 않고 `login_required` 오류로 멈춥니다.
- 원본 `ERP_Auto_Web`와 `DS_WMS` 저장소는 수정하지 않습니다. 참고한 파일 목록은 `reference/`와 `docs/`에 남깁니다.
