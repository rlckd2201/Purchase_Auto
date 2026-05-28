from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


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


class PurchaseAssetRecipient(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    department: str | None = Field(
        default=None,
        validation_alias=AliasChoices("asset_department", "asset_dept", "department", "dept", "부서", "지급부서"),
    )
    user: str | None = Field(
        default=None,
        validation_alias=AliasChoices("asset_user", "asset_target", "recipient", "target", "user", "사용자", "대상", "지급대상"),
    )
    purpose: str | None = Field(
        default=None,
        validation_alias=AliasChoices("asset_purpose", "purpose", "용도"),
    )
    note: str | None = Field(
        default=None,
        validation_alias=AliasChoices("asset_note", "note", "비고"),
    )

    @field_validator("department", "user", "purpose", "note", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class PurchaseItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    url: str
    quantity: int = Field(ge=1)
    asset_department: str | None = Field(
        default=None,
        validation_alias=AliasChoices("asset_department", "asset_dept", "department", "dept", "부서", "지급부서"),
    )
    asset_user: str | None = Field(
        default=None,
        validation_alias=AliasChoices("asset_user", "asset_target", "recipient", "target", "user", "사용자", "대상", "지급대상"),
    )
    asset_purpose: str | None = Field(
        default=None,
        validation_alias=AliasChoices("asset_purpose", "purpose", "용도"),
    )
    asset_note: str | None = Field(
        default=None,
        validation_alias=AliasChoices("asset_note", "note", "비고"),
    )
    asset_recipients: list[PurchaseAssetRecipient] = Field(
        default_factory=list,
        validation_alias=AliasChoices("asset_recipients", "assetRecipients", "recipients", "지급대상목록"),
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        text = value.strip()
        if not text.startswith(("http://", "https://")):
            raise ValueError("상품 URL은 http:// 또는 https:// 로 시작해야 합니다.")
        return text

    @field_validator("asset_department", "asset_user", "asset_purpose", "asset_note", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class CreatePurchaseJobRequest(BaseModel):
    corp: str
    items: list[PurchaseItem] = Field(min_length=1)
    title: str | None = None
    requester: str | None = None
    memo: str | None = None



class RunCompuzoneOrderRequest(BaseModel):
    compuzone_login_id: str | None = None
    force_restart: bool = True


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
