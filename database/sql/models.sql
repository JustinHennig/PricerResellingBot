-- models_insert
INSERT OR IGNORE INTO phone_models (brand, model)
VALUES (:brand, :model);

-- models_select_id
SELECT id
FROM phone_models
WHERE brand = :brand
  AND model = :model
