from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _path_env(name: str, default: str) -> Path:
    value = Path(_env(name, default))
    if value.is_absolute():
        return value
    return PROJECT_ROOT / value


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    db_path: Path
    artifact_dir: Path
    dry_run: bool
    headless: bool
    enable_live_compuzone_order: bool
    enable_live_groupware_submit: bool
    compuzone_profile_dir: Path
    groupware_profile_dir: Path
    compuzone_cart_url: str
    compuzone_quote_url_template: str
    compuzone_depositor_name: str
    groupware_base_url: str
    groupware_form_urls: dict[str, str]


def load_settings() -> Settings:
    return Settings(
        host=_env("PURCHASE_AUTO_HOST", "127.0.0.1"),
        port=_int_env("PURCHASE_AUTO_PORT", 5008),
        db_path=_path_env("PURCHASE_AUTO_DB_PATH", "data/purchase_auto.sqlite3"),
        artifact_dir=_path_env("PURCHASE_AUTO_ARTIFACT_DIR", "artifacts"),
        dry_run=_bool_env("PURCHASE_AUTO_DRY_RUN", True),
        headless=_bool_env("PURCHASE_AUTO_HEADLESS", False),
        enable_live_compuzone_order=_bool_env("PURCHASE_AUTO_ENABLE_LIVE_COMPUZONE_ORDER", False),
        enable_live_groupware_submit=_bool_env("PURCHASE_AUTO_ENABLE_LIVE_GROUPWARE_SUBMIT", False),
        compuzone_profile_dir=_path_env("PURCHASE_AUTO_COMPUZONE_PROFILE_DIR", ".profiles/compuzone"),
        groupware_profile_dir=_path_env("PURCHASE_AUTO_GROUPWARE_PROFILE_DIR", ".profiles/groupware"),
        compuzone_cart_url=_env("PURCHASE_AUTO_COMPUZONE_CART_URL", "https://www.compuzone.co.kr/mypage/cart.htm"),
        compuzone_quote_url_template=_env(
            "PURCHASE_AUTO_COMPUZONE_QUOTE_URL_TEMPLATE",
            "https://www.compuzone.co.kr/order/order_quote_pdf.htm?OrderNo={order_no}",
        ),
        compuzone_depositor_name=_env("PURCHASE_AUTO_COMPUZONE_DEPOSITOR_NAME", ""),
        groupware_base_url=_env("PURCHASE_AUTO_GROUPWARE_BASE_URL", "https://gw.dae-seung.co.kr"),
        groupware_form_urls={
            "daeseung": _env(
                "PURCHASE_AUTO_GROUPWARE_FORM_URL_DAESEUNG",
                "https://gw.dae-seung.co.kr/app/approval/document/new/223/5646",
            ),
            "daeseung_precision": _env("PURCHASE_AUTO_GROUPWARE_FORM_URL_DAESEUNG_PRECISION", ""),
            "ilgang": _env("PURCHASE_AUTO_GROUPWARE_FORM_URL_ILGANG", ""),
        },
    )
