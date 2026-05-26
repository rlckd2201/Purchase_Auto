from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from datetime import datetime, timezone

import pytest

from purchase_auto.config import Settings
from purchase_auto.compuzone_order import (
    _cart_visible_product_count,
    _factory_business_number,
    _job_tax_business_selection,
    _raise_if_product_unavailable,
)
from purchase_auto.groupware_approval import (
    _approval_body_html,
    _approval_rule_for_job,
    _approval_title,
    _delegate_level_for_job,
    _recipient_rows_for_job,
)
from purchase_auto.models import CreatePurchaseJobRequest, PurchaseJob, PurchaseItem, PurchaseStatus
from purchase_auto.services import (
    create_purchase_job,
    get_purchase_job,
    mark_tax_invoice_received,
    run_compuzone_order_step,
    submit_approval_step,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=5008,
        db_path=tmp_path / "purchase_auto.sqlite3",
        artifact_dir=tmp_path / "artifacts",
        dry_run=True,
        headless=True,
        enable_live_compuzone_order=False,
        enable_live_groupware_submit=False,
        compuzone_profile_dir=tmp_path / "profiles" / "compuzone",
        groupware_profile_dir=tmp_path / "profiles" / "groupware",
        compuzone_cdp_url="",
        groupware_cdp_url="",
        allow_existing_browser_cdp=False,
        compuzone_login_id="",
        compuzone_login_password="",
        groupware_login_id="",
        groupware_login_password="",
        compuzone_cart_url="https://www.compuzone.co.kr/bsk/basket_main.htm",
        compuzone_quote_url_template="https://www.compuzone.co.kr/form/form_assemble.htm?wd=&tb=iorder&from_where=internet_manager&order_state_no={order_no}&settle=settle",
        compuzone_clear_cart_before_order=True,
        compuzone_depositor_name="",
        compuzone_delivery_name="평택 전산팀",
        compuzone_delivery_keywords=("평택 전산팀", "수월암4길 200", "010-2227-0009"),
        compuzone_business_number="125-81-05619",
        compuzone_business_contact_name="윤기옥",
        groupware_base_url="https://gw.dae-seung.co.kr",
        groupware_form_urls={
            "daeseung": "https://gw.dae-seung.co.kr/app/approval/document/new/223/5646",
            "daeseung_precision": "",
            "ilgang": "",
        },
    )


def _request() -> CreatePurchaseJobRequest:
    return CreatePurchaseJobRequest(
        corp="대승",
        title="컴퓨존 테스트 구매",
        requester="테스트",
        memo="드라이런",
        items=[{"url": "https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=123456", "quantity": 2}],
    )


