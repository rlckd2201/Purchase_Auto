from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import Settings, load_settings
from .corps import CORPS
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


def submit_groupware_approval(job: PurchaseJob, settings: Settings | None = None) -> ApprovalResult:
    settings = settings or load_settings()
    if settings.dry_run:
        return _dry_run_submit(job, settings)
    if not settings.enable_live_groupware_submit:
        raise ApprovalAutomationNotEnabledError(
            "실제 그룹웨어 상신은 PURCHASE_AUTO_ENABLE_LIVE_GROUPWARE_SUBMIT=1 일 때만 실행됩니다."
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
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(settings.groupware_profile_dir),
            headless=settings.headless,
            accept_downloads=True,
        )
        page = context.new_page()
        try:
            page.goto(form_url, wait_until="domcontentloaded", timeout=60000)
            _raise_if_login_required(page)
            _fill_title(page, job.title or f"{corp.display_name} 컴퓨존 구매 품의")
            _fill_body(page, _approval_body(job))
            _attach_quote(page, Path(job.quote_pdf_path))
            _add_finance_reference_group(page, corp.finance_reference_group)
            _click_first(
                page,
                [
                    "button:has-text('결재요청')",
                    "a:has-text('결재요청')",
                    "button:has-text('상신')",
                    "a:has-text('상신')",
                    "text=결재요청",
                ],
                "결재요청 버튼을 찾지 못했습니다.",
                timeout=5000,
            )
            _confirm_if_present(page)
            page.wait_for_load_state("domcontentloaded", timeout=60000)
            document_url = page.url
            document_id = _extract_document_id(document_url, page.locator("body").inner_text(timeout=10000))
            return ApprovalResult(document_id=document_id, document_url=document_url, raw_status="approval_submitted")
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"그룹웨어 자동화 타임아웃: {exc}") from exc
        finally:
            context.close()


def _approval_body(job: PurchaseJob) -> str:
    item_lines = "\n".join(
        f"- {index + 1}번 상품: {item.url} / 수량 {item.quantity}" for index, item in enumerate(job.items)
    )
    return "\n".join(
        [
            f"구매 목적: {job.memo or '컴퓨존 구매 요청'}",
            f"컴퓨존 주문번호: {job.order_no or ''}",
            f"주문 상태: 무통장입금 대기",
            f"금액: {job.amount if job.amount is not None else '견적서 참조'}",
            "구매 품목:",
            item_lines,
            "",
            "첨부: 컴퓨존 견적서 PDF",
        ]
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


def _raise_if_login_required(page) -> None:
    text = ""
    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        pass
    if "로그인" in text and ("비밀번호" in text or "아이디" in text):
        raise GroupwareLoginRequiredError("그룹웨어 로그인이 필요합니다. 담당자 PC 브라우저 세션을 먼저 확인해 주세요.")


def _fill_title(page, title: str) -> None:
    selectors = [
        "input[name='title']",
        "input[name='subject']",
        "input[id*='title']",
        "input[id*='subject']",
        "input[placeholder*='제목']",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.fill(title, timeout=3000)
            return
        except Exception:
            continue


def _fill_body(page, body: str) -> None:
    selectors = [
        "textarea[name='content']",
        "textarea[id*='content']",
        "div[contenteditable='true']",
        "iframe",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if selector == "iframe":
                frame = locator.content_frame()
                frame.locator("body").fill(body, timeout=3000)
            else:
                locator.fill(body, timeout=3000)
            return
        except Exception:
            continue


def _attach_quote(page, quote_path: Path) -> None:
    file_inputs = page.locator("input[type='file']")
    if file_inputs.count() > 0:
        file_inputs.first.set_input_files(str(quote_path))
        return
    _click_first(
        page,
        ["button:has-text('첨부')", "a:has-text('첨부')", "text=첨부"],
        "첨부 버튼 또는 파일 입력칸을 찾지 못했습니다.",
    )
    page.locator("input[type='file']").first.set_input_files(str(quote_path))


def _add_finance_reference_group(page, group_name: str) -> None:
    _click_first(page, ["text=결재 정보", "button:has-text('결재 정보')", "a:has-text('결재 정보')"], "결재 정보 버튼을 찾지 못했습니다.")
    _click_first(page, ["text=참조자", "button:has-text('참조자')", "a:has-text('참조자')"], "참조자 탭을 찾지 못했습니다.")
    _click_first(page, ["text=개인 그룹", "button:has-text('개인 그룹')", "a:has-text('개인 그룹')"], "개인 그룹 탭을 찾지 못했습니다.")
    _click_first(page, [f"text={group_name}"], f"참조자 개인 그룹을 찾지 못했습니다: {group_name}")
    for selector in ["button:has-text('>')", "button[title*='추가']", "a[title*='추가']", "button:has-text('추가')"]:
        try:
            page.locator(selector).first.click(timeout=1000)
            break
        except Exception:
            continue
    _click_first(page, ["button:has-text('확인')", "a:has-text('확인')", "text=확인"], "참조자 확인 버튼을 찾지 못했습니다.")


def _confirm_if_present(page) -> None:
    for selector in ["button:has-text('확인')", "button:has-text('예')", "text=확인", "text=예"]:
        try:
            page.locator(selector).first.click(timeout=1500)
            return
        except Exception:
            continue


def _extract_document_id(url: str, text: str) -> str:
    for pattern in [r"/document/(?:view/)?([0-9A-Za-z_-]+)", r"문서\s*번호\s*[:：]?\s*([0-9A-Za-z_-]+)"]:
        match = re.search(pattern, url) or re.search(pattern, text)
        if match:
            return match.group(1)
    return url.rstrip("/").rsplit("/", 1)[-1]
