from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .corps import CorpConfig
from .models import CreatePurchaseJobRequest, PurchaseItem, PurchaseJob, PurchaseStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_db(db_path: Path) -> None:
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS purchase_jobs (
                job_id TEXT PRIMARY KEY,
                corp TEXT NOT NULL,
                corp_code TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT,
                requester TEXT,
                memo TEXT,
                items_json TEXT NOT NULL,
                order_no TEXT,
                amount INTEGER,
                item_summary TEXT,
                quote_pdf_path TEXT,
                approval_document_id TEXT,
                approval_document_url TEXT,
                logs_json TEXT NOT NULL,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _loads_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _row_to_job(row: sqlite3.Row) -> PurchaseJob:
    items = [PurchaseItem.model_validate(item) for item in _loads_json(row["items_json"], [])]
    return PurchaseJob(
        job_id=row["job_id"],
        corp=row["corp"],
        corp_code=row["corp_code"],
        status=PurchaseStatus(row["status"]),
        items=items,
        title=row["title"],
        requester=row["requester"],
        memo=row["memo"],
        order_no=row["order_no"],
        amount=row["amount"],
        item_summary=row["item_summary"],
        quote_pdf_path=row["quote_pdf_path"],
        approval_document_id=row["approval_document_id"],
        approval_document_url=row["approval_document_url"],
        logs=_loads_json(row["logs_json"], []),
        error_message=row["error_message"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def create_job(db_path: Path, request: CreatePurchaseJobRequest, corp: CorpConfig) -> PurchaseJob:
    ensure_db(db_path)
    job_id = uuid.uuid4().hex
    now = _now()
    items_json = json.dumps([item.model_dump() for item in request.items], ensure_ascii=False)
    title = request.title or f"{corp.display_name} 컴퓨존 구매 품의"
    logs_json = json.dumps(
        [{"at": now, "level": "info", "message": "구매 작업이 생성되었습니다."}],
        ensure_ascii=False,
    )
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO purchase_jobs (
                job_id, corp, corp_code, status, title, requester, memo, items_json,
                logs_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                corp.display_name,
                corp.code,
                PurchaseStatus.CREATED.value,
                title,
                request.requester,
                request.memo,
                items_json,
                logs_json,
                now,
                now,
            ),
        )
        conn.commit()
    job = get_job(db_path, job_id)
    if job is None:
        raise RuntimeError("생성한 구매 작업을 다시 읽지 못했습니다.")
    return job


def get_job(db_path: Path, job_id: str) -> PurchaseJob | None:
    ensure_db(db_path)
    with closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM purchase_jobs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return _row_to_job(row)


def list_jobs(db_path: Path) -> list[PurchaseJob]:
    ensure_db(db_path)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM purchase_jobs ORDER BY created_at DESC").fetchall()
    return [_row_to_job(row) for row in rows]


def update_job(db_path: Path, job_id: str, **fields: Any) -> PurchaseJob:
    ensure_db(db_path)
    if not fields:
        job = get_job(db_path, job_id)
        if job is None:
            raise KeyError(job_id)
        return job
    fields["updated_at"] = _now()
    assignments = ", ".join(f"{name} = ?" for name in fields)
    values = [value.value if isinstance(value, PurchaseStatus) else value for value in fields.values()]
    values.append(job_id)
    with closing(_connect(db_path)) as conn:
        conn.execute(f"UPDATE purchase_jobs SET {assignments} WHERE job_id = ?", values)
        conn.commit()
    job = get_job(db_path, job_id)
    if job is None:
        raise KeyError(job_id)
    return job


def append_log(db_path: Path, job_id: str, message: str, level: str = "info") -> PurchaseJob:
    job = get_job(db_path, job_id)
    if job is None:
        raise KeyError(job_id)
    logs = [*job.logs, {"at": _now(), "level": level, "message": message}]
    return update_job(db_path, job_id, logs_json=json.dumps(logs, ensure_ascii=False))


def set_failed(db_path: Path, job_id: str, message: str) -> PurchaseJob:
    append_log(db_path, job_id, message, level="error")
    return update_job(db_path, job_id, status=PurchaseStatus.FAILED, error_message=message)
