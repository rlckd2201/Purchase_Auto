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
