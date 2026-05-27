from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from . import db
from .compuzone_order import QuoteDownloadError, SoldOutProductError, run_compuzone_order
from .config import Settings, load_settings
from .corps import get_corp
from .groupware_approval import submit_groupware_approval
from .models import CreatePurchaseJobRequest, PurchaseJob, PurchaseStatus


class PurchaseStepBusyError(RuntimeError):
    pass


@dataclass(frozen=True)
class _RunningStep:
    token: str
    label: str
    key: str
    started_at: float


_RUNNING_STEPS_LOCK = threading.Lock()
_RUNNING_STEPS: dict[str, _RunningStep] = {}


@contextmanager
def _step_guard(label: str, key: str):
    token = uuid4().hex
    with _RUNNING_STEPS_LOCK:
        if key in _RUNNING_STEPS:
            raise PurchaseStepBusyError(f"{label} 자동화가 이미 실행 중입니다. 현재 실행이 끝난 뒤 다시 시도하세요.")
        _RUNNING_STEPS[key] = _RunningStep(token=token, label=label, key=key, started_at=time.monotonic())
    try:
        yield
    finally:
        with _RUNNING_STEPS_LOCK:
            current = _RUNNING_STEPS.get(key)
            if current and current.token == token:
                _RUNNING_STEPS.pop(key, None)


def _powershell_single_quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _kill_browser_processes_for_profile(profile_dir: Path) -> list[str]:
    if os.name != "nt":
        return []

    profile = str(profile_dir.resolve())
    command = (
        "$needle = "
        + _powershell_single_quoted(profile.lower())
        + "; Get-CimInstance Win32_Process | "
        + "Where-Object { $_.CommandLine -and $_.ProcessName -match 'chrome|chromium|msedge' "
        + "-and $_.CommandLine.ToLowerInvariant().Contains($needle) } | "
        + "Select-Object -ExpandProperty ProcessId"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    killed: list[str] = []
    for raw_pid in result.stdout.splitlines():
        raw_pid = raw_pid.strip()
        if not raw_pid.isdigit():
            continue
        pid = int(raw_pid)
        if pid == os.getpid():
            continue
        try:
            kill_result = subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if kill_result.returncode == 0:
            killed.append(str(pid))
    return killed


def _remove_chromium_profile_locks(profile_dir: Path) -> list[str]:
    removed: list[str] = []
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        target = profile_dir / name
        try:
            if target.is_dir():
                shutil.rmtree(target)
                removed.append(name)
            elif target.exists():
                target.unlink()
                removed.append(name)
        except OSError:
            continue
    return removed


def _reset_browser_step(label: str, key: str, profile_dir: Path, wait_seconds: float = 2.0) -> list[str]:
    details: list[str] = []
    killed = _kill_browser_processes_for_profile(profile_dir)
    if killed:
        details.append(f"killed_browser_pids={','.join(killed)}")
    removed = _remove_chromium_profile_locks(profile_dir)
    if removed:
        details.append(f"removed_profile_locks={','.join(removed)}")

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        with _RUNNING_STEPS_LOCK:
            if key not in _RUNNING_STEPS:
                return details
        time.sleep(0.25)

    with _RUNNING_STEPS_LOCK:
        running = _RUNNING_STEPS.pop(key, None)
    if running:
        elapsed = max(0.0, time.monotonic() - running.started_at)
        details.append(f"released_stuck_step={label}:{elapsed:.1f}s")
    return details


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


def run_compuzone_order_step(job_id: str, settings: Settings | None = None, force_restart: bool = False) -> PurchaseJob:
    cfg = _settings(settings)
    if cfg.dry_run:
        raise ValueError(
            "Purchase_Auto 테스트모드(dry_run=True)라 실제 컴퓨존 주문/견적 실행을 하지 않습니다. "
            "실행하려면 PURCHASE_AUTO_DRY_RUN=0 및 PURCHASE_AUTO_ENABLE_LIVE_COMPUZONE_ORDER=1 설정이 필요합니다."
        )
    job = get_purchase_job(job_id, cfg)
    guard_key = f"compuzone:{cfg.compuzone_profile_dir.resolve()}"
    if force_restart:
        reset_details = _reset_browser_step("compuzone", guard_key, cfg.compuzone_profile_dir)
        if reset_details:
            db.append_log(cfg.db_path, job_id, "이전 컴퓨존 자동화 세션을 정리했습니다: " + "; ".join(reset_details))
    try:
        with _step_guard("컴퓨존 주문/견적", guard_key):
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
    except PurchaseStepBusyError:
        raise
    except QuoteDownloadError as exc:
        db.update_job(
            cfg.db_path,
            job_id,
            status=PurchaseStatus.ORDER_SUBMITTED_PENDING_PAYMENT,
            order_no=exc.order_no,
            amount=exc.amount,
            item_summary=exc.item_summary,
            error_message=str(exc),
        )
        db.append_log(
            cfg.db_path,
            job_id,
            f"컴퓨존 주문은 생성되었지만 견적서 PDF 저장에 실패했습니다. 주문번호: {exc.order_no}",
            level="error",
        )
        raise
    except SoldOutProductError as exc:
        db.set_failed(cfg.db_path, job_id, str(exc))
        raise
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
        guard_key = f"groupware:{cfg.groupware_profile_dir.resolve()}"
        with _step_guard("그룹웨어 품의", guard_key):
            db.append_log(cfg.db_path, job_id, "그룹웨어 품의 자동상신을 시작합니다.")
            result = submit_groupware_approval(job, cfg)
        db.update_job(
            cfg.db_path,
            job_id,
            status=PurchaseStatus.APPROVAL_SUBMITTED,
            approval_document_id=result.document_id,
            approval_document_url=result.document_url,
            error_message=None,
        )
        db.append_log(cfg.db_path, job_id, f"그룹웨어 품의가 상신되었습니다: {result.document_url}")
        updated = db.update_job(cfg.db_path, job_id, status=PurchaseStatus.WAITING_TAX_INVOICE)
        db.append_log(cfg.db_path, job_id, "세금계산서 수신 대기 상태로 전환했습니다.")
        return get_purchase_job(updated.job_id, cfg)
    except PurchaseStepBusyError:
        raise
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
