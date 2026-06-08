# 그룹웨어 품의 문서 자동작성 기술명세서

작성일: 2026-06-01

## 목적

구매 자동화가 컴퓨존 주문/견적까지 완료한 뒤, 그룹웨어 품의 문서 작성 화면을 열고 제목, 전결 기준, 참조자, 첨부, 본문을 자동으로 입력한다.

중요: 이 기능은 `상신`하지 않는다. 사용자가 열린 그룹웨어 화면에서 직접 확인하고 수정한 뒤 수동으로 상신한다.

## 현재 구조

현재 Purchase_Auto에는 이미 그룹웨어 상신 자동화가 있다.

- API: `POST /api/purchase-jobs/{job_id}/submit-approval`
- 서비스: `purchase_auto/services.py::submit_approval_step`
- 실제 브라우저 자동화: `purchase_auto/groupware_approval.py::submit_groupware_approval`
- 본문 생성: `purchase_auto/groupware_approval.py::_approval_body_html`
- 제목 생성: `purchase_auto/groupware_approval.py::_approval_title`
- 미리보기 API: `GET /api/purchase-jobs/{job_id}/approval-preview`

기존 `_live_submit()` 흐름은 아래 순서다.

1. 그룹웨어 양식 URL 또는 전자결재 홈으로 이동
2. 로그인 확인
3. 양식 선택
4. 본문 HTML 생성
5. 전결권자 설정
6. 전결 규정 입력
7. 제목 입력
8. 견적서 PDF 첨부
9. 재정 참조자 추가
10. 본문 입력
11. 본문 입력 검증
12. 결재요청 버튼 클릭
13. 완료 문서 URL 검증
14. 작업 상태를 `approval_submitted`, 이후 `waiting_tax_invoice`로 변경

이번 기능은 1~11까지만 수행하고 12~14는 절대 실행하지 않는다.

## 신규 기능 이름

권장 명칭:

- 서비스 함수: `fill_approval_draft_step`
- 그룹웨어 함수: `fill_groupware_approval_draft`
- API: `POST /api/purchase-jobs/{job_id}/fill-approval-draft`
- WMS 프록시 API: `POST /api/purchase-cart/jobs/:jobId/fill-approval-draft`
- 응답 상태 문자열: `approval_draft_filled`

## 입력 조건

이 기능은 컴퓨존 주문/견적 완료 뒤 실행한다.

필수 조건:

- `job.order_no` 존재
- `job.quote_pdf_path` 존재
- `job.quote_pdf_path` 파일 존재
- 그룹웨어 로그인 ID/PW 존재
- 해당 법인의 그룹웨어 양식명 또는 양식 URL 확인

실패 처리:

- 주문번호 없음: `컴퓨존 주문번호가 없어 품의 문서를 작성할 수 없습니다.`
- 견적서 없음: `컴퓨존 견적서 PDF가 없어 품의 문서를 작성할 수 없습니다.`
- 양식명 미확인: `그룹웨어 양식명을 찾지 못했습니다. 양식명을 설정한 뒤 다시 실행하세요.`

## API 설계

### Purchase_Auto

```http
POST /api/purchase-jobs/{job_id}/fill-approval-draft
```

요청 예시:

```json
{
  "groupware_login_id": "user01",
  "groupware_login_password": "password",
  "approval_form_label": "추후 확정 양식명",
  "keep_browser_open": true
}
```

`approval_form_label`은 선택값이다. 다른 세션에서 최종 양식명을 받으면 이 값으로 넘긴다. 없으면 `purchase_auto/corps.py`의 법인별 기본 양식명을 사용한다.

응답 예시:

```json
{
  "job": {},
  "message": "그룹웨어 품의 작성 화면에 내용을 입력했습니다. 상신은 하지 않았습니다.",
  "draft": {
    "raw_status": "approval_draft_filled",
    "current_url": "https://gw.dae-seung.co.kr/app/approval/document/new/...",
    "form_label": "대승정밀 - (관리총괄)기안용지(관리직)",
    "browser_session_id": "groupware-draft-{job_id}"
  }
}
```

### WMS

WMS는 Purchase_Auto API를 그대로 프록시한다.

```http
POST /api/purchase-cart/jobs/:jobId/fill-approval-draft
```

프론트 API 함수:

```js
fillApprovalDraft: (jobId, data = {}) =>
  axios.post(`${API_BASE_URL}/purchase-cart/jobs/${jobId}/fill-approval-draft`, data, { headers: getAuthHeader() })
```

## 브라우저 세션 유지 설계

작성 화면을 사용자가 봐야 하므로 브라우저를 닫으면 안 된다.

기존 `_live_submit()`은 `with sync_playwright()`와 `context.close()`로 끝나면 브라우저가 닫힌다. 신규 draft 기능은 다음 방식 중 하나로 구현한다.

