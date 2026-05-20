from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

import pymysql


def split_sql(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply the Compuzone purchase-history MariaDB schema.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", required=True)
    parser.add_argument("--database", default="warehouse_pos")
    parser.add_argument("--password-env", default="COMPUZONE_DB_PASSWORD")
    parser.add_argument("--password-prompt", action="store_true")
    parser.add_argument(
        "--schema-file",
        default="sql/compuzone_purchase_history_schema.sql",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    password = os.environ.get(args.password_env)
    if args.password_prompt or password is None:
        password = getpass.getpass("MariaDB password: ")

    schema_path = Path(args.schema_file)
    sql = schema_path.read_text(encoding="utf-8")
    statements = [statement.replace("USE warehouse_pos", f"USE {args.database}") for statement in split_sql(sql)]

    connection = pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=password,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=10,
    )
    try:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)

            cursor.execute(
                """
                SELECT table_name, table_type
                FROM information_schema.tables
                WHERE table_schema = %s
                  AND (table_name LIKE 'compuzone_%%' OR table_name LIKE 'v_compuzone_%%')
                ORDER BY table_name
                """,
                (args.database,),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()

    print(f"database={args.database}")
    for table_name, table_type in rows:
        print(f"{table_name}\t{table_type}")


if __name__ == "__main__":
    main()
