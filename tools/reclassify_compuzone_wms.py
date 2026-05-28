from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compuzone_history_parser import (  # noqa: E402
    DESKTOP_SPLIT_VERSION,
    NORMALIZATION_RULE_VERSION,
    ProductNameNormalizer,
    ProductSummary,
    extract_desktop_model_name,
    has_dedicated_graphics,
    is_desktop_pc_text,
    normalize_space,
)


BACKUP_TABLES = (
    "compuzone_products",
    "compuzone_order_lines",
    "compuzone_wms_product_map",
    "compuzone_wms_category_rules",
    "categories",
    "products",
    "item_codes",
)

EXCLUDE_STOCK_ITEMS: set[str] = set()
SOFTWARE_ASSET_ITEMS = {"오피스", "운영체제"}
EXPENSE_ITEMS = {
    "소프트웨어이용료",
    "유지보수료",
    "클라우드서비스",
    "호스팅",
    "보안관제",
    "그룹웨어이용료",
    "조립/설치서비스",
}
SOFTWARE_ASSET_PATH = "전산 > 컴퓨터소프트웨어"
EXPENSE_BASE_PATH = "전산 > 비용"
FIXTURE_BASE_PATH = "전산 > 집기비품"
CONSUMABLE_BASE_PATH = "전산 > 소모품"


@dataclass(frozen=True)
class CategorySuggestion:
    path: str | None
    stock_policy: str
    status: str
    confidence: float
    reason: str


def db_password(args: argparse.Namespace) -> str:
    password = os.environ.get(args.db_password_env, "")
    if password:
        return password
    if args.db_password_prompt:
        return getpass.getpass("MariaDB password: ")
    raise RuntimeError(f"{args.db_password_env} 환경변수가 없습니다.")


def connect(args: argparse.Namespace):
    import pymysql

    return pymysql.connect(
        host=args.db_host,
        port=args.db_port,
        user=args.db_user,
        password=db_password(args),
        database=args.db_name,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


def table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (table_name,),
    )
    return cursor.fetchone() is not None


def column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (table_name, column_name),
    )
    return cursor.fetchone() is not None


def backup_tables(cursor, suffix: str) -> list[str]:
    created: list[str] = []
    for table in BACKUP_TABLES:
        if not table_exists(cursor, table):
            continue
        backup_name = f"{table}_bak_{suffix}"
        cursor.execute(f"CREATE TABLE `{backup_name}` LIKE `{table}`")
        cursor.execute(f"INSERT INTO `{backup_name}` SELECT * FROM `{table}`")
        created.append(backup_name)
    return created


