from __future__ import annotations

from dataclasses import replace
import inspect
from pathlib import Path
from datetime import datetime, timezone

import pytest

from purchase_auto import compuzone_order, services
from purchase_auto.config import Settings
from purchase_auto.compuzone_order import (
    CompuzoneOrderResult,
    CompuzoneProductLine,
    SoldOutProductError,
    _cart_visible_product_count,
    _click_add_to_cart,
    _dialog_excerpt,
    _factory_business_number,
    _item_summary,
    _job_tax_business_selection,
    _merge_product_lines,
    _raise_if_dialog_blocked_order,
    _raise_if_product_unavailable,
)
from purchase_auto.groupware_approval import (
    _approval_body_html,
    _approval_rule_for_job,
    _approval_title,
    _delegate_level_for_job,
    _fallback_groupware_form_url,
    _groupware_text_pattern,
    _groupware_form_env_name,
    _item_category,
    _model_from_item,
    _normalize_groupware_label_text,
    _recipient_rows_for_job,
)
from purchase_auto.models import CreatePurchaseJobRequest, PurchaseJob, PurchaseItem, PurchaseStatus, RunCompuzoneOrderRequest
from purchase_auto.services import (
    PurchaseStepBusyError,
    _step_guard,
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


def test_groupware_missing_ilgang_url_falls_back_to_approval_home(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert _fallback_groupware_form_url(settings) == "https://gw.dae-seung.co.kr/app/approval"


def test_groupware_form_env_name_handles_known_corps() -> None:
    assert _groupware_form_env_name("ilgang") == "PURCHASE_AUTO_GROUPWARE_FORM_URL_ILGANG"
    assert _groupware_form_env_name("daeseung_precision") == "PURCHASE_AUTO_GROUPWARE_FORM_URL_DAESEUNG_PRECISION"


def test_groupware_form_label_matching_tolerates_spacing_and_dash_variants() -> None:
    expected = "일강 - (경영)기안용지"
    pattern = _groupware_text_pattern(expected)

    assert _normalize_groupware_label_text(expected) == _normalize_groupware_label_text("일강-(경영) 기안용지")
    assert pattern.search("일강-(경영)기안용지")
    assert pattern.search("일강 – (경영) 기안용지")


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


def test_compuzone_item_summary_keeps_each_product_line() -> None:
    now = datetime.now(timezone.utc)
    job = PurchaseJob(
        job_id="job",
        corp="\ub300\uc2b9\uc815\ubc00",
        corp_code="daeseung_precision",
        status=PurchaseStatus.CREATED,
        items=[
            PurchaseItem(url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=960306", quantity=1),
            PurchaseItem(url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1087083", quantity=1),
        ],
        created_at=now,
        updated_at=now,
    )

    summary = _item_summary(
        job,
        [
            CompuzoneProductLine("[케이엘시스템] 블루투스동글 KL-BTD50", 1, 2280, 2280, "960306"),
            CompuzoneProductLine("[이지넷유비쿼터스] 블루투스 동글 NEXT-304BT", 1, 4210, 4210, "1087083"),
        ],
    )

    assert "1\ubc88 \uc0c1\ud488" not in summary
    assert "KL-BTD50\t1\t2280\t2280" in summary
    assert "NEXT-304BT\t1\t4210\t4210" in summary


def test_order_page_product_code_rows_do_not_override_real_product_lines() -> None:
    now = datetime.now(timezone.utc)
    job = PurchaseJob(
        job_id="job",
        corp="\ub300\uc2b9\uc815\ubc00",
        corp_code="daeseung_precision",
        status=PurchaseStatus.CREATED,
        items=[
            PurchaseItem(url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1303277", quantity=1),
            PurchaseItem(url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1195294", quantity=2),
            PurchaseItem(url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1301049", quantity=1),
        ],
        created_at=now,
        updated_at=now,
    )
    detail_lines = [
        CompuzoneProductLine("[EFM] ipTIME H8008R-IGMP (스위칭허브/8포트/1000Mbps/IGMP)", 1, 24040, 24040, "1303277"),
        CompuzoneProductLine("[크로스오버] 27FD100SB IPS FHD 100 블랙 [무결점]", 2, 112100, 224200, "1195294"),
        CompuzoneProductLine("[HP] 프로데스크 2 G1a C27L5AT R5-8500G (16GB/512GB/400W/FD)", 1, 1324020, 1324020, "1301049"),
    ]
    order_page_lines = [
        CompuzoneProductLine("제품코드 : 1303277", 1303277, 7000, 1438000, "1303277"),
        CompuzoneProductLine("제품코드 : 1195294", 1195294, 130, 1438000, "1195294"),
        CompuzoneProductLine("제품코드 : 1301049", 1195294, 130, 1438000, "1301049"),
    ]

    summary = _item_summary(job, _merge_product_lines(detail_lines, order_page_lines))

    assert "\uc81c\ud488\ucf54\ub4dc" not in summary
    assert "1303277\t1303277" not in summary
    assert "H8008R-IGMP" in summary
    assert "27FD100SB" in summary
    assert "C27L5AT" in summary
    assert "\t1\t24040\t24040" in summary
    assert "\t2\t112100\t224200" in summary
    assert "\t1\t1324020\t1324020" in summary


def test_consumable_approval_body_expands_product_line_summary() -> None:
    now = datetime.now(timezone.utc)
    summary = "\n".join(
        [
            "[케이엘시스템] 블루투스동글 KL-BTD50\t1\t2280\t2280",
            "[이지넷유비쿼터스] 블루투스 동글 NEXT-304BT\t1\t4210\t4210",
        ]
    )
    job = PurchaseJob(
        job_id="job",
        corp="\ub300\uc2b9\uc815\ubc00",
        corp_code="daeseung_precision",
        status=PurchaseStatus.QUOTE_SAVED,
        items=[
            PurchaseItem(url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=960306", quantity=1),
            PurchaseItem(url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1087083", quantity=1),
        ],
        title="\uc804\uc0b0 \uc18c\ubaa8\ud488 \uad6c\ub9e4 \uac74(P3\uacf5\uc7a5)",
        order_no="28174999",
        amount=9490,
        memo="P3\uacf5\uc7a5",
        item_summary=summary,
        created_at=now,
        updated_at=now,
    )

    body = _approval_body_html(job)

    assert "1\ubc88 \uc0c1\ud488" not in body
    assert "KL-BTD50" in body
    assert "NEXT-304BT" in body
    assert body.find("NEXT-304BT") < body.find("KL-BTD50")
    assert "\\4,210" in body
    assert "\\2,280" in body
    assert "\\3,000" in body
    assert "\\12,665" not in body


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


def test_asset_product_rows_split_item_category_and_model_for_office_equipment() -> None:
    assert _item_category("[EPSON] L14150 완성형 정품무한잉크 복합기 (잉크포함) : 컴퓨존") == "복합기"
    assert _model_from_item("[EPSON] L14150 완성형 정품무한잉크 복합기 (잉크포함) : 컴퓨존") == "L14150"
    assert _item_category("[삼성전자] SL-M2680N 흑백레이저복합기 (토너포함) : 컴퓨존") == "복합기"
    assert _model_from_item("[삼성전자] SL-M2680N 흑백레이저복합기 (토너포함) : 컴퓨존") == "SL-M2680N"
    assert _item_category("[FIFINE] K050 : 컴퓨존") == "마이크"
    assert _model_from_item("[FIFINE] K050 : 컴퓨존") == "K050"
    assert _item_category("[브리츠] 브리즈 고감도 구즈넥 콘덴서 마이크 BE-GM3 : 컴퓨존") == "마이크"
    assert _model_from_item("[브리츠] 브리즈 고감도 구즈넥 콘덴서 마이크 BE-GM3 : 컴퓨존") == "BE-GM3"


def test_asset_recipients_can_be_collected_per_product_line() -> None:
    now = datetime.now(timezone.utc)
    summary = "\n".join(
        [
            "[EPSON] L14150 완성형 정품무한잉크 복합기 (잉크포함) : 컴퓨존\t1\t561030\t561030",
            "[삼성전자] SL-M2680N 흑백레이저복합기 (토너포함) : 컴퓨존\t1\t227700\t227700",
            "[FIFINE] K050 : 컴퓨존\t1\t35100\t35100",
            "[브리츠] 브리즈 고감도 구즈넥 콘덴서 마이크 BE-GM3 : 컴퓨존\t1\t20690\t20690",
        ]
    )
    job = PurchaseJob(
        job_id="job",
        corp="일강",
        corp_code="ilgang",
        status=PurchaseStatus.QUOTE_SAVED,
        items=[
            PurchaseItem(
                url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1",
                quantity=1,
                asset_department="전산팀",
                asset_user="김기창",
                asset_purpose="업무용",
            ),
            PurchaseItem(
                url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=2",
                quantity=1,
                asset_department="전산팀",
                asset_user="안효일",
                asset_purpose="업무용",
            ),
            PurchaseItem(
                url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=3",
                quantity=1,
                asset_department="총무팀",
                asset_user="노양래",
                asset_purpose="회의용",
            ),
            PurchaseItem(
                url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=4",
                quantity=1,
                asset_department="총무팀",
                asset_user="금문정",
                asset_purpose="회의용",
            ),
        ],
        title="전산 집기비품 구매 건(D1공장)",
        order_no="28185435",
        amount=830860,
        memo="D1공장",
        item_summary=summary,
        created_at=now,
        updated_at=now,
    )

    assert _recipient_rows_for_job(job) == [
        [1, "전산팀", "김기창", "업무용", "복합기 / L14150"],
        [2, "전산팀", "안효일", "업무용", "복합기 / SL-M2680N"],
        [3, "총무팀", "노양래", "회의용", "마이크 / K050"],
        [4, "총무팀", "금문정", "회의용", "마이크 / BE-GM3"],
    ]
    body = _approval_body_html(job)
    assert "L14150 완성형 정품무한잉크 복합기" not in body
    assert "SL-M2680N 흑백레이저복합기" not in body
    assert "K050 : 컴퓨존" not in body
    assert "BE-GM3 : 컴퓨존" not in body
    assert "복합기" in body
    assert "마이크" in body
    assert "L14150" in body
    assert "SL-M2680N" in body
    assert "K050" in body
    assert "BE-GM3" in body


def test_asset_approval_requires_recipient_input_for_asset_lines() -> None:
    now = datetime.now(timezone.utc)
    job = PurchaseJob(
        job_id="job",
        corp="일강",
        corp_code="ilgang",
        status=PurchaseStatus.QUOTE_SAVED,
        items=[PurchaseItem(url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1", quantity=1)],
        title="전산 집기비품 구매 건(D1공장)",
        order_no="28185435",
        amount=561030,
        memo="D1공장",
        item_summary="[EPSON] L14150 완성형 정품무한잉크 복합기 (잉크포함)\t1\t561030\t561030",
        created_at=now,
        updated_at=now,
    )

    with pytest.raises(RuntimeError, match="지급대상 정보가 부족"):
        _approval_body_html(job)


def test_groupware_body_rejects_product_code_rows_and_keeps_p4_factory() -> None:
    now = datetime.now(timezone.utc)
    summary = "\n".join(
        [
            "[EFM] ipTIME H8008R-IGMP (스위칭허브/8포트/1000Mbps/IGMP)\t1\t24040\t24040",
            "[크로스오버] 27FD100SB IPS FHD 100 블랙 [무결점]\t2\t112100\t224200",
            "[HP] 프로데스크 2 G1a C27L5AT R5-8500G (16GB/512GB/400W/FD) [Win11Pro FPP 설치]\t1\t1324020\t1324020",
            "제품코드 : 1303277\t1303277\t7000\t1438000",
            "제품코드 : 1195294\t1195294\t130\t1438000",
        ]
    )
    job = PurchaseJob(
        job_id="job",
        corp="\ub300\uc2b9\uc815\ubc00",
        corp_code="daeseung_precision",
        status=PurchaseStatus.QUOTE_SAVED,
        items=[
            PurchaseItem(
                url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1303277",
                quantity=1,
                asset_department="전산팀",
                asset_user="김기창",
                asset_purpose="업무용",
            ),
            PurchaseItem(
                url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1195294",
                quantity=2,
                asset_department="전산팀",
                asset_user="김기창",
                asset_purpose="업무용",
            ),
            PurchaseItem(
                url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1301049",
                quantity=1,
                asset_department="전산팀",
                asset_user="김기창",
                asset_purpose="업무용",
            ),
        ],
        title="\uc804\uc0b0 \uc9d1\uae30\ube44\ud488 \uad6c\ub9e4 \uac74(P4\uacf5\uc7a5)",
        order_no="28179522",
        amount=1572260,
        memo="\uc0ac\uc5c5\uc790\ubc88\ud638=118-85-07029\n\ubc30\uc1a1\uc9c0=\uae40\uc81c\uc804\uc0b0\ud300",
        item_summary=summary,
        created_at=now,
        updated_at=now,
    )

    body = _approval_body_html(job)

    assert "P4\uacf5\uc7a5" in body
    assert "P3\uacf5\uc7a5" not in body
    assert "\uc81c\ud488\ucf54\ub4dc" not in body
    assert "1303277 EA" not in body
    assert "1195294 EA" not in body
    assert "H8008R-IGMP" in body
    assert "27FD100SB" in body
    assert "C27L5AT" in body
    assert "2 EA" in body
    assert "\\1,572,260" in body


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


def test_compuzone_tax_business_uses_p4_factory_over_stale_p3_memo_number(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    job = PurchaseJob(
        job_id="job",
        corp="대승정밀",
        corp_code="daeseung_precision",
        status=PurchaseStatus.CREATED,
        items=[PurchaseItem(url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1294126", quantity=1)],
        title="전산 집기비품 구매 건(P4공장)",
        requester="TEST",
        memo="사업자번호=844-85-00770\nP4공장",
        created_at=now,
        updated_at=now,
    )

    business_number, contact_name = _job_tax_business_selection(job, _settings(tmp_path))

    assert _factory_business_number(job) == "118-85-07029"
    assert business_number == "118-85-07029"
    assert contact_name == "윤기옥"


def test_cart_visible_product_count_reads_compuzone_delivery_count() -> None:
    body = "장바구니\n컴퓨존 배송상품 2\n상품명/옵션\n주문하기"

    assert _cart_visible_product_count(body) == 2


def test_cart_visible_product_count_sums_direct_delivery_sections() -> None:
    body = (
        "장바구니\n"
        "컴퓨존 배송상품 3\n"
        "상품명/옵션 상품가격 수량 주문금액\n"
        "업체 직배송상품 1\n"
        "상품명/옵션 상품가격 수량 주문금액"
    )

    assert _cart_visible_product_count(body) == 4


def test_dialog_excerpt_keeps_compuzone_alert_text() -> None:
    assert _dialog_excerpt(["첫 알림", "두 번째 알림"], 0) == "첫 알림 / 두 번째 알림"


def test_dialog_blocked_order_raises_sold_out_product() -> None:
    item = PurchaseItem(
        url="https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=1087083",
        quantity=1,
    )

    with pytest.raises(SoldOutProductError) as exc:
        _raise_if_dialog_blocked_order(["해당 상품은 품절되어 구매할 수 없습니다."], 0, item)

    assert exc.value.product_no == "1087083"


def test_cart_confirmation_matches_product_number_in_cart_dom() -> None:
    class FakeBodyLocator:
        def inner_text(self, timeout: int = 3000) -> str:
            return "cart delivery products 1 name option quantity order"

    class FakePage:
        url = "https://www.compuzone.co.kr/bsk/basket_main.htm"

        def locator(self, selector: str):
            assert selector == "body"
            return FakeBodyLocator()

        def evaluate(self, script: str):
            return [
                "https://www.compuzone.co.kr/product/product_detail.htm?ProductNo=758974",
                "basket_insert_detail2",
            ]

    assert compuzone_order._cart_page_contains_products(FakePage(), 1, [["758974", "long product title"]])


def test_compuzone_cart_add_does_not_use_direct_buy_button() -> None:
    source = inspect.getsource(_click_add_to_cart)
    lines = {line.strip() for line in source.splitlines()}

    assert '".total_price .btn_area a.cart",' not in lines
    assert '".btn_area a.cart",' not in lines
    assert "a.buy[onclick*='basket_insert_direct']" not in source
    assert "button[onclick*='basket_insert_direct']" not in source
    assert "a.cart[onclick*='basket_insert_detail']" in source
    assert "a.cart[onclick*='basket_insert_direct']" in source
    assert "basket_insert_detail" in source
    assert "hasDetailBasketAction" in source
    assert "isBasketPageLink" in source
    assert "hasDirectBasketAction && !hasCartText && !hasCartClass" in source


def test_compuzone_cart_add_supports_recommend_pc_cart_action() -> None:
    source = inspect.getsource(_click_add_to_cart)

    assert "new_recommendpc_insert" in source
    assert "new_compuzonepremiumpc_insert" in source
    assert "hasRecommendPcCartAction" in source
    assert "_insert(?!_order)" in source
    assert "a.cart[href*='new_recommendpc_insert']" in source
    assert "a[onclick*='new_recommendpc_insert']:not([onclick*='_order'])" in source


def test_compuzone_cart_iframe_wait_does_not_stall_on_basket_navigation() -> None:
    source = inspect.getsource(compuzone_order)

    assert "basket_main\\.htm" in source
    assert "timeout=2500" in source
    assert "timeout=8000" not in inspect.getsource(compuzone_order._wait_for_cart_insert_iframe)


def test_compuzone_cart_add_waits_for_hidden_iframe_result() -> None:
    source = inspect.getsource(compuzone_order)

    assert "common_iframe" in source
    assert "_wait_for_cart_insert_iframe" in source
    assert "_cart_click_diagnostic_summary" in source
    assert "browser_diag=" not in inspect.getsource(compuzone_order._confirm_cart_add)


def test_compuzone_cart_click_summary_omits_raw_browser_noise() -> None:
    summary = compuzone_order._cart_click_diagnostic_summary(
        {
            "method": "selector",
            "selector": ".total_price .btn_area a.cart[onclick*='basket_insert_detail']",
            "element": {
                "className": "cart",
                "onclick": "buy_direct('758974','일반',1,0,'basket_insert_detail2','0',event);",
            },
            "iframe": {
                "src": "../order/order_function.php?actype=basket_insert_detail2&cProductNo=758974",
                "readyState": "complete",
            },
        }
    )

    assert "장바구니버튼=감지" in summary
    assert "장바구니요청=전송" in summary
    assert "basket_insert_detail2" not in summary
    assert "758974" not in summary


def test_step_guard_rejects_concurrent_same_key() -> None:
    key = "compuzone:test-profile"

    with _step_guard("컴퓨존 주문/견적", key):
        with pytest.raises(PurchaseStepBusyError):
            with _step_guard("컴퓨존 주문/견적", key):
                pass

    with _step_guard("컴퓨존 주문/견적", key):
        pass


def test_run_compuzone_request_defaults_to_force_restart() -> None:
    assert RunCompuzoneOrderRequest().force_restart is True


def test_compuzone_force_restart_releases_stuck_guard_and_runs(monkeypatch, tmp_path: Path) -> None:
    settings = replace(_settings(tmp_path), dry_run=False, enable_live_compuzone_order=True)
    job = create_purchase_job(_request(), settings)
    guard_key = f"compuzone:{settings.compuzone_profile_dir.resolve()}"

    with services._RUNNING_STEPS_LOCK:
        services._RUNNING_STEPS[guard_key] = services._RunningStep(
            token="stuck",
            label="compuzone",
            key=guard_key,
            started_at=0.0,
        )

    monkeypatch.setattr(services, "_kill_browser_processes_for_profile", lambda profile_dir: ["1234"])
    monkeypatch.setattr(services, "_remove_chromium_profile_locks", lambda profile_dir: ["SingletonLock"])
    monkeypatch.setattr(
        services,
        "run_compuzone_order",
        lambda purchase_job, cfg, log=None: CompuzoneOrderResult(
            order_no="28170000",
            amount=12000,
            item_summary="테스트 상품\t1\t12000\t12000",
            quote_pdf_path=str(tmp_path / "quote.pdf"),
            raw_status="order_submitted_pending_payment",
        ),
    )

    updated = run_compuzone_order_step(job.job_id, settings, force_restart=True)

    assert updated.order_no == "28170000"
    assert updated.status == PurchaseStatus.QUOTE_SAVED
    assert any("killed_browser_pids=1234" in entry["message"] for entry in updated.logs)
    with services._RUNNING_STEPS_LOCK:
        assert guard_key not in services._RUNNING_STEPS


def test_compuzone_launch_error_mentions_profile_lock() -> None:
    source = inspect.getsource(compuzone_order)

    assert "launch_persistent_context" in source
    assert "프로필이 이미 사용 중" in source


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
