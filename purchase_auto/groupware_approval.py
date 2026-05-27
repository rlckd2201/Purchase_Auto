from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

from .config import Settings, load_settings
from .corps import CORPS, CorpConfig
from .models import PurchaseJob


class ApprovalAutomationNotEnabledError(RuntimeError):
    pass


class GroupwareLoginRequiredError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApprovalResult:
    document_id: str
    document_url: str
    raw_status: str


@dataclass(frozen=True)
class ApprovalProductLine:
    name: str
    quantity: int
    unit_price: int
    amount: int


FACTORY_BY_BUSINESS_NUMBER = {
    "125-81-05619": "D1공장",
    "403-85-07607": "D2공장",
    "403-85-23311": "D3공장",
    "125-81-32697": "P1공장",
    "403-85-15640": "P2공장",
    "844-85-00770": "P3공장",
    "118-85-07029": "P4공장",
}
_FACTORY_RE = re.compile(r"\b([DP])\s*([1-4])\s*공장\b", re.IGNORECASE)
_BUSINESS_NUMBER_RE = re.compile(r"\b(\d{3})-?(\d{2})-?(\d{5})\b")
_PRODUCT_CODE_LINE_RE = re.compile(
    r"^\s*(?:제품코드|상품코드|상품번호|Product\s*No\.?)\s*[:：]?\s*\d+\s*$",
    re.IGNORECASE,
)


def _factory_label_from_text(value: str) -> str:
    match = _FACTORY_RE.search(value or "")
    if not match:
        return ""
    return f"{match.group(1).upper()}{match.group(2)}공장"


def _business_number_from_text(value: str) -> str:
    match = _BUSINESS_NUMBER_RE.search(value or "")
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _is_suspicious_approval_product_line(name: str, quantity: int, unit_price: int, amount: int) -> bool:
    normalized_name = re.sub(r"\s+", " ", name or "").strip()
    if not normalized_name or _PRODUCT_CODE_LINE_RE.match(normalized_name):
        return True
    if quantity > 999:
        return True
    expected = unit_price * quantity
    tolerance = max(1000, abs(expected) // 20)
    return abs(expected - amount) > tolerance


def submit_groupware_approval(job: PurchaseJob, settings: Settings | None = None) -> ApprovalResult:
    settings = settings or load_settings()
    if settings.dry_run:
        return _dry_run_submit(job, settings)
    if not settings.enable_live_groupware_submit:
        raise ApprovalAutomationNotEnabledError(
            "실제 그룹웨어 상신은 PURCHASE_AUTO_ENABLE_LIVE_GROUPWARE_SUBMIT=1 일 때만 실행합니다."
        )
    return _live_submit(job, settings)


def _dry_run_submit(job: PurchaseJob, settings: Settings) -> ApprovalResult:
    document_id = f"DRY-GW-{job.job_id[:8]}"
    return ApprovalResult(
        document_id=document_id,
        document_url=f"{settings.groupware_base_url.rstrip('/')}/app/approval/document/{document_id}",
        raw_status="dry_run_approval_submitted",
    )


def _live_submit(job: PurchaseJob, settings: Settings) -> ApprovalResult:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    corp = CORPS[job.corp_code]
    form_url = settings.groupware_form_urls.get(job.corp_code, "")
    if not form_url:
        raise RuntimeError(f"{corp.display_name} 그룹웨어 양식 URL이 설정되지 않았습니다.")
    if not job.quote_pdf_path or not Path(job.quote_pdf_path).exists():
        raise RuntimeError("견적서 PDF가 없어 품의를 상신할 수 없습니다.")

    settings.groupware_profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        close_context = True
        if settings.groupware_cdp_url and settings.allow_existing_browser_cdp:
            browser = p.chromium.connect_over_cdp(settings.groupware_cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context(accept_downloads=True)
            close_context = False
        else:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(settings.groupware_profile_dir),
                headless=settings.headless,
                accept_downloads=True,
                args=[
                    "--disable-save-password-bubble",
                    "--disable-features=PasswordManagerOnboarding,PasswordManagerEnabled",
                ],
            )
        page = context.new_page()
        try:
            page.goto(form_url, wait_until="domcontentloaded", timeout=60000)
            _ensure_groupware_session(page, settings, form_url)
            body_html = _approval_body_html(job)
            _set_delegate_level(page, _delegate_level_for_job(job))
            _fill_approval_rule(page, _approval_rule_for_job(job))
            _fill_title(page, _approval_title(job))
            _attach_quote(page, Path(job.quote_pdf_path))
            _add_finance_reference_group(page, corp)
            _fill_body(page, body_html)
            _assert_body_ready_for_submit(page, body_html)
            _request_approval(page)
            try:
                page.wait_for_url(re.compile(r".*/app/approval/document/(?!new(?:/|$))[0-9A-Za-z_-]+.*"), timeout=60000)
            except Exception as exc:
                _save_debug_screenshot(page, job, settings, "groupware_submit_not_confirmed")
                raise RuntimeError("그룹웨어 결재요청 후 완료 문서 URL로 이동하지 못했습니다.") from exc
            page.wait_for_load_state("domcontentloaded", timeout=60000)
            _assert_submitted_body_visible(page, job)
            document_url = page.url
            body_text = page.locator("body").inner_text(timeout=10000)
            document_id = _extract_document_id(document_url, body_text)
            return ApprovalResult(document_id=document_id, document_url=document_url, raw_status="approval_submitted")
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"그룹웨어 자동화 대기시간을 초과했습니다: {exc}") from exc
        except Exception:
            _save_debug_screenshot(page, job, settings, "groupware_error")
            raise
        finally:
            if close_context:
                context.close()


def _save_debug_screenshot(page, job: PurchaseJob, settings: Settings, stem: str) -> None:
    try:
        debug_dir = settings.artifact_dir / job.job_id
        debug_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(debug_dir / f"{stem}.png"), full_page=True)
    except Exception:
        pass


def _delegate_level_for_amount(amount: int | None) -> str:
    value = amount or 0
    if value < 300_000:
        return "파트장"
    if value < 1_000_000:
        return "팀장"
    if value < 3_000_000:
        return "본부장"
    if value < 10_000_000:
        return "총괄"
    return "부회장"


def _approval_rule_for_amount(amount: int | None) -> str:
    value = amount or 0
    if value < 300_000:
        return "21-4-1. 30만원 미만 ( 개당 구매금액 )"
    if value < 1_000_000:
        return "21-4-2. 100만원 미만 ( 개당 구매금액 )"
    if value < 3_000_000:
        return "21-4-3. 300만원 미만 ( 개당 구매금액 )"
    if value < 10_000_000:
        return "21-4-4. 1000만원 미만 ( 개당 구매금액 )"
    return "21-4-5. 1000만원 이상 ( 개당 구매금액 )"


def _approval_basis_amount(job: PurchaseJob) -> int:
    product_lines = _approval_product_lines(job)
    if product_lines:
        return max(line.unit_price for line in product_lines)
    quantity = sum(item.quantity for item in job.items) or 1
    amount = job.amount or 0
    shipping = _shipping_fee_for_consumable(job, amount, quantity)
    return max(amount - shipping, 0) // quantity if quantity else amount


def _delegate_level_for_job(job: PurchaseJob) -> str:
    return _delegate_level_for_amount(_approval_basis_amount(job))


def _approval_rule_for_job(job: PurchaseJob) -> str:
    return _approval_rule_for_amount(_approval_basis_amount(job))


def _approval_title(job: PurchaseJob) -> str:
    document_label = _document_purchase_label(job)
    if job.title:
        if "소모품" in job.title and document_label != "소모품":
            return job.title.replace("소모품", document_label)
        return job.title
    factory = _factory_label(job)
    return f"전산 {document_label} 구매 건({factory})"


def _factory_label(job: PurchaseJob) -> str:
    for text in (job.title or "", job.memo or ""):
        label = _factory_label_from_text(text)
        if label:
            return label
    for text in (job.memo or "", job.title or ""):
        business_number = _business_number_from_text(text)
        if business_number and business_number in FACTORY_BY_BUSINESS_NUMBER:
            return FACTORY_BY_BUSINESS_NUMBER[business_number]
    if job.corp_code == "daeseung_precision":
        return "P3공장"
    return "D1공장"


def _approval_body_html(job: PurchaseJob) -> str:
    if _document_purchase_label(job) != "소모품":
        return _asset_approval_body_html(job)
    return _consumable_approval_body_html(job)


