from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .config import load_settings
from .models import CreatePurchaseJobRequest, PurchaseJob, RunStepResponse
from .services import (
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
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


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
def run_order(job_id: str) -> RunStepResponse:
    try:
        job = run_compuzone_order_step(job_id)
        return RunStepResponse(job=job, message="컴퓨존 무통장 주문 및 견적서 저장 단계가 완료되었습니다.")
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/purchase-jobs/{job_id}/submit-approval", response_model=RunStepResponse)
def submit_approval(job_id: str) -> RunStepResponse:
    try:
        job = submit_approval_step(job_id)
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
