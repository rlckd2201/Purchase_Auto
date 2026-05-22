from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class PurchaseStatus(str, Enum):
    CREATED = "created"
    CART_READY = "cart_ready"
    ORDER_SUBMITTED_PENDING_PAYMENT = "order_submitted_pending_payment"
    QUOTE_SAVED = "quote_saved"
    APPROVAL_SUBMITTED = "approval_submitted"
    WAITING_TAX_INVOICE = "waiting_tax_invoice"
    TAX_INVOICE_RECEIVED = "tax_invoice_received"
    COMPLETED = "completed"
    FAILED = "failed"


class PurchaseItem(BaseModel):
    url: str
    quantity: int = Field(ge=1)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        text = value.strip()
        if not text.startswith(("http://", "https://")):
            raise ValueError("상품 URL은 http:// 또는 https:// 로 시작해야 합니다.")
        return text


class CreatePurchaseJobRequest(BaseModel):
    corp: str
    items: list[PurchaseItem] = Field(min_length=1)
    title: str | None = None
    requester: str | None = None
    memo: str | None = None



class RunCompuzoneOrderRequest(BaseModel):
    compuzone_login_id: str | None = None


class SubmitApprovalRequest(BaseModel):
    groupware_login_id: str | None = None
    groupware_login_password: str | None = None


class PurchaseJob(BaseModel):
    job_id: str
    corp: str
    corp_code: str
    status: PurchaseStatus
    items: list[PurchaseItem]
    title: str | None = None
    requester: str | None = None
    memo: str | None = None
    order_no: str | None = None
    amount: int | None = None
    item_summary: str | None = None
    quote_pdf_path: str | None = None
    approval_document_id: str | None = None
    approval_document_url: str | None = None
    logs: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class RunStepResponse(BaseModel):
    job: PurchaseJob
    message: str
