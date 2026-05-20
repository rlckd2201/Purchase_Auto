from __future__ import annotations

import argparse
import getpass
import os
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any


BACKUP_TABLES = (
    "products",
    "item_codes",
    "product_warehouse_stocks",
    "compuzone_wms_product_map",
)


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


def max_numeric_suffix(cursor, table: str, column: str, prefix: str) -> int:
    cursor.execute(
        f"""
        SELECT `{column}` AS value
        FROM `{table}`
        WHERE `{column}` REGEXP %s
        ORDER BY CAST(SUBSTRING(`{column}`, %s) AS UNSIGNED) DESC
        LIMIT 1
        """,
        (f"^{re.escape(prefix)}[0-9]+$", len(prefix) + 1),
    )
    row = cursor.fetchone()
    if not row or not row["value"]:
        return 0
    return int(str(row["value"])[len(prefix) :])


def next_item_codes(cursor, count: int) -> list[str]:
    start = max_numeric_suffix(cursor, "products", "productCode", "ITM-") + 1
    return [f"ITM-{value:06d}" for value in range(start, start + count)]


def next_barcodes(cursor, count: int) -> list[str]:
    start = max_numeric_suffix(cursor, "item_codes", "codeValue", "W") + 1
    values: list[str] = []
    number = start
    reserved = {"W99998", "W99999"}
    while len(values) < count:
        barcode = f"W{number:05d}"
        number += 1
        if barcode in reserved:
            continue
        values.append(barcode)
    return values


def decimal_to_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, Decimal):
        return int(round(float(value)))
    return int(round(float(value)))


def load_candidates(cursor) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT
          p.product_uid,
          p.product_no,
          p.item_name,
          p.model_name,
          p.raw_name,
          m.target_category_id,
          m.target_category_path,
          COALESCE(SUM(l.quantity), 0) AS purchase_quantity,
          ROUND(
            SUM(CASE WHEN l.unit_price IS NOT NULL THEN l.unit_price * l.quantity ELSE 0 END)
            / NULLIF(SUM(CASE WHEN l.unit_price IS NOT NULL THEN l.quantity ELSE 0 END), 0)
          ) AS average_unit_price
        FROM compuzone_products p
        JOIN compuzone_wms_product_map m ON m.product_uid = p.product_uid
        LEFT JOIN compuzone_order_lines l ON l.product_uid = p.product_uid
        WHERE m.stock_policy = 'stock'
          AND m.mapping_status = 'new_candidate'
          AND m.suggested_wms_product_id IS NULL
          AND m.target_category_id IS NOT NULL
        GROUP BY
          p.product_uid, p.product_no, p.item_name, p.model_name, p.raw_name,
          m.target_category_id, m.target_category_path
        ORDER BY m.target_category_path, p.item_name, p.model_name, p.product_no
        """
    )
    return cursor.fetchall()


def load_warehouses(cursor) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, warehouseName, deptId
        FROM warehouses
        WHERE isActive = 1 AND deptId = 22
        ORDER BY id
        """
    )
    return cursor.fetchall()


def load_category_safety(cursor, category_ids: set[int]) -> dict[int, int]:
    if not category_ids:
        return {}
    placeholders = ",".join(["%s"] * len(category_ids))
    cursor.execute(
        f"SELECT id, safetyStock FROM categories WHERE id IN ({placeholders})",
        tuple(category_ids),
    )
    return {int(row["id"]): int(row["safetyStock"] or 0) for row in cursor.fetchall()}


def load_category_warehouse_safety(cursor, category_ids: set[int]) -> dict[tuple[int, int], int]:
    if not category_ids:
        return {}
    placeholders = ",".join(["%s"] * len(category_ids))
    cursor.execute(
        f"""
        SELECT categoryId, warehouseId, safetyStock
        FROM category_warehouse_stocks
        WHERE categoryId IN ({placeholders})
        """,
        tuple(category_ids),
    )
    return {
        (int(row["categoryId"]), int(row["warehouseId"])): int(row["safetyStock"] or 0)
        for row in cursor.fetchall()
    }