def _asset_approval_body_html(job: PurchaseJob) -> str:
    factory = _factory_label(job)
    amount = job.amount or 0
    document_label = _document_purchase_label(job)
    recipient_rows = _recipient_rows_for_job(job)
    product_lines = _approval_product_lines(job)
    sorted_product_lines = _sort_product_lines_for_table(product_lines)
    if product_lines:
        product_amount = sum(line.amount for line in product_lines)
        shipping = max(amount - product_amount, 0) if amount else 0
        if _shipping_marked_free(job):
            shipping = 0
        product_rows = [
            _asset_product_row(factory, line.name, line.quantity, line.unit_price, line.amount)
            for line in sorted_product_lines
        ]
    else:
        item_name = _item_name(job)
        quantity = sum(item.quantity for item in job.items) or 1
        shipping = _shipping_fee_for_consumable(job, amount, quantity)
        product_amount = max(amount - shipping, 0)
        unit_price = product_amount // quantity if quantity else product_amount
        product_rows = [_asset_product_row(factory, item_name, quantity, unit_price, unit_price * quantity)]
    asset_table_style = _asset_table_style(_asset_column_specs(product_rows))
    payment_amount = _won(amount)

    return "\n".join(
        [
            _approval_paragraph("상기 제목건에 대하여 아래와 같은 사유로 신규 구매 하고자 하오니 재가 바랍니다.", line_height=20),
            _approval_blank_paragraph(),
            _approval_paragraph("- 아&nbsp;&nbsp; 래 -", align="center", escape_text=False),
            _approval_blank_paragraph(),
            _approval_paragraph(f"1. 사유 : {factory}&nbsp;전산 {document_label} 구매 건", escape_text=False),
            _approval_blank_paragraph(),
            _approval_paragraph("2. 구매가격(V.A.T 포함)", escape_text=False),
            _asset_purchase_table(product_rows, _won(amount), _won(shipping) if shipping else None),
            _approval_blank_paragraph(),
            _approval_paragraph("3. 지급대상", line_height=20),
            _recipient_table(recipient_rows, table_style=asset_table_style),
            _approval_blank_paragraph(),
            _approval_paragraph(
                '4. 입금계좌 정보<span style="color: rgb(51, 51, 51); text-align: center; '
                'font-family: &quot;맑은 고딕&quot;; font-size: 10pt;">(V.A.T 포함)</span>',
                line_height=20,
                escape_text=False,
            ),
            _consumable_payment_table(
                [[factory, "신한은행", "140008099980", "(주)컴퓨존", payment_amount, "O", "", ""]],
                table_style=asset_table_style,
            ),
            _approval_paragraph(f"※ 입금기한 : {_payment_deadline_text(job)}", line_height=20),
            _approval_blank_paragraph(),
            _approval_paragraph("5. 업체 : 컴퓨존", line_height=20),
            _approval_paragraph(f"6. 금액 : {_won(amount)}(V.A.T 포함)", line_height=20),
            _approval_paragraph("7.&nbsp;결제방법 : 세금계산서발행", line_height=20, escape_text=False),
            _approval_paragraph("8. 첨부", line_height=20),
            _approval_paragraph(f"&nbsp;&nbsp;&nbsp;&nbsp;- {factory}&nbsp;{document_label} 견적서&nbsp;1부", line_height=20, escape_text=False),
            _approval_paragraph(f"9. 주문번호 : {_e(job.order_no or '')}", escape_text=False),
            _approval_blank_paragraph(),
            _approval_paragraph("- 끝 -", align="center"),
        ]
    )


def _consumable_approval_body_html(job: PurchaseJob) -> str:
    factory = _factory_label(job)
    amount = job.amount or 0
    document_label = _document_purchase_label(job)
    product_lines = _approval_product_lines(job)
    sorted_product_lines = _sort_product_lines_for_table(product_lines)
    if product_lines:
        product_amount = sum(line.amount for line in product_lines)
        shipping = max(amount - product_amount, 0) if amount else 0
        if _shipping_marked_free(job):
            shipping = 0
        product_rows = [
            _approval_product_row(line.name, line.quantity, line.unit_price, line.amount)
            for line in sorted_product_lines
        ]
    else:
        item_name = _item_name(job)
        quantity = sum(item.quantity for item in job.items) or 1
        shipping = _shipping_fee_for_consumable(job, amount, quantity)
        product_amount = max(amount - shipping, 0)
        unit_price = product_amount // quantity if quantity else product_amount
        product_rows = [_approval_product_row(item_name, quantity, unit_price, unit_price * quantity)]
    payment_amount = _won(amount)

    return "\n".join(
        [
            _approval_paragraph("상기 제목건에 대하여 아래와 같은 사유로 신규 구매 하고자 하오니 재가 바랍니다.", line_height=20),
            _approval_blank_paragraph(),
            _approval_paragraph("- 아&nbsp;&nbsp; 래 -", align="center", escape_text=False),
            _approval_blank_paragraph(),
            _approval_paragraph(f"1. 사유 : 전산팀&nbsp;{document_label} 구매 건", escape_text=False),
            _approval_blank_paragraph(),
            _approval_paragraph(
                '2. 구매내역<span style="color: rgb(51, 51, 51); text-align: center; '
                'font-family: &quot;맑은 고딕&quot;; font-size: 10pt;">(V.A.T 포함)</span>',
                escape_text=False,
            ),
            _consumable_purchase_table(product_rows, _won(amount), _won(shipping) if shipping else None),
            _approval_paragraph(
                '3. 입금계좌 정보<span style="color: rgb(51, 51, 51); text-align: center; '
                'font-family: &quot;맑은 고딕&quot;; font-size: 10pt;">(V.A.T 포함)</span>',
                line_height=20,
                escape_text=False,
            ),
            _consumable_payment_table([[factory, "신한은행", "140008099980", "(주)컴퓨존", payment_amount, "O", "", ""]]),
            _approval_paragraph(f"※ 입금기한 : {_payment_deadline_text(job)}", line_height=20),
            _approval_blank_paragraph(),
            _approval_paragraph("4. 업체 : 컴퓨존", line_height=20),
            _approval_paragraph(f"5. 금액 : {_won(amount)}(V.A.T 포함)", line_height=20),
            _approval_paragraph("6.&nbsp;결제방법 : 세금계산서발행", line_height=20, escape_text=False),
            _approval_paragraph(f"7.&nbsp;첨부 : {factory}&nbsp;{document_label} 견적서", escape_text=False),
            _approval_paragraph(f"8.&nbsp;주문번호 : {_e(job.order_no or '')}", escape_text=False),
            _approval_blank_paragraph(),
            _approval_paragraph("- 끝 -", align="center"),
        ]
    )


def _shipping_fee_for_consumable(job: PurchaseJob, amount: int, quantity: int) -> int:
    if amount <= 0 or quantity <= 0:
        return 0
    if _shipping_marked_free(job):
        return 0
    common_fee = 3000
    if amount > common_fee and (amount - common_fee) % quantity == 0:
        return common_fee
    return 0


def _shipping_marked_free(job: PurchaseJob) -> bool:
    shipping_hint = " ".join(filter(None, [job.memo, job.item_summary])).lower()
    no_shipping_markers = ("운반료 없음", "운송료 없음", "배송비 없음", "택배비 없음", "무료배송", "배송비 할인")
    return any(marker in shipping_hint for marker in no_shipping_markers)


def _approval_product_lines(job: PurchaseJob) -> list[ApprovalProductLine]:
    lines: list[ApprovalProductLine] = []
    for raw_line in (job.item_summary or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) < 4:
            parts = [part.strip() for part in line.split("|")]
        if len(parts) < 4:
            continue
        name = parts[0]
        quantity = _parse_int(parts[1])
        unit_price = _parse_int(parts[2])
        amount = _parse_int(parts[3])
        if not name or quantity is None or unit_price is None or amount is None:
            continue
        if quantity <= 0 or unit_price < 0 or amount < 0:
            continue
        if _is_suspicious_approval_product_line(name, quantity, unit_price, amount):
            continue
        lines.append(ApprovalProductLine(name=name, quantity=quantity, unit_price=unit_price, amount=amount))
    return lines


def _sort_product_lines_for_table(lines: list[ApprovalProductLine]) -> list[ApprovalProductLine]:
    return sorted(lines, key=lambda line: (-line.unit_price, line.name))


def _document_purchase_label(job: PurchaseJob) -> str:
    categories = [_item_document_category(line.name) for line in _approval_product_lines(job)]
    if not categories and job.item_summary:
        categories = [_item_document_category(_item_name(job))]
    if "집기비품" in categories:
        return "집기비품"
    if "컴퓨터소프트웨어" in categories:
        return "컴퓨터소프트웨어"
    return "소모품"


def _item_document_category(name: str) -> str:
    lowered = name.lower()
    consumable_markers = ("가방", "케이블", "젠더", "더미", "플러그", "마우스", "키보드")
    if any(marker in lowered or marker in name for marker in consumable_markers):
        return "소모품"
    if "office" in lowered or "windows" in lowered or "소프트웨어" in name or "라이선스" in name:
        return "컴퓨터소프트웨어"
    fixture_markers = (
        "노트북",
        "아이디어패드",
        "thinkpad",
        "갤럭시북",
        "그램",
        "vivobook",
        "zenbook",
        "데스크탑",
        "pc",
        "프린터",
        "복합기",
        "모니터",
    )
    if any(marker in lowered or marker in name for marker in fixture_markers):
        return "집기비품"
    return "소모품"


