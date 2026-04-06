-- summaries_upsert
INSERT INTO price_summaries
    (phone_model_id, computed_date, avg_price, median_price, min_price, max_price, sample_count)
VALUES
    (:model_id, :computed_date, :avg_price, :median_price, :min_price, :max_price, :sample_count)
ON CONFLICT(phone_model_id, computed_date) DO UPDATE SET
    avg_price    = excluded.avg_price,
    median_price = excluded.median_price,
    min_price    = excluded.min_price,
    max_price    = excluded.max_price,
    sample_count = excluded.sample_count;

-- summaries_select_latest
SELECT
    pm.brand,
    pm.model,
    ps.computed_date,
    ps.avg_price,
    ps.median_price,
    ps.min_price,
    ps.max_price,
    ps.sample_count
FROM price_summaries ps
JOIN phone_models pm ON pm.id = ps.phone_model_id
WHERE ps.computed_date = (
    SELECT MAX(ps2.computed_date)
    FROM price_summaries ps2
    WHERE ps2.phone_model_id = ps.phone_model_id
)
