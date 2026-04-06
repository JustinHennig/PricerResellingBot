CREATE TABLE IF NOT EXISTS phone_models (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    brand TEXT    NOT NULL,
    model TEXT    NOT NULL,
    UNIQUE(brand, model)
);

CREATE TABLE IF NOT EXISTS sold_listings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_model_id INTEGER NOT NULL REFERENCES phone_models(id),
    item_id        TEXT    UNIQUE,
    title          TEXT    NOT NULL,
    price          REAL    NOT NULL,
    currency       TEXT    NOT NULL DEFAULT 'EUR',
    condition      TEXT,
    end_time       TEXT,
    listing_url    TEXT,
    fetched_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS price_summaries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_model_id INTEGER NOT NULL REFERENCES phone_models(id),
    computed_date  TEXT    NOT NULL,
    avg_price      REAL,
    median_price   REAL,
    min_price      REAL,
    max_price      REAL,
    sample_count   INTEGER,
    UNIQUE(phone_model_id, computed_date)
);

CREATE INDEX IF NOT EXISTS idx_sold_model   ON sold_listings(phone_model_id);
CREATE INDEX IF NOT EXISTS idx_sold_endtime ON sold_listings(end_time)