def _recipient_rows_for_job(job: PurchaseJob) -> list[list[object]]:
    dept = _memo_field(job, ("지급부서", "부서", "asset_dept", "dept")) or "전산팀"
    target = _memo_field(job, ("지급대상", "대상", "asset_target", "target")) or "전산팀"
    purpose = _memo_field(job, ("용도", "asset_purpose", "purpose")) or "업무용"
    note = _memo_field(job, ("비고", "asset_note", "note")) or ""
    if _document_purchase_label(job) == "소모품":
        return [[1, dept, target, purpose, note]]

    targets = _split_recipient_targets(target)
    item_labels = _recipient_item_labels(job)
    if not item_labels:
        return [[1, dept, target, purpose, note]]

    rows: list[list[object]] = []
    for target_name in targets:
        for item_label in item_labels:
            rows.append([len(rows) + 1, dept, target_name, purpose, item_label])
    return rows


def _split_recipient_targets(target: str) -> list[str]:
    targets = [part.strip() for part in re.split(r"[,，/]+", target) if part.strip()]
    return targets or [target]


def _recipient_item_labels(job: PurchaseJob) -> list[str]:
    labels: list[tuple[int, str]] = []
    seen: set[str] = set()
    for line in _approval_product_lines(job):
        label = _recipient_item_label(line.name)
        if not label or label[1] in seen:
            continue
        seen.add(label[1])
        labels.append(label)
    labels.sort(key=lambda item: (item[0], item[1]))
    return [label for _, label in labels]


def _recipient_item_label(name: str) -> tuple[int, str] | None:
    category = _item_document_category(name)
    if category == "소모품":
        return None
    lowered = name.lower()
    if "office" in lowered or "오피스" in name:
        return (20, "OFFICE")
    if "windows" in lowered or "윈도우" in name:
        return (20, "Windows")
    if "소프트웨어" in name or "라이선스" in name:
        return (20, "소프트웨어")
    if "프린터" in name:
        return (10, "프린터")
    if "복합기" in name:
        return (10, "복합기")
    if "모니터" in name:
        return (10, "모니터")
    if "데스크탑" in name or re.search(r"\bpc\b", lowered):
        return (10, "PC")
    laptop_markers = ("노트북", "아이디어패드", "thinkpad", "갤럭시북", "그램", "vivobook", "zenbook")
    if any(marker in lowered or marker in name for marker in laptop_markers):
        return (10, "노트북")
    if category == "컴퓨터소프트웨어":
        return (20, "소프트웨어")
    return (10, _item_category(name))


def _memo_field(job: PurchaseJob, names: tuple[str, ...]) -> str:
    memo = job.memo or ""
    if not memo:
        return ""
    escaped_names = "|".join(re.escape(name) for name in names)
    patterns = [
        rf"(?:^|[\n;,])\s*(?:{escaped_names})\s*[:=]\s*([^\n;]+)",
        rf"(?:{escaped_names})\s+([^\n;]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, memo, flags=re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ,;/")
    return ""


def _parse_int(value: str) -> int | None:
    digits = re.sub(r"[^\d-]", "", value)
    if not digits or digits == "-":
        return None
    return int(digits)


def _item_category(name: str) -> str:
    lowered = name.lower()
    if "프린터" in name:
        return "프린터"
    if "office" in lowered or "오피스" in name:
        return "Office"
    if "가방" in name:
        return "노트북 가방" if "노트북" in name else "가방"
    laptop_markers = ("노트북", "아이디어패드", "thinkpad", "갤럭시북", "그램", "vivobook", "zenbook")
    if any(marker in lowered or marker in name for marker in laptop_markers):
        return "노트북"
    if "더미" in name and "플러그" in name:
        if "hdmi" in lowered:
            version = re.search(r"(HDMI\s*[0-9.]+)", name, re.IGNORECASE)
            return f"{version.group(1).upper()} 더미 플러그" if version else "HDMI 더미 플러그"
        return "더미 플러그"
    if "케이블" in name:
        return "케이블"
    if "마우스" in name and "키보드" in name:
        return "키보드 + 마우스"
    if "키보드" in name:
        return "키보드"
    if "젠더" in name or ("hdmi" in lowered and ("플러그" in name or "더미" in name or "plug" in lowered)):
        return "젠더"
    return _short_product_name(name)


def _approval_product_row(name: str, quantity: int, unit_price: int, amount: int) -> list[str]:
    return [
        _item_category(name),
        _maker_from_item(name),
        _model_from_item(name),
        f"{quantity} EA",
        _won(unit_price),
        _won(amount),
        "",
    ]


def _short_product_name(name: str) -> str:
    cleaned = re.sub(r"\[[^\]]+\]", "", name).strip()
    return cleaned.split(",", 1)[0].strip() or name


def _approval_paragraph(
    value: str,
    *,
    line_height: int = 18,
    align: str | None = None,
    escape_text: bool = True,
) -> str:
    text = _e(value) if escape_text else value
    align_attr = f' align="{align}"' if align else ""
    align_style = f" text-align: {align};" if align else ""
    return (
        f'<p{align_attr} style="margin: 0px; padding: 0px; line-height: {line_height}px; '
        'font-size: 9pt; border-width: 0px; border-color: rgb(208, 208, 208); border-style: solid; '
        f'background-color: rgb(255, 255, 255); font-family: 굴림;{align_style}">'
        '<span style="margin: 0px; padding: 0px; font-family: &quot;맑은 고딕&quot;; font-size: 10pt;">'
        f"{text}</span></p>"
    )


def _approval_blank_paragraph() -> str:
    return (
        '<p style="margin: 0px; padding: 0px; line-height: 18px; font-size: 9pt; '
        'border-width: 0px; border-color: rgb(208, 208, 208); border-style: solid; '
        'background-color: rgb(255, 255, 255); font-family: 굴림;">'
        '<br style="margin: 0px; padding: 0px;"></p>'
    )


_CONSUMABLE_PURCHASE_LABELS = ["품목", "제조사", "모델", "수량", "단가", "금액", "비고"]
_PURCHASE_EQUAL_WIDTH_COLUMNS = {3, 4, 5}
_PURCHASE_MONEY_COLUMNS = {4, 5}

_ASSET_PURCHASE_LABELS = ["구분", "품목", "제조사", "모델명", "수량", "단가", "금액", "직전구매단가", "비고"]
_ASSET_PURCHASE_EQUAL_WIDTH_COLUMNS = {4, 5, 6}
_ASSET_PURCHASE_MONEY_COLUMNS = {5, 6, 7}
_ASSET_PURCHASE_MINIMUMS = [5, 8, 6, 10, 5, 5, 5, 10, 4]

_CONSUMABLE_PAYMENT_COLUMNS = [
    ("구분", "101px", "center"),
    ("은행명", "76px", "center"),
    ("계좌번호", "122px", "center"),
    ("예금주", "121px", "center"),
    ("금액", "96px", "center"),
    ("가지급금", "92px", "center"),
    ("미지급금", "92px", "center"),
    ("비고", "64px", "center"),
]

_RECIPIENT_COLUMNS = [
    ("NO", "149px", "center"),
    ("부서", "150px", "center"),
    ("대상", "149px", "center"),
    ("용도", "150px", "center"),
    ("비고", "166px", "center"),
]


def _asset_product_row(factory: str, name: str, quantity: int, unit_price: int, amount: int) -> list[str]:
    return [
        factory,
        _item_category(name),
        _maker_from_item_for_asset(name),
        _model_from_item(name),
        f"{quantity} EA",
        _won(unit_price),
        _won(amount),
        "-",
        _asset_remark(name),
    ]


def _maker_from_item_for_asset(name: str) -> str:
    maker = _maker_from_item(name)
    if maker == "마이크로소프트":
        return "MS"
    return maker


def _asset_remark(name: str) -> str:
    text = _clean_model_text(name)
    if "노트북" in name or "아이디어패드" in name or "thinkpad" in name.lower():
        cpu = re.search(r"\b(Ryzen\s*\d|R[3579]-\d{4,5}[A-Z]*|i[3579]-\d{4,5}[A-Z]*)\b", text, re.IGNORECASE)
        memory = re.search(r"총\s*(\d+GB)|(\d+GB)\s*RAM\s*추가|/(\d+GB)/", text, re.IGNORECASE)
        parts: list[str] = []
        if cpu:
            parts.append(cpu.group(1))
        if memory:
            parts.append(next(group for group in memory.groups() if group))
        return "/".join(parts)
    return ""


def _asset_purchase_table(rows: list[list[str]], total_text: str, shipping_text: str | None = None) -> str:
    specs = _asset_column_specs(rows)
    shipping_row = _asset_shipping_row(shipping_text, specs) if shipping_text else ""
    table_style = _asset_table_style(specs)
    return (
        '<table bordercolor="#6e6e6e" border="1" cellspacing="0" cellpadding="0" '
        'style="color: rgb(0, 0, 0); font-family: 돋움, dotum, AppleGothic, arial, Helvetica, sans-serif; '
        'font-size: 12px; margin: 0px; padding: 0px; border: 0px solid rgb(0, 0, 0); '
        f"border-spacing: 0px; {table_style} "
        'background-color: white; border-collapse: collapse;">'
        f"{_purchase_colgroup(specs)}"
        '<tbody style="margin: 0px; padding: 0px;">'
        f"{_asset_purchase_row(_ASSET_PURCHASE_LABELS, specs, header=True)}"
        f"{''.join(_asset_purchase_row(row, specs) for row in rows)}"
        f"{shipping_row}"
        f"{_asset_total_row(total_text, specs)}"
        "</tbody></table>"
    )