def existing_product_id(cursor, item_name: str, model_name: str, category_id: int) -> int | None:
    cursor.execute(
        """
        SELECT id
        FROM products
        WHERE isActive = 1
          AND productName = %s
          AND COALESCE(specification, '') = %s
          AND categoryId = %s
          AND unit = '개'
        ORDER BY id
        LIMIT 1
        """,
        (item_name, model_name or "", category_id),
    )
    row = cursor.fetchone()
    return int(row["id"]) if row else None


def existing_code(cursor, code_value: str) -> bool:
    cursor.execute("SELECT 1 FROM item_codes WHERE codeValue = %s LIMIT 1", (code_value,))
    return cursor.fetchone() is not None


def insert_item_code(cursor, product_id: int, code_type: str, code_value: str, notes: str | None = None) -> None:
    if not code_value or existing_code(cursor, code_value):
        return
    cursor.execute(
        """
        INSERT INTO item_codes (itemId, codeType, codeValue, supplierId, notes, createdAt, updatedAt)
        VALUES (%s, %s, %s, NULL, %s, NOW(), NOW())
        """,
        (product_id, code_type, code_value, notes),
    )


def ensure_warehouse_rows(
    cursor,
    product_id: int,
    category_id: int,
    warehouses: list[dict[str, Any]],
    category_safety: dict[int, int],
    category_warehouse_safety: dict[tuple[int, int], int],
) -> None:
    for warehouse in warehouses:
        warehouse_id = int(warehouse["id"])
        safety_stock = category_warehouse_safety.get((category_id, warehouse_id), category_safety.get(category_id, 0))
        cursor.execute(
            """
            INSERT INTO product_warehouse_stocks (
              productId, warehouseId, currentStock, safetyStock,
              safetyStockMode, manualSafetyStock, autoSafetyStock,
              leadTimeDays, serviceLevel, zValue,
              createdAt, updatedAt
            )
            SELECT %s, %s, 0, %s, 'manual', %s, 0, 3, 95.00, 1.650, NOW(), NOW()
            WHERE NOT EXISTS (
              SELECT 1
              FROM product_warehouse_stocks
              WHERE productId = %s AND warehouseId = %s
            )
            """,
            (product_id, warehouse_id, safety_stock, safety_stock, product_id, warehouse_id),
        )


