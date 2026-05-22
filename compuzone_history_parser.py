from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


HISTORY_URL = "https://www.compuzone.co.kr/mypage/order_list.htm"
LOGIN_URL = "https://www.compuzone.co.kr/login/login.htm"
PRODUCT_DETAIL_BASE_URL = "https://www.compuzone.co.kr/product/product_detail.htm?ProductNo="
CSV_FIELDS = [
    "계정",
    "품목명",
    "제품명",
    "원문",
    "해당 제품 마지막 구매일자",
    "구매횟수",
    "구매수량",
    "상품번호",
    "상품URL",
    "평균단가",
]
YEARS = (2026, 2025, 2024)
TARGET_STATUSES = ("상품발송", "배송완료")
ORDERS_PER_PAGE = 5
URL_FIELD = "상품URL"
NORMALIZATION_RULE_VERSION = 7
DESKTOP_SPLIT_VERSION = 1
DEFAULT_ACCOUNTS = ("ds1500", "reum0009")
DESKTOP_PC_CATEGORIES = {"데스크탑PC", "CAD PC", "사무용 PC"}
PREFERRED_DESKTOP_CATEGORIES = ("CAD PC", "사무용 PC")
DESKTOP_PC_PATTERNS = (
    r"조립pc[_\s-]*[a-z0-9]",
    r"게이밍\s*추천\s*조립pc",
    r"프로데스크",
    r"(?<!무선\s)(?<!블루투스\s)데스크탑(?!\s*세트)",
    r"\btower\s+[a-z0-9]",
    r"아이웍스[0-9x-]*",
    r"워크스테이션",
    r"\baio\b",
    r"올인원\s*pc",
    r"pc\s*본체",
)
DESKTOP_PC_EXCLUSION_PATTERNS = (
    r"데스크탑\s*세트",
    r"조립비",
    r"탑재\s*시\s*필수",
    r"하드웨어조립",
    r"os\s*설치비",
)
DEDICATED_GPU_PATTERNS = (
    r"\brtx\s*[0-9]{4}(?:\s*ti)?\b",
    r"\bgtx\s*[0-9]{3,4}(?:\s*ti)?\b",
    r"\bgeforce\b",
    r"지포스",
    r"그래픽카드",
    r"\bquadro\b",
    r"\bnvidia\s+(?:rtx|t[0-9]{3,4}|a[0-9]{4})\b",
    r"\bradeon\s+(?:rx|pro)\b",
    r"\brx\s*[0-9]{4}\b",
    r"[/_-](?:3050|3060|3070|3080|3090|4050|4060|4070|4080|4090|5050|5060|5070|5080|5090)(?:\D|$)",
)
INTEGRATED_GPU_PATTERNS = (
    r"radeon\s+graphics",
    r"intel\s+(?:uhd|iris|arc)?\s*graphics",
    r"uhd\s+graphics",
    r"내장\s*그래픽",
)
CATEGORY_RULES = (
    ("액정보호필름", ("액정보호필름", "정보보호", "보안기", "보호필름")),
    ("노트북가방", ("노트북 서류가방", "노트북 파우치", "랩탑 브리프케이스")),
    ("미니PC", ("nuc-", "미니pc", "슬림pc")),
    ("노트북", ("아이디어패드", "thinkpad", "갤럭시북", "자비스", "lg 그램", "pro 15 essential", " 노트북 ")),
    ("TV거치대", ("tv 거치대", "tv스탠드", "티비거치대")),
    ("TV", (" tv ", "중소기업 tv")),
    ("모니터암", ("모니터암", "mount")),
    ("모니터받침대", ("모니터받침대", "받침대")),
    ("웹캠", ("웹캠", "화상카메라")),
    ("프로젝터", ("빔프로젝터", "프로젝터")),
    ("프린터/복합기", ("프린터", "복합기", "무한잉크")),
    ("조립/설치서비스", ("일반조립비", "조립비", "하드웨어조립", "os 설치비")),
    ("오피스", ("office", "microsoft 365", "한컴오피스", " 한글 ")),
    ("운영체제", ("windows 11", "win11", "처음사용자용 패키지", "os설치비포함", "복구솔루션")),
    ("메인보드", ("메인보드", "m-atx", "인텔b", "amd b650", "amd b850", "b760/atx")),
    ("CPU", ("라이젠", "코어 i", "9800x3d", "cpu ")),
    ("그래픽카드", ("그래픽카드", "geforce", "지포스", "radeon", " rtx ")),
    ("파워서플라이", ("80plus", "1000w", "650w", "atx3.1")),
    ("케이스", ("미들타워", "미니타워", "빅타워", "btf")),
    ("CPU쿨러", ("cpu쿨러",)),
    ("시스템쿨러", ("시스템쿨러",)),
    ("네트워크허브", ("스위칭허브",)),
    ("USB허브", ("usb허브", "멀티허브", "usb4.0", "usb3.1")),
    ("블루투스동글", ("블루투스 동글",)),
    ("무선수신기", ("무선 수신기", "logi bolt")),
    ("무선AP", ("무선ap",)),
    ("KVM", (" kvm ", "거리연장기")),
    ("SFP모듈", ("sfp+ 모듈", "sfp 광 모듈", "미니지빅")),
    ("모니터", ("모니터", "무결점", "ips fhd", "qhd", "touch75", "27fd")),
    ("HDD", (" hdd", "3.5hdd", "sata3", "cmr")),
    ("SSD", (" ssd", "nvme", "m.2", "green sata", "240gb tlc")),
    ("메모리", (" ddr", "메모리", "ram")),
    ("랜카드", ("랜카드", "유선랜", "무선랜")),
    ("랜커넥터", ("utp", "랜 커넥터", "랜커넥터", "rj-45", "inline")),
    ("케이블", ("케이블", "광점퍼", "전원 케이블", "hdmi", "dp케이블", "usb 케이블")),
    ("멀티탭", ("멀티탭",)),
    ("충전기/거치대", ("무선충전", "차량용", "거치대")),
    ("스피커", ("스피커", "사운드바")),
    ("헤드셋", ("헤드셋",)),
    ("이어폰", ("이어폰",)),
    ("마이크", ("마이크", "콘덴서", "fifine")),
    ("영상장비", ("인켈 ik-",)),
    ("USB메모리", ("usb, 울트라", "usb 메모리", "cz73")),
    ("공기청정기", ("공기청정기",)),
    ("마우스", ("마우스",)),
    ("키보드", ("키보드",)),
    ("아답터", ("아답터", "어댑터")),
    ("건전지", ("건전지", "알카라인", "cr2032")),
    ("마우스패드", ("마우스패드", "패드")),
    ("라벨용지", ("라벨 용지", "봉인라벨")),
    ("테이프", ("테이프",)),
    ("공구", ("드라이버", "펜치", "비트세트", "자화기", "임팩트툴", "고무 망치", "랜툴")),
    ("테스터기", ("테스터기", "탐지용")),
    ("충전기", ("충전기", "gan pd")),
    ("젠더", ("젠더", "변환젠더")),
    ("랙선반", ("허브랙", "전면거치 선반")),
    ("보관함", ("보관함",)),
    ("가습기", ("가습기",)),
    ("온습도계", ("온습도계",)),
)
MODEL_EXCLUDE_TOKENS = {
    "IPS",
    "FHD",
    "QHD",
    "UHD",
    "LED",
    "USB",
    "HDD",
    "SSD",
    "SATA3",
    "CMR",
    "NVME",
    "M2",
    "PC",
    "OS",
    "MAX",
    "EA",
    "HDMI",
    "DP",
    "DDR",
    "RAM",
    "CM",
    "MM",
    "VGA",
    "RGB",
    "KM",
    "HZ",
    "PORT",
    "PORTS",
    "MBPS",
    "GBPS",
}
SPEC_MODEL_WORDS = (
    "가상",
    "내경",
    "외경",
    "색상",
    "블랙",
    "화이트",
    "무료배송",
    "기본제품",
    "정품",
    "행사",
    "호환",
    "무전원",
    "ct입",
    "c타입",
    "usb3",
    "full-hd",
    "1000mbps",
    "랙마운트",
    "연결용",
    "설치",
    "추가",
    "ram",
    "nvme",
    "km",
    "기가비트",
    "싱글모드",
    "멀티모드",
    "포트",
    "mbps",
    "gbps",
)

ATTRIBUTE_ONLY_MODEL_PATTERN = re.compile(
    r"^\d+(?:\.\d+)?\s*(?:GB|TB|MB|W|V|A|M|CM|MM|KM|HZ|BIT|RPM|MAH|PORT|포트|형|인치)$",
    flags=re.IGNORECASE,
)
PROTOCOL_VERSION_PATTERN = re.compile(
    r"^(?:DDR|USB|HDMI|DP|SATA|PCIE|PCI-E)\s*\d+(?:\.\d+)?$",
    flags=re.IGNORECASE,
)
MODEL_CODE_PATTERNS = (
    r"\b(PC[45]-\d{4,6})\b",
    r"\b(TB\d+(?:-\d+(?:\.\d+)?M)?)\b",
    r"\b(MTF\d{2,4})\b",
    r"\b(WC\d{2,4})\b",
    r"\b(CEM\d{2,4})\b",
    r"\b(MC-\d{2,4})\b",
    r"\b(HANDS\d*)\b",
    r"\b(APL-[A-Z0-9-]+)\b",
)


@dataclass(frozen=True)
class OrderLine:
    account: str
    product_name: str
    product_no: str | None
    purchase_date: date | None
    quantity: int
    unit_price: int | None
    order_no: str | None
    order_status: str
    source_url: str
    raw_text: str


@dataclass
class ProductSummary:
    account: str
    product_name: str
    product_no: str | None
    last_purchase_date: date | None = None
    purchase_quantity: int = 0
    price_amount_total: int = 0
    price_quantity: int = 0
    order_keys: set[str] = field(default_factory=set)
    accounts: set[str] = field(default_factory=set)

    @property
    def purchase_count(self) -> int:
        return len(self.order_keys)

    @property
    def account_label(self) -> str:
        accounts = sorted(account for account in self.accounts if account)
        if accounts:
            return ", ".join(accounts)
        return self.account

    @property
    def average_unit_price(self) -> int | None:
        if self.price_quantity <= 0:
            return None
        return round(self.price_amount_total / self.price_quantity)