def _asset_column_specs(rows: list[list[str]]) -> list[str]:
    widths: list[int] = []
    for index, label in enumerate(_ASSET_PURCHASE_LABELS):
        column_values = [label, *(row[index] for row in rows if len(row) > index)]
        widths.append(max(_ASSET_PURCHASE_MINIMUMS[index], max(_display_width(value) for value in column_values) + 1))
    equal_width = max(widths[index] for index in _ASSET_PURCHASE_EQUAL_WIDTH_COLUMNS)
    for index in _ASSET_PURCHASE_EQUAL_WIDTH_COLUMNS:
        widths[index] = equal_width
    return [f"min-width: {width}ch;" for width in widths]


def _asset_table_style(specs: list[str]) -> str:
    widths: list[int] = []
    for spec in specs:
        match = re.search(r"min-width:\s*(\d+)ch", spec)
        if match:
            widths.append(int(match.group(1)))
    min_width = max(94, sum(widths))
    return f"width: auto; min-width: {min_width}ch; max-width: 100%; table-layout: auto;"


def _asset_purchase_row(values: list[str], specs: list[str], *, header: bool = False) -> str:
    cells: list[str] = []
    for index, value in enumerate(values):
        align = "center" if header or index not in _ASSET_PURCHASE_MONEY_COLUMNS else "right"
        background = "background-color: rgb(226, 226, 226);" if header else ""
        cells.append(
            '<td rowspan="1" colspan="1" '
            'style="word-break: keep-all; white-space: nowrap; margin: 0px; padding: 0px 4px; color: rgb(51, 51, 51); '
            f'border: 1px solid rgb(0, 0, 0); {specs[index]} height: 25px; text-align: {align}; '
            f'line-height: 1; vertical-align: middle; {background}">'
            '<p style="margin: 0px; padding: 0px; line-height: 24px; font-family: &quot;맑은 고딕&quot;; font-size: 12pt;">'
            f'<span style="font-size: 10pt;">{_e(value)}</span></p></td>'
        )
    return f"<tr>{''.join(cells)}</tr>"


def _asset_shipping_row(value: str, specs: list[str]) -> str:
    return (
        '<tr>'
        '<td rowspan="1" colspan="6" style="word-break: keep-all; white-space: nowrap; margin: 0px; padding: 0px 4px; '
        'color: rgb(51, 51, 51); border: 1px solid rgb(0, 0, 0); height: 25px; '
        'text-align: center; line-height: 1; vertical-align: middle;">'
        '<p style="margin: 0px; padding: 0px; line-height: 24px; font-family: &quot;맑은 고딕&quot;; font-size: 12pt;">'
        '<span style="font-size: 10pt;">운반료</span></p></td>'
        f'<td rowspan="1" style="word-break: keep-all; white-space: nowrap; margin: 0px; padding: 0px 4px; color: rgb(51, 51, 51); '
        f'border: 1px solid rgb(0, 0, 0); {specs[6]} height: 25px; text-align: right; line-height: 1; vertical-align: middle;">'
        '<p style="margin: 0px; padding: 0px; line-height: 24px; font-family: &quot;맑은 고딕&quot;; font-size: 12pt;">'
        f'<span style="font-size: 10pt;">{_e(value)}</span></p></td>'
        '<td rowspan="1" colspan="2" style="word-break: keep-all; white-space: nowrap; margin: 0px; padding: 0px 4px; color: rgb(51, 51, 51); '
        'border: 1px solid rgb(0, 0, 0); height: 25px; text-align: center; line-height: 1; vertical-align: middle;">'
        '<p><br></p></td></tr>'
    )


def _asset_total_row(value: str, specs: list[str]) -> str:
    return (
        '<tr>'
        '<td colspan="6" style="word-break: keep-all; white-space: nowrap; margin: 0px; padding: 0px 4px; color: rgb(51, 51, 51); '
        'border: 1px solid rgb(0, 0, 0); height: 24px; text-align: center; line-height: 1; '
        'vertical-align: middle; background-color: rgb(247, 247, 247);">'
        '<p style="margin: 0px; padding: 0px; line-height: 24px; border-width: 0px; border-color: rgb(208, 208, 208); '
        'border-style: solid; font-family: &quot;맑은 고딕&quot;; font-size: 12pt;">'
        '<span style="font-size: 10pt;">계</span></p></td>'
        f'<td style="word-break: keep-all; white-space: nowrap; margin: 0px; padding: 0px 4px; color: rgb(51, 51, 51); '
        f'border: 1px solid rgb(0, 0, 0); {specs[6]} height: 24px; text-align: right; line-height: 1; '
        'vertical-align: middle; background-color: rgb(247, 247, 247);">'
        '<p style="margin: 0px; padding: 0px; line-height: 24px; border-width: 0px; border-color: rgb(208, 208, 208); '
        'border-style: solid; font-family: &quot;맑은 고딕&quot;; font-size: 12pt;">'
        f'<span style="font-size: 10pt;"><b style="font-size: 10pt;">{_e(value)}</b></span></p></td>'
        '<td colspan="2" style="word-break: keep-all; white-space: nowrap; margin: 0px; padding: 0px 4px; color: rgb(51, 51, 51); '
        'border: 1px solid rgb(0, 0, 0); height: 24px; text-align: center; line-height: 1; '
        'vertical-align: middle; background-color: rgb(247, 247, 247);">'
        '<p style="margin: 0px; padding: 0px; line-height: 24px; border-width: 0px; border-color: rgb(208, 208, 208); '
        'border-style: solid; font-family: &quot;맑은 고딕&quot;; font-size: 12pt;">'
        '<span style="font-size: 10pt;">&nbsp;</span></p></td>'
        '</tr>'
    )


def _recipient_table(rows: list[list[object]], *, table_style: str | None = None) -> str:
    normalized_rows = [[str(value) for value in row] for row in rows]
    table_style = table_style or "width: auto; min-width: 94ch; max-width: 100%; table-layout: auto;"
    return (
        '<table bordercolor="#6e6e6e" border="1" cellspacing="0" cellpadding="0" '
        'style="color: rgb(0, 0, 0); font-family: 돋움, dotum, AppleGothic, arial, Helvetica, sans-serif; '
        'font-size: 12px; margin: 0px; padding: 0px; border: 0px solid rgb(0, 0, 0); '
        f"border-spacing: 0px; {table_style} background-color: white; border-collapse: collapse;\">"
        '<tbody style="margin: 0px; padding: 0px;">'
        f"{_consumable_row([label for label, _, _ in _RECIPIENT_COLUMNS], _RECIPIENT_COLUMNS, header=True)}"
        f"{''.join(_consumable_row(row, _RECIPIENT_COLUMNS) for row in normalized_rows)}"
        "</tbody></table>"
    )


def _consumable_purchase_table(rows: list[list[str]], total_text: str, shipping_text: str | None = None) -> str:
    specs = _purchase_column_specs(rows)
    shipping_row = _shipping_row(shipping_text) if shipping_text else ""
    return (
        '<table bordercolor="#6e6e6e" border="1" cellspacing="0" cellpadding="0" '
        'style="color: rgb(0, 0, 0); font-family: 돋움, dotum, AppleGothic, arial, Helvetica, sans-serif; '
        'font-size: 12px; margin: 0px; padding: 0px; border: 0px solid rgb(0, 0, 0); '
        'border-spacing: 0px; width: auto; min-width: 748px; max-width: 100%; table-layout: auto; '
        'background-color: white; border-collapse: collapse;">'
        f"{_purchase_colgroup(specs)}"
        '<tbody style="margin: 0px; padding: 0px;">'
        f"{_purchase_row(_CONSUMABLE_PURCHASE_LABELS, specs, header=True)}"
        f"{''.join(_purchase_row(row, specs) for row in rows)}"
        f"{shipping_row}"
        f"{_consumable_total_row(total_text)}"
        "</tbody></table>"
    )


def _purchase_column_specs(rows: list[list[str]]) -> list[str]:
    minimums = [6, 6, 8, 5, 5, 5, 3]
    widths: list[int] = []
    for index, label in enumerate(_CONSUMABLE_PURCHASE_LABELS):
        column_values = [label, *(row[index] for row in rows if len(row) > index)]
        widths.append(max(minimums[index], max(_display_width(value) for value in column_values) + 1))
    equal_width = max(widths[index] for index in _PURCHASE_EQUAL_WIDTH_COLUMNS)
    for index in _PURCHASE_EQUAL_WIDTH_COLUMNS:
        widths[index] = equal_width
    return [f"min-width: {width}ch;" for width in widths]