1. 기존 Chrome CDP 사용 권장
   - `PURCHASE_AUTO_GROUPWARE_CDP_URL` 사용
   - `settings.allow_existing_browser_cdp=True`
   - 기존 사용자 Chrome 탭에 문서를 작성하고 함수 종료 후에도 창이 남는다.

2. 자체 persistent context 사용 시
   - `sync_playwright().start()`를 직접 사용한다.
   - `browser/context/page` 객체를 모듈 전역 `_DRAFT_SESSIONS` 딕셔너리에 보관한다.
   - 함수 종료 시 `context.close()`를 호출하지 않는다.
   - 별도 정리 API를 둘 수 있다.

권장 전역 세션 형태:

```python
_DRAFT_SESSIONS: dict[str, DraftSession] = {}
```

세션 키:

```text
groupware-draft-{job_id}
```

선택 정리 API:

```http
POST /api/purchase-jobs/{job_id}/close-approval-draft-session
```

## 창 앞으로 가져오기

자동 입력이 끝났을 때 사용자가 바로 볼 수 있어야 한다.

자동 입력 완료 직후 아래 순서로 실행한다.

```python
page.bring_to_front()
page.evaluate("window.focus()")
```

가능하면 context 생성 시 아래 옵션을 사용한다.

```python
headless=False
args=[
    "--start-maximized",
    "--disable-save-password-bubble",
    "--disable-features=PasswordManagerOnboarding,PasswordManagerEnabled",
]
```

CDP로 기존 Chrome에 붙은 경우도 최종 단계에서 `page.bring_to_front()`를 호출한다.

## 그룹웨어 자동 입력 상세 순서

신규 `fill_groupware_approval_draft(job, settings, approval_form_label=None)` 함수는 기존 `_live_submit()`의 helper를 재사용한다.

실행 순서:

1. `corp = CORPS[job.corp_code]`
2. `configured_form_url = settings.groupware_form_urls.get(job.corp_code, "")`
3. 양식 URL이 있으면 해당 URL로 이동
4. 양식 URL이 없으면 `https://gw.dae-seung.co.kr/app/approval`로 이동
5. `_ensure_groupware_session(page, settings, form_url)`
6. 양식 URL이 없으면 양식명으로 양식 선택
   - `approval_form_label` 요청값이 있으면 우선 사용
   - 없으면 `corp.approval_form_label` 사용
7. `body_html = _approval_body_html(job)`
8. `_set_delegate_level(page, _delegate_level_for_job(job))`
9. `_fill_approval_rule(page, _approval_rule_for_job(job))`
10. `_fill_title(page, _approval_title(job))`
11. `_attach_quote(page, Path(job.quote_pdf_path))`
12. `_add_finance_reference_group(page, corp)`
13. `_fill_body(page, body_html)`
14. `_assert_body_ready_for_submit(page, body_html)`
15. `page.bring_to_front()`
16. 응답 반환

금지:

- `_request_approval(page)` 호출 금지
- 완료 문서 URL 대기 금지
- `_assert_submitted_body_visible()` 호출 금지
- `PurchaseStatus.APPROVAL_SUBMITTED` 변경 금지
- `WAITING_TAX_INVOICE` 변경 금지

## 상태 처리

상신을 하지 않으므로 기존 job 상태는 유지한다.

권장:

- `order_submitted_pending_payment` 상태 유지
- 로그만 추가

로그 예시:

```text
그룹웨어 품의 작성 화면 자동입력을 시작합니다.
그룹웨어 품의 작성 화면에 내용을 입력했습니다. 상신은 하지 않았습니다.
```

DB에 별도 상태를 추가하고 싶다면 `approval_draft_filled`를 추가할 수 있지만, 세금계산서 대기로 넘어가면 안 된다.

## 법인 및 사업자 정보

현재 기준 법인 정보는 `purchase_auto/corps.py`와 WMS 구매 매핑에 맞춘다.