def test_dry_run_blocks_live_steps(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    job = create_purchase_job(_request(), settings)
    assert job.status == PurchaseStatus.CREATED

    with pytest.raises(ValueError, match="dry_run=True"):
        run_compuzone_order_step(job.job_id, settings)

    with pytest.raises(ValueError, match="dry_run=True"):
        submit_approval_step(job.job_id, settings)

    with pytest.raises(ValueError, match="dry_run=True"):
        mark_tax_invoice_received(job.job_id, settings)


def test_approval_requires_order_and_quote(tmp_path: Path) -> None:
    settings = replace(_settings(tmp_path), dry_run=False)
    job = create_purchase_job(_request(), settings)

    with pytest.raises(ValueError):
        submit_approval_step(job.job_id, settings)

    failed = get_purchase_job(job.job_id, settings)
    assert failed.status == PurchaseStatus.FAILED


def test_consumable_approval_body_follows_html2_shape() -> None:
    now = datetime.now(timezone.utc)
    job = PurchaseJob(
        job_id="job",
        corp="\ub300\uc2b9\uc815\ubc00",
        corp_code="daeseung_precision",
        status=PurchaseStatus.QUOTE_SAVED,
        items=[PurchaseItem(url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1306581", quantity=3)],
        title="\uc804\uc0b0 \uc18c\ubaa8\ud488 \uad6c\ub9e4 \uac74(P2\uacf5\uc7a5)",
        requester="\uae40\uae30\ucc3d",
        memo="P2\uacf5\uc7a5",
        order_no="28140035",
        amount=13230,
        item_summary="[\uc778\ub124\ud2b8\uc6cc\ud06c] HDMI 2.0 \ub354\ubbf8 \ud50c\ub7ec\uadf8, \uace8\ub4dc\uba54\ud0c8, IN-HDPGD / ING031",
        created_at=now,
        updated_at=now,
    )

    body = _approval_body_html(job)

    assert "\uc6b4\ubc18\ub8cc" in body
    assert "\\3,000" in body
    assert "\uc5c6\uc74c" not in body
    assert "rgb(212, 244, 250)" not in body
    assert "table-layout: auto" in body
    assert "min-width: 748px" in body
    assert "min-width:" in body
    assert "width: 180px" not in body
    assert "word-break: keep-all" in body
    assert "HDMI 2.0 \ub354\ubbf8 \ud50c\ub7ec\uadf8" in body
    assert "IN-HDPGD / ING031" in body
    assert "HDMI 2.0 \uace8\ub4dc\uba54\ud0c8 IN-HDPGD / ING031" not in body
    assert "\uc8fc\ubb38\uc11c \ucc38\uc870" not in body
    assert "\uacac\uc801\uc11c" in body
    assert "28140035" in body


def test_consumable_approval_body_omits_no_shipping_row() -> None:
    now = datetime.now(timezone.utc)
    job = PurchaseJob(
        job_id="job",
        corp="\ub300\uc2b9\uc815\ubc00",
        corp_code="daeseung_precision",
        status=PurchaseStatus.QUOTE_SAVED,
        items=[PurchaseItem(url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1306581", quantity=3)],
        title="\uc804\uc0b0 \uc18c\ubaa8\ud488 \uad6c\ub9e4 \uac74(P2\uacf5\uc7a5)",
        order_no="28140035",
        amount=10230,
        memo="\ubc30\uc1a1\ube44 \uc5c6\uc74c",
        item_summary="[\uc778\ub124\ud2b8\uc6cc\ud06c] HDMI 2.0 \ub354\ubbf8 \ud50c\ub7ec\uadf8, \uace8\ub4dc\uba54\ud0c8, IN-HDPGD / ING031",
        created_at=now,
        updated_at=now,
    )

    body = _approval_body_html(job)

    assert "\uc6b4\ubc18\ub8cc" not in body
    assert "\uc5c6\uc74c" not in body
    assert "\uad6c\ub9e4\ub0b4\uc5ed" in body


def test_asset_approval_body_uses_notebook_template_for_mixed_assets() -> None:
    now = datetime.now(timezone.utc)
    summary = "\n".join(
        [
            "[마이크로소프트] Office Home & Business 2024 PKC [기업용/패키지/한글]\t2\t260000\t520000",
            "[율럽] 비즈니스 노트북 가방, 심플&모던 40.6cm [블랙] ▶ 16형 ◀\t2\t18420\t36840",
            "[레노버] 아이디어패드 Slim3 14AHP10 83K9000JKR (R5-8640HS/8GB/256GB/FreeDos/OLED) [8GB RAM 추가(총16GB) + Win11Pro 설치]\t2\t1227520\t2455040",
        ]
    )
    job = PurchaseJob(
        job_id="job",
        corp="\ub300\uc2b9\uc815\ubc00",
        corp_code="daeseung_precision",
        status=PurchaseStatus.QUOTE_SAVED,
        items=[
            PurchaseItem(url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1177871", quantity=2),
            PurchaseItem(url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1237779", quantity=2),
            PurchaseItem(url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1236050&opt_chk=Y", quantity=2),
        ],
        title="\uc804\uc0b0 \uc18c\ubaa8\ud488 \uad6c\ub9e4 \uac74(P3\uacf5\uc7a5)",
        order_no="28145151",
        amount=3011880,
        memo="P3\uacf5\uc7a5\n\ubd80\uc11c=\uc784\uc6d0\n\ub300\uc0c1=\uc0ac\uc7a5, \ubd80\ud68c\uc7a5\n\uc6a9\ub3c4=\uc5c5\ubb34\uc6a9",
        item_summary=summary,
        created_at=now,
        updated_at=now,
    )

    body = _approval_body_html(job)

    assert "\uc18c\ubaa8\ud488" not in body
    assert "\uc9d1\uae30\ube44\ud488" in body
    assert "\uad6c\ub9e4\uac00\uaca9" in body
    assert "\uc9c0\uae09\ub300\uc0c1" in body
    assert "\uad6c\ub9e4\ub0b4\uc5ed" not in body
    assert "\uc6b4\ubc18\ub8cc" not in body
    assert _recipient_rows_for_job(job) == [
        [1, "\uc784\uc6d0", "\uc0ac\uc7a5", "\uc5c5\ubb34\uc6a9", "\ub178\ud2b8\ubd81"],
        [2, "\uc784\uc6d0", "\uc0ac\uc7a5", "\uc5c5\ubb34\uc6a9", "OFFICE"],
        [3, "\uc784\uc6d0", "\ubd80\ud68c\uc7a5", "\uc5c5\ubb34\uc6a9", "\ub178\ud2b8\ubd81"],
        [4, "\uc784\uc6d0", "\ubd80\ud68c\uc7a5", "\uc5c5\ubb34\uc6a9", "OFFICE"],
    ]
    assert "\uc0ac\uc7a5, \ubd80\ud68c\uc7a5" not in body
    assert "\uc0ac\uc7a5" in body
    assert "\ubd80\ud68c\uc7a5" in body
    assert "OFFICE" in body
    assert "\ub178\ud2b8\ubd81" in body
    assert "14AHP10" in body
    assert "H&amp;B 2024" in body
    assert "노트북 가방" in body
    assert body.find("14AHP10") < body.find("H&amp;B 2024") < body.find("노트북 가방")
    assert body.count("2 EA") == 3
    assert "\\2,455,040" in body
    assert "\\520,000" in body
    assert "\\36,840" in body
    assert "\\3,011,880" in body
    assert "\\3,011,880 원" not in body
    assert "\\3,011,880원" not in body
    assert "6. 금액 : \\3,011,880(V.A.T 포함)" in body
    assert body.count("table-layout: auto") >= 3
    assert "\uc5c6\uc74c" not in body
    assert _approval_title(job) == "\uc804\uc0b0 \uc9d1\uae30\ube44\ud488 \uad6c\ub9e4 \uac74(P3\uacf5\uc7a5)"
    assert _delegate_level_for_job(job) == "\ubcf8\ubd80\uc7a5"
    assert _approval_rule_for_job(job).startswith("21-4-3")


def test_compuzone_tax_business_uses_factory_hint_over_stale_memo_number(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    job = PurchaseJob(
        job_id="job",
        corp="대승정밀",
        corp_code="daeseung_precision",
        status=PurchaseStatus.CREATED,
        items=[PurchaseItem(url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=960306", quantity=1)],
        title="전산 소모품 구매 건(P3공장)",
        requester="TEST",
        memo="사업자번호=403-85-15640\nP3공장",
        created_at=now,
        updated_at=now,
    )

    business_number, contact_name = _job_tax_business_selection(job, _settings(tmp_path))

    assert _factory_business_number(job) == "844-85-00770"
    assert business_number == "844-85-00770"
    assert contact_name == "윤기옥"


def test_cart_visible_product_count_reads_compuzone_delivery_count() -> None:
    body = "장바구니\n컴퓨존 배송상품 2\n상품명/옵션\n주문하기"

    assert _cart_visible_product_count(body) == 2


class _FakeProductPage:
    def __init__(self, status: dict[str, object]) -> None:
        self._status = status

    def evaluate(self, _script: str) -> dict[str, object]:
        return self._status


def test_compuzone_unavailable_product_reports_sold_out() -> None:
    page = _FakeProductPage(
        {
            "hasOrderControl": False,
            "hasSoldOutControl": True,
            "bodyText": "총 합계 금액 2,340원 품절",
        }
    )
    item = PurchaseItem(
        url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=960306",
        quantity=1,
    )

    with pytest.raises(RuntimeError, match="상품번호=960306"):
        _raise_if_product_unavailable(page, item)