def group_candidates(candidates: list[dict[str, Any]]) -> dict[tuple[int, str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        key = (
            int(row["target_category_id"]),
            str(row["item_name"]).strip(),
            str(row["model_name"] or "").strip(),
        )
        grouped[key].append(row)
    return grouped


def import_products(cursor) -> dict[str, int]:
    candidates = load_candidates(cursor)
    grouped = group_candidates(candidates)
    warehouses = load_warehouses(cursor)
    category_ids = {int(row["target_category_id"]) for row in candidates}
    category_safety = load_category_safety(cursor, category_ids)
    category_warehouse_safety = load_category_warehouse_safety(cursor, category_ids)
    product_codes = iter(next_item_codes(cursor, len(grouped)))
    barcodes = iter(next_barcodes(cursor, len(grouped)))

    counts = {
        "candidate_rows": len(candidates),
        "product_groups": len(grouped),
        "created_products": 0,
        "reused_products": 0,
        "vendor_codes": 0,
        "barcode_codes": 0,
        "warehouse_rows": 0,
        "mapped_rows": 0,
    }

    for (category_id, item_name, model_name), rows in grouped.items():
        product_id = existing_product_id(cursor, item_name, model_name, category_id)
        if product_id:
            counts["reused_products"] += 1
        else:
            product_code = next(product_codes)
            barcode = next(barcodes)
            total_qty = sum(decimal_to_int(row["purchase_quantity"]) for row in rows)
            if total_qty > 0:
                weighted_amount = sum(decimal_to_int(row["average_unit_price"]) * decimal_to_int(row["purchase_quantity"]) for row in rows)
                unit_price = round(weighted_amount / total_qty)
            else:
                unit_price = max(decimal_to_int(row["average_unit_price"]) for row in rows)
            safety_stock = category_safety.get(category_id, 0)
            notes = "컴퓨존 구매이력 기반 자동 생성"
            cursor.execute(
                """
                INSERT INTO products (
                  productCode, productName, category, unit, unitPrice,
                  currentStock, safetyStock, warehouseId, description,
                  isActive, createdAt, updatedAt, categoryId, barcode,
                  isDraft, specification, notes
                )
                VALUES (%s, %s, 'office', '개', %s, 0, %s, NULL, NULL, 1, NOW(), NOW(), %s, %s, 0, %s, %s)
                """,
                (product_code, item_name, unit_price, safety_stock, category_id, barcode, model_name or None, notes),
            )
            product_id = int(cursor.lastrowid)
            counts["created_products"] += 1
            insert_item_code(cursor, product_id, "barcode", barcode, "WMS 자동 생성 바코드")
            counts["barcode_codes"] += 1

        before_rows = cursor.rowcount
        ensure_warehouse_rows(cursor, product_id, category_id, warehouses, category_safety, category_warehouse_safety)
        counts["warehouse_rows"] += max(0, cursor.rowcount if cursor.rowcount >= 0 else before_rows)

        for row in rows:
            if row["product_no"]:
                before = cursor.rowcount
                insert_item_code(cursor, product_id, "vendor", f"COMPUZONE:{row['product_no']}", row["raw_name"][:250])
                if cursor.rowcount != before and cursor.rowcount > 0:
                    counts["vendor_codes"] += 1
            cursor.execute(
                """
                UPDATE compuzone_wms_product_map
                SET suggested_wms_product_id = %s,
                    mapping_status = 'imported',
                    mapping_confidence = GREATEST(COALESCE(mapping_confidence, 0), 0.950),
                    mapping_reason = CONCAT(COALESCE(mapping_reason, ''), '; WMS 품목 생성/연결 완료'),
                    mapping_source = 'import_products'
                WHERE product_uid = %s
                  AND reviewed_at IS NULL
                """,
                (product_id, row["product_uid"]),
            )
            counts["mapped_rows"] += cursor.rowcount

    return counts


def print_summary(cursor, backup_names: list[str], counts: dict[str, int]) -> None:
    print("backup_tables=" + (", ".join(backup_names) if backup_names else "none"))
    print("import_counts=" + ", ".join(f"{key}:{value}" for key, value in counts.items()))
    cursor.execute(
        """
        SELECT mapping_status, stock_policy, COUNT(*) cnt
        FROM compuzone_wms_product_map
        GROUP BY mapping_status, stock_policy
        ORDER BY stock_policy, mapping_status
        """
    )
    print("--- mapping status ---")
    for row in cursor.fetchall():
        print(f"{row['stock_policy']} / {row['mapping_status']} = {row['cnt']}")

    cursor.execute(
        """
        SELECT p.id, p.productCode, p.productName, p.specification, p.categoryId, p.barcode
        FROM products p
        WHERE p.notes = '컴퓨존 구매이력 기반 자동 생성'
        ORDER BY p.id DESC
        LIMIT 20
        """
    )
    print("--- created product samples ---")
    for row in cursor.fetchall():
        print(row)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="컴퓨존 WMS 매핑 후보를 WMS 품목관리 products로 생성")
    parser.add_argument("--db-host", default="172.16.19.35")
    parser.add_argument("--db-port", type=int, default=3306)
    parser.add_argument("--db-name", default="warehouse_pos")
    parser.add_argument("--db-user", default="root")
    parser.add_argument("--db-password-env", default="COMPUZONE_DB_PASSWORD")
    parser.add_argument("--db-password-prompt", action="store_true")
    parser.add_argument("--skip-backup", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    connection = connect(args)
    try:
        with connection.cursor() as cursor:
            backup_names = [] if args.skip_backup else backup_tables(cursor, suffix)
            counts = import_products(cursor)
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