class ProductNameNormalizer:
    def __init__(
        self,
        path: Path | None = None,
        use_openai: bool = False,
        openai_model: str = "gpt-5-nano",
        openai_api_key_env: str = "OPENAI_API_KEY",
        openai_timeout: int = 30,
        openai_threshold: float = 0.8,
        use_gemini: bool = False,
        gemini_model: str = "gemini-2.5-flash-lite",
        gemini_api_key_env: str = "GEMINI_API_KEY",
        gemini_timeout: int = 60,
        gemini_threshold: float = 0.8,
        gemini_batch_size: int = 40,
    ) -> None:
        self.path = path
        self.use_openai = use_openai
        self.openai_model = openai_model
        self.openai_api_key_env = openai_api_key_env
        self.openai_timeout = openai_timeout
        self.openai_threshold = openai_threshold
        self.use_gemini = use_gemini
        self.gemini_model = gemini_model
        self.gemini_api_key_env = gemini_api_key_env
        self.gemini_timeout = gemini_timeout
        self.gemini_threshold = gemini_threshold
        self.gemini_batch_size = gemini_batch_size
        self.payload: dict[str, Any] = {"version": 1, "items": {}}
        self.changed = False
        if path and path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = {}
            if isinstance(loaded, dict):
                items = loaded.get("items")
                if isinstance(items, dict):
                    self.payload = {"version": loaded.get("version", 1), "items": items}

    def key_for(self, summary: ProductSummary) -> str:
        if summary.product_no:
            return f"product_no:{summary.product_no}"
        return f"name:{normalize_key(summary.product_name)}"

    def rule_entry(self, summary: ProductSummary) -> dict[str, Any]:
        item_name = classify_item_name(summary.product_name)
        model_name = extract_model_name(summary.product_name)
        confidence = 0.85 if item_name != "기타" and model_name != summary.product_name else 0.55
        return self.finalize_entry(summary, {
            "item_name": item_name,
            "model_name": model_name,
            "raw_name": summary.product_name,
            "product_no": summary.product_no or "",
            "source": "rules",
            "rule_version": NORMALIZATION_RULE_VERSION,
            "confidence": confidence,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })

    def finalize_entry(self, summary: ProductSummary, entry: dict[str, Any]) -> dict[str, Any]:
        if entry.get("locked") or entry.get("normalization_locked"):
            return entry
        before = dict(entry)
        normalized = postprocess_normalized_entry(summary, entry)
        if normalized != before:
            self.changed = True
        return normalized

    def needs_desktop_gemini_refresh(self, summary: ProductSummary, entry: Any) -> bool:
        if not self.use_gemini or not isinstance(entry, dict):
            return False
        item_name = normalize_space(str(entry.get("item_name") or ""))
        if item_name not in DESKTOP_PC_CATEGORIES and not is_desktop_pc_text(summary.product_name):
            return False
        try:
            split_version = int(entry.get("desktop_split_version") or 0)
        except (TypeError, ValueError):
            split_version = 0
        return item_name == "데스크탑PC" or split_version < DESKTOP_SPLIT_VERSION

    def should_reuse_entry(self, entry: Any) -> bool:
        if not isinstance(entry, dict) or not entry.get("item_name") or not entry.get("model_name"):
            return False
        if entry.get("locked") or entry.get("normalization_locked"):
            return True
        if entry.get("source") == "rules":
            return entry.get("rule_version") == NORMALIZATION_RULE_VERSION and not (
                (self.use_gemini and float(entry.get("confidence") or 0) < self.gemini_threshold)
                or (self.use_openai and float(entry.get("confidence") or 0) < self.openai_threshold)
            )
        return True

    def prepare(self, summaries: list[ProductSummary]) -> None:
        if not self.use_gemini:
            return

        items = self.payload.setdefault("items", {})
        pending: list[tuple[str, ProductSummary, dict[str, Any]]] = []
        seen: set[str] = set()
        for summary in summaries:
            key = self.key_for(summary)
            if key in seen:
                continue
            seen.add(key)
            existing = items.get(key)
            if self.should_reuse_entry(existing) and not self.needs_desktop_gemini_refresh(summary, existing):
                continue
            fallback = self.rule_entry(summary)
            if self.needs_desktop_gemini_refresh(summary, existing) or float(fallback.get("confidence") or 0) < self.gemini_threshold:
                pending.append((key, summary, fallback))
            else:
                items[key] = fallback
                self.changed = True

        for start in range(0, len(pending), self.gemini_batch_size):
            chunk = pending[start : start + self.gemini_batch_size]
            try:
                normalized = self.normalize_with_gemini_batch(chunk)
            except Exception as error:
                for key, _, fallback in chunk:
                    fallback["api_error"] = str(error)[:300]
                    items[key] = fallback
                self.changed = True
                continue
            for key, entry in normalized.items():
                items[key] = entry
                self.changed = True

    def normalize(self, summary: ProductSummary) -> dict[str, Any]:
        key = self.key_for(summary)
        items = self.payload.setdefault("items", {})
        entry = items.get(key)
        if self.should_reuse_entry(entry) and not self.needs_desktop_gemini_refresh(summary, entry):
            return self.finalize_entry(summary, entry)

        entry = self.rule_entry(summary)
        if self.use_gemini and (
            self.needs_desktop_gemini_refresh(summary, items.get(key))
            or float(entry.get("confidence") or 0) < self.gemini_threshold
        ):
            try:
                batch = self.normalize_with_gemini_batch([(key, summary, entry)])
                entry = batch.get(key, entry)
            except Exception as error:
                entry["api_error"] = str(error)[:300]
        if self.use_openai and float(entry.get("confidence") or 0) < self.openai_threshold:
            try:
                entry = self.normalize_with_openai(summary, entry)
            except Exception as error:
                entry["api_error"] = str(error)[:300]
        items[key] = entry
        self.changed = True
        return entry

    def normalize_with_gemini_batch(
        self,
        batch: list[tuple[str, ProductSummary, dict[str, Any]]],
    ) -> dict[str, dict[str, Any]]:
        api_key = os.environ.get(self.gemini_api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"{self.gemini_api_key_env} 환경변수가 없습니다.")

        categories = list(dict.fromkeys([*PREFERRED_DESKTOP_CATEGORIES, *[category for category, _ in CATEGORY_RULES]]))
        requests = [
            {
                "key": key,
                "raw_name": summary.product_name,
                "product_no": summary.product_no or "",
                "rule_guess": {
                    "item_name": fallback.get("item_name", ""),
                    "model_name": fallback.get("model_name", ""),
                },
            }
            for key, summary, fallback in batch
        ]
        prompt = {
            "task": "컴퓨존 상품명을 구매/품의 엑셀용 품목명과 제품명으로 정규화한다.",
            "rules": [
                "item_name은 구매 분류용 한국어 카테고리명이다. 기타는 최대한 피한다.",
                "model_name은 브랜드/행사문구/색상/배송/설치/업그레이드 설명을 제거한 핵심 모델명 또는 제품 식별명이다.",
                "노트북, 미니PC, 데스크탑PC에 Win11, RAM, SSD가 포함되어도 운영체제나 메모리로 분류하지 말고 본체 종류로 분류한다.",
                "데스크탑/조립PC/워크스테이션/프로데스크는 데스크탑PC라고 쓰지 말고, 전용 그래픽카드가 있으면 CAD PC, 없으면 사무용 PC로 분류한다.",
                "RTX/GTX/GeForce/Quadro/Radeon RX/NVIDIA T/A 시리즈 또는 조립PC 코드의 /4060, /5060 같은 표기는 전용 그래픽카드로 본다.",
                "Radeon Graphics, Intel UHD/Iris Graphics, 내장그래픽은 전용 그래픽카드가 아니므로 사무용 PC로 본다.",
                "TV는 모니터로 분류하지 않는다.",
                "아답터/어댑터는 삼성모니터전용 같은 주변 문구보다 아답터로 분류한다.",
                "HP ProDesk 같은 완제품 PC에서 G1a 같은 세대명과 C27L5AT 같은 모델코드가 함께 있으면 model_name은 모델코드만 남긴다.",
                "상품명 안에 모델명이 없으면 제품을 식별할 수 있는 가장 짧은 제품명으로 둔다.",
            ],
            "preferred_categories": categories,
            "items": requests,
        }
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "item_name": {"type": "string"},
                    "model_name": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["key", "item_name", "model_name", "confidence"],
            },
        }
        body = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": (
                                "Return only JSON matching the schema. "
                                "Normalize the following Korean ecommerce product names.\n"
                                + json.dumps(prompt, ensure_ascii=False)
                            )
                        }
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent"
        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.gemini_timeout) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Gemini API 오류 HTTP {error.code}: {detail[:300]}") from error

        parsed = json.loads(extract_gemini_response_text(response_payload))
        if not isinstance(parsed, list):
            raise RuntimeError("Gemini 응답이 배열 JSON이 아닙니다.")

        fallback_by_key = {key: (summary, fallback) for key, summary, fallback in batch}
        normalized: dict[str, dict[str, Any]] = {}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "")
            if key not in fallback_by_key:
                continue
            summary, fallback = fallback_by_key[key]
            item_name = normalize_space(str(item.get("item_name") or fallback.get("item_name") or "기타"))
            model_name = normalize_space(str(item.get("model_name") or fallback.get("model_name") or summary.product_name))
            try:
                confidence = max(0.0, min(1.0, float(item.get("confidence", fallback.get("confidence", 0.5)))))
            except (TypeError, ValueError):
                confidence = float(fallback.get("confidence") or 0.5)
            normalized[key] = {
                "item_name": item_name,
                "model_name": model_name,
                "raw_name": summary.product_name,
                "product_no": summary.product_no or "",
                "source": "gemini",
                "model": self.gemini_model,
                "confidence": confidence,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            normalized[key] = postprocess_normalized_entry(summary, normalized[key])

        for key, (summary, fallback) in fallback_by_key.items():
            if key not in normalized:
                fallback["api_error"] = "Gemini 응답에 해당 key가 없습니다."
                normalized[key] = fallback
        return normalized

    def normalize_with_openai(self, summary: ProductSummary, fallback: dict[str, Any]) -> dict[str, Any]:
        api_key = os.environ.get(self.openai_api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"{self.openai_api_key_env} 환경변수가 없습니다.")

        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["item_name", "model_name", "confidence"],
            "properties": {
                "item_name": {
                    "type": "string",
                    "description": "Korean purchasing category, for example 모니터, CAD PC, 사무용 PC, 운영체제, 마우스, 케이블.",
                },
                "model_name": {
                    "type": "string",
                    "description": "Clean model or product identifier only. Remove brand, specs, marketing copy, and condition labels.",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
            },
        }
        prompt = {
            "raw_name": summary.product_name,
            "product_no": summary.product_no or "",
            "rule_guess": {
                "item_name": fallback.get("item_name", ""),
                "model_name": fallback.get("model_name", ""),
            },
            "instruction": (
                "컴퓨존 상품명을 구매/품의용 품목명과 제품명으로 정규화해라. "
                "품목명은 일반 카테고리명으로 짧게, 제품명은 모델명/식별명만 남겨라. "
                "데스크탑/조립PC는 전용 그래픽카드가 있으면 CAD PC, 없으면 사무용 PC로 나눈다. "
                "브랜드명, 색상, 무결점, 행사문구, OS설치 같은 부가 설명은 제품명에서 제거한다."
            ),
        }
        body = {
            "model": self.openai_model,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "You normalize Korean ecommerce product names for procurement spreadsheets. "
                        "Return only the requested structured JSON."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "compuzone_product_name",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.openai_timeout) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI API 오류 HTTP {error.code}: {detail[:300]}") from error

        content = extract_openai_response_text(response_payload)
        parsed = json.loads(content)
        item_name = normalize_space(str(parsed.get("item_name") or fallback.get("item_name") or "기타"))
        model_name = normalize_space(str(parsed.get("model_name") or fallback.get("model_name") or summary.product_name))
        confidence = parsed.get("confidence", fallback.get("confidence", 0.5))
        try:
            confidence_float = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence_float = float(fallback.get("confidence") or 0.5)
        return self.finalize_entry(summary, {
            "item_name": item_name,
            "model_name": model_name,
            "raw_name": summary.product_name,
            "product_no": summary.product_no or "",
            "source": "openai",
            "model": self.openai_model,
            "confidence": confidence_float,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })

    def save(self) -> None:
        if not self.path or not self.changed:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.changed = False


def extract_openai_response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()

    raise RuntimeError("OpenAI 응답에서 텍스트를 찾지 못했습니다.")


def extract_gemini_response_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise RuntimeError("Gemini 응답에 candidates가 없습니다.")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content") or {}
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            continue
        texts = [
            part.get("text")
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        text = "".join(texts).strip()
        if text:
            return text
    raise RuntimeError("Gemini 응답에서 텍스트를 찾지 못했습니다.")


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_key(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "", value or "").lower()


def normalize_env_suffix(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "_", value or "").upper().strip("_")


def parse_date(value: str) -> date | None:
    text = value or ""
    patterns = (
        r"(20\d{2})[-./년\s]+(0?[1-9]|1[0-2])[-./월\s]+(0?[1-9]|[12]\d|3[01])",
        r"(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        year, month, day = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return None


def parse_quantity(value: str) -> int:
    text = value or ""
    patterns = (
        r"(?:구매\s*)?(?:주문\s*)?수량\s*[:：]?\s*([0-9,]+)\s*(?:개|ea|EA)?",
        r"(?:^|\s)([0-9,]+)\s*(?:개|ea|EA)(?:\s|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return max(1, int(match.group(1).replace(",", "")))
    return 1


def parse_unit_price(value: str) -> int | None:
    text = normalize_space(value)
    preferred_patterns = (
        r"([0-9][0-9,]*)\s*원\s*(?:[|/·,]|\s)*(?:수량|[0-9,]+\s*(?:개|ea|EA))",
        r"(?:단가|판매가|상품금액|금액)\s*[:：]?\s*([0-9][0-9,]*)\s*원",
    )
    for pattern in preferred_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1).replace(",", ""))

    amounts = [
        int(match.group(1).replace(",", ""))
        for match in re.finditer(r"([0-9][0-9,]*)\s*원", text)
    ]
    return amounts[0] if amounts else None


def parse_order_no(value: str) -> str | None:
    text = value or ""
    patterns = (
        r"(?:주문번호|주문\s*번호|Order\s*No\.?)\s*[:：]?\s*([0-9]{6,})",
        r"\b([0-9]{8,14})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def parse_product_no(value: str) -> str | None:
    text = value or ""
    patterns = (
        r"[?&]ProductNo=([0-9]+)",
        r"(?:상품번호|상품코드|ProductNo)\s*[:：]?\s*([0-9]{3,})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def product_url(product_no: str | None) -> str:
    return f"{PRODUCT_DETAIL_BASE_URL}{product_no}" if product_no else ""


def strip_leading_brand(value: str) -> str:
    text = normalize_space(value)
    brand = ""
    match = re.match(r"^\[([^\]]+)\]\s*(.*)$", text)
    if match:
        brand = normalize_space(match.group(1))
        text = normalize_space(match.group(2))
    if brand:
        text = re.sub(rf"^{re.escape(brand)}\s+", "", text, flags=re.IGNORECASE)
    return text


def dedicated_gpu_text(value: str) -> str:
    text = normalize_space(value).lower()
    for pattern in INTEGRATED_GPU_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
    return normalize_space(text)


def has_dedicated_graphics(value: str) -> bool:
    text = dedicated_gpu_text(value)
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in DEDICATED_GPU_PATTERNS)


def is_desktop_pc_text(value: str) -> bool:
    text = f" {normalize_space(value).lower()} "
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in DESKTOP_PC_EXCLUSION_PATTERNS):
        return False
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in DESKTOP_PC_PATTERNS)


def classify_desktop_pc_category(value: str) -> str:
    return "CAD PC" if has_dedicated_graphics(value) else "사무용 PC"


def classify_item_name(value: str) -> str:
    text = f" {normalize_space(value).lower()} "
    if "tv 거치대" in text or "tv스탠드" in text or "티비거치대" in text:
        return "TV거치대"
    if "windows 11" in text or "win11" in text or ("마이크로소프트" in text and "os설치비포함" in text):
        return "운영체제"
    if "조립비" in text or "하드웨어조립" in text or re.search(r"\bos\s*설치비\b", text):
        return "조립/설치서비스"
    if "데스크탑 세트" in text:
        return "키보드/마우스세트"
    if "무선충전" in text or ("거치대" in text and "tv" not in text):
        return "충전기/거치대"
    if "공기청정기" in text:
        return "공기청정기"
    if "아답터" in text or "어댑터" in text:
        return "아답터"
    if " tv " in text or "중소기업 tv" in text:
        return "TV"
    if is_desktop_pc_text(value):
        return classify_desktop_pc_category(value)
    if (
        ("displayport" in text or " hdmi" in text or " dp " in text or "rgb(vga)" in text or " vga" in text)
        and ("케이블" in text or "변환" in text or "광점퍼" in text)
    ):
        return "케이블"
    if "rj-45" in text or "inline" in text or "utp" in text or "랜 커넥터" in text or "랜커넥터" in text:
        return "랜커넥터"
    for category, keywords in CATEGORY_RULES:
        if any(keyword in text for keyword in keywords):
            if category == "운영체제" and not (
                "마이크로소프트" in text or "처음사용자용" in text or "os설치비포함" in text or "복구솔루션" in text
            ):
                continue
            if category == "메모리" and ("ram 추가" in text or "ram +" in text):
                continue
            return category
    return "기타"


def cleanup_model_token(token: str) -> str:
    token = token.strip("[]{}|,./:; ")
    token = re.sub(r"^[★▶◀]+|[★▶◀]+$", "", token)
    token = re.sub(r"^(?:PC|MODEL|NO)[_-]+(?=[A-Za-z0-9])", "", token, flags=re.IGNORECASE)
    return token


def is_attribute_only_model_token(token: str) -> bool:
    cleaned = normalize_space(cleanup_model_token(token))
    compact = re.sub(r"\s+", "", cleaned)
    if ATTRIBUTE_ONLY_MODEL_PATTERN.fullmatch(cleaned) or ATTRIBUTE_ONLY_MODEL_PATTERN.fullmatch(compact):
        return True
    if PROTOCOL_VERSION_PATTERN.fullmatch(cleaned) or PROTOCOL_VERSION_PATTERN.fullmatch(compact):
        return True
    return False


def first_model_code(text: str) -> str | None:
    for pattern in MODEL_CODE_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return normalize_space(match.group(1)).upper()
    return None


def extract_memory_model_name(text: str) -> str | None:
    pc_code = first_regex_group(text, (r"\b(PC[45]-\d{4,6})\b",))
    if not pc_code:
        return None
    capacity = first_regex_group(text, (r"\[(\d+(?:\.\d+)?\s*(?:GB|TB))\]", r"\b(\d+(?:\.\d+)?\s*(?:GB|TB))\b"))
    speed = first_regex_group(text, (r"\((\d{4,5})\)",))
    parts = [pc_code.upper()]
    if capacity:
        parts.append(capacity.replace(" ", "").upper())
    if speed:
        parts.append(f"({speed})")
    return normalize_space(" ".join(parts))


def strip_brand_and_promotions(value: str) -> str:
    text = strip_leading_brand(value)
    text = re.sub(r"색상선택\|.*$", "", text)
    text = re.sub(r"\s+★.*$", "", text)
    text = re.sub(r"\s+※.*$", "", text)
    text = re.sub(r"▶.*?◀", "", text)
    return normalize_space(text)


def extract_desktop_model_name(value: str) -> str | None:
    return first_regex_group(
        value,
        (
            r"조립PC[_\s-]+([A-Za-z0-9-]+)",
            r"(아이웍스[0-9A-Za-z-]+)",
            r"데스크탑\s+Tower\s+([A-Z0-9-]+(?:/[A-Z0-9-]+)?)",
            r"\bTower\s+([A-Z0-9-]+(?:/[A-Z0-9-]+)?)",
            r"데스크탑\s+([A-Z0-9]+(?:[.-][A-Z0-9]+)+)",
            r"\bAIO\s+([0-9]{2}-[A-Z0-9-]+)",
        ),
    )


def postprocess_normalized_entry(summary: ProductSummary, entry: dict[str, Any]) -> dict[str, Any]:
    raw = summary.product_name
    model_name = normalize_space(str(entry.get("model_name") or ""))
    hp_code = re.search(r"\b(C\d{2}[A-Z0-9]{4})\b", raw, flags=re.IGNORECASE)
    if "프로데스크" in raw and hp_code:
        entry["model_name"] = hp_code.group(1).upper()
        entry["confidence"] = max(float(entry.get("confidence") or 0), 0.95)
    elif model_name.upper() == "G1A" and hp_code:
        entry["model_name"] = hp_code.group(1).upper()

    item_name = normalize_space(str(entry.get("item_name") or ""))
    raw_is_desktop = is_desktop_pc_text(raw)
    if "조립비" in raw:
        entry["item_name"] = "조립/설치서비스"
        entry["confidence"] = max(float(entry.get("confidence") or 0), 0.9)
    elif raw_is_desktop:
        entry["item_name"] = classify_desktop_pc_category(raw)
        desktop_model = extract_desktop_model_name(raw)
        if desktop_model:
            entry["model_name"] = desktop_model
        entry["desktop_split_version"] = DESKTOP_SPLIT_VERSION
        entry["desktop_split_basis"] = "dedicated_gpu" if has_dedicated_graphics(raw) else "no_dedicated_gpu"
        entry["confidence"] = max(float(entry.get("confidence") or 0), 0.9)
    elif item_name in DESKTOP_PC_CATEGORIES:
        entry["item_name"] = classify_item_name(raw)
        entry.pop("desktop_split_version", None)
        entry.pop("desktop_split_basis", None)
        entry["confidence"] = min(float(entry.get("confidence") or 0.7), 0.8)
    return entry


def is_spec_token(token: str) -> bool:
    lowered = normalize_space(token).lower()
    return any(word in lowered for word in SPEC_MODEL_WORDS)


def is_model_token(token: str) -> bool:
    cleaned = cleanup_model_token(token)
    if len(cleaned) < 3:
        return False
    if is_attribute_only_model_token(cleaned):
        return False
    if is_spec_token(cleaned):
        return False
    compact = re.sub(r"[^0-9A-Za-z]+", "", cleaned)
    if len(compact) < 3:
        return False
    if not (re.search(r"[A-Za-z]", compact) and re.search(r"\d", compact)):
        return False
    if compact.upper() in MODEL_EXCLUDE_TOKENS:
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?(?:GB|TB|MB|W|V|A|M|CM|MM|KM|HZ|BIT|RPM|MAH|PORT|포트|형|인치)", cleaned, flags=re.IGNORECASE):
        return False
    if re.fullmatch(r"(?:DDR|USB|HDMI|DP|SATA|PCIE|PCI-E)\d+(?:\.\d+)?", compact, flags=re.IGNORECASE):
        return False
    return True


def first_regex_group(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return normalize_space(match.group(1) if match.groups() else match.group(0))
    return None


def extract_model_name(value: str) -> str:
    text = strip_brand_and_promotions(value)

    memory_model = extract_memory_model_name(text)
    if memory_model:
        return memory_model

    explicit_model = first_model_code(text)
    if explicit_model:
        return explicit_model

    bracket_tokens = re.findall(r"\[([^\]]+)\]", text)
    for token in bracket_tokens:
        cleaned = cleanup_model_token(token)
        if is_model_token(cleaned):
            return cleaned

    special = first_regex_group(
        text,
        (
            r"(Windows\s*11\s*(?:Pro|Home)[^()\[\]★]*)",
            r"(한컴오피스\s*\d{4}\s*한글)",
            r"(Office\s+Home\s*&\s*Business\s*\d{4}\s*PKC)",
            r"조립PC[_\s-]+([A-Za-z0-9-]+)",
            r"(NUC-\d+\([^)]*\))",
            r"(NEXT-[A-Za-z0-9+]+(?:-[A-Za-z0-9+]+)*)",
            r"(NM-[A-Za-z0-9-]+)",
            r"(LS-[A-Za-z0-9-]+)",
            r"(ipTIME\s+[A-Za-z0-9-]+)",
            r"(SL-[A-Za-z0-9-]+)",
            r"(NT[0-9A-Za-z-]+)",
            r"(PV[0-9A-Za-z-]+)",
            r"(ECT[0-9A-Za-z-]+)",
            r"(83K[0-9A-Z]+)",
            r"(21L[0-9A-Z]+)",
            r"([0-9]{2}-FD[0-9A-Z-]+)",
            r"(RTX\s+\d{4}\s*(?:Ti)?\s+[A-Za-z0-9-]+(?:\s+D\d)?(?:\s+\d+GB)?)",
            r"(B\d{3,4}M?(?:\s+[A-Za-z0-9]+){0,3})",
            r"(MAG\s+B\d{3}\s+[A-Za-z0-9\s]+)",
            r"(i[3579]-\d+[A-Z]*)",
            r"(라이젠\d?\s+[A-Za-z0-9]+)",
            r"아답터,\s*([^[]+)",
            r"어댑터,\s*([^[]+)",
            r"일반조립비\s*(?:\+\s*OS설치)?",
        ),
    )
    if special:
        return cleanup_model_token(special)

    main_text = re.sub(r"\[[^\]]+\]", " ", text)
    candidates = re.findall(r"(?<![0-9A-Za-z])([A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*)(?![0-9A-Za-z])", main_text)
    for token in candidates:
        cleaned = cleanup_model_token(token)
        if is_model_token(cleaned):
            return cleaned

    for token in bracket_tokens:
        cleaned = cleanup_model_token(token)
        if is_model_token(cleaned):
            return cleaned

    candidates = re.findall(r"(?<![0-9A-Za-z])([A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*)(?![0-9A-Za-z])", text)
    for token in candidates:
        cleaned = cleanup_model_token(token)
        if is_model_token(cleaned):
            return cleaned

    fallback = re.sub(r"\[[^\]]+\]", "", text)
    fallback = re.sub(r"\([^)]*\)", "", fallback)
    fallback = re.sub(r"\s+\|.*$", "", fallback)
    return normalize_space(fallback)


def parse_order_status(value: str) -> str:
    text = value or ""
    for status in ("배송완료", "상품발송", "상품준비", "결제완료", "입금확인중", "주문취소"):
        if status in text:
            return status
    return ""


def parse_total_count(value: str) -> int | None:
    text = normalize_space(value)
    patterns = (
        r"조회\s*([0-9,]+)\s*건",
        r"([0-9,]+)\s*건\s*취소\s*주문\s*제외",
        r"주문내역.*?([0-9,]+)\s*건",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def clean_product_name(value: str) -> str:
    text = normalize_space(value)
    text = re.sub(r"^(?:상품명|제품명)\s*[:：]?\s*", "", text)
    text = re.sub(r"\s*(?:상세보기|상품보기|바로가기)\s*$", "", text)
    return normalize_space(text)


def is_login_required(page: Page) -> bool:
    if "login" in page.url.lower():
        return True
    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return False
    normalized = normalize_space(text)
    return "로그인이 필요합니다" in normalized or (
        "비회원 주문내역 조회" in normalized and "아이디" in normalized and "비밀번호" in normalized
    )


def fill_login_form(page: Page, username: str, password: str) -> None:
    filled = page.evaluate(
        """
        ({ username, password }) => {
          const textOf = el => [el.id, el.name, el.placeholder, el.title, el.value].join(' ').toLowerCase();
          const visible = el => {
            const style = window.getComputedStyle(el);
            const box = el.getBoundingClientRect();
            return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
          };
          const setValue = (el, value) => {
            el.focus();
            el.value = value;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
          };

          const inputs = Array.from(document.querySelectorAll('input')).filter(visible);
          const idInput = inputs.find(el => {
            const type = String(el.type || '').toLowerCase();
            if (['password', 'hidden', 'checkbox', 'radio', 'button', 'submit'].includes(type)) return false;
            const text = textOf(el);
            return text.includes('id') || text.includes('login') || text.includes('user') || text.includes('member') || text.includes('아이디');
          }) || inputs.find(el => !['password', 'hidden', 'checkbox', 'radio', 'button', 'submit'].includes(String(el.type || '').toLowerCase()));
          const pwInput = inputs.find(el => String(el.type || '').toLowerCase() === 'password');

          if (!idInput || !pwInput) {
            return false;
          }
          setValue(idInput, username);
          setValue(pwInput, password);
          return true;
        }
        """,
        {"username": username, "password": password},
    )
    if not filled:
        raise RuntimeError("컴퓨존 로그인 입력칸을 찾지 못했습니다.")


def submit_login_form(page: Page) -> None:
    clicked = page.evaluate(
        """
        () => {
          const normalize = value => String(value || '').replace(/\\s+/g, '').toLowerCase();
          if (typeof login_check === 'function') {
            login_check();
            return true;
          }
          const visible = el => {
            const style = window.getComputedStyle(el);
            const box = el.getBoundingClientRect();
            return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
          };
          const passwordInput = Array.from(document.querySelectorAll('input[type="password"]')).find(visible);
          const form = passwordInput && passwordInput.closest('form');
          const root = form || document;
          const controls = Array.from(root.querySelectorAll('button, input[type="button"], input[type="submit"], input[type="image"], a')).filter(visible);
          const target = controls.find(el => {
            const text = normalize(el.innerText || el.textContent || el.value || el.alt || el.title);
            return text.includes('로그인') || text.includes('login');
          }) || controls.find(el => String(el.type || '').toLowerCase() === 'submit');
          if (!target) {
            if (passwordInput) {
              passwordInput.focus();
            }
            if (form && typeof form.requestSubmit === 'function') {
              form.requestSubmit();
              return true;
            }
            return false;
          }
          target.click();
          return true;
        }
        """
    )
    if not clicked:
        page.keyboard.press("Enter")
    page.wait_for_timeout(5000)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30000)
    except PlaywrightTimeoutError:
        pass


def password_for_account(args: argparse.Namespace, username: str | None) -> str:
    if username:
        account_env = f"{args.password_env}_{normalize_env_suffix(username)}"
        password = os.environ.get(account_env, "")
        if password:
            return password
    password = os.environ.get(args.password_env, "")
    if password:
        return password
    return getpass.getpass("Compuzone password: ")


def login_if_needed(page: Page, args: argparse.Namespace, username: str | None = None) -> None:
    if not is_login_required(page):
        return

    username = username or args.username
    if username:
        password = password_for_account(args, username)
        page.goto(args.login_url, wait_until="domcontentloaded", timeout=60000)
        fill_login_form(page, username, password)
        submit_login_form(page)
        page.goto(args.history_url, wait_until="domcontentloaded", timeout=60000)
        if is_login_required(page):
            raise RuntimeError("컴퓨존 로그인이 완료되지 않았습니다. 브라우저에서 추가 인증 여부를 확인하세요.")
        return

    if args.headless:
        raise RuntimeError("--headless 모드에서는 --username 또는 로그인된 --profile-dir 이 필요합니다.")

    print("브라우저에서 컴퓨존 로그인을 완료한 뒤 Enter를 누르세요.")
    input()
    page.goto(args.history_url, wait_until="domcontentloaded", timeout=60000)
    if is_login_required(page):
        raise RuntimeError("컴퓨존 로그인이 확인되지 않았습니다.")


def expand_all_order_items(page: Page) -> None:
    for _ in range(5):
        try:
            clicked = page.evaluate(
                """
                () => {
                  const normalize = value => String(value || '').replace(/\\s+/g, ' ').trim();
                  const visible = el => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
                  };
                  const controls = Array.from(document.querySelectorAll('[id^="more_btn_box_"], .listMore_bottom')).filter(visible);
                  const target = controls.find(el => normalize(el.innerText || el.textContent).includes('전체보기'));
                  if (!target) {
                    return false;
                  }
                  target.click();
                  return true;
                }
                """
            )
        except Exception:
            return
        if not clicked:
            return
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass
        page.wait_for_timeout(500)


def extract_order_lines(page: Page, account: str = "") -> list[OrderLine]:
    raw_blocks: list[dict[str, Any]] = page.evaluate(
        """
        () => {
          const normalize = value => String(value || '').replace(/\\s+/g, ' ').trim();
          return Array.from(document.querySelectorAll('div.orderList_Twrap21')).map(block => {
            const blockText = normalize(block.innerText || block.textContent || '');
            const items = Array.from(block.querySelectorAll('a.myP_name[href*="ProductNo"], a.myP_name[href*="product_detail"]'))
              .map(anchor => {
                const row = anchor.closest('tr');
                const wrap = anchor.closest('.myP_NameWrap') || anchor.parentElement;
                return {
                  href: anchor.href || anchor.getAttribute('href') || '',
                  name: normalize(anchor.innerText || anchor.textContent || anchor.title || ''),
                  itemText: normalize((row && (row.innerText || row.textContent)) || (wrap && (wrap.innerText || wrap.textContent)) || ''),
                };
              });
            return {
              blockText,
              sourceUrl: location.href,
              items,
            };
          }).filter(block => block.items.length > 0);
        }
        """
    )

    lines: list[OrderLine] = []
    for block in raw_blocks:
        block_text = block.get("blockText") or ""
        order_status = parse_order_status(block_text)
        purchase_date = parse_date(block_text)
        order_no = parse_order_no(block_text)
        for item in block.get("items") or []:
            item_text = item.get("itemText") or ""
            href = item.get("href") or ""
            product_name = clean_product_name(item.get("name") or "")
            if not product_name or product_name in {"이미지", "Image"}:
                product_name = clean_product_name(first_product_name_from_context(item_text))
            if not product_name:
                continue

            product_no = parse_product_no(href) or parse_product_no(item_text) or parse_product_no(block_text)
            lines.append(
                OrderLine(
                    account=account,
                    product_name=product_name,
                    product_no=product_no,
                    purchase_date=purchase_date,
                    quantity=parse_quantity(item_text),
                    unit_price=parse_unit_price(item_text),
                    order_no=order_no,
                    order_status=order_status,
                    source_url=block.get("sourceUrl") or page.url,
                    raw_text=f"{block_text}\n{item_text}",
                )
            )
    return lines


def first_product_name_from_context(value: str) -> str:
    for line in (value or "").splitlines():
        text = clean_product_name(line)
        if not text:
            continue
        if any(skip in text for skip in ("주문번호", "주문일", "수량", "금액", "배송", "결제")):
            continue
        if parse_product_no(text) or parse_date(text):
            continue
        return text
    return ""


def extract_detail_urls(page: Page) -> list[str]:
    return page.evaluate(
        """
        () => {
          const normalize = value => String(value || '').replace(/\\s+/g, '').toLowerCase();
          const absolute = value => {
            try {
              const url = new URL(value, location.href);
              return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
            } catch {
              return '';
            }
          };
          const urls = [];
          for (const el of document.querySelectorAll('a[href], area[href]')) {
            const href = el.getAttribute('href') || '';
            const text = normalize(el.innerText || el.textContent || el.title || el.alt || href);
            const compactHref = normalize(href);
            if (compactHref.includes('product_detail')) continue;
            if (
              text.includes('상세') ||
              compactHref.includes('order_detail') ||
              compactHref.includes('order_view') ||
              compactHref.includes('order_state_no')
            ) {
              const url = absolute(href);
              if (url) urls.push(url);
            }
          }
          for (const el of document.querySelectorAll('[onclick]')) {
            const text = normalize(el.innerText || el.textContent || el.value || el.title || '');
            const onclick = el.getAttribute('onclick') || '';
            if (!text.includes('상세') && !normalize(onclick).includes('order')) continue;
            const match = onclick.match(/['"]([^'"]*(?:order_detail|order_view|order_state_no|order)[^'"]*)['"]/i);
            if (match) {
              const url = absolute(match[1]);
              if (url) urls.push(url);
            }
          }
          return Array.from(new Set(urls));
        }
        """
    )


def click_next_page(page: Page) -> bool:
    before_url = page.url
    clicked = page.evaluate(
        """
        () => {
          const normalize = value => String(value || '').replace(/\\s+/g, '').toLowerCase();
          const visible = el => {
            const style = window.getComputedStyle(el);
            const box = el.getBoundingClientRect();
            return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
          };
          const controls = Array.from(document.querySelectorAll('a, button, input[type="button"], input[type="submit"]')).filter(visible);
          const target = controls.find(el => {
            const text = normalize(el.innerText || el.textContent || el.value || el.title || el.alt);
            const disabled = el.disabled || normalize(el.className).includes('disabled');
            return !disabled && (text === '다음' || text === 'next' || text === '>' || text.includes('다음페이지'));
          });
          if (!target) return false;
          target.click();
          return true;
        }
        """
    )
    if not clicked:
        return False
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(800)
    return page.url != before_url or True


def submit_year_search(page: Page, year: int, status: str, page_num: int = 1, start_num: int = 0) -> None:
    if page_num == 1 and start_num == 0:
        page.goto(HISTORY_URL, wait_until="domcontentloaded", timeout=60000)
    elif page.locator("form#order_state_form, form[name='order_state_form']").count() == 0:
        page.goto(HISTORY_URL, wait_until="domcontentloaded", timeout=60000)

    page.locator("#SchYear").select_option(str(year), timeout=10000)
    page.locator("select[name='sch_ord_state']").select_option(status, timeout=10000)
    page.evaluate(
        """
        ({ year, status, pageNum, startNum }) => {
          const form = document.order_state_form;
          if (!form) {
            throw new Error('컴퓨존 주문내역 검색 폼을 찾지 못했습니다.');
          }
          const setValue = (el, value) => {
            if (!el) return;
            el.removeAttribute('readonly');
            el.value = value;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
          };
          setValue(form.StartDate, `${year}-01-01`);
          setValue(form.EndDate, `${year}-12-31`);
          setValue(form.SearchValue, '');
          form.SchOrderPeriod.value = '';
          form.SchOrderWeek.value = '';
          form.SchOrderToday.value = '';
          form.SchYear.value = String(year);
          form.StartDate.value = `${year}-01-01`;
          form.EndDate.value = `${year}-12-31`;
          form.sch_ord_state.value = status;
          form.PageNum.value = String(pageNum);
          form.StartNum.value = String(startNum);
          form.target = '';
          form.submit();
        }
        """,
        {"year": year, "status": status, "pageNum": page_num, "startNum": start_num},
    )
    page.wait_for_load_state("domcontentloaded", timeout=60000)
    page.wait_for_timeout(1200)


def current_total_count(page: Page) -> int | None:
    try:
        text = page.evaluate(
            """
            () => {
              const root = document.querySelector('.order_list21') || document.querySelector('#order_state_form') || document.body;
              return String(root.innerText || root.textContent || '');
            }
            """
        )
    except Exception:
        return None
    return parse_total_count(text)


def scrape_year_status(page: Page, args: argparse.Namespace, year: int, status: str, account: str = "") -> list[OrderLine]:
    submit_year_search(page, year, status, 1, 0)
    total_count = None
    for _ in range(5):
        total_count = current_total_count(page)
        if total_count is not None:
            break
        page.wait_for_timeout(500)
    if total_count is None:
        print(f"{year}년 {status} 총 건수를 읽지 못해 최대 {args.max_pages}페이지까지만 확인합니다.")
    page_count = math.ceil(total_count / ORDERS_PER_PAGE) if total_count is not None else args.max_pages
    page_count = max(1, min(page_count, args.max_pages))

    lines: list[OrderLine] = []
    seen_page_keys: set[str] = set()
    seen_order_pages: set[tuple[str, ...]] = set()
    for page_index in range(page_count):
        page_num = page_index + 1
        start_num = page_index * ORDERS_PER_PAGE
        if page_index:
            submit_year_search(page, year, status, page_num, start_num)

        page_key = f"{year}:{status}:{page_num}:{start_num}:{page.url}"
        if page_key in seen_page_keys:
            break
        seen_page_keys.add(page_key)

        print(f"{year}년 {status} 주문내역 수집 중: {page_num}/{page_count}")
        if page.locator("div.orderList_Twrap21").count() == 0:
            submit_year_search(page, year, status, page_num, start_num)
        try:
            order_ids = tuple(
                page.evaluate(
                    """
                    () => Array.from(document.querySelectorAll('div[id^="tb_list_"]'))
                      .map(el => String(el.id || '').replace(/^tb_list_/, ''))
                      .filter(Boolean)
                    """
                )
            )
        except Exception:
            page.wait_for_timeout(1000)
            submit_year_search(page, year, status, page_num, start_num)
            order_ids = tuple(
                page.evaluate(
                    """
                    () => Array.from(document.querySelectorAll('div[id^="tb_list_"]'))
                      .map(el => String(el.id || '').replace(/^tb_list_/, ''))
                      .filter(Boolean)
                    """
                )
            )
        if page_index and order_ids and order_ids in seen_order_pages:
            print(f"{year}년 {status} {page_num}페이지가 이전 페이지와 같아 중단합니다.")
            break
        if order_ids:
            seen_order_pages.add(order_ids)
        try:
            extracted = extract_order_lines(page, account)
        except Exception:
            page.wait_for_timeout(1000)
            submit_year_search(page, year, status, page_num, start_num)
            extracted = extract_order_lines(page, account)
        page_lines = [
            line
            for line in extracted
            if line.order_status == status
            and line.purchase_date is not None
            and line.purchase_date.year == year
        ]
        lines.extend(page_lines)
    return lines


def scrape_year(page: Page, args: argparse.Namespace, year: int, account: str = "") -> list[OrderLine]:
    lines: list[OrderLine] = []
    for status in args.required_statuses:
        lines.extend(scrape_year_status(page, args, year, status, account))
    return lines


def resolve_accounts(args: argparse.Namespace) -> list[str | None]:
    if args.accounts:
        return list(dict.fromkeys(args.accounts))
    if args.use_default_accounts:
        return list(DEFAULT_ACCOUNTS)
    if args.username:
        return [args.username]
    return [None]


def account_profile_dir(args: argparse.Namespace, username: str | None, multi_account: bool) -> Path:
    profile_dir = Path(args.profile_dir).resolve()
    if not multi_account or not username:
        return profile_dir
    return profile_dir / normalize_env_suffix(username).lower()


def scrape_account_history(args: argparse.Namespace, username: str | None, multi_account: bool) -> dict[int, list[OrderLine]]:
    profile_dir = account_profile_dir(args, username, multi_account)
    profile_dir.mkdir(parents=True, exist_ok=True)
    account_name = username or args.username or ""

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=args.headless,
            accept_downloads=True,
        )
        page = context.new_page()
        try:
            page.goto(args.history_url, wait_until="domcontentloaded", timeout=60000)
            login_if_needed(page, args, username)

            lines_by_year = {year: scrape_year(page, args, year, account_name) for year in args.years}

            if args.keep_browser_open and not args.headless:
                print("브라우저 확인 후 Enter를 누르면 종료합니다.")
                input()
            return lines_by_year
        finally:
            context.close()


def scrape_history(args: argparse.Namespace) -> dict[int, list[OrderLine]]:
    accounts = resolve_accounts(args)
    lines_by_year: dict[int, list[OrderLine]] = {year: [] for year in args.years}
    multi_account = len([account for account in accounts if account]) > 1
    for account in accounts:
        label = account or "manual"
        if multi_account:
            print(f"\n계정 {label} 주문내역 수집 시작")
        account_lines = scrape_account_history(args, account, multi_account)
        for year, lines in account_lines.items():
            lines_by_year.setdefault(year, []).extend(lines)
    return lines_by_year


def aggregate_lines(lines: list[OrderLine], by_account: bool = False) -> list[ProductSummary]:
    summaries: dict[str, ProductSummary] = {}
    seen_lines: set[str] = set()

    for line in lines:
        account_key = normalize_key(line.account) or "manual"
        base_key = line.product_no or normalize_key(line.product_name)
        product_key = f"{account_key}|{base_key}" if by_account else base_key
        if not product_key:
            continue

        order_key = line.order_no or "|".join(
            [
                line.account,
                line.purchase_date.isoformat() if line.purchase_date else "",
                line.product_no or normalize_key(line.product_name),
                str(line.quantity),
                normalize_key(line.raw_text[:200]),
            ]
        )
        dedupe_key = f"{product_key}|{order_key}"
        if dedupe_key in seen_lines:
            continue
        seen_lines.add(dedupe_key)

        summary = summaries.get(product_key)
        if summary is None:
            summary = ProductSummary(
                account=line.account if by_account else "",
                product_name=line.product_name,
                product_no=line.product_no,
            )
            summaries[product_key] = summary

        if line.account:
            summary.accounts.add(line.account)
        if not summary.product_no and line.product_no:
            summary.product_no = line.product_no
        if len(line.product_name) > len(summary.product_name):
            summary.product_name = line.product_name
        if line.purchase_date and (
            summary.last_purchase_date is None or line.purchase_date > summary.last_purchase_date
        ):
            summary.last_purchase_date = line.purchase_date
        summary.purchase_quantity += line.quantity
        if line.unit_price is not None:
            summary.price_amount_total += line.unit_price * line.quantity
            summary.price_quantity += line.quantity
        summary.order_keys.add(order_key)

    return sorted(
        summaries.values(),
        key=lambda item: (item.last_purchase_date or date.min, item.product_name),
        reverse=True,
    )


def summary_to_row(summary: ProductSummary, normalizer: ProductNameNormalizer | None = None) -> dict[str, str | int]:
    normalized = normalizer.normalize(summary) if normalizer else {
        "item_name": classify_item_name(summary.product_name),
        "model_name": extract_model_name(summary.product_name),
    }
    return {
        "계정": summary.account_label,
        "품목명": normalize_space(str(normalized.get("item_name") or "")),
        "제품명": normalize_space(str(normalized.get("model_name") or "")),
        "원문": summary.product_name,
        "해당 제품 마지막 구매일자": summary.last_purchase_date.isoformat() if summary.last_purchase_date else "",
        "구매횟수": summary.purchase_count,
        "구매수량": summary.purchase_quantity,
        "상품번호": summary.product_no or "",
        "상품URL": product_url(summary.product_no),
        "평균단가": summary.average_unit_price or "",
    }


def write_csv(path: Path, summaries: list[ProductSummary], normalizer: ProductNameNormalizer | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for summary in summaries:
            writer.writerow(summary_to_row(summary, normalizer))


def write_json(path: Path, summaries: list[ProductSummary], normalizer: ProductNameNormalizer | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [summary_to_row(summary, normalizer) for summary in summaries]
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def appdata_output_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home()
    return base / "compuzone_purchase_history.xlsx"


def appdata_json_output_path() -> Path:
    return appdata_output_path().with_suffix(".json")


def appdata_normalization_db_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home()
    return base / "compuzone_product_normalization_db.json"


def parse_int(value: Any) -> int:
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


def summary_from_row(row: dict[str, Any]) -> ProductSummary | None:
    product_name = normalize_space(str(row.get("원문") or row.get("제품명") or ""))
    if not product_name:
        return None

    account = normalize_space(str(row.get("계정") or ""))
    product_no = normalize_space(str(row.get("상품번호") or "")) or None
    summary = ProductSummary(account=account, product_name=product_name, product_no=product_no)
    summary.accounts = {part.strip() for part in account.split(",") if part.strip()}
    summary.last_purchase_date = parse_date(str(row.get("해당 제품 마지막 구매일자") or ""))
    summary.purchase_quantity = parse_int(row.get("구매수량"))
    average_unit_price = parse_int(row.get("평균단가"))
    if average_unit_price and summary.purchase_quantity:
        summary.price_amount_total = average_unit_price * summary.purchase_quantity
        summary.price_quantity = summary.purchase_quantity
    purchase_count = parse_int(row.get("구매횟수"))
    summary.order_keys = {f"existing-{index}" for index in range(purchase_count)}
    return summary


def load_yearly_summaries_from_json(path: Path) -> dict[int, list[ProductSummary]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}

    yearly: dict[int, list[ProductSummary]] = {}
    for sheet_name, rows in payload.items():
        if not str(sheet_name).isdigit() or not isinstance(rows, list):
            continue
        summaries = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            summary = summary_from_row(row)
            if summary is not None:
                summaries.append(summary)
        yearly[int(sheet_name)] = summaries
    return yearly


def product_summary_key(summary: ProductSummary) -> str:
    return summary.product_no or normalize_key(summary.product_name)


def account_product_summary_key(summary: ProductSummary) -> str:
    account_key = normalize_key(summary.account_label) or "manual"
    return f"{account_key}|{product_summary_key(summary)}"


def merge_existing_summaries(summaries: list[ProductSummary], by_account: bool = False) -> list[ProductSummary]:
    grouped: dict[str, ProductSummary] = {}
    for source_index, summary in enumerate(summaries):
        key = account_product_summary_key(summary) if by_account else product_summary_key(summary)
        if not key:
            continue
        target = grouped.get(key)
        if target is None:
            target = ProductSummary(
                account=summary.account if by_account else "",
                product_name=summary.product_name,
                product_no=summary.product_no,
            )
            grouped[key] = target

        target.accounts.update(summary.accounts)
        if summary.account:
            target.accounts.add(summary.account)
        if not target.product_no and summary.product_no:
            target.product_no = summary.product_no
        if len(summary.product_name) > len(target.product_name):
            target.product_name = summary.product_name
        if summary.last_purchase_date and (
            target.last_purchase_date is None or summary.last_purchase_date > target.last_purchase_date
        ):
            target.last_purchase_date = summary.last_purchase_date
        target.purchase_quantity += summary.purchase_quantity
        target.price_amount_total += summary.price_amount_total
        target.price_quantity += summary.price_quantity
        for index in range(summary.purchase_count):
            target.order_keys.add(f"{source_index}:{key}:{index}")

    return sorted(
        grouped.values(),
        key=lambda item: (item.last_purchase_date or date.min, item.product_name),
        reverse=True,
    )


def combine_yearly_summaries(summaries_by_year: dict[int, list[ProductSummary]]) -> list[ProductSummary]:
    combined: dict[str, ProductSummary] = {}
    for year, summaries in summaries_by_year.items():
        for summary in summaries:
            key = product_summary_key(summary)
            if not key:
                continue

            target = combined.get(key)
            if target is None:
                target = ProductSummary(
                    account=summary.account,
                    product_name=summary.product_name,
                    product_no=summary.product_no,
                )
                combined[key] = target

            if not target.account and summary.account:
                target.account = summary.account
            target.accounts.update(summary.accounts)
            if summary.account:
                target.accounts.add(summary.account)
            if not target.product_no and summary.product_no:
                target.product_no = summary.product_no
            if len(summary.product_name) > len(target.product_name):
                target.product_name = summary.product_name
            if summary.last_purchase_date and (
                target.last_purchase_date is None or summary.last_purchase_date > target.last_purchase_date
            ):
                target.last_purchase_date = summary.last_purchase_date

            target.purchase_quantity += summary.purchase_quantity
            target.price_amount_total += summary.price_amount_total
            target.price_quantity += summary.price_quantity
            for index in range(summary.purchase_count):
                target.order_keys.add(f"{year}:{key}:{index}")

    return sorted(
        combined.values(),
        key=lambda item: (item.last_purchase_date or date.min, item.product_name),
        reverse=True,
    )


def output_year_order(requested_years: list[int], summaries_by_year: dict[int, list[ProductSummary]]) -> list[int]:
    ordered: list[int] = []
    for year in requested_years:
        if year not in ordered:
            ordered.append(year)
    for year in sorted(summaries_by_year, reverse=True):
        if year not in ordered:
            ordered.append(year)
    return ordered


def write_json_sheets(
    path: Path,
    all_summaries: list[ProductSummary],
    summaries_by_year: dict[int, list[ProductSummary]],
    output_years: list[int],
    normalizer: ProductNameNormalizer | None = None,
    extra_sheets: dict[str, list[ProductSummary]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"종합": [summary_to_row(summary, normalizer) for summary in all_summaries]}
    for name, summaries in (extra_sheets or {}).items():
        payload[name] = [summary_to_row(summary, normalizer) for summary in summaries]
    for year in output_years:
        payload[str(year)] = [summary_to_row(summary, normalizer) for summary in summaries_by_year.get(year, [])]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def cell_xml(row_index: int, col_index: int, value: str | int | None, style: int = 0) -> str:
    ref = f"{column_name(col_index)}{row_index}"
    style_attr = f' s="{style}"' if style else ""
    if value is None:
        return f'<c r="{ref}"{style_attr}/>'
    if isinstance(value, int):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    text = xml_escape(str(value))
    preserve = ' xml:space="preserve"' if str(value).strip() != str(value) else ""
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t{preserve}>{text}</t></is></c>'


def xml_attr_escape(value: str) -> str:
    return xml_escape(str(value), {'"': "&quot;", "'": "&apos;"})


def hyperlink_entries(rows: list[list[str | int | None]]) -> list[tuple[str, str, str]]:
    try:
        url_col_index = CSV_FIELDS.index(URL_FIELD) + 1
    except ValueError:
        return []

    entries: list[tuple[str, str, str]] = []
    for row_index, row in enumerate(rows[1:], start=2):
        if len(row) < url_col_index:
            continue
        url = normalize_space(str(row[url_col_index - 1] or ""))
        if not url.lower().startswith(("http://", "https://")):
            continue
        entries.append((f"{column_name(url_col_index)}{row_index}", f"rId{len(entries) + 1}", url))
    return entries


def sheet_xml(rows: list[list[str | int | None]]) -> str:
    row_xml = []
    try:
        url_col_index = CSV_FIELDS.index(URL_FIELD) + 1
    except ValueError:
        url_col_index = 0
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            style = 1 if row_index == 1 else 0
            if row_index > 1 and col_index == url_col_index and value:
                style = 2
            cells.append(cell_xml(row_index, col_index, value, style=style))
        cells = "".join(cells)
        row_xml.append(f'<row r="{row_index}">{cells}</row>')
    last_row = max(1, len(rows))
    hyperlinks = "".join(
        f'<hyperlink ref="{ref}" r:id="{relationship_id}"/>'
        for ref, relationship_id, _ in hyperlink_entries(rows)
    )
    hyperlinks_xml = f"<hyperlinks>{hyperlinks}</hyperlinks>" if hyperlinks else ""
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <cols>
    <col min="1" max="1" width="14" customWidth="1"/>
    <col min="2" max="2" width="16" customWidth="1"/>
    <col min="3" max="3" width="24" customWidth="1"/>
    <col min="4" max="4" width="72" customWidth="1"/>
    <col min="5" max="5" width="18" customWidth="1"/>
    <col min="6" max="7" width="12" customWidth="1"/>
    <col min="8" max="8" width="14" customWidth="1"/>
    <col min="9" max="9" width="72" customWidth="1"/>
    <col min="10" max="10" width="14" customWidth="1"/>
  </cols>
  <sheetData>{''.join(row_xml)}</sheetData>
  <autoFilter ref="A1:J{last_row}"/>
  {hyperlinks_xml}
</worksheet>'''


def sheet_hyperlink_rels(rows: list[list[str | int | None]]) -> str:
    entries = hyperlink_entries(rows)
    if not entries:
        return ""
    relationships = "".join(
        f'<Relationship Id="{relationship_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="{xml_attr_escape(url)}" TargetMode="External"/>'
        for _, relationship_id, url in entries
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{relationships}</Relationships>'''


def workbook_xml(sheet_names: list[str]) -> str:
    sheets = "".join(
        f'<sheet name="{xml_escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>{sheets}</sheets>
</workbook>'''


def workbook_rels(sheet_names: list[str]) -> str:
    relationships = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index, _ in enumerate(sheet_names, start=1)
    )
    relationships += f'<Relationship Id="rId{len(sheet_names) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{relationships}</Relationships>'''


def content_types(sheet_names: list[str]) -> str:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index, _ in enumerate(sheet_names, start=1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  {overrides}
</Types>'''


def styles_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="3"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font><font><u/><color rgb="FF0563C1"/><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def rows_for_summaries(
    summaries: list[ProductSummary],
    normalizer: ProductNameNormalizer | None = None,
) -> list[list[str | int | None]]:
    rows: list[list[str | int | None]] = [CSV_FIELDS]
    for summary in summaries:
        row = summary_to_row(summary, normalizer)
        rows.append([row[field] for field in CSV_FIELDS])
    return rows


def write_xlsx(
    path: Path,
    sheets: dict[str, list[ProductSummary]],
    normalizer: ProductNameNormalizer | None = None,
) -> Path:
    sheet_names = list(sheets)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", content_types(sheet_names))
            archive.writestr(
                "_rels/.rels",
                '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''',
            )
            archive.writestr("xl/workbook.xml", workbook_xml(sheet_names))
            archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels(sheet_names))
            archive.writestr("xl/styles.xml", styles_xml())
            for index, name in enumerate(sheet_names, start=1):
                rows = rows_for_summaries(sheets[name], normalizer)
                archive.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml(rows))
                rels = sheet_hyperlink_rels(rows)
                if rels:
                    archive.writestr(f"xl/worksheets/_rels/sheet{index}.xml.rels", rels)
    except PermissionError:
        fallback = appdata_output_path()
        if path.resolve() == fallback.resolve():
            raise
        return write_xlsx(fallback, sheets, normalizer)
    return path


def product_uid(product_no: str | None, product_name: str) -> str:
    product_no = normalize_space(product_no or "")
    if product_no:
        return f"product_no:{product_no}"
    key = normalize_key(product_name)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]
    return f"name:{digest}"


def product_uid_for_summary(summary: ProductSummary) -> str:
    return product_uid(summary.product_no, summary.product_name)


def product_uid_for_line(line: OrderLine) -> str:
    return product_uid(line.product_no, line.product_name)


def line_uid(line: OrderLine) -> str:
    payload = {
        "account": line.account,
        "order_no": line.order_no or "",
        "product_uid": product_uid_for_line(line),
        "purchase_date": line.purchase_date.isoformat() if line.purchase_date else "",
        "order_status": line.order_status,
        "quantity": line.quantity,
        "unit_price": line.unit_price,
        "product_name": normalize_key(line.product_name),
        "raw_text_hash": hashlib.sha256((line.raw_text or "").encode("utf-8")).hexdigest(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def db_password(args: argparse.Namespace) -> str:
    password = os.environ.get(args.db_password_env, "")
    if password:
        return password
    if args.db_password_prompt:
        return getpass.getpass("MariaDB password: ")
    raise RuntimeError(f"{args.db_password_env} 환경변수가 없습니다.")


def write_database(
    args: argparse.Namespace,
    summaries: list[ProductSummary],
    lines: list[OrderLine],
    normalizer: ProductNameNormalizer,
) -> dict[str, int]:
    try:
        import pymysql
    except ImportError as error:
        raise RuntimeError("pymysql 패키지가 필요합니다. requirements.txt 설치를 확인하세요.") from error

    started_at = datetime.now()
    password = db_password(args)
    connection = pymysql.connect(
        host=args.db_host,
        port=args.db_port,
        user=args.db_user,
        password=password,
        database=args.db_name,
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=args.db_connect_timeout,
    )
    try:
        with connection.cursor() as cursor:
            for summary in summaries:
                normalized = normalizer.normalize(summary)
                cursor.execute(
                    """
                    INSERT INTO compuzone_products (
                      product_uid, product_no, item_name, model_name, raw_name, product_url,
                      normalization_source, normalization_model, normalization_confidence,
                      desktop_split_version, desktop_split_basis
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      product_no = VALUES(product_no),
                      item_name = IF(normalization_locked = 1, item_name, VALUES(item_name)),
                      model_name = IF(normalization_locked = 1, model_name, VALUES(model_name)),
                      raw_name = VALUES(raw_name),
                      product_url = VALUES(product_url),
                      normalization_source = IF(normalization_locked = 1, normalization_source, VALUES(normalization_source)),
                      normalization_model = IF(normalization_locked = 1, normalization_model, VALUES(normalization_model)),
                      normalization_confidence = IF(normalization_locked = 1, normalization_confidence, VALUES(normalization_confidence)),
                      desktop_split_version = IF(normalization_locked = 1, desktop_split_version, VALUES(desktop_split_version)),
                      desktop_split_basis = IF(normalization_locked = 1, desktop_split_basis, VALUES(desktop_split_basis))
                    """,
                    (
                        product_uid_for_summary(summary),
                        summary.product_no or None,
                        normalize_space(str(normalized.get("item_name") or "")),
                        normalize_space(str(normalized.get("model_name") or "")),
                        summary.product_name,
                        product_url(summary.product_no) or None,
                        normalized.get("source"),
                        normalized.get("model"),
                        normalized.get("confidence"),
                        normalized.get("desktop_split_version"),
                        normalized.get("desktop_split_basis"),
                    ),
                )

            for line in lines:
                cursor.execute(
                    """
                    INSERT INTO compuzone_order_lines (
                      line_uid, account, order_no, product_uid, purchase_date, order_status,
                      quantity, unit_price, source_url, raw_text
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      account = VALUES(account),
                      order_no = VALUES(order_no),
                      product_uid = VALUES(product_uid),
                      purchase_date = VALUES(purchase_date),
                      order_status = VALUES(order_status),
                      quantity = VALUES(quantity),
                      unit_price = VALUES(unit_price),
                      source_url = VALUES(source_url),
                      raw_text = VALUES(raw_text)
                    """,
                    (
                        line_uid(line),
                        line.account or "manual",
                        line.order_no,
                        product_uid_for_line(line),
                        line.purchase_date,
                        line.order_status,
                        line.quantity,
                        line.unit_price,
                        line.source_url or None,
                        line.raw_text,
                    ),
                )

            cursor.execute(
                """
                INSERT INTO compuzone_sync_runs (
                  started_at, finished_at, status, accounts, years, line_count, product_count, message
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    started_at,
                    datetime.now(),
                    "SUCCESS",
                    ", ".join(sorted({line.account for line in lines if line.account})),
                    ", ".join(str(year) for year in args.years),
                    len(lines),
                    len(summaries),
                    "compuzone_history_parser db upsert",
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {"products": len(summaries), "order_lines": len(lines)}


def print_table(summaries: list[ProductSummary], limit: int = 20) -> None:
    rows = [summary_to_row(summary) for summary in summaries[:limit]]
    if not rows:
        print("수집된 구매 상품이 없습니다.")
        return
    widths = {
        field: max(len(field), *(len(str(row[field])) for row in rows))
        for field in CSV_FIELDS
    }
    print(" | ".join(field.ljust(widths[field]) for field in CSV_FIELDS))
    print("-+-".join("-" * widths[field] for field in CSV_FIELDS))
    for row in rows:
        print(" | ".join(str(row[field]).ljust(widths[field]) for field in CSV_FIELDS))


def write_outputs(
    args: argparse.Namespace,
    lines_by_year: dict[int, list[OrderLine]],
    preserve_existing_years: bool = False,
) -> tuple[Path, list[int], dict[int, list[ProductSummary]], list[ProductSummary], int]:
    requested_years = list(args.years)
    summaries_by_year: dict[int, list[ProductSummary]] = {}

    if preserve_existing_years and args.json_output:
        summaries_by_year.update(load_yearly_summaries_from_json(Path(args.json_output)))

    for year in requested_years:
        summaries_by_year[year] = aggregate_lines(lines_by_year.get(year, []), by_account=False)

    output_years = output_year_order(requested_years, summaries_by_year)
    all_summaries = combine_yearly_summaries(
        {year: summaries_by_year.get(year, []) for year in output_years}
    )
    all_lines = [line for year in requested_years for line in lines_by_year.get(year, [])]
    account_summaries = aggregate_lines(all_lines, by_account=True)

    output_path = Path(args.output)
    normalization_db = Path(args.normalization_db) if args.normalization_db else None
    normalizer = ProductNameNormalizer(
        normalization_db,
        use_openai=args.normalize_with_openai,
        openai_model=args.openai_model,
        openai_api_key_env=args.openai_api_key_env,
        openai_timeout=args.openai_timeout,
        openai_threshold=args.openai_normalize_threshold,
        use_gemini=args.normalize_with_gemini,
        gemini_model=args.gemini_model,
        gemini_api_key_env=args.gemini_api_key_env,
        gemini_timeout=args.gemini_timeout,
        gemini_threshold=args.gemini_normalize_threshold,
        gemini_batch_size=args.gemini_batch_size,
    )
    sheets: dict[str, list[ProductSummary]] = {"종합": all_summaries, "계정별": account_summaries}
    for year in output_years:
        sheets[str(year)] = summaries_by_year.get(year, [])
    normalizer.prepare([summary for summaries in sheets.values() for summary in summaries])
    saved_path = write_xlsx(output_path, sheets, normalizer)

    if args.db_write:
        args.db_write_result = write_database(args, all_summaries, all_lines, normalizer)

    if args.csv_output:
        write_csv(Path(args.csv_output), all_summaries, normalizer)
    if args.json_output:
        write_json_sheets(
            Path(args.json_output),
            all_summaries,
            summaries_by_year,
            output_years,
            normalizer,
            extra_sheets={"계정별": account_summaries},
        )

    normalizer.save()

    scraped_line_count = sum(len(lines_by_year.get(year, [])) for year in requested_years)
    return saved_path, output_years, summaries_by_year, all_summaries, scraped_line_count


def print_run_summary(
    args: argparse.Namespace,
    lines_by_year: dict[int, list[OrderLine]],
    saved_path: Path,
    output_years: list[int],
    summaries_by_year: dict[int, list[ProductSummary]],
    all_summaries: list[ProductSummary],
    scraped_line_count: int,
) -> None:
    print_table(all_summaries)
    requested_years = set(args.years)
    for year in args.years:
        print(f"{year}년 대상 상태 라인: {len(lines_by_year.get(year, []))}건 / 집계 상품: {len(summaries_by_year.get(year, []))}건")
    for year in output_years:
        if year not in requested_years:
            print(f"{year}년 기존 JSON 보존 상품: {len(summaries_by_year.get(year, []))}건")
    print(f"\n대상 주문상태: {', '.join(args.required_statuses)}")
    print(f"이번 수집 대상 상태 라인: {scraped_line_count}건")
    print(f"종합 집계 상품: {len(all_summaries)}건")
    db_result = getattr(args, "db_write_result", None)
    if db_result:
        print(f"DB 저장: 상품 {db_result['products']}건 / 주문라인 {db_result['order_lines']}건")
    print(f"XLSX 저장: {saved_path.resolve()}")
    if args.csv_output:
        print(f"CSV 저장: {Path(args.csv_output).resolve()}")
    if args.json_output:
        print(f"JSON 저장: {Path(args.json_output).resolve()}")


def run_once(args: argparse.Namespace, preserve_existing_years: bool = False) -> Path:
    lines_by_year = scrape_history(args)
    saved_path, output_years, summaries_by_year, all_summaries, scraped_line_count = write_outputs(
        args,
        lines_by_year,
        preserve_existing_years=preserve_existing_years,
    )
    print_run_summary(
        args,
        lines_by_year,
        saved_path,
        output_years,
        summaries_by_year,
        all_summaries,
        scraped_line_count,
    )
    return saved_path


def run_current_year_watch(args: argparse.Namespace) -> None:
    if args.watch_interval_hours <= 0:
        raise ValueError("--watch-interval-hours 는 0보다 커야 합니다.")

    current_year = date.today().year
    args.years = [current_year]
    if not args.json_output:
        args.json_output = str(appdata_json_output_path())
    if args.keep_browser_open:
        print("--keep-browser-open 은 감시 모드에서 해제합니다.")
        args.keep_browser_open = False

    sleep_seconds = int(args.watch_interval_hours * 60 * 60)
    run_count = 0
    while args.watch_runs == 0 or run_count < args.watch_runs:
        run_count += 1
        print(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {current_year}년 주문내역 갱신 시작")
        try:
            run_once(args, preserve_existing_years=True)
        except KeyboardInterrupt:
            raise
        except Exception as error:
            print(f"주문내역 갱신 실패: {error}")

        if args.watch_runs and run_count >= args.watch_runs:
            break

        next_run = datetime.now() + timedelta(seconds=sleep_seconds)
        print(f"다음 갱신 예정: {next_run:%Y-%m-%d %H:%M:%S}")
        time.sleep(sleep_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="컴퓨존 주문내역을 제품별 구매 이력으로 집계합니다.")
    parser.add_argument("--username", help="컴퓨존 아이디. 비밀번호는 저장하지 않고 실행 중 입력받습니다.")
    parser.add_argument("--accounts", nargs="+", help="여러 컴퓨존 아이디를 순서대로 수집")
    parser.add_argument(
        "--use-default-accounts",
        action="store_true",
        help=f"기본 계정 목록({', '.join(DEFAULT_ACCOUNTS)})을 모두 수집",
    )
    parser.add_argument("--password-env", default="COMPUZONE_PASSWORD", help="비밀번호를 읽을 환경변수 이름")
    parser.add_argument("--history-url", default=HISTORY_URL, help="컴퓨존 주문내역 URL")
    parser.add_argument("--login-url", default=LOGIN_URL, help="컴퓨존 로그인 URL")
    parser.add_argument("--profile-dir", default=".profiles/compuzone-history", help="브라우저 프로필 저장 폴더")
    parser.add_argument("--output", default=str(appdata_output_path()), help="XLSX 저장 경로")
    parser.add_argument("--json-output", help="JSON 저장 경로")
    parser.add_argument("--csv-output", help="종합 CSV도 함께 저장할 경로")
    parser.add_argument(
        "--normalization-db",
        default=str(appdata_normalization_db_path()),
        help="품목명/제품명 정규화 학습DB JSON 경로",
    )
    parser.add_argument("--normalize-with-openai", action="store_true", help="정규화 DB에 없는 애매한 상품명을 OpenAI API로 보정")
    parser.add_argument("--openai-model", default="gpt-5-nano", help="상품명 정규화에 사용할 OpenAI 모델")
    parser.add_argument("--openai-api-key-env", default="OPENAI_API_KEY", help="OpenAI API 키를 읽을 환경변수 이름")
    parser.add_argument("--openai-timeout", type=int, default=30, help="OpenAI API 호출 제한 시간(초)")
    parser.add_argument(
        "--openai-normalize-threshold",
        type=float,
        default=0.8,
        help="룰 신뢰도가 이 값보다 낮을 때만 OpenAI API 사용",
    )
    parser.add_argument("--normalize-with-gemini", action="store_true", help="정규화 DB에 없는 애매한 상품명을 Gemini API로 보정")
    parser.add_argument("--gemini-model", default="gemini-2.5-flash-lite", help="상품명 정규화에 사용할 Gemini 모델")
    parser.add_argument("--gemini-api-key-env", default="GEMINI_API_KEY", help="Gemini API 키를 읽을 환경변수 이름")
    parser.add_argument("--gemini-timeout", type=int, default=60, help="Gemini API 호출 제한 시간(초)")
    parser.add_argument(
        "--gemini-normalize-threshold",
        type=float,
        default=0.8,
        help="룰 신뢰도가 이 값보다 낮을 때만 Gemini API 사용",
    )
    parser.add_argument("--gemini-batch-size", type=int, default=40, help="Gemini 정규화 1회 호출당 상품 수")
    parser.add_argument("--years", type=int, nargs="+", default=list(YEARS), help="수집할 주문 연도")
    parser.add_argument("--required-statuses", nargs="+", default=list(TARGET_STATUSES), help="집계할 주문상태 목록")
    parser.add_argument("--max-pages", type=int, default=300, help="연도별 주문내역 최대 수집 페이지 수")
    parser.add_argument("--max-detail-pages", type=int, default=200, help="상세보기 페이지 최대 수집 수")
    parser.add_argument("--no-detail-pages", action="store_true", help="상세보기 페이지를 열지 않고 목록 페이지만 파싱")
    parser.add_argument("--headless", action="store_true", help="브라우저 창 없이 실행")
    parser.add_argument("--keep-browser-open", action="store_true", help="종료 전에 브라우저 확인 대기")
    parser.add_argument("--watch-current-year", action="store_true", help="현재 연도 주문내역만 주기적으로 갱신")
    parser.add_argument("--watch-interval-hours", type=float, default=2.0, help="감시 모드 갱신 주기(시간)")
    parser.add_argument("--watch-runs", type=int, default=0, help="감시 모드 실행 횟수. 0이면 계속 실행")
    parser.add_argument("--db-write", action="store_true", help="수집 결과를 MariaDB 테이블에 upsert")
    parser.add_argument("--db-host", default="172.16.19.35", help="MariaDB host")
    parser.add_argument("--db-port", type=int, default=3306, help="MariaDB port")
    parser.add_argument("--db-name", default="warehouse_pos", help="MariaDB database/schema name")
    parser.add_argument("--db-user", default="root", help="MariaDB user")
    parser.add_argument("--db-password-env", default="COMPUZONE_DB_PASSWORD", help="MariaDB 비밀번호 환경변수")
    parser.add_argument("--db-password-prompt", action="store_true", help="MariaDB 비밀번호를 실행 중 입력")
    parser.add_argument("--db-connect-timeout", type=int, default=10, help="MariaDB 연결 제한 시간(초)")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.scan_details = not args.no_detail_pages

    if args.watch_current_year:
        run_current_year_watch(args)
        return

    run_once(args)


if __name__ == "__main__":
    main()