def _display_width(value: str) -> int:
    width = 0
    for char in value:
        width += 1 if ord(char) < 128 else 2
    return width


def _purchase_colgroup(specs: list[str]) -> str:
    cols = "".join(f'<col style="{style}">' for style in specs)
    return f"<colgroup>{cols}</colgroup>"


def _purchase_row(values: list[str], specs: list[str], *, header: bool = False) -> str:
    cells: list[str] = []
    for index, value in enumerate(values):
        align = "center" if header or index not in _PURCHASE_MONEY_COLUMNS else "right"
        background = "background-color: rgb(226, 226, 226);" if header else ""
        cells.append(
            '<td rowspan="1" colspan="1" '
            'style="word-break: keep-all; white-space: nowrap; margin: 0px; padding: 0px 4px; color: rgb(51, 51, 51); '
            f'border: 1px solid rgb(0, 0, 0); {specs[index]} height: 25px; text-align: {align}; '
            f'line-height: 1; vertical-align: middle; {background}">'
            '<p style="margin: 0px; padding: 0px; line-height: 24px; font-family: &quot;맑은 고딕&quot;; font-size: 12pt;">'
            f'<span style="font-size: 10pt;">{_e(value)}</span></p></td>'
        )
    return f"<tr>{''.join(cells)}</tr>"


def _consumable_payment_table(rows: list[list[str]], *, table_style: str | None = None) -> str:
    table_style = table_style or "width: auto; min-width: 94ch; max-width: 100%; table-layout: auto;"
    return (
        '<table border="1" cellspacing="0" cellpadding="0" '
        'style="color: rgb(0, 0, 0); font-family: 돋움, dotum, AppleGothic, arial, Helvetica, sans-serif; '
        'font-size: 12px; margin: 0px; padding: 0px; border: 0px solid rgb(63, 63, 63); '
        f"border-spacing: 0px; {table_style} background-color: rgb(255, 255, 255); "
        'border-collapse: collapse; height: 51.3333px;">'
        '<tbody style="margin: 0px; padding: 0px;">'
        f"{_consumable_row([label for label, _, _ in _CONSUMABLE_PAYMENT_COLUMNS], _CONSUMABLE_PAYMENT_COLUMNS, header=True, border='rgb(63, 63, 63)')}"
        f"{''.join(_consumable_row(row, _CONSUMABLE_PAYMENT_COLUMNS, border='rgb(63, 63, 63)') for row in rows)}"
        "</tbody></table>"
    )


def _consumable_row(
    values: list[str],
    columns: list[tuple[str, str, str]],
    *,
    header: bool = False,
    border: str = "rgb(0, 0, 0)",
) -> str:
    cells: list[str] = []
    for value, (_, width, align) in zip(values, columns, strict=False):
        background = "background-color: rgb(226, 226, 226);" if header else ""
        cell_align = "right" if not header and "\\" in value else align
        cells.append(
            '<td rowspan="1" colspan="1" '
            'style="word-break: keep-all; white-space: nowrap; margin: 0px; padding: 0px; color: rgb(51, 51, 51); '
            f'border: 1px solid {border}; width: {width}; height: 25px; text-align: {cell_align}; '
            f'line-height: 1; vertical-align: middle; {background}">'
            '<p style="margin: 0px; padding: 0px; line-height: 24px; font-family: &quot;맑은 고딕&quot;; font-size: 12pt;">'
            f'<span style="font-size: 10pt;">{_e(value)}</span></p></td>'
        )
    return f"<tr>{''.join(cells)}</tr>"


def _shipping_row(value: str) -> str:
    return (
        '<tr>'
        '<td rowspan="1" colspan="5" style="word-break: keep-all; white-space: nowrap; margin: 0px; padding: 0px 4px; '
        'color: rgb(51, 51, 51); border: 1px solid rgb(0, 0, 0); height: 25px; '
        'text-align: center; line-height: 1; vertical-align: middle;">'
        '<p style="margin: 0px; padding: 0px; line-height: 24px; font-family: &quot;맑은 고딕&quot;; font-size: 12pt;">'
        '<span style="font-size: 10pt;">운반료</span></p></td>'
        '<td rowspan="1" style="word-break: keep-all; white-space: nowrap; margin: 0px; padding: 0px 4px; color: rgb(51, 51, 51); '
        'border: 1px solid rgb(0, 0, 0); height: 25px; text-align: right; line-height: 1; vertical-align: middle;">'
        '<p style="margin: 0px; padding: 0px; line-height: 24px; font-family: &quot;맑은 고딕&quot;; font-size: 12pt;">'
        f'<span style="font-size: 10pt;">{_e(value)}</span></p></td>'
        '<td rowspan="1" colspan="1" style="word-break: keep-all; white-space: nowrap; margin: 0px; padding: 0px 4px; color: rgb(51, 51, 51); '
        'border: 1px solid rgb(0, 0, 0); height: 25px; text-align: center; line-height: 1; vertical-align: middle;">'
        '<p><br></p></td></tr>'
    )


def _consumable_total_row(value: str) -> str:
    return (
        '<tr>'
        '<td colspan="5" style="word-break: keep-all; white-space: nowrap; margin: 0px; padding: 0px 4px; color: rgb(51, 51, 51); '
        'border: 1px solid rgb(0, 0, 0); height: 24px; text-align: center; line-height: 1; '
        'vertical-align: middle; background-color: rgb(247, 247, 247);">'
        '<p style="margin: 0px; padding: 0px; line-height: 24px; border-width: 0px; border-color: rgb(208, 208, 208); '
        'border-style: solid; font-family: &quot;맑은 고딕&quot;; font-size: 12pt;">'
        '<span style="font-size: 10pt;">계</span></p></td>'
        '<td style="word-break: keep-all; white-space: nowrap; margin: 0px; padding: 0px 4px; color: rgb(51, 51, 51); '
        'border: 1px solid rgb(0, 0, 0); height: 24px; text-align: right; line-height: 1; '
        'vertical-align: middle; background-color: rgb(247, 247, 247);">'
        '<p style="margin: 0px; padding: 0px; line-height: 24px; border-width: 0px; border-color: rgb(208, 208, 208); '
        'border-style: solid; font-family: &quot;맑은 고딕&quot;; font-size: 12pt;">'
        f'<span style="font-size: 10pt;"><b style="font-size: 10pt;">{_e(value)}</b></span></p></td>'
        '<td style="word-break: keep-all; white-space: nowrap; margin: 0px; padding: 0px 4px; color: rgb(51, 51, 51); '
        'border: 1px solid rgb(0, 0, 0); height: 24px; text-align: center; line-height: 1; '
        'vertical-align: middle; background-color: rgb(247, 247, 247);">'
        '<p style="margin: 0px; padding: 0px; line-height: 24px; border-width: 0px; border-color: rgb(208, 208, 208); '
        'border-style: solid; font-family: &quot;맑은 고딕&quot;; font-size: 12pt;">'
        '<span style="font-size: 10pt;">&nbsp;</span></p></td>'
        '</tr>'
    )


def _legacy_approval_body_html(job: PurchaseJob) -> str:
    factory = _factory_label(job)
    amount = job.amount or 0
    item_name = _item_name(job)
    maker = _maker_from_item(item_name)
    model = _model_from_item(item_name)
    quantity = sum(item.quantity for item in job.items) or 1
    unit_price = max(amount - 3000, 0) // quantity if amount else 0
    product_amount = unit_price * quantity

    rows = [
        _table_row([_e(item_name), _e(maker), _e(model), f"{quantity} EA", _won(unit_price), _won(product_amount), ""]),
    ]
    if amount and amount > product_amount:
        rows.append(_table_row(["운송료", "컴퓨존", "-", "1 EA", _won(amount - product_amount), _won(amount - product_amount), ""]))

    return f"""
<div style="font-family: Malgun Gothic, Arial, sans-serif; font-size: 10pt; line-height: 1.2;">
  <p style="margin:0; line-height:20px;">상기 제목건에 대하여 아래와 같은 사유로 신규 구매 하고자 하오니 재가 바랍니다.</p>
  <p style="margin:0; line-height:18px;"><br></p>
  <p style="margin:0; line-height:18px; text-align:center;">- 아&nbsp;&nbsp;래 -</p>
  <p style="margin:0; line-height:18px;"><br></p>
  <p style="margin:0; line-height:18px;">1. 사유 : {factory} 전산 소모품 구매 건</p>
  <p style="margin:0; line-height:18px;"><br></p>
  <p style="margin:0; line-height:18px;">2. 구매내역(V.A.T 포함)</p>
  <table border="1" cellspacing="0" cellpadding="0" style="border-collapse:collapse; width:748px; font-size:10pt; color:#333;">
    <tbody>
      {_table_header(["품목", "제조사", "모델", "수량", "단가", "금액", "비고"])}
      {''.join(rows)}
      {_table_total_row(5, _won(amount))}
    </tbody>
  </table>
  <p style="margin:0; line-height:18px;"><br></p>
  <p style="margin:0; line-height:18px;">3. 입금계좌 정보(V.A.T 포함)</p>
  <table border="1" cellspacing="0" cellpadding="0" style="border-collapse:collapse; width:748px; font-size:10pt; color:#333;">
    <tbody>
      {_table_header(["구분", "은행명", "계좌번호", "예금주", "금액", "가지급금", "미지급금", "비고"])}
      {_table_row([factory, "신한은행", "140008099980", "컴퓨존", _won(amount), "O", "", ""])}
    </tbody>
  </table>
  <p style="margin:0; line-height:18px;">※ 입금기한 : 주문서 참조</p>
  <p style="margin:0; line-height:18px;"><br></p>
  <p style="margin:0; line-height:18px;">4. 업체 : 컴퓨존</p>
  <p style="margin:0; line-height:18px;">5. 금액 : {_won(amount)}원(V.A.T 포함)</p>
  <p style="margin:0; line-height:18px;">6. 결제방법 : 세금계산서발행 / 무통장입금</p>
  <p style="margin:0; line-height:18px;">7. 주문번호 : {_e(job.order_no or "")}</p>
  <p style="margin:0; line-height:18px;">8. 첨부 : 견적서 - 컴퓨존({ _e(job.order_no or '') }).pdf</p>
  <p style="margin:0; line-height:18px;"><br></p>
  <p style="margin:0; line-height:18px; text-align:center;">- 끝 -</p>
</div>
"""