def ensure_schema(cursor) -> None:
    if not column_exists(cursor, "compuzone_products", "normalization_locked"):
        cursor.execute(
            """
            ALTER TABLE compuzone_products
            ADD COLUMN normalization_locked TINYINT(1) NOT NULL DEFAULT 0
            AFTER normalization_confidence
            """
        )
    if not column_exists(cursor, "compuzone_products", "normalization_lock_reason"):
        cursor.execute(
            """
            ALTER TABLE compuzone_products
            ADD COLUMN normalization_lock_reason VARCHAR(160) NULL
            AFTER normalization_locked
            """
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS compuzone_wms_category_rules (
          rule_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
          rule_name VARCHAR(120) NOT NULL,
          match_item_name VARCHAR(80) NULL,
          match_pattern VARCHAR(300) NULL,
          target_category_id INT NULL,
          target_category_path VARCHAR(500) NOT NULL,
          stock_policy VARCHAR(24) NOT NULL DEFAULT 'stock',
          priority INT NOT NULL DEFAULT 100,
          is_active TINYINT(1) NOT NULL DEFAULT 1,
          notes VARCHAR(500) NULL,
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (rule_id),
          UNIQUE KEY ux_compuzone_wms_category_rules_name (rule_name),
          KEY ix_compuzone_wms_category_rules_item (match_item_name),
          KEY ix_compuzone_wms_category_rules_target (target_category_id),
          KEY ix_compuzone_wms_category_rules_active_priority (is_active, priority)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS compuzone_wms_product_map (
          product_uid VARCHAR(96) NOT NULL,
          product_no VARCHAR(32) NULL,
          suggested_wms_product_id INT NULL,
          target_category_id INT NULL,
          target_category_path VARCHAR(500) NULL,
          stock_policy VARCHAR(24) NOT NULL DEFAULT 'stock',
          mapping_status VARCHAR(32) NOT NULL DEFAULT 'review_required',
          mapping_confidence DECIMAL(4,3) NULL,
          mapping_reason VARCHAR(500) NULL,
          mapping_source VARCHAR(40) NOT NULL DEFAULT 'rules',
          reviewed_by VARCHAR(80) NULL,
          reviewed_at DATETIME NULL,
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (product_uid),
          KEY ix_compuzone_wms_product_map_product_no (product_no),
          KEY ix_compuzone_wms_product_map_wms_product (suggested_wms_product_id),
          KEY ix_compuzone_wms_product_map_category (target_category_id),
          KEY ix_compuzone_wms_product_map_status (mapping_status, stock_policy),
          CONSTRAINT fk_compuzone_wms_product_map_product
            FOREIGN KEY (product_uid)
            REFERENCES compuzone_products (product_uid)
            ON UPDATE CASCADE
            ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def extract_bracket_model(raw: str) -> str | None:
    for token in re.findall(r"\[([^\]]+)\]", raw):
        cleaned = normalize_space(token.strip("[]{}|,./:; "))
        compact = re.sub(r"[^0-9A-Za-z-]+", "", cleaned)
        if re.fullmatch(r"[A-Z]{2,}[A-Z0-9]*-[A-Z0-9-]+", compact):
            return compact
    return None


def apply_corrections(raw: str, normalized: dict[str, Any]) -> dict[str, Any]:
    text = f" {normalize_space(raw).lower()} "
    entry = dict(normalized)
    item_name = normalize_space(str(entry.get("item_name") or "기타"))
    model_name = normalize_space(str(entry.get("model_name") or ""))

    if "microsoft 365" in text or "creative cloud" in text or "saas" in text or "구독" in text:
        entry["item_name"] = "소프트웨어이용료"

    if "유지보수" in text or "업데이트 비용" in text or "기술지원" in text:
        entry["item_name"] = "유지보수료"

    if "클라우드" in text or "cloud" in text:
        entry["item_name"] = "클라우드서비스"

    if "호스팅" in text or "hosting" in text:
        entry["item_name"] = "호스팅"

    if "보안관제" in text:
        entry["item_name"] = "보안관제"

    if "그룹웨어 이용료" in text:
        entry["item_name"] = "그룹웨어이용료"

    if "tv 거치대" in text or "tv스탠드" in text or "티비거치대" in text:
        entry["item_name"] = "TV거치대"

    if "windows 11" in text or "win11" in text or ("마이크로소프트" in text and "os설치비포함" in text):
        entry["item_name"] = "운영체제"

    if "조립비" in text or "하드웨어조립" in text or re.search(r"\bos\s*설치비\b", text):
        entry["item_name"] = "조립/설치서비스"

    if (
        ("displayport" in text or " hdmi" in text or "rgb(vga)" in text or " vga" in text)
        and ("케이블" in text or "변환" in text or "광점퍼" in text)
    ):
        entry["item_name"] = "케이블"

    is_sfp_module = (
        "sfp 광 모듈" in text
        or "sfp+ 모듈" in text
        or "미니지빅" in text
        or re.search(r"\bsfp\+?\s*모듈", text) is not None
    )
    if is_sfp_module:
        entry["item_name"] = "SFP모듈"
        bracket_model = extract_bracket_model(raw)
        if bracket_model:
            entry["model_name"] = bracket_model

    if ("nuc-" in text or "미니pc" in text) and "ram" in text and "win11" in text:
        entry["item_name"] = "미니PC"

    if "프로데스크" in raw:
        hp_code = re.search(r"\b(C\d{2}[A-Z0-9]{4})\b", raw, flags=re.IGNORECASE)
        if hp_code:
            entry["model_name"] = hp_code.group(1).upper()

    if is_desktop_pc_text(raw):
        entry["item_name"] = "CAD PC" if has_dedicated_graphics(raw) else "사무용 PC"
        desktop_model = extract_desktop_model_name(raw)
        if desktop_model:
            entry["model_name"] = desktop_model
        entry["desktop_split_version"] = DESKTOP_SPLIT_VERSION
        entry["desktop_split_basis"] = "dedicated_gpu" if has_dedicated_graphics(raw) else "no_dedicated_gpu"

    model_name = normalize_space(str(entry.get("model_name") or model_name))
    if re.fullmatch(r"\d+\s*(?:km|m|cm|mm|hz|포트)", model_name, flags=re.IGNORECASE):
        bracket_model = extract_bracket_model(raw)
        if bracket_model:
            entry["model_name"] = bracket_model

    entry["item_name"] = normalize_space(str(entry.get("item_name") or item_name or "기타"))
    entry["model_name"] = normalize_space(str(entry.get("model_name") or model_name or raw))
    entry["source"] = "rules_v6"
    entry["rule_version"] = NORMALIZATION_RULE_VERSION
    entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
    if entry["item_name"] != "기타" and entry["model_name"] != normalize_space(raw):
        entry["confidence"] = max(float(entry.get("confidence") or 0), 0.92)
    return entry


def monitor_inch(raw: str) -> str:
    text = normalize_space(raw)
    if "24TOUCH75" in text.upper() or re.search(r"\b24(?:형|인치|\\\")", text):
        return "24인치"
    if "27FD" in text.upper() or "27MR" in text.upper() or re.search(r"\b27(?:형|인치|\\\")", text):
        return "27인치"
    if "S32" in text.upper() or re.search(r"\b32(?:형|인치|\\\")", text):
        return "32인치"
    match = re.search(r"(\d{2,3})(?:cm|형|인치)", text, flags=re.IGNORECASE)
    if match:
        number = int(match.group(1))
        if number >= 100:
            inch = round(number / 2.54)
            return f"{inch}인치"
        return f"{number}인치"
    return "기타 인치"


def network_switch_path(raw: str) -> str:
    text = raw.lower()
    if "48" in text:
        return f"{FIXTURE_BASE_PATH} > 네트워크장비 > 스위치 허브 > 48포트 SFP" if "sfp" in text else f"{FIXTURE_BASE_PATH} > 네트워크장비 > 스위치 허브 > 48포트"
    if "24" in text:
        return f"{FIXTURE_BASE_PATH} > 네트워크장비 > 스위치 허브 > 24포트"
    if "8포트" in text or "h8008" in text:
        return f"{FIXTURE_BASE_PATH} > 네트워크장비 > 스위치 허브 > 8포트"
    return f"{FIXTURE_BASE_PATH} > 네트워크장비 > 스위치 허브"


def usb_hub_path(raw: str) -> str:
    text = raw.lower()
    has_lan = "lan" in text or "rj" in text or "유선랜" in text
    is_c = "c타입" in text or "type-c" in text or "c-lan" in text or "tc" in text
    if is_c and has_lan:
        return "전산 > 소모품 > 네트워크 > USB 허브 > USB C-LAN 허브"
    if is_c:
        return "전산 > 소모품 > 네트워크 > USB 허브 > USB C-허브"
    if has_lan:
        return "전산 > 소모품 > 네트워크 > USB 허브 > USB A-LAN 허브"
    return "전산 > 소모품 > 네트워크 > USB 허브 > USB A-허브"


def cable_path(raw: str) -> str:
    text = raw.lower()
    if "c to c" in text or "c-c" in text or "usb c-c" in text:
        return "전산 > 소모품 > 케이블 > C to C"
    if "usb" in text and "연장" in text:
        return "전산 > 소모품 > 케이블 > USB C-C연장 케이블" if "c" in text else "전산 > 소모품 > 케이블 > USB 연장 케이블"
    if "dp" in text and "dvi" in text:
        return "전산 > 소모품 > 케이블 > DP to DVI"
    if ("displayport" in text or "dp" in text) and "hdmi" in text:
        return "전산 > 소모품 > 케이블 > HDMI to DP"
    if "hdmi" in text and ("rgb" in text or "vga" in text):
        return "전산 > 소모품 > 케이블 > RGB to HDMI"
    if "rgb" in text and ("dvi" in text):
        return "전산 > 소모품 > 케이블 > RGB to DVI"
    if "rgb" in text and ("dp" in text or "displayport" in text):
        return "전산 > 소모품 > 케이블 > RGB to DP"
    if "hdmi" in text and "dvi" in text:
        return "전산 > 소모품 > 케이블 > HDMI-DVI"
    if "hdmi" in text:
        return "전산 > 소모품 > 케이블 > HDMI-HDMI"
    if "rgb" in text or "vga" in text:
        return "전산 > 소모품 > 케이블 > RGB-RGB"
    return "전산 > 소모품 > 케이블"


def suggest_category(item_name: str, model_name: str, raw: str) -> CategorySuggestion:
    text = f" {normalize_space(raw).lower()} "
    if item_name in EXCLUDE_STOCK_ITEMS:
        return CategorySuggestion(None, "exclude", "ignored", 1.0, "창고 재고가 아닌 서비스 항목")
    if item_name in EXPENSE_ITEMS:
        return CategorySuggestion(f"{EXPENSE_BASE_PATH} > {item_name}", "expense", "expense", 0.95, "비용성 서비스/구독 항목")
    if item_name in SOFTWARE_ASSET_ITEMS:
        return CategorySuggestion(SOFTWARE_ASSET_PATH, "non_stock", "non_stock", 0.95, "컴퓨터소프트웨어 무형자산 항목")

    if item_name == "사무용 PC":
        return CategorySuggestion(f"{FIXTURE_BASE_PATH} > 컴퓨터 > PC > 일반용", "stock", "new_candidate", 0.95, "전용 그래픽카드 없음")
    if item_name == "CAD PC":
        return CategorySuggestion(f"{FIXTURE_BASE_PATH} > 컴퓨터 > PC > 캐드용", "stock", "new_candidate", 0.95, "전용 그래픽카드 감지")
    if item_name == "미니PC":
        return CategorySuggestion(f"{FIXTURE_BASE_PATH} > 컴퓨터 > PC > 미니PC", "stock", "new_candidate", 0.94, "미니 PC 본체")
    if item_name == "노트북":
        return CategorySuggestion(f"{FIXTURE_BASE_PATH} > 컴퓨터 > 노트북", "stock", "new_candidate", 0.92, "노트북 본체")
    if item_name == "모니터":
        inch = monitor_inch(raw)
        return CategorySuggestion(f"{FIXTURE_BASE_PATH} > 모니터 > {inch}", "stock", "new_candidate", 0.92, "모니터 인치별 분류")
    if item_name == "TV":
        return CategorySuggestion(f"{FIXTURE_BASE_PATH} > 영상장비 > TV", "stock", "new_candidate", 0.9, "TV는 모니터와 분리")
    if item_name in {"프로젝터", "영상장비"}:
        return CategorySuggestion(f"{FIXTURE_BASE_PATH} > 영상장비 > {item_name}", "stock", "new_candidate", 0.88, "영상장비")
    if item_name == "프린터/복합기":
        return CategorySuggestion(f"{FIXTURE_BASE_PATH} > 프린터/복합기", "stock", "new_candidate", 0.9, "전산 집기비품")

    if item_name in {"메모리", "SSD", "HDD", "CPU", "그래픽카드", "메인보드", "파워서플라이", "케이스", "CPU쿨러", "시스템쿨러"}:
        return CategorySuggestion(f"{CONSUMABLE_BASE_PATH} > 부품 > {item_name}", "stock", "new_candidate", 0.9, "PC 소모성 부품")
    if item_name == "네트워크허브":
        return CategorySuggestion(network_switch_path(raw), "stock", "new_candidate", 0.9, "스위치 허브 포트 기준")
    if item_name == "SFP모듈":
        if "멀티" in raw or "sx" in model_name.lower() or "mm" in raw.lower():
            return CategorySuggestion(f"{CONSUMABLE_BASE_PATH} > 네트워크 > 모듈 > 멀티 모듈", "stock", "new_candidate", 0.9, "SFP 멀티모드")
        return CategorySuggestion(f"{CONSUMABLE_BASE_PATH} > 네트워크 > 모듈 > 싱글 모듈", "stock", "new_candidate", 0.9, "SFP 싱글모드")
    if item_name == "USB허브":
        return CategorySuggestion(usb_hub_path(raw), "stock", "new_candidate", 0.9, "USB 허브 타입 기준")
    if item_name in {"무선AP", "KVM"}:
        return CategorySuggestion(f"{FIXTURE_BASE_PATH} > 네트워크장비 > {item_name}", "stock", "new_candidate", 0.88, "장기간 사용하는 네트워크 장비")
    if item_name in {"랜카드", "블루투스동글", "무선수신기"}:
        return CategorySuggestion(f"{CONSUMABLE_BASE_PATH} > 네트워크 > {item_name}", "stock", "new_candidate", 0.85, "네트워크 연결 소모품")
    if item_name == "랜커넥터":
        return CategorySuggestion(f"{CONSUMABLE_BASE_PATH} > 네트워크 > 랜커넥터", "stock", "new_candidate", 0.85, "랜 커넥터")
    if item_name in {"케이블", "젠더"}:
        return CategorySuggestion(cable_path(raw) if item_name == "케이블" else f"{CONSUMABLE_BASE_PATH} > 케이블 > 젠더", "stock", "new_candidate", 0.88, "케이블/젠더")
    if item_name in {"건전지", "아답터", "충전기", "멀티탭"}:
        return CategorySuggestion(f"{CONSUMABLE_BASE_PATH} > 전원 > {item_name}", "stock", "new_candidate", 0.88, "전원 소모품")
    if item_name == "충전기/거치대":
        return CategorySuggestion("전산 > 소모품 > 전원 > 충전기/거치대", "stock", "new_candidate", 0.84, "충전/거치 소모품")
    if item_name == "TV거치대":
        return CategorySuggestion(f"{FIXTURE_BASE_PATH} > 영상장비 > TV거치대", "stock", "new_candidate", 0.9, "장기간 사용하는 TV 부속 장비")
    if item_name in {"마우스", "키보드", "키보드/마우스세트"}:
        if item_name == "마우스":
            path = "전산 > 소모품 > 입력장치 > 무선 > 마우스" if "무선" in text or "블루투스" in text else "전산 > 소모품 > 입력장치 > 유선 > 마우스"
        elif item_name == "키보드/마우스세트":
            path = "전산 > 소모품 > 입력장치 > 무선 > 키보드&마우스"
        else:
            path = "전산 > 소모품 > 입력장치 > 유선 > 일반키보드"
        return CategorySuggestion(path, "stock", "new_candidate", 0.86, "입력장치")
    if item_name in {"마이크", "스피커", "웹캠", "모니터암", "모니터받침대"}:
        return CategorySuggestion(f"{FIXTURE_BASE_PATH} > 회의용장비 > {item_name}", "stock", "new_candidate", 0.86, "장기간 사용하는 전산/회의 장비")
    if item_name in {"헤드셋", "이어폰", "마우스패드", "액정보호필름", "노트북가방"}:
        existing = {
            "헤드셋": "전산 > 소모품 > 기타 > 헤드셋",
            "마우스패드": "전산 > 소모품 > 기타 > 마우스패드",
        }
        return CategorySuggestion(existing.get(item_name, f"전산 > 소모품 > 기타 > {item_name}"), "stock", "new_candidate", 0.84, "전산 주변 소모품")
    if item_name in {"공구", "테스터기", "랙선반", "보관함", "라벨용지", "테이프"}:
        return CategorySuggestion(f"전산 > 소모품 > 기타 > {item_name}", "stock", "new_candidate", 0.8, "관리 소모품")

    return CategorySuggestion(None, "review", "review_required", 0.3, "자동 매핑 불확실")


def load_category_paths(cursor) -> dict[str, int]:
    cursor.execute(
        """
        WITH RECURSIVE cat AS (
          SELECT id, name, parentId, level, CAST(name AS CHAR(500)) AS path
          FROM categories
          WHERE parentId IS NULL AND isActive = 1
          UNION ALL
          SELECT c.id, c.name, c.parentId, c.level, CONCAT(cat.path, ' > ', c.name)
          FROM categories c
          JOIN cat ON c.parentId = cat.id
          WHERE c.isActive = 1
        )
        SELECT id, path FROM cat
        """
    )
    return {row["path"]: row["id"] for row in cursor.fetchall()}


def ensure_category_path(cursor, path: str, path_map: dict[str, int]) -> int:
    parts = [part.strip() for part in path.split(">")]
    if len(parts) > 5:
        raise RuntimeError(f"WMS 카테고리는 최대 5단계까지만 가능합니다: {path}")
    current_path = ""
    parent_id = None
    parent_color = None
    for level, name in enumerate(parts, start=1):
        current_path = name if not current_path else f"{current_path} > {name}"
        if current_path in path_map:
            parent_id = path_map[current_path]
            cursor.execute("SELECT color FROM categories WHERE id=%s", (parent_id,))
            row = cursor.fetchone()
            parent_color = row["color"] if row else parent_color
            continue

        cursor.execute("SELECT COUNT(*) AS cnt FROM categories WHERE parentId <=> %s", (parent_id,))
        sort_order = int(cursor.fetchone()["cnt"]) + 1
        cursor.execute(
            """
            INSERT INTO categories (name, level, parentId, isActive, color, sortOrder, safetyStock, createdAt, updatedAt)
            VALUES (%s, %s, %s, 1, %s, %s, 0, NOW(), NOW())
            """,
            (name, level, parent_id, parent_color, sort_order),
        )
        parent_id = cursor.lastrowid
        path_map[current_path] = parent_id
    return int(parent_id)


def seed_category_rules(cursor, path_map: dict[str, int]) -> None:
    rules = [
        ("expense_setup_service", "조립/설치서비스", None, f"{EXPENSE_BASE_PATH} > 조립/설치서비스", "expense", 10, "설치/조립 비용"),
        ("expense_subscription", "소프트웨어이용료", None, f"{EXPENSE_BASE_PATH} > 소프트웨어이용료", "expense", 15, "구독형 소프트웨어 비용"),
        ("expense_maintenance", "유지보수료", None, f"{EXPENSE_BASE_PATH} > 유지보수료", "expense", 15, "유지보수/업데이트/기술지원 비용"),
        ("expense_cloud", "클라우드서비스", None, f"{EXPENSE_BASE_PATH} > 클라우드서비스", "expense", 15, "클라우드 사용료"),
        ("expense_hosting", "호스팅", None, f"{EXPENSE_BASE_PATH} > 호스팅", "expense", 15, "호스팅 비용"),
        ("expense_security", "보안관제", None, f"{EXPENSE_BASE_PATH} > 보안관제", "expense", 15, "보안관제 비용"),
        ("expense_groupware", "그룹웨어이용료", None, f"{EXPENSE_BASE_PATH} > 그룹웨어이용료", "expense", 15, "그룹웨어 이용료"),
        ("non_stock_office", "오피스", None, SOFTWARE_ASSET_PATH, "non_stock", 20, "컴퓨터소프트웨어 라이선스"),
        ("non_stock_os", "운영체제", None, SOFTWARE_ASSET_PATH, "non_stock", 20, "OS 라이선스"),
        ("tv_mount_fixture", "TV거치대", None, f"{FIXTURE_BASE_PATH} > 영상장비 > TV거치대", "stock", 30, "장기간 사용하는 TV 부속 장비"),
        ("office_pc", "사무용 PC", None, f"{FIXTURE_BASE_PATH} > 컴퓨터 > PC > 일반용", "stock", 40, "전용 GPU 없음"),
        ("cad_pc", "CAD PC", None, f"{FIXTURE_BASE_PATH} > 컴퓨터 > PC > 캐드용", "stock", 40, "전용 GPU 있음"),
        ("mini_pc", "미니PC", None, f"{FIXTURE_BASE_PATH} > 컴퓨터 > PC > 미니PC", "stock", 45, "미니PC"),
        ("network_switch", "네트워크허브", None, f"{FIXTURE_BASE_PATH} > 네트워크장비 > 스위치 허브", "stock", 45, "장기간 사용하는 네트워크 장비"),
        ("wireless_ap", "무선AP", None, f"{FIXTURE_BASE_PATH} > 네트워크장비 > 무선AP", "stock", 45, "장기간 사용하는 네트워크 장비"),
        ("kvm", "KVM", None, f"{FIXTURE_BASE_PATH} > 네트워크장비 > KVM", "stock", 45, "장기간 사용하는 네트워크 장비"),
        ("meeting_mic", "마이크", None, f"{FIXTURE_BASE_PATH} > 회의용장비 > 마이크", "stock", 45, "장기간 사용하는 전산/회의 장비"),
        ("meeting_webcam", "웹캠", None, f"{FIXTURE_BASE_PATH} > 회의용장비 > 웹캠", "stock", 45, "장기간 사용하는 전산/회의 장비"),
        ("meeting_speaker", "스피커", None, f"{FIXTURE_BASE_PATH} > 회의용장비 > 스피커", "stock", 45, "장기간 사용하는 전산/회의 장비"),
        ("monitor_24", "모니터", "24", f"{FIXTURE_BASE_PATH} > 모니터 > 24인치", "stock", 50, "모니터 인치"),
        ("monitor_27", "모니터", "27", f"{FIXTURE_BASE_PATH} > 모니터 > 27인치", "stock", 50, "모니터 인치"),
        ("monitor_32", "모니터", "32", f"{FIXTURE_BASE_PATH} > 모니터 > 32인치", "stock", 50, "모니터 인치"),
    ]
    for rule_name, item_name, pattern, path, policy, priority, notes in rules:
        target_id = path_map.get(path) if path else None
        cursor.execute(
            """
            INSERT INTO compuzone_wms_category_rules (
              rule_name, match_item_name, match_pattern, target_category_id,
              target_category_path, stock_policy, priority, is_active, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s)
            ON DUPLICATE KEY UPDATE
              match_item_name = VALUES(match_item_name),
              match_pattern = VALUES(match_pattern),
              target_category_id = VALUES(target_category_id),
              target_category_path = VALUES(target_category_path),
              stock_policy = VALUES(stock_policy),
              priority = VALUES(priority),
              is_active = 1,
              notes = VALUES(notes)
            """,
            (rule_name, item_name, pattern, target_id, path or "", policy, priority, notes),
        )


def load_wms_products(cursor) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT p.id, p.productName, p.specification, p.categoryId,
               LOWER(TRIM(COALESCE(p.specification, ''))) AS spec_key,
               LOWER(TRIM(COALESCE(p.productName, ''))) AS name_key
        FROM products p
        WHERE p.isActive = 1
        """
    )
    return cursor.fetchall()


def find_wms_match(wms_products: list[dict[str, Any]], item_name: str, model_name: str, target_category_id: int | None) -> tuple[int | None, str, float]:
    model_key = normalize_space(model_name).lower()
    item_key = normalize_space(item_name).lower()
    if not model_key or model_key in {"기타", "1.5m", "2m", "10km"}:
        return None, "모델명이 일반 규격이라 자동 매칭 제외", 0.0

    spec_matches = [p for p in wms_products if p["spec_key"] == model_key]
    if target_category_id:
        same_category = [p for p in spec_matches if int(p["categoryId"] or 0) == int(target_category_id)]
        if len(same_category) == 1:
            return same_category[0]["id"], "WMS 규격+카테고리 일치", 0.96
    if len(spec_matches) == 1:
        return spec_matches[0]["id"], "WMS 규격 단일 일치", 0.9

    name_spec_matches = [
        p for p in wms_products
        if p["name_key"] == item_key and (p["spec_key"] == model_key or not p["spec_key"])
    ]
    if len(name_spec_matches) == 1 and model_key not in {"마우스", "키보드", "케이블"}:
        return name_spec_matches[0]["id"], "WMS 품목명/규격 후보 단일", 0.82

    return None, "기존 WMS 품목 자동 매칭 없음", 0.0


def update_reclassification(cursor, ensure_categories: bool, force_locked_normalization: bool) -> dict[str, int]:
    cursor.execute(
        """
        SELECT product_uid, product_no, item_name, model_name, raw_name,
               normalization_locked, normalization_lock_reason
        FROM compuzone_products
        ORDER BY product_uid
        """
    )
    rows = cursor.fetchall()
    normalizer = ProductNameNormalizer(path=None, use_gemini=False, use_openai=False)
    path_map = load_category_paths(cursor)
    wms_products = load_wms_products(cursor)

    counts = {"total": 0, "updated": 0, "locked_skip": 0, "map_rows": 0, "categories_created": 0}
    for row in rows:
        counts["total"] += 1
        raw = row["raw_name"]
        summary = ProductSummary(account="", product_name=raw, product_no=row["product_no"])
        normalized = apply_corrections(raw, normalizer.rule_entry(summary))
        suggestion = suggest_category(normalized["item_name"], normalized["model_name"], raw)

        target_category_id = None
        if suggestion.path:
            before_len = len(path_map)
            if ensure_categories:
                target_category_id = ensure_category_path(cursor, suggestion.path, path_map)
            else:
                target_category_id = path_map.get(suggestion.path)
            counts["categories_created"] += max(0, len(path_map) - before_len)

        suggested_product_id, match_reason, match_confidence = find_wms_match(
            wms_products,
            normalized["item_name"],
            normalized["model_name"],
            target_category_id,
        )
        mapping_status = suggestion.status
        mapping_confidence = suggestion.confidence
        mapping_reason = suggestion.reason
        if suggested_product_id:
            mapping_status = "auto_matched"
            mapping_confidence = max(mapping_confidence, match_confidence)
            mapping_reason = f"{suggestion.reason}; {match_reason}"

        locked = int(row.get("normalization_locked") or 0) == 1
        should_lock = normalized["item_name"] != "기타" and normalized["model_name"] != normalize_space(raw)
        if locked and not force_locked_normalization:
            counts["locked_skip"] += 1
        else:
            cursor.execute(
                """
                UPDATE compuzone_products
                SET item_name = %s,
                    model_name = %s,
                    normalization_source = %s,
                    normalization_model = NULL,
                    normalization_confidence = %s,
                    normalization_locked = %s,
                    normalization_lock_reason = %s,
                    desktop_split_version = %s,
                    desktop_split_basis = %s
                WHERE product_uid = %s
                """,
                (
                    normalized["item_name"],
                    normalized["model_name"],
                    normalized["source"],
                    normalized.get("confidence"),
                    1 if should_lock else 0,
                    "rules_v6_verified" if should_lock else None,
                    normalized.get("desktop_split_version"),
                    normalized.get("desktop_split_basis"),
                    row["product_uid"],
                ),
            )
            counts["updated"] += cursor.rowcount

        cursor.execute(
            """
            INSERT INTO compuzone_wms_product_map (
              product_uid, product_no, suggested_wms_product_id, target_category_id,
              target_category_path, stock_policy, mapping_status, mapping_confidence,
              mapping_reason, mapping_source
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'rules_v6')
            ON DUPLICATE KEY UPDATE
              product_no = VALUES(product_no),
              suggested_wms_product_id = IF(reviewed_at IS NULL, VALUES(suggested_wms_product_id), suggested_wms_product_id),
              target_category_id = IF(reviewed_at IS NULL, VALUES(target_category_id), target_category_id),
              target_category_path = IF(reviewed_at IS NULL, VALUES(target_category_path), target_category_path),
              stock_policy = IF(reviewed_at IS NULL, VALUES(stock_policy), stock_policy),
              mapping_status = IF(reviewed_at IS NULL, VALUES(mapping_status), mapping_status),
              mapping_confidence = IF(reviewed_at IS NULL, VALUES(mapping_confidence), mapping_confidence),
              mapping_reason = IF(reviewed_at IS NULL, VALUES(mapping_reason), mapping_reason),
              mapping_source = IF(reviewed_at IS NULL, VALUES(mapping_source), mapping_source)
            """,
            (
                row["product_uid"],
                row["product_no"],
                suggested_product_id,
                target_category_id,
                suggestion.path,
                suggestion.stock_policy,
                mapping_status,
                mapping_confidence,
                mapping_reason,
            ),
        )
        counts["map_rows"] += 1

    seed_category_rules(cursor, path_map)
    return counts


def print_summary(cursor, backup_names: list[str], counts: dict[str, int]) -> None:
    print("backup_tables=" + (", ".join(backup_names) if backup_names else "none"))
    print("reclassify_counts=" + ", ".join(f"{k}:{v}" for k, v in counts.items()))

    cursor.execute(
        """
        SELECT stock_policy, mapping_status, COUNT(*) AS cnt
        FROM compuzone_wms_product_map
        GROUP BY stock_policy, mapping_status
        ORDER BY stock_policy, mapping_status
        """
    )
    print("--- mapping status ---")
    for row in cursor.fetchall():
        print(f"{row['stock_policy']} / {row['mapping_status']} = {row['cnt']}")

    cursor.execute(
        """
        SELECT p.product_no, p.item_name, p.model_name, m.target_category_path, m.stock_policy, m.mapping_status, LEFT(p.raw_name, 120) AS raw_name
        FROM compuzone_products p
        JOIN compuzone_wms_product_map m ON m.product_uid = p.product_uid
        WHERE p.product_no IN ('1033845', '1195294', '1303277')
           OR p.item_name IN ('TV거치대', '조립/설치서비스')
        ORDER BY p.product_no
        LIMIT 30
        """
    )
    print("--- verification samples ---")
    for row in cursor.fetchall():
        print(
            f"{row['product_no']} | {row['item_name']} | {row['model_name']} | "
            f"{row['stock_policy']} | {row['mapping_status']} | {row['target_category_path']} | {row['raw_name']}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="컴퓨존 구매품 WMS 매핑/재분류 유지보수")
    parser.add_argument("--db-host", default="172.16.19.35")
    parser.add_argument("--db-port", type=int, default=3306)
    parser.add_argument("--db-name", default="warehouse_pos")
    parser.add_argument("--db-user", default="root")
    parser.add_argument("--db-password-env", default="COMPUZONE_DB_PASSWORD")
    parser.add_argument("--db-password-prompt", action="store_true")
    parser.add_argument("--skip-backup", action="store_true", help="테이블 백업 생략")
    parser.add_argument("--no-ensure-categories", action="store_true", help="누락 WMS 카테고리를 자동 생성하지 않음")
    parser.add_argument("--force-locked-normalization", action="store_true", help="잠긴 정규화 값도 이번 규칙으로 재계산")
    parser.add_argument("--dry-run", action="store_true", help="작업 후 rollback")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    connection = connect(args)
    backup_names: list[str] = []
    counts: dict[str, int] = {}
    try:
        with connection.cursor() as cursor:
            if not args.skip_backup:
                backup_names = backup_tables(cursor, suffix)
            ensure_schema(cursor)
            counts = update_reclassification(
                cursor,
                ensure_categories=not args.no_ensure_categories,
                force_locked_normalization=args.force_locked_normalization,
            )
            print_summary(cursor, backup_names, counts)
        if args.dry_run:
            connection.rollback()
            print("dry_run=rollback")
        else:
            connection.commit()
            print("commit=ok")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
