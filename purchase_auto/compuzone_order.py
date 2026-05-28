from __future__ import annotations

import base64
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import Settings, load_settings
from .corps import CORPS
from .models import PurchaseJob
from .pdf import write_minimal_pdf


_BROWSER_DIAGNOSTIC_LIMIT = 16
_RELEVANT_RESPONSE_RE = re.compile(
    r"(order_function|product_detail_opt_function|basket|bsk|cart|buy|buyea)",
    re.IGNORECASE,
)
ProgressLog = Callable[[str], None] | None


def _progress(log: ProgressLog, message: str) -> None:
    if not log:
        return
    try:
        log(message)
    except Exception:
        pass


def _elapsed(started_at: float) -> str:
    return f"{time.perf_counter() - started_at:.1f}초"


class AutomationNotEnabledError(RuntimeError):
    pass


class LoginRequiredError(RuntimeError):
    pass


class SoldOutProductError(RuntimeError):
    code = "SOLD_OUT_PRODUCT"

    def __init__(self, message: str, product_no: str, product_url: str) -> None:
        super().__init__(message)
        self.product_no = product_no
        self.product_url = product_url

    def as_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": str(self),
            "product_no": self.product_no,
            "product_url": self.product_url,
        }


class QuoteDownloadError(RuntimeError):
    def __init__(self, message: str, order_no: str, amount: int | None, item_summary: str) -> None:
        super().__init__(message)
        self.order_no = order_no
        self.amount = amount
        self.item_summary = item_summary


@dataclass(frozen=True)
class CompuzoneOrderResult:
    order_no: str
    amount: int | None
    item_summary: str
    quote_pdf_path: str
    raw_status: str


@dataclass(frozen=True)
class CompuzoneProductLine:
    name: str
    quantity: int
    unit_price: int | None = None
    amount: int | None = None
    product_no: str = ""


_PRODUCT_CODE_LINE_RE = re.compile(
    r"^\s*(?:제품코드|상품코드|상품번호|Product\s*No\.?)\s*[:：]?\s*\d+\s*$",
    re.IGNORECASE,
)