def _item_name(job: PurchaseJob) -> str:
    if job.item_summary:
        for raw_line in job.item_summary.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = [part.strip() for part in line.split("\t")]
            if len(parts) < 4:
                parts = [part.strip() for part in line.split("|")]
            if len(parts) >= 4:
                name = parts[0]
                quantity = _parse_int(parts[1])
                unit_price = _parse_int(parts[2])
                amount = _parse_int(parts[3])
                if (
                    name
                    and quantity is not None
                    and unit_price is not None
                    and amount is not None
                    and quantity > 0
                    and unit_price >= 0
                    and amount >= 0
                    and not _is_suspicious_approval_product_line(name, quantity, unit_price, amount)
                ):
                    return name
                continue
            if not _PRODUCT_CODE_LINE_RE.match(line):
                return line
    return "컴퓨존 전산 소모품"


def _maker_from_item(name: str) -> str:
    bracket = re.search(r"\[([^\]]+)\]", name)
    if bracket:
        return bracket.group(1).strip()
    return "컴퓨존"


def _model_from_item(name: str) -> str:
    cleaned = re.sub(r"\[[^\]]+\]", "", name).strip()
    parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    if "더미" in cleaned and "플러그" in cleaned and len(parts) >= 2:
        model_tokens = [
            part
            for part in parts
            if "더미" not in part and "플러그" not in part and re.search(r"[A-Z]{2,}[-A-Z0-9/ ]*\d", part, re.IGNORECASE)
        ]
        return " / ".join(token.strip(" /") for token in model_tokens) if model_tokens else parts[-1]
    if "가방" in cleaned and len(parts) >= 2:
        return _clean_model_text(parts[-1])
    if "office" in cleaned.lower():
        if re.search(r"Home\s*&\s*Business\s*2024", cleaned, re.IGNORECASE):
            return "H&B 2024"
        office = re.search(r"(Office.+?)(?:\s*\[|$)", cleaned, re.IGNORECASE)
        return _clean_model_text(office.group(1) if office else cleaned)
    if "노트북" in name or "아이디어패드" in cleaned or "thinkpad" in cleaned.lower():
        model_code = re.search(r"\b\d{2}[A-Z]{3,}\d{2}\b", cleaned)
        if model_code:
            return model_code.group(0)
        return _clean_model_text(cleaned.split("(", 1)[0].strip())
    return cleaned or name


def _clean_model_text(value: str) -> str:
    text = value.replace("▶", "").replace("◀", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" /,")


def _payment_deadline_text(job: PurchaseJob) -> str:
    base = job.created_at.astimezone(timezone(timedelta(hours=9))).date()
    return (base + timedelta(days=3)).strftime("%Y.%m.%d")


def _won(value: int | None) -> str:
    return f"\\{value or 0:,}"


def _e(value: str) -> str:
    return html.escape(value, quote=False)


def _table_header(values: list[str]) -> str:
    cells = "".join(
        f'<td style="border:1px solid #000; padding:4px; text-align:center; background:#e2e2e2;">{_e(value)}</td>'
        for value in values
    )
    return f"<tr>{cells}</tr>"


def _table_row(values: list[str]) -> str:
    cells = "".join(
        f'<td style="border:1px solid #000; padding:4px; text-align:center;">{value}</td>'
        for value in values
    )
    return f"<tr>{cells}</tr>"


def _table_total_row(colspan: int, amount: str) -> str:
    return (
        f'<tr><td colspan="{colspan}" style="border:1px solid #000; padding:4px; text-align:center; '
        'background:#f7f7f7;"><b>계</b></td>'
        f'<td style="border:1px solid #000; padding:4px; text-align:right; background:#f7f7f7;"><b>{amount}</b></td>'
        '<td style="border:1px solid #000; padding:4px; background:#f7f7f7;"></td></tr>'
    )


def _ensure_groupware_session(page, settings: Settings, form_url: str) -> None:
    if not _is_login_required(page):
        return
    _login_groupware(page, settings)
    page.goto(form_url, wait_until="domcontentloaded", timeout=60000)
    _raise_if_login_required(page)


def _is_login_required(page) -> bool:
    text = ""
    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        pass
    return "/login" in page.url or ("로그인" in text and ("비밀번호" in text or "아이디" in text))


def _raise_if_login_required(page) -> None:
    if _is_login_required(page):
        raise GroupwareLoginRequiredError(
            "그룹웨어 로그인이 필요합니다. 담당자 PC 브라우저 세션에서 먼저 로그인해 주세요."
        )


def _login_groupware(page, settings: Settings) -> None:
    login_id = settings.groupware_login_id.strip()
    login_password = settings.groupware_login_password.strip()
    if not login_id or not login_password:
        raise GroupwareLoginRequiredError(
            "그룹웨어 로그인이 필요합니다. PURCHASE_AUTO_GROUPWARE_ID/PASSWORD 환경변수를 설정하거나 담당자 PC 세션으로 로그인해 주세요."
        )

    login_url = f"{settings.groupware_base_url.rstrip('/')}/login"
    if "/login" not in page.url:
        page.goto(login_url, wait_until="domcontentloaded", timeout=60000)

    _fill_login_input(page, login_id)
    page.locator("input[type='password']").first.fill(login_password, timeout=10000)

    clicked = False
    for selector in ["button:has-text('로그인')", "input[type='submit']", "a:has-text('로그인')"]:
        try:
            page.locator(selector).first.click(timeout=5000)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        clicked = page.evaluate(
            """() => {
                const visible = (element) => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const candidates = Array.from(document.querySelectorAll('button,input[type=button],input[type=submit],a'));
                const button = candidates.find((element) => visible(element) && ((element.innerText || element.value || '').includes('로그인')));
                if (!button) return false;
                button.click();
                return true;
            }"""
        )
    if not clicked:
        raise GroupwareLoginRequiredError("그룹웨어 로그인 버튼을 찾지 못했습니다.")

    try:
        page.wait_for_url(re.compile(r".*/app/.*"), timeout=15000)
    except Exception:
        page.wait_for_timeout(5000)
    if _is_login_required(page):
        raise GroupwareLoginRequiredError("그룹웨어 로그인에 실패했습니다. 계정 또는 세션 상태를 확인해 주세요.")


def _fill_login_input(page, login_id: str) -> None:
    for selector in ["#username", "#userId", "#loginId", "input[name='username']", "input[name='userId']", "input[name='id']"]:
        try:
            page.locator(selector).first.fill(login_id, timeout=2000)
            return
        except Exception:
            continue
    ok = page.evaluate(
        """([loginId]) => {
            const visible = (element) => {
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
            };
            const inputs = Array.from(document.querySelectorAll('input'))
                .filter((input) => visible(input) && input.type !== 'password' && input.type !== 'hidden');
            const target = inputs.find((input) => /id|login|user/i.test(`${input.name} ${input.id}`)) || inputs[0];
            if (!target) return false;
            target.focus();
            target.value = loginId;
            target.dispatchEvent(new Event('input', {bubbles: true}));
            target.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }""",
        [login_id],
    )
    if not ok:
        raise GroupwareLoginRequiredError("그룹웨어 로그인 아이디 입력칸을 찾지 못했습니다.")


def _fill_title(page, title: str) -> None:
    _fill_first(page, ["#subject", "input[name='subject']", "input[name='title']", "input[id*='subject']"], title)


def _fill_approval_rule(page, rule: str) -> None:
    _fill_first(page, ["#editorForm_9", "input[name='editorForm_9']", "input[id*='editorForm_9']"], rule)