| 법인 코드 | 표시명 | 법인명 | 공장 | 사업자번호 | 기존 기본 양식명 |
|---|---|---|---|---|---|
| `daeseung` | 대승 | `(주)대승` | D1공장 | `125-81-05619` | `대승 - (관리총괄)기안용지(관리직)` |
| `daeseung` | 대승 | `(주)대승` | D2공장 | `403-85-07607` | `대승 - (관리총괄)기안용지(관리직)` |
| `daeseung` | 대승 | `(주)대승` | D3공장 | `403-85-23311` | `대승 - (관리총괄)기안용지(관리직)` |
| `daeseung_precision` | 대승정밀 | `대승정밀(주)` | P1공장 | `125-81-32697` | `대승정밀 - (관리총괄)기안용지(관리직)` |
| `daeseung_precision` | 대승정밀 | `대승정밀(주)` | P2공장 | `403-85-15640` | `대승정밀 - (관리총괄)기안용지(관리직)` |
| `daeseung_precision` | 대승정밀 | `대승정밀(주)` | P3공장 | `844-85-00770` | `대승정밀 - (관리총괄)기안용지(관리직)` |
| `daeseung_precision` | 대승정밀 | `대승정밀(주)` | P4공장 | `118-85-07029` | `대승정밀 - (관리총괄)기안용지(관리직)` |
| `ilgang` | 일강 | `(주)일강` | 일강1공장 | `125-81-51622` | `일강 - (경영)기안용지` |
| `ilgang` | 일강 | `(주)일강` | 일강2공장 | `403-85-20895` | `일강 - (경영)기안용지` |

다른 세션에서 새 양식명을 받으면 아래 중 하나로 반영한다.

1. 요청 body의 `approval_form_label`
2. `purchase_auto/corps.py`의 `approval_form_label`
3. 환경변수 기반 form URL

양식 URL 환경변수:

```text
PURCHASE_AUTO_GROUPWARE_FORM_URL_DAESEUNG
PURCHASE_AUTO_GROUPWARE_FORM_URL_DAESEUNG_PRECISION
PURCHASE_AUTO_GROUPWARE_FORM_URL_ILGANG
```

## WMS 화면 흐름 권장

버튼명은 기존처럼 `구매 & 품의 진행` 또는 새 목적에 맞게 `구매 & 품의 작성`을 사용한다.

권장 흐름:

1. 사용자가 구매 버튼 클릭
2. 집기비품/컴퓨터소프트웨어 지급대상 입력 모달이 필요한 경우 먼저 입력
3. 컴퓨존 계정, 그룹웨어 계정 입력
4. 컴퓨존 주문/견적 실행
5. 그룹웨어 품의 작성 화면 자동입력 API 호출
6. 자동입력이 끝나면 그룹웨어 창을 앞으로 가져옴
7. WMS에는 `그룹웨어 작성 화면에 입력 완료. 상신은 그룹웨어 화면에서 직접 진행하세요.` 표시

상신 버튼 또는 `submit-approval` 호출은 이 흐름에 넣지 않는다.

## 본문 생성 규칙

본문 HTML은 기존 `_approval_body_html(job)`을 그대로 사용한다.

유지해야 할 규칙:

- 소모품은 소모품 본문 템플릿 사용
- 집기비품 및 컴퓨터소프트웨어는 지급대상 입력이 필요
- 구매내역 표의 품목/모델은 WMS에서 넘긴 `product_name`, `product_specification`, `product_model`을 우선 사용
- 사업장 라벨은 사업자번호 매핑을 우선 사용
- 소모품 본문 2번 `구매내역` 표와 3번 `입금계좌 정보` 표는 같은 가로폭을 유지

현재 소모품 표 폭 상수:

```python
_CONSUMABLE_TABLE_STYLE = "width: 748px; min-width: 748px; max-width: 100%; table-layout: auto;"
```

## 테스트 기준

필수 테스트:

1. `fill_groupware_approval_draft`가 `_request_approval`을 호출하지 않는지 확인
2. draft API 호출 후 job 상태가 `approval_submitted` 또는 `waiting_tax_invoice`로 바뀌지 않는지 확인
3. 양식명이 요청 body로 들어오면 corp 기본 양식명보다 우선되는지 확인
4. 대승, 대승정밀, 일강 사업자번호별 공장 라벨이 정확한지 확인
5. 본문 입력 후 `_assert_body_ready_for_submit`이 통과하는지 확인
6. 브라우저 세션이 닫히지 않고 열린 작성 화면이 남는지 확인
7. 소모품 본문 2번/3번 표 폭이 같은지 확인

권장 명령:

```powershell
python -m py_compile purchase_auto\groupware_approval.py purchase_auto\services.py purchase_auto\app.py purchase_auto\models.py
python -m pytest tests\test_state_flow.py -q
```

## 구현 시 주의사항

- 이 기능은 상신 자동화가 아니라 작성 보조 기능이다.
- 기존 `submit-approval` API를 수정해서 상신 동작을 약하게 만드는 방식은 피한다.
- 새 API를 분리해서 사용자가 명확히 `작성만` 실행할 수 있게 한다.
- 브라우저를 닫지 않는 요구 때문에 기존 `_live_submit()`의 finally 구조를 그대로 복사하면 안 된다.
- 그룹웨어 양식명이 확정되기 전에는 요청 body override를 열어둔다.
- 나중에 양식명이 확정되면 `corps.py` 기본값 또는 환경변수 URL로 고정한다.
