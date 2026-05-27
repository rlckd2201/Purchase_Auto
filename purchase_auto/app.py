from __future__ import annotations

from dataclasses import replace

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .compuzone_order import SoldOutProductError
from .config import load_settings
from .models import CreatePurchaseJobRequest, PurchaseJob, RunCompuzoneOrderRequest, RunStepResponse, SubmitApprovalRequest
from .services import (
    PurchaseStepBusyError,
    create_purchase_job,
    get_purchase_job,
    list_purchase_jobs,
    mark_tax_invoice_received,
    run_compuzone_order_step,
    submit_approval_step,
)


app = FastAPI(title="Purchase Auto", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_private_network_access_header(request: Request, call_next):
    if (
        request.method == "OPTIONS"
        and request.headers.get("access-control-request-private-network")
    ):
        response = Response("OK", status_code=200)
        origin = request.headers.get("origin") or "*"
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT"
        response.headers["Access-Control-Allow-Headers"] = request.headers.get(
            "access-control-request-headers",
            "*",
        )
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

    response = await call_next(request)
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="구매 작업을 찾지 못했습니다.")
    if isinstance(exc, PurchaseStepBusyError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, SoldOutProductError):
        return HTTPException(status_code=409, detail=exc.as_detail())
    return HTTPException(status_code=500, detail=str(exc))


def _profile_suffix(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
    return safe[:80] or "default"


def _settings_for_compuzone(request: RunCompuzoneOrderRequest | None):
    settings = load_settings()
    login_id = (request.compuzone_login_id if request else None) or ""
    login_id = login_id.strip()
    if not login_id:
        return settings
    return replace(
        settings,
        compuzone_login_id=login_id,
        compuzone_profile_dir=settings.compuzone_profile_dir / _profile_suffix(login_id),
    )


def _settings_for_groupware(request: SubmitApprovalRequest | None):
    settings = load_settings()
    login_id = (request.groupware_login_id if request else None) or ""
    password = (request.groupware_login_password if request else None) or ""
    login_id = login_id.strip()
    password = password.strip()
    if not login_id or not password:
        raise ValueError("그룹웨어 계정과 비밀번호를 입력하세요.")
    return replace(
        settings,
        groupware_login_id=login_id,
        groupware_login_password=password,
        groupware_profile_dir=settings.groupware_profile_dir / _profile_suffix(login_id),
    )


@app.get("/health")
def health() -> dict[str, str | bool]:
    settings = load_settings()
    return {"ok": True, "dry_run": settings.dry_run}


@app.post("/api/purchase-jobs", response_model=PurchaseJob)
def create_job(request: CreatePurchaseJobRequest) -> PurchaseJob:
    try:
        return create_purchase_job(request)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/purchase-jobs", response_model=list[PurchaseJob])
def list_jobs() -> list[PurchaseJob]:
    return list_purchase_jobs()


@app.get("/api/purchase-jobs/{job_id}", response_model=PurchaseJob)
def get_job(job_id: str) -> PurchaseJob:
    try:
        return get_purchase_job(job_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/purchase-jobs/{job_id}/run-compuzone-order", response_model=RunStepResponse)
def run_order(job_id: str, request: RunCompuzoneOrderRequest | None = None) -> RunStepResponse:
    try:
        force_restart = request.force_restart if request else True
        job = run_compuzone_order_step(job_id, _settings_for_compuzone(request), force_restart=force_restart)
        return RunStepResponse(job=job, message="컴퓨존 무통장 주문 및 견적서 저장 단계가 완료되었습니다.")
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/purchase-jobs/{job_id}/submit-approval", response_model=RunStepResponse)
def submit_approval(job_id: str, request: SubmitApprovalRequest | None = None) -> RunStepResponse:
    try:
        job = submit_approval_step(job_id, _settings_for_groupware(request))
        return RunStepResponse(job=job, message="그룹웨어 품의 자동상신 단계가 완료되었습니다.")
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/purchase-jobs/{job_id}/mark-tax-invoice-received", response_model=RunStepResponse)
def mark_tax_invoice(job_id: str) -> RunStepResponse:
    try:
        job = mark_tax_invoice_received(job_id)
        return RunStepResponse(job=job, message="세금계산서 수신 확인 및 구매 완료 처리가 끝났습니다.")
    except Exception as exc:
        raise _http_error(exc) from exc