def _fill_first(page, selectors: list[str], value: str) -> None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.fill(value, timeout=3000)
            return
        except Exception:
            continue
    raise RuntimeError(f"입력 필드를 찾지 못했습니다: {selectors[0]}")


def _set_delegate_level(page, level: str) -> None:
    try:
        page.wait_for_selector("select", timeout=10000)
    except Exception:
        pass
    ok = page.evaluate(
        r"""([level]) => {
            const clean = (value) => (value || '').replace(/\s+/g, '').trim();
            const expected = clean(level);
            const visible = (element) => {
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
            };
            const selects = Array.from(document.querySelectorAll('select'));
            const target = selects.find(select => visible(select) && (
                clean(select.selectedOptions && select.selectedOptions[0] ? select.selectedOptions[0].textContent : '') === expected ||
                Array.from(select.options).some(option => clean(option.textContent) === expected)
            ));
            if (target) {
                const current = target.selectedOptions && target.selectedOptions[0] ? target.selectedOptions[0] : null;
                if (current && clean(current.textContent) === expected) return true;
                const option = Array.from(target.options).find(option => clean(option.textContent) === expected);
                if (!option) return false;
                target.value = option.value;
                target.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }
            const bodyText = document.body ? document.body.innerText || '' : '';
            return bodyText.includes('전결권자') && clean(bodyText).includes(expected);
        }""",
        [level],
    )
    if not ok:
        raise RuntimeError(f"전결권자 드롭다운에서 {level} 옵션을 선택하지 못했습니다.")


def _fill_body(page, body_html: str) -> None:
    try:
        page.wait_for_function(
            "() => window.DEXT5 && DEXT5.SetBodyValue && DEXT5.GetBodyValue",
            timeout=10000,
        )
        result = page.evaluate(
            """(html) => {
                DEXT5.SetBodyValue(html, 'appContent');
                if (DEXT5.DoSaveHTML) DEXT5.DoSaveHTML('appContent');
                try {
                    if (DEXT5.SaveCurrValueInMultiValue) DEXT5.SaveCurrValueInMultiValue('appContent');
                    if (DEXT5.SetDirty) DEXT5.SetDirty('appContent');
                } catch (error) {
                    // DEXT5.SetBodyValue + DoSaveHTML is the durable save path.
                }
                const target = document.querySelector('span#appContent, [data-id="appContent"]');
                if (target) {
                    target.setAttribute('data-value', html);
                    target.setAttribute('value', html);
                }
                const probe = document.createElement('div');
                probe.innerHTML = html;
                const probeText = (probe.innerText || probe.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 24);
                const bodyValue = DEXT5.GetBodyValue ? DEXT5.GetBodyValue('appContent') : '';
                const htmlValue = DEXT5.GetHtmlValue ? DEXT5.GetHtmlValue('appContent') : '';
                const payload = window.jQuery && jQuery.editorParser && jQuery.editorParser.getFormData
                    ? jQuery.editorParser.getFormData(jQuery('#document_content'))
                    : '';
                return {
                    bodyLength: bodyValue.length,
                    htmlLength: htmlValue.length,
                    payloadLength: payload.length,
                    probeText,
                    bodyContainsProbe: !probeText || bodyValue.includes(probeText),
                    htmlContainsProbe: !probeText || htmlValue.includes(probeText),
                    payloadContainsProbe: !probeText || payload.includes(probeText),
                };
            }""",
            body_html,
        )
        if (
            result.get("bodyLength", 0) > 0
            and result.get("htmlLength", 0) > 0
            and result.get("payloadContainsProbe")
        ):
            return
    except Exception:
        pass

    try:
        page.wait_for_function(
            "() => window.GO && GO.Editor && GO.Editor.getInstance && GO.Editor.getInstance('appContent')",
            timeout=10000,
        )
        result = page.evaluate(
            """(html) => {
                const editor = GO.Editor.getInstance('appContent');
                editor.setContent(html);
                const content = editor.getContent ? editor.getContent() : html;
                const target = document.querySelector('span#appContent, [data-id="appContent"]');
                if (target) {
                    target.setAttribute('data-value', content);
                    target.setAttribute('value', content);
                }
                if (window.DEXT5) {
                    try {
                        if (DEXT5.SaveCurrValueInMultiValue) DEXT5.SaveCurrValueInMultiValue('appContent');
                        if (DEXT5.SetDirty) DEXT5.SetDirty('appContent');
                    } catch (error) {
                        // The GO.Editor wrapper is the authoritative save path.
                    }
                }
                const probe = document.createElement('div');
                probe.innerHTML = html;
                const probeText = (probe.innerText || probe.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 24);
                const payload = window.jQuery && jQuery.editorParser && jQuery.editorParser.getFormData
                    ? jQuery.editorParser.getFormData(jQuery('#document_content'))
                    : content;
                return {
                    contentLength: content.length,
                    payloadLength: payload.length,
                    probeText,
                    payloadContainsProbe: !probeText || payload.includes(probeText),
                };
            }""",
            body_html,
        )
        if result.get("contentLength", 0) > 0 and result.get("payloadContainsProbe"):
            return
    except Exception:
        pass

    frame = page.frame_locator("#dext_frame_appContent")
    try:
        frame.locator("body").evaluate(
            """(body, html) => {
                body.innerHTML = html;
                body.dispatchEvent(new Event('input', {bubbles: true}));
                body.dispatchEvent(new Event('keyup', {bubbles: true}));
                body.dispatchEvent(new Event('change', {bubbles: true}));
            }""",
            body_html,
            timeout=5000,
        )
        return
    except Exception:
        pass
    _fill_first(page, ["textarea[name='content']", "textarea[id*='content']", "div[contenteditable='true']"], body_html)


def _assert_body_ready_for_submit(page, body_html: str) -> None:
    probe = _body_probe_text(body_html)
    ok = page.evaluate(
        """([probe]) => {
            const bodyValue = window.DEXT5 && DEXT5.GetBodyValue ? DEXT5.GetBodyValue('appContent') : '';
            const htmlValue = window.DEXT5 && DEXT5.GetHtmlValue ? DEXT5.GetHtmlValue('appContent') : '';
            const payload = window.jQuery && jQuery.editorParser && jQuery.editorParser.getFormData
                ? jQuery.editorParser.getFormData(jQuery('#document_content'))
                : '';
            return bodyValue.includes(probe) && htmlValue.includes(probe) && payload.includes(probe);
        }""",
        [probe],
    )
    if not ok:
        raise RuntimeError("결재요청 직전 제출 데이터에 본문이 들어가지 않았습니다.")


def _assert_submitted_body_visible(page, job: PurchaseJob) -> None:
    expected = ["구매가격" if _document_purchase_label(job) != "소모품" else "구매내역", "입금계좌"]
    if job.order_no:
        expected.append(job.order_no)
    deadline = 15000
    step = 1000
    for _ in range(deadline // step):
        text = _page_text_with_frames(page)
        if all(value in text for value in expected):
            return
        page.wait_for_timeout(step)
    _save_debug_screenshot(page, job, load_settings(), "groupware_submitted_body_missing")
    raise RuntimeError("상신된 그룹웨어 문서 화면에서 본문을 확인하지 못했습니다.")


def _page_text_with_frames(page) -> str:
    texts: list[str] = []
    try:
        texts.append(page.locator("body").inner_text(timeout=3000))
    except Exception:
        pass
    for frame in page.frames:
        try:
            texts.append(frame.locator("body").inner_text(timeout=1000))
        except Exception:
            continue
    return "\n".join(texts)


def _body_probe_text(body_html: str) -> str:
    if "운반료" in body_html:
        return "운반료"
    text = re.sub(r"<[^>]+>", " ", body_html)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:20]


def _attach_quote(page, quote_path: Path) -> None:
    file_inputs = page.locator("input[type='file']")
    if file_inputs.count() > 0:
        file_inputs.first.set_input_files(str(quote_path))
        return
    _click_first(page, ["button:has-text('첨부')", "a:has-text('첨부')", "text=첨부"], "첨부 버튼을 찾지 못했습니다.")
    page.locator("input[type='file']").first.set_input_files(str(quote_path))


def _add_finance_reference_group(page, corp: CorpConfig) -> None:
    # 결재선/결재자는 이 팝업에서 수정하지 않는다. 참조자 개인그룹만 추가한다.
    _click_first(page, ["#act_edit_apprflow", "text=결재 정보"], "결재 정보 버튼을 찾지 못했습니다.")
    page.wait_for_timeout(1000)
    if not _click_modal_tab(page, "참조자"):
        raise RuntimeError("결재 정보 팝업에서 참조자 탭을 찾지 못했습니다.")
    page.wait_for_timeout(500)
    if not (_click_modal_tab(page, "개인 그룹") or _click_text(page, "개인 그룹")):
        raise RuntimeError("참조자 탭에서 개인 그룹을 찾지 못했습니다.")
    page.wait_for_timeout(500)
    before_count = _selected_reference_count(page)
    for group_name in _reference_group_candidates(corp):
        if _click_text(page, group_name):
            page.wait_for_timeout(1000)
            _add_selected_reference_group_members(page, before_count)
            _assert_reference_added(page, before_count, group_name)
            _click_modal_confirm(page)
            return
    raise RuntimeError(f"참조자 개인그룹을 찾지 못했습니다: {', '.join(_reference_group_candidates(corp))}")


