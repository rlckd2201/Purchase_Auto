from __future__ import annotations

from pathlib import Path

import pytest

from purchase_auto.config import Settings
from purchase_auto.models import CreatePurchaseJobRequest, PurchaseStatus
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
        compuzone_cart_url="https://www.compuzone.co.kr/mypage/cart.htm",
        compuzone_quote_url_template="https://www.compuzone.co.kr/order/order_quote_pdf.htm?OrderNo={order_no}",
        compuzone_depositor_name="",
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


def test_dry_run_lifecycle(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    job = create_purchase_job(_request(), settings)
    assert job.status == PurchaseStatus.CREATED

    job = run_compuzone_order_step(job.job_id, settings)
    assert job.status == PurchaseStatus.QUOTE_SAVED
    assert job.order_no
    assert job.quote_pdf_path
    assert Path(job.quote_pdf_path).exists()

    job = submit_approval_step(job.job_id, settings)
    assert job.status == PurchaseStatus.WAITING_TAX_INVOICE
    assert job.approval_document_id
    assert job.approval_document_url

    job = mark_tax_invoice_received(job.job_id, settings)
    assert job.status == PurchaseStatus.COMPLETED


def test_approval_requires_order_and_quote(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    job = create_purchase_job(_request(), settings)

    with pytest.raises(ValueError):
        submit_approval_step(job.job_id, settings)

    failed = get_purchase_job(job.job_id, settings)
    assert failed.status == PurchaseStatus.FAILED