def _normalized_product_no(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _is_suspicious_product_line(line: CompuzoneProductLine) -> bool:
    name = re.sub(r"\s+", " ", line.name or "").strip()
    if not name:
        return True
    product_no = _normalized_product_no(line.product_no)
    if _PRODUCT_CODE_LINE_RE.match(name):
        return True
    if product_no and str(line.quantity) == product_no:
        return True
    if (line.quantity or 0) > 999:
        return True
    if line.unit_price is not None and line.amount is not None and line.quantity > 0:
        expected = line.unit_price * line.quantity
        tolerance = max(1000, abs(expected) // 20)
        if abs(expected - line.amount) > tolerance:
            return True
    return False


def _usable_product_lines(lines: list[CompuzoneProductLine]) -> list[CompuzoneProductLine]:
    return [line for line in lines if not _is_suspicious_product_line(line)]


def run_compuzone_order(
    job: PurchaseJob,
    settings: Settings | None = None,
    log: ProgressLog = None,
) -> CompuzoneOrderResult:
    settings = settings or load_settings()
    if settings.dry_run:
        return _dry_run_order(job, settings)
    if not settings.enable_live_compuzone_order:
        raise AutomationNotEnabledError("실제 컴퓨존 주문은 PURCHASE_AUTO_ENABLE_LIVE_COMPUZONE_ORDER=1 일 때만 실행됩니다.")
    return _live_order(job, settings, log=log)


def _job_artifact_dir(job: PurchaseJob, settings: Settings) -> Path:
    return settings.artifact_dir / job.job_id


def _item_summary(job: PurchaseJob, product_lines: list[CompuzoneProductLine] | None = None) -> str:
    formatted = _format_product_lines(product_lines or [])
    if formatted:
        return formatted
    return ", ".join(f"{index + 1}번 상품 x {item.quantity}" for index, item in enumerate(job.items))


def _format_product_lines(product_lines: list[CompuzoneProductLine]) -> str:
    rows: list[str] = []
    for line in product_lines:
        if _is_suspicious_product_line(line):
            continue
        name = re.sub(r"\s+", " ", line.name or "").strip()
        quantity = line.quantity or 0
        unit_price = line.unit_price
        amount = line.amount if line.amount is not None else (unit_price * quantity if unit_price is not None else None)
        if not name or quantity <= 0 or unit_price is None or amount is None:
            continue
        rows.append(f"{name}\t{quantity}\t{unit_price}\t{amount}")
    return "\n".join(rows)


def _dry_run_order(job: PurchaseJob, settings: Settings) -> CompuzoneOrderResult:
    order_no = f"9{abs(hash(job.job_id)) % 10000000:07d}"
    quote_path = _quote_pdf_path(job, settings, order_no)
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


def _live_order(job: PurchaseJob, settings: Settings, log: ProgressLog = None) -> CompuzoneOrderResult:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    settings.compuzone_profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = None
        close_context = True
        if settings.compuzone_cdp_url and settings.allow_existing_browser_cdp:
            browser = p.chromium.connect_over_cdp(settings.compuzone_cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context(accept_downloads=True)
            close_context = False
        else:
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(settings.compuzone_profile_dir),
                    headless=settings.headless,
                    accept_downloads=True,
                )
            except Exception as exc:
                raise RuntimeError(
                    "컴퓨존 브라우저 실행에 실패했습니다. "
                    f"프로필이 이미 사용 중이거나 Chromium이 즉시 종료되었습니다: {settings.compuzone_profile_dir}"
                ) from exc
        page = context.new_page()
        dialog_messages: list[str] = []
        browser_diagnostics = _attach_browser_diagnostics(page)

        def _accept_dialog(dialog) -> None:
            try:
                dialog_messages.append(dialog.message)
            except Exception:
                pass
            dialog.accept()

        page.on("dialog", _accept_dialog)
        try:
            session_started = time.perf_counter()
            _progress(log, "컴퓨존 세션 확인 시작")
            _ensure_compuzone_session(page, settings)
            _progress(log, f"컴퓨존 세션 확인 완료 ({_elapsed(session_started)})")
            cart_started = time.perf_counter()
            if len(job.items) == 1:
                product_lines = _open_single_item_order_page(page, job.items[0], dialog_messages)
            else:
                product_lines = _open_cart_order_page(page, job, settings, dialog_messages, browser_diagnostics, log=log)
            _progress(log, f"컴퓨존 주문 페이지 준비 완료 ({_elapsed(cart_started)})")
            _raise_if_login_required(page)
            prepare_started = time.perf_counter()
            _prepare_order_page(page, settings, job, log=log)
            _progress(log, f"컴퓨존 주문서 입력 완료 ({_elapsed(prepare_started)})")
            product_lines = _merge_product_lines(product_lines, _extract_order_page_product_lines(page, job))
            order_started = time.perf_counter()
            _progress(log, "컴퓨존 최종 주문 클릭 시작")
            _click_final_order(page)
            page.wait_for_load_state("domcontentloaded", timeout=60000)
            body = page.locator("body").inner_text(timeout=10000)
            order_no = _extract_order_no(body)
            amount = _extract_amount(body)
            _progress(log, f"컴퓨존 주문 완료 화면 확인 완료: 주문번호 {order_no} ({_elapsed(order_started)})")
            item_summary = _item_summary(job, product_lines)
            try:
                quote_started = time.perf_counter()
                quote_path = _download_quote_pdf(page, order_no, settings, job)
                _progress(log, f"컴퓨존 견적서 PDF 저장 완료 ({_elapsed(quote_started)})")
            except Exception as exc:
                raise QuoteDownloadError(str(exc), order_no, amount, item_summary) from exc
            return CompuzoneOrderResult(
                order_no=order_no,
                amount=amount,
                item_summary=item_summary,
                quote_pdf_path=str(quote_path),
                raw_status="order_submitted_pending_payment",
            )
        except PlaywrightTimeoutError as exc:
            _save_debug_screenshot(page, job, settings, "compuzone_timeout")
            raise RuntimeError(f"컴퓨존 자동화 타임아웃: {exc}") from exc
        except Exception:
            _save_debug_screenshot(page, job, settings, "compuzone_error")
            raise
        finally:
            if close_context:
                context.close()


def _append_limited(values: list[str], value: str, limit: int = _BROWSER_DIAGNOSTIC_LIMIT) -> None:
    value = re.sub(r"\s+", " ", str(value)).strip()
    if not value:
        return
    values.append(value[:1000])
    if len(values) > limit:
        del values[0 : len(values) - limit]


def _request_failure_text(request) -> str:
    failure = getattr(request, "failure", None)
    try:
        failure = failure() if callable(failure) else failure
    except Exception as exc:
        failure = exc
    return str(failure or "")


def _attach_browser_diagnostics(page) -> dict[str, list[str]]:
    events: dict[str, list[str]] = {
        "console": [],
        "pageerror": [],
        "requestfailed": [],
        "responses": [],
    }

    def _on_console(message) -> None:
        try:
            text = f"{message.type}: {message.text}"
        except Exception as exc:
            text = f"console capture failed: {exc}"
        _append_limited(events["console"], text)

    def _on_page_error(error) -> None:
        _append_limited(events["pageerror"], str(error))

    def _on_request_failed(request) -> None:
        try:
            text = f"{request.method} {request.url} {_request_failure_text(request)}"
        except Exception as exc:
            text = f"requestfailed capture failed: {exc}"
        _append_limited(events["requestfailed"], text)

    def _on_response(response) -> None:
        try:
            url = response.url
            status = response.status
            if status >= 400 or _RELEVANT_RESPONSE_RE.search(url):
                _append_limited(events["responses"], f"{status} {url}")
        except Exception as exc:
            _append_limited(events["responses"], f"response capture failed: {exc}")

    page.on("console", _on_console)
    page.on("pageerror", _on_page_error)
    page.on("requestfailed", _on_request_failed)
    page.on("response", _on_response)
    return events


def _format_browser_diagnostics(events: dict[str, list[str]] | None) -> str:
    if not events:
        return ""
    parts = []
    for key in ("requestfailed", "responses", "console", "pageerror"):
        values = events.get(key) or []
        if values:
            parts.append(f"{key}={values[-5:]}")
    return " ".join(parts)


def _cart_click_diagnostic_summary(cart_click: dict[str, object] | None) -> str:
    if not cart_click:
        return ""

    parts: list[str] = []
    method = str(cart_click.get("method") or "").strip()
    if method:
        parts.append(f"클릭방식={method}")

    element = cart_click.get("element")
    if isinstance(element, dict):
        onclick = str(element.get("onclick") or "")
        class_name = str(element.get("className") or "")
        if "basket_insert" in onclick or "cart" in class_name:
            parts.append("장바구니버튼=감지")

    iframe = cart_click.get("iframe")
    if isinstance(iframe, dict):
        src = str(iframe.get("src") or "")
        ready_state = str(iframe.get("readyState") or "")
        if "basket_insert" in src:
            parts.append("장바구니요청=전송")
        if ready_state == "complete":
            parts.append("iframe=완료")

    if not parts:
        return ""
    unique_parts = list(dict.fromkeys(parts))
    return f" 진단={', '.join(unique_parts)}"


def _save_debug_screenshot(page, job: PurchaseJob, settings: Settings, stem: str) -> None:
    try:
        debug_dir = _job_artifact_dir(job, settings)
        debug_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(debug_dir / f"{stem}.png"), full_page=True)
    except Exception:
        pass


def _open_single_item_order_page(page, item, dialog_messages: list[str]) -> list[CompuzoneProductLine]:
    page.goto(item.url, wait_until="domcontentloaded", timeout=60000)
    _raise_if_login_required(page)
    _set_quantity(page, item.quantity)
    product_line = _product_line_from_detail_page(page, item)
    dialog_start = len(dialog_messages)
    _click_first(
        page,
        [
            "a.buy[onclick*='option_insert_direct']",
            "a[onclick*='option_insert_direct']",
            "button:has-text('구매하기')",
            "a:has-text('구매하기')",
            "input[value*='구매하기']",
        ],
        "바로구매 버튼을 찾지 못했습니다.",
    )
    page.wait_for_timeout(800)
    _raise_if_dialog_blocked_order(dialog_messages, dialog_start, item)
    page.wait_for_load_state("domcontentloaded", timeout=60000)
    return [product_line] if product_line else []


def _open_cart_order_page(
    page,
    job: PurchaseJob,
    settings: Settings,
    dialog_messages: list[str],
    browser_diagnostics: dict[str, list[str]] | None = None,
    log: ProgressLog = None,
) -> list[CompuzoneProductLine]:
    if settings.compuzone_clear_cart_before_order:
        clear_started = time.perf_counter()
        _progress(log, "컴퓨존 기존 장바구니 정리 시작")
        _clear_cart(page, settings)
        _progress(log, f"컴퓨존 기존 장바구니 정리 완료 ({_elapsed(clear_started)})")
    expected_marker_groups: list[list[str]] = []
    product_lines: list[CompuzoneProductLine] = []
    total_items = len(job.items)
    for index, item in enumerate(job.items, start=1):
        product_no = _product_no_from_url(item.url)
        item_started = time.perf_counter()
        _progress(log, f"컴퓨존 상품 {index}/{total_items} 페이지 진입 시작: 상품번호={product_no or '-'}")
        page.goto(item.url, wait_until="domcontentloaded", timeout=60000)
        _raise_if_login_required(page)
        _progress(log, f"컴퓨존 상품 {index}/{total_items} 페이지 진입 완료 ({_elapsed(item_started)})")
        product_markers = _product_markers_from_page(page)
        if product_no and product_no not in product_markers:
            product_markers.insert(0, product_no)
        _raise_if_product_unavailable(page, item)
        _set_quantity(page, item.quantity)
        product_line = _product_line_from_detail_page(page, item)
        dialog_start = len(dialog_messages)
        click_started = time.perf_counter()
        cart_click = _click_add_to_cart(page)
        _progress(
            log,
            f"컴퓨존 상품 {index}/{total_items} 장바구니 클릭 완료 ({_elapsed(click_started)}, {cart_click.get('selector') or cart_click.get('method')})",
        )
        iframe_detail = _wait_for_cart_insert_iframe(page)
        if iframe_detail:
            cart_click["iframe"] = iframe_detail
        _raise_if_dialog_blocked_order(dialog_messages, dialog_start, item)
        expected_marker_groups.append(product_markers)
        if product_line:
            product_lines.append(product_line)
        confirm_started = time.perf_counter()
        _confirm_cart_add(
            page,
            settings,
            item,
            len(expected_marker_groups),
            expected_marker_groups,
            dialog_messages,
            dialog_start,
            cart_click,
            browser_diagnostics,
        )
        _progress(log, f"컴퓨존 상품 {index}/{total_items} 장바구니 반영 확인 완료 ({_elapsed(confirm_started)})")
        _progress(log, f"컴퓨존 상품 {index}/{total_items} 처리 완료 ({_elapsed(item_started)})")

    cart_started = time.perf_counter()
    _progress(log, "컴퓨존 장바구니 주문 페이지 이동 시작")
    page.goto(settings.compuzone_cart_url, wait_until="domcontentloaded", timeout=60000)
    _raise_if_login_required(page)
    _assert_cart_ready_for_order(page, len(job.items), expected_marker_groups)
    _click_first(
        page,
        [
            "button:has-text('전체 주문')",
            "a:has-text('전체 주문')",
            "input[value*='전체 주문']",
            "button:has-text('선택 주문')",
            "a:has-text('선택 주문')",
            "input[value*='선택 주문']",
            "button:has-text('주문하기')",
            "a:has-text('주문하기')",
            "button:has-text('구매하기')",
            "a:has-text('구매하기')",
            "button[onclick*='Order']",
            "a[onclick*='Order']",
            "button[onclick*='order']",
            "a[onclick*='order']",
        ],
        "장바구니 주문 버튼을 찾지 못했습니다.",
    )
    page.wait_for_load_state("domcontentloaded", timeout=60000)
    _progress(log, f"컴퓨존 장바구니 주문 페이지 이동 완료 ({_elapsed(cart_started)})")
    return product_lines


def _product_no_from_url(url: str) -> str:
    match = re.search(r"(?:ProductNo|product_no|productNo)=([0-9]+)", url)
    return match.group(1) if match else ""


def _product_markers_from_page(page) -> list[str]:
    try:
        values = page.evaluate(
            """
            () => {
              const values = [];
              for (const selector of [
                'meta[property="og:title"]',
                'meta[name="title"]',
                'h1',
                'h2',
                '.prod_name',
                '.product_name',
                '.product-title',
                '.product_title',
                '[class*="product"][class*="name"]',
                '[class*="prod"][class*="name"]'
              ]) {
                for (const element of document.querySelectorAll(selector)) {
                  const text = element.getAttribute('content') || element.innerText || element.textContent || '';
                  if (text.trim()) values.push(text.trim());
                }
              }
              if (document.title) values.push(document.title);
              return values;
            }
            """
        )
    except Exception:
        values = []

    markers: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", str(value)).strip()
        cleaned = re.sub(r"^\s*컴퓨존\s*[-–]\s*", "", cleaned)
        cleaned = re.sub(r"\s*[-–]\s*컴퓨존\s*$", "", cleaned)
        if len(cleaned) >= 4 and cleaned not in markers:
            markers.append(cleaned)
        for token in re.findall(r"\b[A-Za-z0-9][A-Za-z0-9._+/-]{2,}\b", cleaned):
            if len(token) >= 4 and any(ch.isdigit() for ch in token) and token not in markers:
                markers.append(token)
    return markers[:8]


def _product_line_from_detail_page(page, item) -> CompuzoneProductLine | None:
    product_no = _product_no_from_url(item.url)
    try:
        raw = page.evaluate(
            """
            () => {
              const normalize = value => String(value || '').replace(/\\s+/g, ' ').trim();
              const cleanTitle = value => normalize(value)
                .replace(/^컴퓨존\\s*[-–]\\s*/i, '')
                .replace(/\\s*[-–]\\s*컴퓨존$/i, '');
              const nameSelectors = [
                'meta[property="og:title"]',
                'meta[name="title"]',
                'h1',
                'h2',
                '.prod_name',
                '.product_name',
                '.product-title',
                '.product_title',
                '[class*="product"][class*="name"]',
                '[class*="prod"][class*="name"]'
              ];
              const names = [];
              for (const selector of nameSelectors) {
                for (const element of document.querySelectorAll(selector)) {
                  const text = cleanTitle(element.getAttribute('content') || element.innerText || element.textContent || '');
                  if (text && !names.includes(text)) names.push(text);
                }
              }
              if (document.title) {
                const title = cleanTitle(document.title);
                if (title && !names.includes(title)) names.push(title);
              }

              const roots = Array.from(document.querySelectorAll(
                '.total_price, .prod_info, .product_info, .product-detail, .product_detail, .right_area, .goods_info, .prodInfo'
              ));
              const searchRoots = roots.length ? roots : [document.body];
              const moneyValues = [];
              const addMoney = text => {
                for (const match of normalize(text).matchAll(/([0-9][0-9,]{2,})\\s*원/g)) {
                  const value = Number(match[1].replace(/,/g, ''));
                  if (Number.isFinite(value) && value > 0) moneyValues.push(value);
                }
              };
              for (const root of searchRoots) {
                const text = normalize(root.innerText || root.textContent || '');
                if (/(판매가|상품가격|가격|금액|원)/.test(text)) addMoney(text);
                for (const element of root.querySelectorAll('[class*="price"], [id*="price"], [class*="Price"], [id*="Price"], strong, em, span')) {
                  addMoney(element.innerText || element.textContent || '');
                }
              }
              return { name: names[0] || '', prices: moneyValues };
            }
            """
        )
    except Exception:
        raw = {}

    name = _clean_summary_name(str(raw.get("name") or ""))
    if not name:
        markers = _product_markers_from_page(page)
        name = _clean_summary_name(markers[0]) if markers else ""
    if not name:
        name = f"상품번호 {product_no}" if product_no else ""

    unit_price = _choose_unit_price(raw.get("prices") or [], item.quantity)
    amount = unit_price * item.quantity if unit_price is not None else None
    if not name:
        return None
    return CompuzoneProductLine(
        name=name,
        quantity=item.quantity,
        unit_price=unit_price,
        amount=amount,
        product_no=product_no,
    )


def _extract_order_page_product_lines(page, job: PurchaseJob) -> list[CompuzoneProductLine]:
    try:
        raw_lines = page.evaluate(
            """
            () => {
              const normalize = value => String(value || '').replace(/\\s+/g, ' ').trim();
              const productNoOf = href => {
                const match = String(href || '').match(/(?:ProductNo|product_no|productNo)=([0-9]+)/i);
                return match ? match[1] : '';
              };
              const moneyValues = text => Array.from(normalize(text).matchAll(/([0-9][0-9,]{2,})\\s*원/g))
                .map(match => Number(match[1].replace(/,/g, '')))
                .filter(value => Number.isFinite(value) && value > 0);
              const quantityOf = (root, fallback) => {
                for (const input of root.querySelectorAll('input')) {
                  const raw = String(input.value || input.getAttribute('value') || '').trim();
                  if (/^[0-9]+$/.test(raw)) return Number(raw);
                }
                const text = normalize(root.innerText || root.textContent || '');
                const patterns = [
                  /수량\\s*[:：]?\\s*([0-9]+)/,
                  /([0-9]+)\\s*EA/i,
                  /([0-9]+)\\s*개/
                ];
                for (const pattern of patterns) {
                  const match = text.match(pattern);
                  if (match) return Number(match[1]);
                }
                return fallback || 1;
              };
              const nameFromContext = (root, anchorText) => {
                const anchorName = normalize(anchorText);
                if (anchorName && !/^(이미지|상품 페이지|상세보기)$/.test(anchorName)) return anchorName;
                const lines = normalize(root.innerText || root.textContent || '')
                  .split(/\\n| {2,}/)
                  .map(normalize)
                  .filter(Boolean);
                return lines.find(line =>
                  !/(주문번호|주문일|수량|금액|배송|결제|적립|바로주문|삭제|선택)/.test(line) &&
                  !/[0-9,]+\\s*원/.test(line)
                ) || '';
              };
              const containerFor = anchor => {
                let node = anchor;
                let best = anchor.closest('tr, li') || anchor.parentElement || anchor;
                for (let depth = 0; node && depth < 8; depth += 1) {
                  const text = normalize(node.innerText || node.textContent || '');
                  if (text.includes('원') && /(수량|상품|가격|주문|금액)/.test(text) && text.length < 1600) {
                    best = node;
                    break;
                  }
                  node = node.parentElement;
                }
                return best;
              };

              const seen = new Set();
              const lines = [];
              for (const anchor of Array.from(document.querySelectorAll('a[href*="product_detail"], a[href*="ProductNo"]'))) {
                const href = anchor.href || anchor.getAttribute('href') || '';
                const productNo = productNoOf(href);
                const root = containerFor(anchor);
                const text = normalize(root.innerText || root.textContent || '');
                const values = moneyValues(text);
                const quantity = quantityOf(root, 1);
                let amount = null;
                let unitPrice = null;
                if (values.length) {
                  const sorted = [...values].sort((a, b) => a - b);
                  amount = sorted[sorted.length - 1];
                  unitPrice = quantity > 1 && amount % quantity === 0 ? amount / quantity : sorted[0];
                }
                const name = nameFromContext(root, anchor.innerText || anchor.textContent || anchor.title || '');
                const key = `${productNo || name}|${quantity}|${unitPrice || ''}|${amount || ''}`;
                if (!name || seen.has(key)) continue;
                seen.add(key);
                lines.push({ name, quantity, unitPrice, amount, productNo });
              }
              return lines;
            }
            """
        )
    except Exception:
        return []

    requested_product_nos = [_product_no_from_url(item.url) for item in job.items]
    requested_product_no_set = {value for value in requested_product_nos if value}
    raw_candidates = list(raw_lines or [])
    if requested_product_no_set:
        raw_candidates = [
            raw
            for raw in raw_candidates
            if str(raw.get("productNo") or "") in requested_product_no_set
        ]

    requested_quantities = [item.quantity for item in job.items]
    lines: list[CompuzoneProductLine] = []
    for index, raw in enumerate(raw_candidates):
        name = _clean_summary_name(str(raw.get("name") or ""))
        quantity = _parse_money_value(raw.get("quantity")) or (
            requested_quantities[index] if index < len(requested_quantities) else 1
        )
        unit_price = _parse_money_value(raw.get("unitPrice"))
        amount = _parse_money_value(raw.get("amount"))
        if amount is not None and unit_price is None and quantity:
            unit_price = amount // quantity
        if unit_price is not None and amount is None:
            amount = unit_price * quantity
        if not name or quantity <= 0:
            continue
        lines.append(
            CompuzoneProductLine(
                name=name,
                quantity=quantity,
                unit_price=unit_price,
                amount=amount,
                product_no=str(raw.get("productNo") or ""),
            )
        )

    if len(lines) < len(job.items):
        return []
    return lines[: len(job.items)]


def _merge_product_lines(
    detail_lines: list[CompuzoneProductLine],
    order_page_lines: list[CompuzoneProductLine],
) -> list[CompuzoneProductLine]:
    detail_clean = _usable_product_lines(detail_lines)
    order_clean = _usable_product_lines(order_page_lines)
    if not detail_clean:
        return order_clean
    if not order_clean:
        return detail_clean

    merged: list[CompuzoneProductLine] = []
    used_order_ids: set[int] = set()
    order_by_no = {
        _normalized_product_no(line.product_no): line
        for line in order_clean
        if _normalized_product_no(line.product_no)
    }

    for detail in detail_clean:
        key = _normalized_product_no(detail.product_no)
        incoming = order_by_no.get(key) if key else None
        if incoming:
            used_order_ids.add(id(incoming))
            merged.append(
                CompuzoneProductLine(
                    name=detail.name or incoming.name,
                    quantity=detail.quantity or incoming.quantity,
                    unit_price=detail.unit_price if detail.unit_price is not None else incoming.unit_price,
                    amount=detail.amount if detail.amount is not None else incoming.amount,
                    product_no=detail.product_no or incoming.product_no,
                )
            )
            continue
        merged.append(detail)

    for incoming in order_clean:
        if id(incoming) not in used_order_ids:
            merged.append(incoming)
    return merged


def _clean_summary_name(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    text = re.sub(r"^\s*컴퓨존\s*[-–]\s*", "", text)
    text = re.sub(r"\s*[-–]\s*컴퓨존\s*$", "", text)
    return text.strip()


def _parse_money_value(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    digits = re.sub(r"[^\d-]", "", str(value))
    if not digits or digits == "-":
        return None
    parsed = int(digits)
    return parsed if parsed >= 0 else None


def _choose_unit_price(values, quantity: int) -> int | None:
    prices = sorted(
        {
            parsed
            for parsed in (_parse_money_value(value) for value in (values or []))
            if parsed is not None and parsed >= 300
        }
    )
    if not prices:
        return None
    if quantity > 1:
        price_set = set(prices)
        for price in prices:
            if price * quantity in price_set:
                return price
        if len(prices) == 1 and prices[0] % quantity == 0:
            return prices[0] // quantity
    return prices[0]


def _locator_debug(locator) -> dict[str, object]:
    try:
        result = locator.evaluate(
            """
            element => ({
              tag: element.tagName,
              text: String(element.innerText || element.value || element.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 160),
              className: String(element.className || '').slice(0, 120),
              id: String(element.id || '').slice(0, 80),
              href: String(element.getAttribute('href') || '').slice(0, 200),
              onclick: String(element.getAttribute('onclick') || '').slice(0, 260),
            })
            """
        )
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _iframe_cart_insert_debug(page) -> dict[str, str]:
    try:
        result = page.evaluate(
            """
            () => {
              const iframe = document.getElementById('common_iframe');
              if (!iframe) return {};
              let text = '';
              let title = '';
              let readyState = '';
              try {
                const doc = iframe.contentDocument || iframe.contentWindow?.document;
                if (doc) {
                  readyState = String(doc.readyState || '');
                  title = String(doc.title || '');
                  text = String(doc.body?.innerText || doc.body?.textContent || '').replace(/\\s+/g, ' ').trim();
                }
              } catch (error) {
                text = `iframe access failed: ${error && error.message ? error.message : error}`;
              }
              return {
                src: String(iframe.getAttribute('src') || iframe.src || '').slice(0, 600),
                readyState: readyState.slice(0, 80),
                title: title.slice(0, 120),
                text: text.slice(0, 600),
              };
            }
            """
        )
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        return {"error": str(exc)[:500]}


def _wait_for_cart_insert_iframe(page) -> dict[str, str]:
    before = _iframe_cart_insert_debug(page).get("src", "")
    try:
        page.wait_for_function(
            """
            previousSrc => {
              if (/\\/bsk\\/basket_main\\.htm/i.test(String(window.location.href || ''))) {
                return true;
              }
              const iframe = document.getElementById('common_iframe');
              if (!iframe) return false;
              const src = String(iframe.getAttribute('src') || iframe.src || '');
              return Boolean(src) && src !== previousSrc;
            }
            """,
            arg=before,
            timeout=2500,
        )
    except Exception:
        return _iframe_cart_insert_debug(page)

    if re.search(r"/bsk/basket_main\.htm", page.url, re.IGNORECASE):
        return _iframe_cart_insert_debug(page)

    try:
        page.wait_for_function(
            """
            () => {
              const iframe = document.getElementById('common_iframe');
              if (!iframe) return false;
              try {
                const doc = iframe.contentDocument || iframe.contentWindow?.document;
                return Boolean(doc) && ['interactive', 'complete'].includes(String(doc.readyState || ''));
              } catch (error) {
                return true;
              }
            }
            """,
            timeout=3000,
        )
    except Exception:
        pass
    page.wait_for_timeout(300)
    return _iframe_cart_insert_debug(page)


def _click_add_to_cart(page) -> dict[str, object]:
    scoped_selectors = [
        ".total_price .btn_area a.cart[onclick*='basket_insert_detail']",
        ".total_price .btn_area button.cart[onclick*='basket_insert_detail']",
        ".total_price .btn_area a[onclick*='basket_insert_detail']",
        ".total_price .btn_area button[onclick*='basket_insert_detail']",
        ".total_price .btn_area a.cart[href*='new_recommendpc_insert']",
        ".total_price .btn_area a[href*='new_recommendpc_insert']:not([href*='_order'])",
        ".total_price .btn_area a[onclick*='new_recommendpc_insert']:not([onclick*='_order'])",
        ".total_price .btn_area button.cart[onclick*='new_recommendpc_insert']",
        ".total_price .btn_area button[onclick*='new_recommendpc_insert']:not([onclick*='_order'])",
        ".total_price .btn_area a.cart[href*='new_compuzonepremiumpc_insert']",
        ".total_price .btn_area a[href*='new_compuzonepremiumpc_insert']:not([href*='_order'])",
        ".total_price .btn_area a[onclick*='new_compuzonepremiumpc_insert']:not([onclick*='_order'])",
        ".total_price .btn_area button.cart[onclick*='new_compuzonepremiumpc_insert']",
        ".total_price .btn_area button[onclick*='new_compuzonepremiumpc_insert']:not([onclick*='_order'])",
        ".total_price .btn_area a.cart[onclick*='basket_insert_direct']",
        ".total_price .btn_area button.cart[onclick*='basket_insert_direct']",
        ".total_price .btn_area a.cart[onclick*='option_insert']",
        ".total_price .btn_area a[onclick*='option_insert'][onclick*='cart']",
        ".total_price .btn_area a[onclick*='option_insert'][onclick*='Cart']",
        ".total_price .btn_area button[onclick*='option_insert'][onclick*='cart']",
        ".total_price .btn_area button[onclick*='option_insert'][onclick*='Cart']",
        ".total_price .btn_area button:has-text('장바구니')",
        ".total_price .btn_area a:has-text('장바구니')",
        ".total_price .btn_area input[value*='장바구니']",
        ".btn_area a[onclick*='basket_insert_detail']",
        ".btn_area button[onclick*='basket_insert_detail']",
        ".btn_area a.cart[href*='new_recommendpc_insert']",
        ".btn_area a[href*='new_recommendpc_insert']:not([href*='_order'])",
        ".btn_area a[onclick*='new_recommendpc_insert']:not([onclick*='_order'])",
        ".btn_area button.cart[onclick*='new_recommendpc_insert']",
        ".btn_area button[onclick*='new_recommendpc_insert']:not([onclick*='_order'])",
        ".btn_area a.cart[href*='new_compuzonepremiumpc_insert']",
        ".btn_area a[href*='new_compuzonepremiumpc_insert']:not([href*='_order'])",
        ".btn_area a[onclick*='new_compuzonepremiumpc_insert']:not([onclick*='_order'])",
        ".btn_area button.cart[onclick*='new_compuzonepremiumpc_insert']",
        ".btn_area button[onclick*='new_compuzonepremiumpc_insert']:not([onclick*='_order'])",
        ".btn_area a.cart[onclick*='basket_insert_direct']",
        ".btn_area button.cart[onclick*='basket_insert_direct']",
        ".btn_area a.cart[onclick*='option_insert']",
        ".btn_area a[onclick*='option_insert'][onclick*='cart']",
        ".btn_area button[onclick*='option_insert'][onclick*='cart']",
        ".btn_area button:has-text('장바구니')",
        ".btn_area a:has-text('장바구니')",
        ".btn_area input[value*='장바구니']",
        "a[onclick*='basket_insert_detail']",
        "button[onclick*='basket_insert_detail']",
        "a.cart[href*='new_recommendpc_insert']",
        "a[href*='new_recommendpc_insert']:not([href*='_order'])",
        "a[onclick*='new_recommendpc_insert']:not([onclick*='_order'])",
        "button.cart[onclick*='new_recommendpc_insert']",
        "button[onclick*='new_recommendpc_insert']:not([onclick*='_order'])",
        "a.cart[href*='new_compuzonepremiumpc_insert']",
        "a[href*='new_compuzonepremiumpc_insert']:not([href*='_order'])",
        "a[onclick*='new_compuzonepremiumpc_insert']:not([onclick*='_order'])",
        "button.cart[onclick*='new_compuzonepremiumpc_insert']",
        "button[onclick*='new_compuzonepremiumpc_insert']:not([onclick*='_order'])",
        "a.cart[onclick*='basket_insert_direct']",
        "button.cart[onclick*='basket_insert_direct']",
        "a.cart[onclick*='option_insert']",
        "a[onclick*='option_insert'][onclick*='cart']",
        "a[onclick*='option_insert'][onclick*='Cart']",
        "button[onclick*='option_insert'][onclick*='cart']",
        "button[onclick*='option_insert'][onclick*='Cart']",
        "input[value*='장바구니']",
    ]
    for selector in scoped_selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() <= 0:
                continue
            element_debug = _locator_debug(locator)
            locator.click(timeout=700)
            return {"method": "selector", "selector": selector, "element": element_debug}
        except Exception:
            continue

    result = page.evaluate(
        """
        () => {
          const visible = element => {
            const style = window.getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== 'none'
              && style.visibility !== 'hidden'
              && rect.width > 0
              && rect.height > 0;
          };
          const textOf = element => [
            element.innerText,
            element.value,
            element.getAttribute('title'),
            element.getAttribute('aria-label'),
            element.textContent,
          ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
          const attrOf = element => [
            element.getAttribute('onclick'),
            element.getAttribute('href'),
            element.className,
            element.id,
          ].filter(Boolean).join(' ');
          const isGlobalNav = element => Boolean(element.closest(
            'header, #header, .header, .top, .gnb, .nav, .quick, .quick_menu, .right_quick, .floating, .footer, #footer, .wish, .favorite'
          ));
          const inBuyArea = element => Boolean(element.closest(
            '.total_price, .btn_area, .prod_info, .product_info, .product_detail, .right_area, .goods_info'
          ));
          const elements = Array.from(document.querySelectorAll('a, button, input, [role="button"], [onclick]'))
            .filter(visible);
          const candidates = [];
          for (const element of elements) {
            const text = textOf(element);
            const attr = attrOf(element);
            const className = String(element.className || '');
            const haystack = `${text} ${attr}`;
            const hasCartText = /장바구니|담기/.test(text);
            const hasCartClass = /(^|\\s|_|-)(cart|basket)(\\s|_|-|$)/i.test(className);
            const hasOptionCartAction = /option_insert/i.test(attr) && /cart|basket|장바구니/i.test(haystack);
            const hasDetailBasketAction = /basket_insert_detail/i.test(attr);
            const hasDirectBasketAction = /basket_insert_direct/i.test(attr);
            const hasRecommendPcCartAction = /new_(recommendpc|compuzonepremiumpc)_insert(?!_order)/i.test(attr);
            const isDirectBuy = /구매하기|바로구매|주문하기|바로주문/.test(text) || /(^|\\s|_|-)buy(\\s|_|-|$)/i.test(className);
            const isBasketPageLink = /basket_main\\.htm/i.test(attr) && !/option_insert|basket_insert_direct/i.test(attr);
            let score = 0;
            if (hasCartText) score += 140;
            if (hasCartClass && !isBasketPageLink) score += 110;
            if (hasOptionCartAction) score += 100;
            if (hasDetailBasketAction) score += 130;
            if (hasRecommendPcCartAction) score += 160;
            if (hasDirectBasketAction && (hasCartText || hasCartClass)) score += 80;
            if (/basket|cart/i.test(attr) && !hasDirectBasketAction) score += 35;
            if (inBuyArea(element)) score += 35;
            if (isBasketPageLink) score -= 220;
            if (hasDirectBasketAction && !hasCartText && !hasCartClass) score -= 150;
            if (isDirectBuy) score -= 170;
            if (/관심|찜|위시|wish|favorite|keep|보관/.test(haystack)) score -= 80;
            if (isGlobalNav(element)) score -= 120;
            if (!text && /basket|cart/i.test(attr) && !/option_insert/i.test(attr)) score -= 90;
            if (score > 0 || /장바구니|담기|구매하기|바로구매|basket_insert_direct|option_insert|new_recommendpc_insert|new_compuzonepremiumpc_insert/i.test(haystack)) {
              candidates.push({ element, score, text, attr: String(attr).slice(0, 160), inBuyArea: inBuyArea(element) });
            }
          }
          candidates.sort((a, b) => b.score - a.score);
          const picked = candidates.find(candidate => candidate.score >= 100);
          if (!picked) {
            return { clicked: false, candidates: candidates.slice(0, 8).map(({ score, text, attr, inBuyArea }) => ({ score, text, attr, inBuyArea })) };
          }
          picked.element.click();
          return {
            clicked: true,
            picked: { score: picked.score, text: picked.text, attr: picked.attr, inBuyArea: picked.inBuyArea },
            candidates: candidates.slice(0, 8).map(({ score, text, attr, inBuyArea }) => ({ score, text, attr, inBuyArea })),
          };
        }
        """
    )
    if result.get("clicked"):
        page.wait_for_timeout(800)
        return {"method": "candidate", "picked": result.get("picked")}
    candidates = result.get("candidates") or []
    raise RuntimeError(f"장바구니 버튼을 찾지 못했습니다. 감지된 후보={candidates}")


def _dialog_messages_since(dialog_messages: list[str], start_index: int) -> list[str]:
    return [
        re.sub(r"\s+", " ", str(message)).strip()
        for message in dialog_messages[start_index:]
        if str(message).strip()
    ]


def _dialog_excerpt(dialog_messages: list[str], start_index: int) -> str:
    messages = _dialog_messages_since(dialog_messages, start_index)
    return " / ".join(messages[-5:])


def _raise_if_dialog_blocked_order(dialog_messages: list[str], start_index: int, item) -> None:
    messages = _dialog_messages_since(dialog_messages, start_index)
    if not messages:
        return
    text = " / ".join(messages)
    product_no = _product_no_from_url(item.url)
    detail = f"상품번호={product_no} " if product_no else ""
    if re.search(r"로그인|login", text, re.IGNORECASE):
        raise LoginRequiredError("컴퓨존 로그인이 필요합니다. 담당자 PC 브라우저 세션을 먼저 확인해 주세요.")
    if re.search(
        r"품절|일시\s*품절|판매\s*(중지|종료|완료)|구매\s*(불가|할 수 없)|주문\s*(불가|할 수 없)|"
        r"재고\s*(부족|없)|단종|삭제된\s*상품|판매하지\s*않",
        text,
    ):
        raise SoldOutProductError(
            f"컴퓨존 상품이 품절/구매불가라 장바구니에 담을 수 없습니다: {detail}{item.url} 알림={text}",
            product_no,
            item.url,
        )


def _raise_if_product_unavailable(page, item) -> None:
    try:
        status = page.evaluate(
            """
            () => {
              const visible = element => {
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
              };
              const controls = Array.from(document.querySelectorAll(
                '.total_price .btn_area a, .total_price .btn_area button, .total_price .btn_area input, ' +
                '.btn_area a, .btn_area button, .btn_area input'
              )).filter(visible);
              const controlText = controls.map(el => String(el.innerText || el.value || el.textContent || '').replace(/\\s+/g, ' ').trim());
              const hasOrderControl = controlText.some(text => /장바구니|담기|구매|주문/.test(text));
              const hasSoldOutControl = controlText.some(text => /^품절$|품절/.test(text));
              const bodyText = String(document.body ? document.body.innerText || '' : '');
              return { hasOrderControl, hasSoldOutControl, bodyText };
            }
            """
        )
    except Exception:
        return

    body_text = str(status.get("bodyText", ""))
    is_unavailable = bool(status.get("hasSoldOutControl")) and not bool(status.get("hasOrderControl"))
    if not is_unavailable and "품절" in body_text and "장바구니" not in body_text and "구매하기" not in body_text:
        is_unavailable = True
    if is_unavailable:
        product_no = _product_no_from_url(item.url)
        detail = f"상품번호={product_no} " if product_no else ""
        raise SoldOutProductError(
            f"컴퓨존 상품이 품절이라 구매할 수 없습니다: {detail}{item.url}",
            product_no,
            item.url,
        )


def _confirm_cart_add(
    page,
    settings: Settings,
    item,
    expected_count: int,
    expected_marker_groups: list[list[str]],
    dialog_messages: list[str],
    dialog_start: int,
    cart_click: dict[str, object] | None = None,
    browser_diagnostics: dict[str, list[str]] | None = None,
) -> None:
    page.wait_for_timeout(400)
    if _cart_page_contains_products(page, expected_count, expected_marker_groups):
        return

    page.goto(settings.compuzone_cart_url, wait_until="domcontentloaded", timeout=60000)
    _raise_if_login_required(page)
    if _cart_page_contains_products(page, expected_count, expected_marker_groups):
        return

    page.wait_for_timeout(1500)
    page.reload(wait_until="domcontentloaded", timeout=60000)
    _raise_if_login_required(page)
    if _cart_page_contains_products(page, expected_count, expected_marker_groups):
        return

    product_no = _product_no_from_url(item.url)
    detail = f" 상품번호={product_no}" if product_no else ""
    dialog_detail = _dialog_excerpt(dialog_messages, dialog_start)
    if dialog_detail:
        dialog_detail = f" 컴퓨존알림={dialog_detail}"
    click_detail = _cart_click_diagnostic_summary(cart_click)
    screen_summary = _page_text_excerpt(page, max_chars=180)
    screen_detail = f" 화면요약={screen_summary}" if screen_summary else ""
    raise RuntimeError(
        f"상품이 장바구니에 담겼는지 확인하지 못했습니다:{detail} "
        f"상품페이지={item.url} 현재페이지={page.url}{dialog_detail}{click_detail}{screen_detail}"
    )


def _page_text_excerpt(page, max_chars: int = 500) -> str:
    try:
        body = page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""
    lines = []
    for raw_line in body.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if line in lines:
            continue
        lines.append(line)
        if len(" / ".join(lines)) >= max_chars:
            break
    return " / ".join(lines)[:max_chars]



def _cart_page_contains_products(
    page,
    expected_count: int,
    expected_marker_groups: list[list[str]],
) -> bool:
    if expected_count <= 0:
        return _cart_has_any_item(page)
    try:
        body = page.locator("body").inner_text(timeout=3000)
    except Exception:
        body = ""
    cart_signature = _cart_page_signature(page, body)
    visible_count = _cart_visible_product_count(body)
    if visible_count is not None and visible_count < expected_count:
        return False
    marker_groups_match = _cart_contains_marker_groups(cart_signature, expected_marker_groups)
    is_cart_context = _cart_page_looks_like_cart(page)
    if expected_marker_groups and not marker_groups_match:
        return False
    if visible_count is not None:
        return visible_count >= expected_count
    if expected_marker_groups and marker_groups_match and is_cart_context:
        return True
    return _cart_has_any_item(page)


def _assert_cart_ready_for_order(page, expected_count: int, expected_marker_groups: list[list[str]]) -> None:
    try:
        body = page.locator("body").inner_text(timeout=5000)
    except Exception:
        body = ""
    visible_count = _cart_visible_product_count(body)
    if visible_count is not None and visible_count < expected_count:
        raise RuntimeError(f"장바구니 품목 수가 맞지 않습니다. 요청 {expected_count}건, 장바구니 {visible_count}건")
    if not _cart_contains_marker_groups(_cart_page_signature(page, body), expected_marker_groups):
        raise RuntimeError("장바구니에 요청한 상품이 모두 담겼는지 확인하지 못했습니다.")


def _cart_contains_marker_groups(normalized_body: str, expected_marker_groups: list[list[str]]) -> bool:
    non_empty_groups = [group for group in expected_marker_groups if group]
    if not non_empty_groups:
        return True
    return all(
        any(_normalize_text(marker) in normalized_body for marker in marker_group)
        for marker_group in non_empty_groups
    )


def _cart_page_looks_like_cart(page) -> bool:
    page_url = str(getattr(page, "url", "") or "").lower()
    return "basket" in page_url or "/bsk/" in page_url


def _cart_page_signature(page, body: str = "") -> str:
    values = [body or ""]
    try:
        extra_values = page.evaluate(
            """
            () => {
              const values = [
                window.location.href,
                document.body ? document.body.innerText || '' : '',
                document.body ? document.body.textContent || '' : ''
              ];
              for (const element of document.querySelectorAll('a[href], img[src], [onclick], input[value], button[value]')) {
                values.push(
                  element.getAttribute('href') || '',
                  element.getAttribute('src') || '',
                  element.getAttribute('onclick') || '',
                  element.getAttribute('value') || '',
                  element.getAttribute('title') || '',
                  element.getAttribute('alt') || '',
                  element.textContent || ''
                );
              }
              return values;
            }
            """
        )
    except Exception:
        extra_values = []
    if isinstance(extra_values, list):
        values.extend(str(value) for value in extra_values if value)
    return _normalize_text(" ".join(values))


def _cart_visible_product_count(body: str) -> int | None:
    compact = _normalize_text(body)
    delivery_counts = [
        int(match.group(1))
        for match in re.finditer(r"(?:컴퓨존|업체직|업체|판매자|제조사|일반)?배송상품\(?(\d+)\)?", compact)
    ]
    if delivery_counts:
        return sum(delivery_counts)
    match = re.search(r"장바구니상품\(?(\d+)\)?", compact)
    if match:
        return int(match.group(1))
    return None


def _cart_has_any_item(page) -> bool:
    try:
        body = page.locator("body").inner_text(timeout=3000)
    except Exception:
        return False
    normalized = _normalize_text(body)
    if any(token in normalized for token in ("장바구니가비어", "상품이없", "담긴상품이없")):
        return False
    return "장바구니" in normalized and ("수량" in normalized or "주문" in normalized or "상품" in normalized)


def _clear_cart(page, settings: Settings) -> None:
    page.goto(settings.compuzone_cart_url, wait_until="domcontentloaded", timeout=60000)
    _raise_if_login_required(page)
    if not _body_contains(page, "장바구니 비우기"):
        return
    try:
        _click_first(
            page,
            [
                "button:has-text('장바구니 비우기')",
                "a:has-text('장바구니 비우기')",
                "input[value*='장바구니 비우기']",
                "button:has-text('비우기')",
                "a:has-text('비우기')",
                "input[value*='비우기']",
            ],
            "장바구니 비우기 버튼을 찾지 못했습니다.",
            timeout=2000,
        )
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        page.wait_for_timeout(1500)


def _click_first(page, selectors: list[str], error_message: str, timeout: int = 5000) -> None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.click(timeout=timeout)
            return
        except Exception:
            continue
    raise RuntimeError(error_message)


def _ensure_compuzone_session(page, settings: Settings) -> None:
    page.goto("https://www.compuzone.co.kr/login/login.htm", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    if _compuzone_logged_in(page):
        return
    if not settings.compuzone_login_id or not settings.compuzone_login_password:
        _raise_if_login_required(page)
        return
    _fill_login_input(
        page,
        "#member_id, input[name='member_id'], input[name='login_id'], input[name='id']",
        settings.compuzone_login_id,
    )
    _fill_login_input(
        page,
        "#member_password, input[name='member_password'], input[type='password']",
        settings.compuzone_login_password,
    )
    _submit_compuzone_login(page)
    if _wait_for_compuzone_login(page, timeout_ms=15000):
        return

    page.goto("https://www.compuzone.co.kr/main/main.htm", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    if not _compuzone_logged_in(page):
        raise LoginRequiredError("컴퓨존 로그인에 실패했습니다. 계정 또는 보안 확인이 필요합니다.")


def _submit_compuzone_login(page) -> None:
    button_selectors = [
        "button.login-btn.bg-blue",
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('로그인')",
        "a:has-text('로그인')",
        "input[value*='로그인']",
        "[onclick*='login_check']",
        "[onclick*='Login']",
        "[onclick*='login']",
    ]
    clicked = False
    for selector in button_selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=800):
                locator.click(timeout=3000)
                clicked = True
                break
        except Exception:
            continue

    if clicked:
        page.wait_for_timeout(1500)
    else:
        try:
            page.locator("#member_password, input[name='member_password'], input[type='password']").first.press(
                "Enter", timeout=3000
            )
            page.wait_for_timeout(1500)
        except Exception:
            pass

    if _compuzone_logged_in(page):
        return

    try:
        page.evaluate(
            """
            () => {
              if (typeof window.login_check === 'function') {
                return window.login_check();
              }
              const password = document.querySelector('#member_password, input[name="member_password"], input[type="password"]');
              const form = password ? password.closest('form') : document.querySelector('form');
              if (form) {
                if (typeof form.requestSubmit === 'function') {
                  form.requestSubmit();
                } else {
                  form.submit();
                }
              }
              return null;
            }
            """
        )
    except Exception:
        pass


def _wait_for_compuzone_login(page, timeout_ms: int) -> bool:
    step_ms = 500
    waited_ms = 0
    while waited_ms <= timeout_ms:
        if _compuzone_logged_in(page):
            return True
        page.wait_for_timeout(step_ms)
        waited_ms += step_ms
    return False


def _compuzone_logged_in(page) -> bool:
    try:
        body = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return False
    return "로그아웃" in body


def _fill_login_input(page, selector: str, value: str) -> None:
    locator = page.locator(selector).first
    locator.wait_for(state="visible", timeout=10000)
    locator.fill(value, timeout=5000)


def _set_quantity(page, quantity: int) -> None:
    selectors = [
        "input[name='OrdQty']",
        "input[name='quantity']",
        "input[name='qty']",
        "input[id*='qty']",
        "input[id^='last_ea']",
        "input[name^='last_ea']",
        "input.num",
        "input[class*='last_']",
        "input[title*='수량']",
    ]
    for selector in selectors:
        for locator in page.locator(selector).all():
            try:
                if not locator.is_visible(timeout=500):
                    continue
                locator.fill(str(quantity), timeout=3000)
                locator.evaluate(
                    """
                    el => {
                      el.dispatchEvent(new Event('input', { bubbles: true }));
                      el.dispatchEvent(new Event('change', { bubbles: true }));
                      el.dispatchEvent(new Event('blur', { bubbles: true }));
                      for (const fn of ['every_total_price', 'Add_Total_Price']) {
                        try {
                          if (typeof window[fn] === 'function') {
                            window[fn]();
                          }
                        } catch (_) {
                        }
                      }
                    }
                    """
                )
                return
            except Exception:
                continue
    if quantity != 1:
        raise RuntimeError("수량 입력칸을 찾지 못했습니다.")


def _raise_if_login_required(page) -> None:
    if "login" in page.url.lower():
        raise LoginRequiredError("컴퓨존 로그인이 필요합니다. 담당자 PC 브라우저 세션을 먼저 확인해 주세요.")
    login_form_visible = False
    try:
        login_form_visible = page.locator(
            "#member_id, input[name='member_id'], #member_password, input[name='member_password']"
        ).first.is_visible(timeout=500)
    except Exception:
        pass
    if login_form_visible:
        raise LoginRequiredError("컴퓨존 로그인이 필요합니다. 담당자 PC 브라우저 세션을 먼저 확인해 주세요.")


def _prepare_order_page(page, settings: Settings, job: PurchaseJob, log: ProgressLog = None) -> None:
    delivery_name, delivery_keywords = _job_delivery_selection(job, settings)
    business_number, business_contact_name = _job_tax_business_selection(job, settings)
    _dismiss_order_info_modal(page)
    step_started = time.perf_counter()
    _select_delivery_address(page, delivery_name, delivery_keywords)
    _progress(log, f"컴퓨존 주문서 배송지 선택 완료: {delivery_name} ({_elapsed(step_started)})")
    step_started = time.perf_counter()
    _select_bank_transfer(page)
    _progress(log, f"컴퓨존 주문서 무통장 결제 선택 완료 ({_elapsed(step_started)})")
    step_started = time.perf_counter()
    _select_tax_business(
        page,
        business_number,
        business_contact_name,
    )
    _progress(log, f"컴퓨존 주문서 사업자 선택 완료: {business_number} ({_elapsed(step_started)})")
    step_started = time.perf_counter()
    _fill_depositor_name(page, settings.compuzone_depositor_name)
    _progress(log, f"컴퓨존 주문서 입금자 입력 완료 ({_elapsed(step_started)})")
    step_started = time.perf_counter()
    _check_invoice_options(page)
    _progress(log, f"컴퓨존 주문서 증빙 옵션 확인 완료 ({_elapsed(step_started)})")
    step_started = time.perf_counter()
    _check_required_agreements(page)
    _progress(log, f"컴퓨존 주문서 필수 동의 확인 완료 ({_elapsed(step_started)})")
    _dismiss_order_info_modal(page)


def _job_delivery_selection(job: PurchaseJob, settings: Settings) -> tuple[str, tuple[str, ...]]:
    delivery_name = _memo_field(job, ("배송지", "delivery_name", "delivery")) or settings.compuzone_delivery_name
    raw_keywords = _memo_field(job, ("배송키워드", "delivery_keywords", "delivery_keyword"))
    if raw_keywords:
        keywords = tuple(part.strip() for part in re.split(r"[,，/]+", raw_keywords) if part.strip())
    else:
        keywords = settings.compuzone_delivery_keywords
    return delivery_name, keywords


def _job_tax_business_selection(job: PurchaseJob, settings: Settings) -> tuple[str, str]:
    factory_business_number = _factory_business_number(job)
    business_number = (
        factory_business_number
        or _memo_field(job, ("사업자번호", "business_number", "business_no"))
        or settings.compuzone_business_number
    )
    contact_name = _memo_field(job, ("사업자담당자", "business_contact_name", "business_contact")) or settings.compuzone_business_contact_name
    return business_number, contact_name


FACTORY_BUSINESS_NUMBERS: dict[tuple[str, str], str] = {
    ("D", "1"): "125-81-05619",
    ("D", "2"): "403-85-07607",
    ("D", "3"): "403-85-23311",
    ("P", "1"): "125-81-32697",
    ("P", "2"): "403-85-15640",
    ("P", "3"): "844-85-00770",
    ("P", "4"): "118-85-07029",
}


def _factory_business_number(job: PurchaseJob) -> str:
    text = " ".join(part for part in (job.title, job.memo, job.corp) if part)
    match = re.search(r"\b([DP])\s*([1-4])\s*공?장?\b", text, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"\b([DP])([1-4])\b", text, flags=re.IGNORECASE)
    if not match:
        return ""

    prefix = match.group(1).upper()
    factory_no = match.group(2)
    corp_code = "daeseung_precision" if prefix == "P" else "daeseung"
    if job.corp_code and job.corp_code != corp_code:
        return ""
    return FACTORY_BUSINESS_NUMBERS.get((prefix, factory_no), "")


def _memo_field(job: PurchaseJob, names: tuple[str, ...]) -> str:
    memo = job.memo or ""
    for line in memo.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized = key.strip().lower().replace(" ", "_")
        for name in names:
            if normalized == name.strip().lower().replace(" ", "_"):
                return value.strip()
    return ""


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _body_text(page, timeout: int = 1000) -> str:
    try:
        return page.locator("body").inner_text(timeout=timeout)
    except Exception:
        return ""


def _body_contains(page, value: str) -> bool:
    if not value:
        return True
    return _normalize_text(value) in _normalize_text(_body_text(page))


def _body_contains_all(page, values: tuple[str, ...]) -> bool:
    targets = [_normalize_text(value) for value in values if value]
    if not targets:
        return True
    normalized_body = _normalize_text(_body_text(page))
    return all(target in normalized_body for target in targets)


def _wait_body_contains_all(page, values: tuple[str, ...], timeout_ms: int = 10000) -> bool:
    deadline = time.perf_counter() + max(1, timeout_ms) / 1000
    while True:
        if _body_contains_all(page, values):
            return True
        if time.perf_counter() >= deadline:
            return _body_contains_all(page, values)
        page.wait_for_timeout(500)


def _wait_body_contains(page, value: str, timeout_ms: int = 10000) -> bool:
    return _wait_body_contains_all(page, (value,), timeout_ms=timeout_ms)


def _open_popup(page, selectors: list[str], error_message: str):
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            try:
                locator.scroll_into_view_if_needed(timeout=1000)
            except Exception:
                pass
            with page.expect_popup(timeout=5000) as popup_info:
                locator.click(timeout=3000)
            popup = popup_info.value
            popup.wait_for_load_state("domcontentloaded", timeout=10000)
            return popup
        except Exception:
            continue
    raise RuntimeError(error_message)


def _open_tax_business_popup(page):
    try:
        return _open_popup(
            page,
            [
                "a[href*='tax_list']",
                "a[onclick*='PopTaxManager']",
                "button[onclick*='tax_list']",
                "a[onclick*='tax_list']",
                "input[onclick*='tax_list']",
                "button[onclick*='PopTaxManager']",
                "input[onclick*='PopTaxManager']",
            ],
            "사업자 수정 버튼을 찾지 못했습니다.",
        )
    except RuntimeError:
        pass

    try:
        with page.expect_popup(timeout=5000) as popup_info:
            clicked = page.evaluate(
                """
                () => {
                  const normalize = value => String(value || '').replace(/\\s+/g, '').toLowerCase();
                  const controls = Array.from(document.querySelectorAll('button, a, input[type="button"], input[type="submit"]'));
                  const target = controls.find(el => {
                    const text = normalize(el.innerText || el.value || el.title);
                    if (!text.includes('수정')) {
                      return false;
                    }
                    let node = el;
                    for (let i = 0; node && i < 5; i += 1) {
                      const context = normalize(node.innerText || node.textContent || '');
                      if (context.includes('사업자')) {
                        return true;
                      }
                      node = node.parentElement;
                    }
                    return false;
                  });
                  if (!target) {
                    return false;
                  }
                  target.click();
                  return true;
                }
                """
            )
            if not clicked:
                raise RuntimeError("사업자 수정 버튼을 찾지 못했습니다.")
        popup = popup_info.value
        popup.wait_for_load_state("domcontentloaded", timeout=10000)
        return popup
    except Exception as exc:
        raise RuntimeError("사업자 수정 버튼을 찾지 못했습니다.") from exc


def _click_popup_row_by_text(popup, needles: list[str], action_text: str | None = None) -> None:
    popup.wait_for_load_state("domcontentloaded", timeout=10000)
    popup.wait_for_timeout(500)
    clicked = False
    for _ in range(8):
        try:
            clicked = popup.evaluate(
                """
                ({ needles, actionText }) => {
                  const normalize = value => String(value || '').replace(/\\s+/g, '').toLowerCase();
                  const wanted = needles.filter(Boolean).map(normalize);
                  const action = normalize(actionText || '');
                  const rows = Array.from(document.querySelectorAll('tr'));

                  for (const row of rows) {
                    const rowText = normalize(row.innerText || row.textContent || '');
                    if (!wanted.every(text => rowText.includes(text))) {
                      continue;
                    }

                    const controls = Array.from(row.querySelectorAll('button, a, input[type="button"], input[type="submit"]'));
                    if (action) {
                      const preferred = controls.find(el => normalize(el.innerText || el.value || el.title).includes(action));
                      if (preferred) {
                        preferred.click();
                        return true;
                      }
                    }

                    const cells = Array.from(row.querySelectorAll('td, th'));
                    const addressNeedles = wanted.slice(1);
                    const matchingCell =
                      cells.find(el => {
                        const text = normalize(el.innerText || el.textContent || '');
                        return addressNeedles.some(needle => text.includes(needle));
                      }) ||
                      cells.find(el => {
                        const text = normalize(el.innerText || el.textContent || '');
                        return wanted.some(needle => text.includes(needle));
                      });
                    if (matchingCell) {
                      matchingCell.click();
                      return true;
                    }

                    if (action) {
                      const fallback = controls.find(el => {
                        const text = normalize(el.innerText || el.value || el.title);
                        return !text.includes('수정') && !text.includes('삭제');
                      });
                      if (fallback) {
                        fallback.click();
                        return true;
                      }
                    }

                    row.click();
                    return true;
                  }

                  return false;
                }
                """,
                {"needles": needles, "actionText": action_text or ""},
            )
            break
        except Exception:
            popup.wait_for_timeout(500)
    if not clicked:
        raise RuntimeError(f"팝업에서 선택 대상({', '.join(needles)})을 찾지 못했습니다.")


def _select_delivery_address(page, delivery_name: str, delivery_keywords: tuple[str, ...]) -> None:
    if _wait_body_contains_all(page, delivery_keywords):
        return
    page.wait_for_timeout(1000)
    _dismiss_order_info_modal(page)
    page.wait_for_timeout(300)
    _dismiss_order_info_modal(page)
    popup = _open_popup(
        page,
        [
            "a:has-text('배송지 목록')",
            "a:has-text('배송지목록')",
            "button:has-text('배송지목록')",
            "a[onclick*='PopAddressManagerList']",
            "button[onclick*='PopAddressManagerList']",
            "input[onclick*='PopAddressManagerList']",
            "a[href*='prevDelivery']",
            "a[onclick*='prevDelivery']",
            "button[onclick*='prevDelivery']",
            "input[onclick*='prevDelivery']",
        ],
        "배송지 목록 버튼을 찾지 못했습니다.",
    )
    try:
        _click_popup_row_by_text(popup, [*delivery_keywords], None)
        popup.wait_for_event("close", timeout=5000)
    except Exception:
        if not popup.is_closed():
            popup.close()
        raise
    page.bring_to_front()
    page.wait_for_timeout(1000)
    if not _wait_body_contains_all(page, delivery_keywords):
        raise RuntimeError(f"배송지가 '{delivery_name}'(으)로 변경되지 않았습니다.")


def _select_bank_transfer(page) -> None:
    selectors = [
        "label:has-text('무통장')",
        "text=무통장입금",
        "input[value*='무통장']",
        "input[value*='BANK']",
        "input[value*='bank']",
    ]
    _click_first(page, selectors, "무통장입금 결제수단을 찾지 못했습니다.", timeout=3000)


def _select_tax_business(page, business_number: str, contact_name: str) -> None:
    if _body_contains(page, business_number) and _body_contains(page, contact_name):
        return
    popup = _open_tax_business_popup(page)
    try:
        needles = [business_number]
        if contact_name:
            needles.append(contact_name)
        try:
            _click_popup_row_by_text(popup, needles, "선택")
        except RuntimeError:
            _click_popup_row_by_text(popup, [business_number], "선택")
        popup.wait_for_event("close", timeout=5000)
    except Exception:
        if not popup.is_closed():
            popup.close()
        raise
    page.bring_to_front()
    page.wait_for_timeout(1000)
    if not _body_contains(page, business_number):
        raise RuntimeError(f"사업자가 '{business_number}'(으)로 변경되지 않았습니다.")


def _fill_depositor_name(page, depositor_name: str) -> None:
    filled = page.evaluate(
        """
        depositorName => {
          const readValue = selector => {
            const el = document.querySelector(selector);
            return el ? String(el.value || '').trim() : '';
          };
          const value =
            String(depositorName || '').trim() ||
            readValue('#TaxAccountName') ||
            readValue('input[name="TaxAccountName"]') ||
            readValue('#LatestOrderDeposit') ||
            readValue('input[name="LatestOrderDeposit"]');
          if (!value) {
            return false;
          }

          const setValue = selector => {
            for (const el of document.querySelectorAll(selector)) {
              el.value = value;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
              el.dispatchEvent(new Event('blur', { bubbles: true }));
            }
          };

          setValue('#OrderDeposit, input[name="OrderDeposit"]');
          setValue('#OrderDepositTax, input[name="OrderDepositTax"]');
          setValue('#LatestOrderDeposit, input[name="LatestOrderDeposit"]');

          for (const selector of [
            '#SelectOrderDeposit2',
            'input[name="SelectOrderDeposit"][value="2"]',
            'input[name="OrderDepositSelect"][value="2"]'
          ]) {
            const radio = document.querySelector(selector);
            if (radio) {
              radio.checked = true;
              radio.dispatchEvent(new Event('input', { bubbles: true }));
              radio.dispatchEvent(new Event('change', { bubbles: true }));
            }
          }

          for (const selector of ['#UseDeliveryBarcodeGift', 'input[name="UseDeliveryBarcodeGift"]']) {
            const checkbox = document.querySelector(selector);
            if (checkbox && checkbox.checked) {
              checkbox.checked = false;
              checkbox.dispatchEvent(new Event('change', { bubbles: true }));
            }
          }
          return true;
        }
        """,
        depositor_name,
    )
    if filled or not depositor_name:
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


def _dismiss_order_info_modal(page) -> None:
    page.evaluate(
        """
        () => {
          const visible = (element) => {
            const style = window.getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
          };
          const closeByText = (needle) => {
            const roots = Array.from(document.querySelectorAll('div, section, article'))
              .filter(element => visible(element) && (element.innerText || element.textContent || '').includes(needle))
              .sort((a, b) => {
                const ar = a.getBoundingClientRect();
                const br = b.getBoundingClientRect();
                return (ar.width * ar.height) - (br.width * br.height);
              });
            for (const root of roots) {
              const close = Array.from(root.querySelectorAll('button, a, span, i, em, div'))
                .filter(visible)
                .find(element => {
                  const text = (element.innerText || element.textContent || '').replace(/\\s+/g, ' ').trim();
                  const klass = String(element.className || '').toLowerCase();
                  const title = String(element.title || '').toLowerCase();
                  return text === '×' || text === 'X' || text === '닫기' || klass.includes('close') || title.includes('닫기');
                });
              if (close) {
                close.click();
                return true;
              }
              root.style.display = 'none';
              return true;
            }
            return false;
          };
          const hideLayerByText = (needle) => {
            const candidates = Array.from(document.querySelectorAll('div, section, article'))
              .filter(element => visible(element) && (element.innerText || element.textContent || '').includes(needle));
            for (const candidate of candidates) {
              let node = candidate;
              for (let depth = 0; node && depth < 8; depth += 1) {
                const style = window.getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                const zIndex = Number.parseInt(style.zIndex || '0', 10);
                const modalLike =
                  style.position === 'fixed' ||
                  style.position === 'absolute' ||
                  zIndex >= 100 ||
                  (rect.width >= 400 && rect.height >= 200);
                if (modalLike) {
                  node.style.display = 'none';
                  node.setAttribute('aria-hidden', 'true');
                  return true;
                }
                node = node.parentElement;
              }
            }
            return false;
          };
          const removeLayerByText = (needle) => {
            const candidates = Array.from(document.querySelectorAll('div, section, article'))
              .filter(element => visible(element) && (element.innerText || element.textContent || '').includes(needle))
              .sort((a, b) => {
                const ar = a.getBoundingClientRect();
                const br = b.getBoundingClientRect();
                return (ar.width * ar.height) - (br.width * br.height);
              });
            for (const candidate of candidates) {
              let node = candidate;
              for (let depth = 0; node && depth < 8; depth += 1) {
                if (node === document.body || node === document.documentElement) {
                  break;
                }
                const style = window.getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                const zIndex = Number.parseInt(style.zIndex || '0', 10);
                const modalLike =
                  style.position === 'fixed' ||
                  style.position === 'absolute' ||
                  zIndex >= 100 ||
                  (rect.width >= 400 && rect.width <= 1400 && rect.height >= 150 && rect.height <= 900);
                if (modalLike) {
                  node.remove();
                  return true;
                }
                node = node.parentElement;
              }
            }
            return false;
          };
          closeByText('상품 배송 안내');
          closeByText('방문결제 제한 상품 안내');
          hideLayerByText('상품 배송 안내');
          hideLayerByText('방문결제 제한 상품 안내');
          removeLayerByText('상품 배송 안내');
          removeLayerByText('방문결제 제한 상품 안내');
          for (const selector of ['#chgOrderInfoLayer', '.chgOrderInfoLayer']) {
            const modal = document.querySelector(selector);
            if (modal) {
              modal.style.display = 'none';
              modal.setAttribute('aria-hidden', 'true');
            }
          }
          document.body.style.overflow = 'unset';
          const stickySummary = document.querySelector('.totalS_wrap');
          if (stickySummary) {
            stickySummary.style.zIndex = '10';
          }
        }
        """
    )


def _click_final_order(page) -> None:
    selectors = [
        "button:has-text('바로 결제하기')",
        "a:has-text('바로 결제하기')",
        "input[value*='바로 결제하기']",
        "button:has-text('결제하기')",
        "button:has-text('주문하기')",
        "a:has-text('결제하기')",
        "a:has-text('주문하기')",
        "input[value*='결제']",
        "input[value*='주문']",
    ]
    _dismiss_order_info_modal(page)
    _click_first(page, selectors, "최종 주문 버튼을 찾지 못했습니다.")
    page.wait_for_timeout(1500)
    if _body_contains(page, "상품 배송 안내"):
        _dismiss_order_info_modal(page)
        page.wait_for_timeout(500)
        _click_first(page, selectors, "최종 주문 버튼을 찾지 못했습니다.")


def _checkbox_context_text(locator) -> str:
    return locator.evaluate(
        """
        el => {
          const chunks = [];
          if (el.id && window.CSS && CSS.escape) {
            const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (label) chunks.push(label.innerText || label.textContent || '');
          }

          const label = el.closest('label');
          if (label) chunks.push(label.innerText || label.textContent || '');

          let node = el.parentElement;
          for (let i = 0; node && i < 3; i += 1) {
            chunks.push(node.innerText || node.textContent || '');
            for (const sibling of [node.previousElementSibling, node.nextElementSibling]) {
              if (sibling) chunks.push(sibling.innerText || sibling.textContent || '');
            }
            node = node.parentElement;
          }
          return chunks.join('\\n');
        }
        """
    )


def _check_checkbox_by_keywords(page, keyword_groups: list[list[str]], error_message: str) -> None:
    for locator in page.locator("input[type='checkbox']").all():
        try:
            context = _checkbox_context_text(locator)
            normalized = _normalize_text(context)
            if not any(all(_normalize_text(keyword) in normalized for keyword in group) for group in keyword_groups):
                continue
            _set_checkbox(locator)
            return
        except Exception:
            continue
    raise RuntimeError(error_message)


def _set_checkbox(locator) -> None:
    try:
        locator.scroll_into_view_if_needed(timeout=1000)
    except Exception:
        pass
    try:
        if locator.is_checked(timeout=500):
            return
    except Exception:
        pass
    try:
        locator.check(timeout=1000, force=True)
        return
    except Exception:
        pass
    locator.evaluate(
        """
        el => {
          if (!el.checked) {
            el.click();
          }
          if (!el.checked) {
            el.checked = true;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
          }
        }
        """
    )


def _check_invoice_options(page) -> None:
    _check_checkbox_by_keywords(
        page,
        [
            ["세금계산서", "발행", "정보"],
            ["세금계산서", "정보", "확인"],
        ],
        "세금계산서 정보 확인 체크박스를 찾지 못했습니다.",
    )
    _check_checkbox_by_keywords(
        page,
        [
            ["온라인", "견적서", "이메일"],
            ["온라인", "견적서", "수신"],
        ],
        "온라인 견적서 이메일 수신 체크박스를 찾지 못했습니다.",
    )


def _check_required_agreements(page) -> None:
    for locator in page.locator("input[type='checkbox']").all():
        try:
            context = _checkbox_context_text(locator)
            normalized = _normalize_text(context)
            should_check = (
                "필수" in normalized
                or "주문내용을확인" in normalized
                or ("정보제공" in normalized and "동의" in normalized)
            )
            if should_check and locator.is_enabled(timeout=500):
                _set_checkbox(locator)
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
    quote_path = _quote_pdf_path(job, settings, order_no)
    quote_path.parent.mkdir(parents=True, exist_ok=True)
    response = page.goto(quote_url, wait_until="networkidle", timeout=60000)
    if response is not None:
        body = response.body()
        content_type = response.headers.get("content-type", "")
        if body.startswith(b"%PDF") or "pdf" in content_type.lower():
            quote_path.write_bytes(body)
            return quote_path

    quote_page = _open_quote_print_page(page)
    _assert_quote_page_accessible(quote_page, order_no)
    _save_page_as_pdf(quote_page, quote_path)
    if not quote_path.exists() or quote_path.stat().st_size < 1024:
        raise RuntimeError(f"컴퓨존 견적서 PDF 저장 결과가 비어 있습니다: {quote_path}")
    return quote_path


def _quote_pdf_path(job: PurchaseJob, settings: Settings, order_no: str) -> Path:
    return _job_artifact_dir(job, settings) / f"견적서 - 컴퓨존({order_no}).pdf"


def _open_quote_print_page(page):
    for selector in (
        "button:has-text('출력하기')",
        "a:has-text('출력하기')",
        "input[type='button'][value*='출력']",
        "button:has-text('출력')",
        "a:has-text('출력')",
    ):
        button = page.locator(selector).first
        try:
            if not button.is_visible(timeout=1000):
                continue
            try:
                with page.context.expect_page(timeout=5000) as popup_info:
                    button.click(timeout=2000)
                popup = popup_info.value
                popup.wait_for_load_state("networkidle", timeout=30000)
                return popup
            except Exception:
                button.click(timeout=2000)
                page.wait_for_load_state("networkidle", timeout=30000)
                return page
        except Exception:
            continue
    return page


def _assert_quote_page_accessible(page, order_no: str) -> None:
    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        text = ""
    compact = re.sub(r"\D+", "", text)
    deny_tokens = ("권한", "접근", "존재하지", "조회된", "잘못", "로그인")
    if order_no not in compact and any(token in text for token in deny_tokens):
        raise RuntimeError("컴퓨존 견적서 페이지 접근이 거부되었거나 조회에 실패했습니다.")


def _save_page_as_pdf(page, output_path: Path) -> None:
    try:
        page.emulate_media(media="print")
    except Exception:
        pass
    session = page.context.new_cdp_session(page)
    payload = session.send(
        "Page.printToPDF",
        {
            "printBackground": True,
            "paperWidth": 8.27,
            "paperHeight": 11.69,
            "marginTop": 0.2,
            "marginBottom": 0.2,
            "marginLeft": 0.2,
            "marginRight": 0.2,
            "preferCSSPageSize": True,
        },
    )
    output_path.write_bytes(base64.b64decode(payload["data"]))
