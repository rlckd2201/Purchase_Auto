USE warehouse_pos;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