def _reference_group_candidates(corp: CorpConfig) -> list[str]:
    names = [
        corp.finance_reference_group,
        corp.finance_reference_group.replace("재정_", "재정팀_"),
        f"재정_{corp.display_name}",
        f"재정팀_{corp.display_name}",
    ]
    result: list[str] = []
    for name in names:
        if name and name not in result:
            result.append(name)
    return result


def _add_selected_reference_group_members(page, before_count: int) -> None:
    if _selected_reference_count(page) > before_count:
        return
    for _ in range(3):
        if _click_reference_add_button(page):
            page.wait_for_timeout(700)
            if _selected_reference_count(page) > before_count:
                return
    _double_click_selected_reference_group(page)
    page.wait_for_timeout(700)


def _assert_reference_added(page, before_count: int, group_name: str) -> None:
    after_count = _selected_reference_count(page)
    if after_count > before_count and not _reference_empty_prompt_visible(page):
        return
    raise RuntimeError(f"참조자 그룹을 선택했지만 실제 참조자 목록에 추가되지 않았습니다: {group_name}")


def _selected_reference_count(page) -> int:
    return int(
        page.evaluate(
            """() => {
                const visible = (element) => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const roots = Array.from(document.querySelectorAll('#gpopupLayer, .go_popup, .layer_normal'))
                    .filter(visible);
                for (const root of roots) {
                    const panels = Array.from(root.querySelectorAll('table, div, ul'))
                        .filter((element) => {
                            const text = normalize(element.innerText || element.textContent || '');
                            return visible(element)
                                && text.includes('이름')
                                && text.includes('부서')
                                && (text.includes('삭제') || text.includes('확인시간'));
                        })
                        .sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width);
                    for (const panel of panels) {
                        const rows = Array.from(panel.querySelectorAll('tr, li, [role=row], .row'))
                            .filter(visible)
                            .map((element) => normalize(element.innerText || element.textContent || ''))
                            .filter((text) => text
                                && !text.includes('이름')
                                && !text.includes('부서')
                                && !text.includes('삭제')
                                && !text.includes('드래그하여 항목을 추가할 수 있습니다.'));
                        if (rows.length > 0) return rows.length;
                        const text = normalize(panel.innerText || panel.textContent || '');
                        if (text.includes('드래그하여 항목을 추가할 수 있습니다.')) return 0;
                    }
                }
                return 0;
            }"""
        )
    )


def _reference_empty_prompt_visible(page) -> bool:
    return bool(
        page.evaluate(
            """() => {
                const visible = (element) => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const roots = Array.from(document.querySelectorAll('#gpopupLayer, .go_popup, .layer_normal'))
                    .filter(visible);
                return roots.some((root) => (root.innerText || root.textContent || '').includes('드래그하여 항목을 추가할 수 있습니다.'));
            }"""
        )
    )


def _click_reference_add_button(page) -> bool:
    return bool(
        page.evaluate(
            """() => {
                const visible = (element) => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const roots = Array.from(document.querySelectorAll('#gpopupLayer, .go_popup, .layer_normal'))
                    .filter(visible);
                const labels = ['>>', '≫', '»', '＞', '추가', '선택'];
                for (const root of roots) {
                    const nodes = Array.from(root.querySelectorAll('a, button, input[type=button], span, td, div'))
                        .filter(visible);
                    for (const label of labels) {
                        const node = nodes.find((element) => {
                            const value = normalize(element.innerText || element.value || element.textContent || '');
                            return value === label || value.includes(label);
                        });
                        if (node) {
                            const clickable = node.closest('a,button,input[type=button],[onclick]') || node;
                            clickable.click();
                            return true;
                        }
                    }
                }
                return false;
            }"""
        )
    )


def _double_click_selected_reference_group(page) -> bool:
    return bool(
        page.evaluate(
            """() => {
                const visible = (element) => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const roots = Array.from(document.querySelectorAll('#gpopupLayer, .go_popup, .layer_normal'))
                    .filter(visible);
                for (const root of roots) {
                    const selected = Array.from(root.querySelectorAll('li.on, li.active, tr.on, tr.active, div.on, div.active, span.on, span.active'))
                        .find(visible);
                    if (!selected) continue;
                    selected.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window }));
                    return true;
                }
                return false;
            }"""
        )
    )


def _click_text(page, text: str) -> bool:
    return bool(
        page.evaluate(
            """([text]) => {
                const visible = (element) => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const roots = Array.from(document.querySelectorAll('#gpopupLayer, .go_popup, .layer_normal'))
                    .filter(visible);
                if (roots.length === 0) roots.push(document);
                for (const root of roots) {
                    const nodes = Array.from(root.querySelectorAll('a,button,li,span,td,div'));
                    const node = nodes
                        .filter(element => visible(element) && (element.innerText || element.textContent || '').includes(text))
                        .sort((a, b) => {
                            const ar = a.getBoundingClientRect();
                            const br = b.getBoundingClientRect();
                            const area = (ar.width * ar.height) - (br.width * br.height);
                            if (area !== 0) return area;
                            return (a.innerText || a.textContent || '').length - (b.innerText || b.textContent || '').length;
                        })[0];
                    if (node) {
                        const clickable = node.closest('a,button,li,tr,[onclick]') || node;
                        clickable.click();
                        return true;
                    }
                }
                return false;
            }""",
            [text],
        )
    )


def _click_modal_tab(page, text: str) -> bool:
    return bool(
        page.evaluate(
            """([text]) => {
                const visible = (element) => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const roots = Array.from(document.querySelectorAll('#gpopupLayer, .go_popup, .layer_normal'))
                    .filter(visible);
                for (const root of roots) {
                    const nodes = Array.from(root.querySelectorAll('a,button,li,span,td,div'));
                    const node = nodes.find((element) => {
                        const value = (element.innerText || element.textContent || '').replace(/\\s+/g, ' ').trim();
                        return visible(element) && value === text;
                    });
                    if (!node) continue;
                    const clickable = node.closest('a,button,li,[role=tab]') || node;
                    clickable.click();
                    return true;
                }
                return false;
            }""",
            [text],
        )
    )


def _click_modal_confirm(page) -> None:
    ok = page.evaluate(
        """() => {
            const visible = (element) => {
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
            };
            const roots = Array.from(document.querySelectorAll('#gpopupLayer, .go_popup, .layer_normal'))
                .filter(visible);
            for (const root of roots) {
                const buttons = Array.from(root.querySelectorAll('a.btn_major_s, button, input[type=button]'));
                const button = buttons.find(element => visible(element) && (element.innerText || element.value || '').includes('확인'));
                if (button) {
                    button.click();
                    return true;
                }
            }
            return false;
        }"""
    )
    if not ok:
        raise RuntimeError("참조자 확인 버튼을 찾지 못했습니다.")
    page.wait_for_timeout(1000)


def _request_approval(page) -> None:
    _click_first(page, ["#act_draft", "a:has-text('결재요청')", "button:has-text('결재요청')"], "결재요청 버튼을 찾지 못했습니다.")
    page.wait_for_timeout(1000)
    if not _click_modal_button(page, ["결재요청", "상신", "확인"]):
        raise RuntimeError("결재요청 확인 버튼을 찾지 못했습니다.")


def _click_modal_button(page, labels: list[str]) -> bool:
    return bool(
        page.evaluate(
            """([labels]) => {
                const visible = (element) => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const roots = Array.from(document.querySelectorAll('#gpopupLayer, .go_popup, .layer_normal'))
                    .filter(visible);
                for (const root of roots) {
                    const buttons = Array.from(root.querySelectorAll('a.btn_major_s, a, button, input[type=button], input[type=submit]'))
                        .filter(visible);
                    for (const label of labels) {
                        const button = buttons.find((element) => {
                            const value = (element.innerText || element.value || '').replace(/\\s+/g, ' ').trim();
                            return value === label || value.includes(label);
                        });
                        if (button) {
                            button.click();
                            return true;
                        }
                    }
                }
                return false;
            }""",
            [labels],
        )
    )


def _click_first(page, selectors: list[str], error_message: str, timeout: int = 3000) -> None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.click(timeout=timeout)
            return
        except Exception:
            continue
    raise RuntimeError(error_message)


def _extract_document_id(url: str, text: str) -> str:
    for pattern in [r"/document/(?!new(?:/|$))(?:view/)?([0-9A-Za-z_-]+)", r"문서\s*번호\s*[:：]?\s*([0-9A-Za-z_-]+)"]:
        match = re.search(pattern, url) or re.search(pattern, text)
        if match:
            return match.group(1)
    return url.rstrip("/").rsplit("/", 1)[-1]
