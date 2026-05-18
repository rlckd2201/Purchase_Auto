from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import Settings, load_settings
from .models import PurchaseJob
from .pdf import write_minimal_pdf


class AutomationNotEnabledError(RuntimeError):
    pass


class LoginRequiredError(RuntimeError):
    pass


@dataclass(frozen=True)
class CompuzoneOrderResult:
    order_no: str
    amount: int | None
    item_summary: str
    quote_pdf_path: str
    raw_status: str


def run_compuzone_order(job: PurchaseJob, settings: Settings | None = None) -> CompuzoneOrderResult:
    settings = settings or load_settings()
    if settings.dry_run:
        return _dry_run_order(job, settings)
    if not settings.enable_live_compuzone_order:
        raise AutomationNotEnabledError("실제 컴퓨존 주문은 PURCHASE_AUTO_ENABLE_LIVE_COMPUZONE_ORDER=1 일 때만 실행됩니다.")
    return _live_order(job, settings)


def _job_artifact_dir(job: PurchaseJob, settings: Settings) -> Path:
    return settings.artifact_dir / job.job_id


def _item_summary(job: PurchaseJob) -> str:
    return ", ".join(f"{index + 1}번 상품 x {item.quantity}" for index, item in enumerate(job.items))


def _dry_run_order(job: PurchaseJob, settings: Settings) -> CompuzoneOrderResult:
    order_no = f"9{abs(hash(job.job_id)) % 10000000:07d}"
    quote_path = _job_artifact_dir(job, settings) / f"compuzone_quote_{order_no}.pdf"
    lines = [
        f"Dry-run order number: {order_no}",
        f"Corp: {job.corp}",
        f"Title: {job.title or ''}",
        f"Items: {_item_summary(job)}",
        "Payment: bank transfer pending",
    ]
    write_minimal_pdf(quote_path, "Compuzone Quote Dry Run", lines)
    return CompuzoneOrderResult(
        order_no=order_no,
        amount=None,
        item_summary=_item_summary(job),
        quote_pdf_path=str(quote_path),
        raw_status="dry_run_order_submitted_pending_payment",
    )


def _live_order(job: PurchaseJob, settings: Settings) -> CompuzoneOrderResult:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    settings.compuzone_profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(settings.compuzone_profile_dir),
            headless=settings.headless,
            accept_downloads=True,
        )
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        try:
            for item in job.items:
                page.goto(item.url, wait_until="domcontentloaded", timeout=60000)
                _raise_if_login_required(page)
                _set_quantity(page, item.quantity)
                _click_first(
                    page,
                    [
                        "button:has-text('장바구니')",
                        "a:has-text('장바구니')",
                        "input[value*='장바구니']",
                        "[onclick*='cart']",
                    ],
                    "장바구니 버튼을 찾지 못했습니다.",
                )
                page.wait_for_timeout(1000)

            page.goto(settings.compuzone_cart_url, wait_until="domcontentloaded", timeout=60000)
            _raise_if_login_required(page)
            _click_first(
                page,
                [
                    "button:has-text('주문하기')",
                    "a:has-text('주문하기')",
                    "button:has-text('구매하기')",
                    "a:has-text('구매하기')",
                ],
                "장바구니 주문 버튼을 찾지 못했습니다.",
            )
            page.wait_for_load_state("domcontentloaded", timeout=60000)
            _raise_if_login_required(page)
            _select_bank_transfer(page)
            _fill_depositor_name(page, settings.compuzone_depositor_name)
            _check_required_agreements(page)
            _click_first(
                page,
                [
                    "button:has-text('결제하기')",
                    "button:has-text('주문하기')",
                    "a:has-text('결제하기')",
                    "a:has-text('주문하기')",
                    "input[value*='결제']",
                    "input[value*='주문']",
                ],
                "최종 주문 버튼을 찾지 못했습니다.",
            )
            page.wait_for_load_state("domcontentloaded", timeout=60000)
            body = page.locator("body").inner_text(timeout=10000)
            order_no = _extract_order_no(body)
            amount = _extract_amount(body)
            quote_path = _download_quote_pdf(page, order_no, settings, job)
            return CompuzoneOrderResult(
                order_no=order_no,
                amount=amount,
                item_summary=_item_summary(job),
                quote_pdf_path=str(quote_path),
                raw_status="order_submitted_pending_payment",
            )
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"컴퓨존 자동화 타임아웃: {exc}") from exc
        finally:
            context.close()


def _click_first(page, selectors: list[str], error_message: str, timeout: int = 5000) -> None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.click(timeout=timeout)
            return
        except Exception:
            continue
    raise RuntimeError(error_message)


def _set_quantity(page, quantity: int) -> None:
    selectors = [
        "input[name='OrdQty']",
        "input[name='quantity']",
        "input[name='qty']",
        "input[id*='qty']",
        "input[title*='수량']",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.fill(str(quantity), timeout=3000)
            return
        except Exception:
            continue
    if quantity != 1:
        raise RuntimeError("수량 입력칸을 찾지 못했습니다.")


def _raise_if_login_required(page) -> None:
    text = ""
    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        pass
    if "로그인" in text and ("아이디" in text or "비밀번호" in text):
        raise LoginRequiredError("컴퓨존 로그인이 필요합니다. 담당자 PC 브라우저 세션을 먼저 확인해 주세요.")


def _select_bank_transfer(page) -> None:
    selectors = [
        "label:has-text('무통장')",
        "text=무통장입금",
        "input[value*='무통장']",
        "input[value*='BANK']",
        "input[value*='bank']",
    ]
    _click_first(page, selectors, "무통장입금 결제수단을 찾지 못했습니다.", timeout=3000)


def _fill_depositor_name(page, depositor_name: str) -> None:
    if not depositor_name:
        return
    selectors = [
        "input[name*='depositor']",
        "input[name*='deposit']",
        "input[id*='depositor']",
        "input[placeholder*='입금']",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.fill(depositor_name, timeout=2000)
            return
        except Exception:
            continue


def _check_required_agreements(page) -> None:
    for locator in page.locator("input[type='checkbox']").all():
        try:
            if locator.is_enabled(timeout=500) and not locator.is_checked(timeout=500):
                locator.check(timeout=500)
        except Exception:
            continue


def _extract_order_no(text: str) -> str:
    patterns = [
        r"주문\s*번호\s*[:：]?\s*([0-9]{6,})",
        r"Order\s*No\.?\s*[:：]?\s*([0-9]{6,})",
        r"\b([0-9]{8,12})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    raise RuntimeError("주문 완료 화면에서 주문번호를 추출하지 못했습니다.")


def _extract_amount(text: str) -> int | None:
    match = re.search(r"(?:결제|주문|입금)\s*금액\s*[:：]?\s*([0-9,]+)\s*원", text)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _download_quote_pdf(page, order_no: str, settings: Settings, job: PurchaseJob) -> Path:
    quote_url = settings.compuzone_quote_url_template.format(order_no=order_no)
    quote_path = _job_artifact_dir(job, settings) / f"compuzone_quote_{order_no}.pdf"
    quote_path.parent.mkdir(parents=True, exist_ok=True)
    response = page.goto(quote_url, wait_until="networkidle", timeout=60000)
    if response is not None:
        body = response.body()
        content_type = response.headers.get("content-type", "")
        if body.startswith(b"%PDF") or "pdf" in content_type.lower():
            quote_path.write_bytes(body)
            return quote_path
    raise RuntimeError("주문번호 기반 견적서 PDF 저장에 실패했습니다. 견적서 URL 템플릿을 확인해 주세요.")
