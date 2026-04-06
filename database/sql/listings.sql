-- listings_insert
INSERT OR IGNORE INTO sold_listings
    (phone_model_id, item_id, title, price, currency, condition, end_time, listing_url)
VALUES
    (:phone_model_id, :item_id, :title, :price, :currency, :condition, :end_time, :listing_url);

-- listings_select_prices
SELECT price
FROM sold_listings
WHERE phone_model_id = :model_id
  AND datetime(end_time) >= datetime('now', :since)
