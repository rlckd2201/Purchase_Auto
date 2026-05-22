from __future__ import annotations

from pathlib import Path

from . import db
from .compuzone_order import run_compuzone_order
from .config import Settings, load_settings
from .corps import get_corp
from .groupware_approval import submit_groupware_approval
from .models import CreatePurchaseJobRequest, PurchaseJob, PurchaseStatus


def _settings(settings: Settings | None) -> Settings:
    return settings or load_settings()


def create_purchase_job(request: CreatePurchaseJobRequest, settings: Settings | None = None) -> PurchaseJob:
    cfg = _settings(settings)
    corp = get_corp(request.corp)
    return db.create_job(cfg.db_path, request, corp)


def list_purchase_jobs(settings: Settings | None = None) -> list[PurchaseJob]:
    cfg = _settings(settings)
    return db.list_jobs(cfg.db_path)


def get_purchase_job(job_id: str, settings: Settings | None = None) -> PurchaseJob:
    cfg = _settings(settings)
    job = db.get_job(cfg.db_path, job_id)
    if job is None:
        raise KeyError(job_id)
    return job


def run_compuzone_order_step(job_id: str, settings: Settings | None = None) -> PurchaseJob:
    cfg = _settings(settings)
    if cfg.dry_run:
        raise ValueError(
            "Purchase_Auto 테스트모드(dry_run=True)라 실제 컴퓨존 주문/견적 실행을 하지 않습니다. "
            "실행하려면 PURCHASE_AUTO_DRY_RUN=0 및 PURCHASE_AUTO_ENABLE_LIVE_COMPUZONE_ORDER=1 설정이 필요합니다."
        )
    job = get_purchase_job(job_id, cfg)
    try:
        db.append_log(cfg.db_path, job_id, "컴퓨존 장바구니/무통장 주문 생성을 시작합니다.")
        result = run_compuzone_order(job, cfg)
        db.update_job(
            cfg.db_path,
            job_id,
            status=PurchaseStatus.ORDER_SUBMITTED_PENDING_PAYMENT,
            order_no=result.order_no,
            amount=result.amount,
            item_summary=result.item_summary,
        )
        db.append_log(cfg.db_path, job_id, f"컴퓨존 무통장 주문이 생성되었습니다. 주문번호: {result.order_no}")
        updated = db.update_job(
            cfg.db_path,
            job_id,
            status=PurchaseStatus.QUOTE_SAVED,
            quote_pdf_path=result.quote_pdf_path,
        )
        db.append_log(cfg.db_path, job_id, f"컴퓨존 견적서 PDF를 저장했습니다: {result.quote_pdf_path}")
        return get_purchase_job(updated.job_id, cfg)
    except Exception as exc:
        db.set_failed(cfg.db_path, job_id, str(exc))
        raise


def submit_approval_step(job_id: str, settings: Settings | None = None) -> PurchaseJob:
    cfg = _settings(settings)
    if cfg.dry_run:
        raise ValueError(
            "Purchase_Auto 테스트모드(dry_run=True)라 실제 그룹웨어 품의 상신을 하지 않습니다. "
            "실행하려면 PURCHASE_AUTO_DRY_RUN=0 및 PURCHASE_AUTO_ENABLE_LIVE_GROUPWARE_SUBMIT=1 설정이 필요합니다."
        )
    job = get_purchase_job(job_id, cfg)
    try:
        if not job.order_no:
            raise ValueError("컴퓨존 주문번호가 없어 품의를 상신할 수 없습니다.")
        if not job.quote_pdf_path:
            raise ValueError("컴퓨존 견적서 PDF가 없어 품의를 상신할 수 없습니다.")
        if not Path(job.quote_pdf_path).exists():
            raise ValueError(f"컴퓨존 견적서 PDF 파일이 존재하지 않습니다: {job.quote_pdf_path}")
        db.append_log(cfg.db_path, job_id, "그룹웨어 품의 자동상신을 시작합니다.")
        result = submit_groupware_approval(job, cfg)
        db.update_job(
            cfg.db_path,
            job_id,
            status=PurchaseStatus.APPROVAL_SUBMITTED,
            approval_document_id=result.document_id,
            approval_document_url=result.document_url,
        )
        db.append_log(cfg.db_path, job_id, f"그룹웨어 품의가 상신되었습니다: {result.document_url}")
        updated = db.update_job(cfg.db_path, job_id, status=PurchaseStatus.WAITING_TAX_INVOICE)
        db.append_log(cfg.db_path, job_id, "세금계산서 수신 대기 상태로 전환했습니다.")
        return get_purchase_job(updated.job_id, cfg)
    except Exception as exc:
        db.set_failed(cfg.db_path, job_id, str(exc))
        raise


def mark_tax_invoice_received(job_id: str, settings: Settings | None = None) -> PurchaseJob:
    cfg = _settings(settings)
    if cfg.dry_run:
        raise ValueError("Purchase_Auto 테스트모드(dry_run=True)에서는 세금계산서 수신 완료 처리를 하지 않습니다.")
    job = get_purchase_job(job_id, cfg)
    try:
        if job.status not in {
            PurchaseStatus.APPROVAL_SUBMITTED,
            PurchaseStatus.WAITING_TAX_INVOICE,
            PurchaseStatus.TAX_INVOICE_RECEIVED,
        }:
            raise ValueError("세금계산서 수신 처리는 품의 상신 이후에만 가능합니다.")
        db.update_job(cfg.db_path, job_id, status=PurchaseStatus.TAX_INVOICE_RECEIVED)
        db.append_log(cfg.db_path, job_id, "컴퓨존 세금계산서 수신이 확인되었습니다.")
        updated = db.update_job(cfg.db_path, job_id, status=PurchaseStatus.COMPLETED)
        db.append_log(cfg.db_path, job_id, "구매 완료 상태로 전환했습니다. WMS 입고 연동은 후속 단계에서 실행합니다.")
        return get_purchase_job(updated.job_id, cfg)
    except Exception as exc:
        db.set_failed(cfg.db_path, job_id, str(exc))
        raise
