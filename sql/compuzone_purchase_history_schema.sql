USE warehouse_pos;

CREATE TABLE IF NOT EXISTS compuzone_products (
  product_uid VARCHAR(96) NOT NULL,
  product_no VARCHAR(32) NULL,
  item_name VARCHAR(80) NOT NULL,
  model_name VARCHAR(160) NOT NULL,
  raw_name TEXT NOT NULL,
  product_url VARCHAR(500) NULL,
  normalization_source VARCHAR(32) NULL,
  normalization_model VARCHAR(80) NULL,
  normalization_confidence DECIMAL(4,3) NULL,
  desktop_split_version TINYINT UNSIGNED NULL,
  desktop_split_basis VARCHAR(40) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (product_uid),
  UNIQUE KEY ux_compuzone_products_product_no (product_no),
  KEY ix_compuzone_products_item_model (item_name, model_name),
  KEY ix_compuzone_products_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS compuzone_order_lines (
  line_uid CHAR(64) NOT NULL,
  account VARCHAR(64) NOT NULL,
  order_no VARCHAR(32) NULL,
  product_uid VARCHAR(96) NOT NULL,
  purchase_date DATE NULL,
  order_status VARCHAR(32) NOT NULL,
  quantity INT NOT NULL DEFAULT 1,
  unit_price INT NULL,
  source_url VARCHAR(500) NULL,
  raw_text MEDIUMTEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (line_uid),
  KEY ix_compuzone_order_lines_account_date (account, purchase_date),
  KEY ix_compuzone_order_lines_product_date (product_uid, purchase_date),
  KEY ix_compuzone_order_lines_order_no (order_no),
  KEY ix_compuzone_order_lines_status (order_status),
  CONSTRAINT fk_compuzone_order_lines_product
    FOREIGN KEY (product_uid)
    REFERENCES compuzone_products (product_uid)
    ON UPDATE CASCADE
    ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS compuzone_sync_runs (
  sync_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  started_at DATETIME NOT NULL,
  finished_at DATETIME NULL,
  status VARCHAR(20) NOT NULL,
  accounts VARCHAR(500) NULL,
  years VARCHAR(100) NULL,
  line_count INT NOT NULL DEFAULT 0,
  product_count INT NOT NULL DEFAULT 0,
  message TEXT NULL,
  PRIMARY KEY (sync_id),
  KEY ix_compuzone_sync_runs_started_at (started_at),
  KEY ix_compuzone_sync_runs_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE OR REPLACE VIEW v_compuzone_product_summary AS
SELECT
  GROUP_CONCAT(DISTINCT l.account ORDER BY l.account SEPARATOR ', ') AS account_label,
  p.item_name,
  p.model_name,
  p.raw_name,
  MAX(l.purchase_date) AS last_purchase_date,
  COUNT(DISTINCT CONCAT(l.account, '|', COALESCE(l.order_no, l.line_uid))) AS purchase_count,
  SUM(l.quantity) AS purchase_quantity,
  p.product_no,
  p.product_url,
  ROUND(
    SUM(CASE WHEN l.unit_price IS NOT NULL THEN l.unit_price * l.quantity ELSE 0 END)
    / NULLIF(SUM(CASE WHEN l.unit_price IS NOT NULL THEN l.quantity ELSE 0 END), 0)
  ) AS average_unit_price
FROM compuzone_order_lines l
JOIN compuzone_products p ON p.product_uid = l.product_uid
WHERE l.order_status IN ('상품발송', '배송완료')
GROUP BY
  p.product_uid,
  p.item_name,
  p.model_name,
  p.raw_name,
  p.product_no,
  p.product_url;

CREATE OR REPLACE VIEW v_compuzone_product_summary_by_account AS
SELECT
  l.account,
  p.item_name,
  p.model_name,
  p.raw_name,
  MAX(l.purchase_date) AS last_purchase_date,
  COUNT(DISTINCT COALESCE(l.order_no, l.line_uid)) AS purchase_count,
  SUM(l.quantity) AS purchase_quantity,
  p.product_no,
  p.product_url,
  ROUND(
    SUM(CASE WHEN l.unit_price IS NOT NULL THEN l.unit_price * l.quantity ELSE 0 END)
    / NULLIF(SUM(CASE WHEN l.unit_price IS NOT NULL THEN l.quantity ELSE 0 END), 0)
  ) AS average_unit_price
FROM compuzone_order_lines l
JOIN compuzone_products p ON p.product_uid = l.product_uid
WHERE l.order_status IN ('상품발송', '배송완료')
GROUP BY
  l.account,
  p.product_uid,
  p.item_name,
  p.model_name,
  p.raw_name,
  p.product_no,
  p.product_url;

CREATE OR REPLACE VIEW v_compuzone_product_summary_by_year AS
SELECT
  YEAR(l.purchase_date) AS purchase_year,
  GROUP_CONCAT(DISTINCT l.account ORDER BY l.account SEPARATOR ', ') AS account_label,
  p.item_name,
  p.model_name,
  p.raw_name,
  MAX(l.purchase_date) AS last_purchase_date,
  COUNT(DISTINCT CONCAT(l.account, '|', COALESCE(l.order_no, l.line_uid))) AS purchase_count,
  SUM(l.quantity) AS purchase_quantity,
  p.product_no,
  p.product_url,
  ROUND(
    SUM(CASE WHEN l.unit_price IS NOT NULL THEN l.unit_price * l.quantity ELSE 0 END)
    / NULLIF(SUM(CASE WHEN l.unit_price IS NOT NULL THEN l.quantity ELSE 0 END), 0)
  ) AS average_unit_price
FROM compuzone_order_lines l
JOIN compuzone_products p ON p.product_uid = l.product_uid
WHERE l.order_status IN ('상품발송', '배송완료')
  AND l.purchase_date IS NOT NULL
GROUP BY
  YEAR(l.purchase_date),
  p.product_uid,
  p.item_name,
  p.model_name,
  p.raw_name,
  p.product_no,
  p.product_url;
