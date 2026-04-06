# database.py – SQLite persistence for eBay sold-price data.
#
# Schema
#   phone_models    – one row per (brand, model) pair
#   sold_listings   – individual sold eBay listings (deduplicated by item_id)
#   price_summaries – daily aggregated stats per model (avg / median / min / max)
#
# SQL statements are stored in database/sql/*.sql and loaded at import time.
#
# Public API used by other bots:
#   get_avg_price_for_model(model_name)  → float | None
#   get_latest_summary(brand, model)     → list[dict]

import logging
import statistics
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event, text

_cfg         = json.loads((Path(__file__).parent.parent.parent / "config.json").read_text(encoding="utf-8"))
DB_PATH      = Path(__file__).parent.parent.parent / _cfg["db_path"]
HISTORY_DAYS = _cfg["history_days"]

logger  = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SQL loader
# ─────────────────────────────────────────────────────────────────────────────

_SQL_DIR = Path(__file__).parent.parent / "sql"

def _sql_file(file: str) -> str:
    # Load an entire .sql file (used for schema and teardown).
    return (_SQL_DIR / f"{file}.sql").read_text(encoding="utf-8")

# _sql(file, query) extracts the block that follows a '-- <query>' comment marker.
def _sql(file: str, query: str) -> str:
    text = (_SQL_DIR / f"{file}.sql").read_text(encoding="utf-8")
    # Split on comment markers and return the section matching query
    sections: dict[str, str] = {}
    current_key: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("-- ") and not stripped.startswith("-- -"):
            if current_key is not None:
                sections[current_key] = "\n".join(lines).strip()
            current_key = stripped[3:].strip()
            lines = []
        else:
            lines.append(line)
    if current_key is not None:
        sections[current_key] = "\n".join(lines).strip()
    return sections[query]

# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)

@event.listens_for(_engine, "connect")
def _set_fk_pragma(dbapi_conn, _record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()

# ─────────────────────────────────────────────────────────────────────────────
# Setup / Teardown
# ─────────────────────────────────────────────────────────────────────────────

def init_db() -> None:
    # Create tables and indexes if they do not exist yet.
    schema = _sql_file("schema")
    with _engine.begin() as conn:
        for stmt in schema.split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                conn.execute(text(stmt))
    logger.info("Database ready at %s", DB_PATH)

def teardown_db(delete_file: bool = False) -> None:
    # Drop all tables. Pass delete_file=True to also remove the .db file.
    teardown = _sql_file("teardown")
    with _engine.begin() as conn:
        for stmt in teardown.split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                conn.execute(text(stmt))
    logger.info("All tables dropped.")
    if delete_file:
        _engine.dispose()
        if DB_PATH.exists():
            DB_PATH.unlink()
            logger.info("Deleted %s", DB_PATH)

# ─────────────────────────────────────────────────────────────────────────────
# Write helpers  (used by the scraper)
# ─────────────────────────────────────────────────────────────────────────────

def get_or_create_model(brand: str, model: str) -> int:
    # Return the primary-key id for (brand, model), inserting it if missing.
    with _engine.begin() as conn:
        conn.execute(text(_sql("models", "models_insert")), {"brand": brand, "model": model})
        row = conn.execute(
            text(_sql("models", "models_select_id")), {"brand": brand, "model": model}
        ).fetchone()
    return row[0]


def save_listings(model_id: int, listings: list[dict]) -> int:
    # Bulk-insert sold listings, skipping duplicates (UNIQUE on item_id).
    # Returns the number of new rows inserted.
    if not listings:
        return 0

    saved = 0
    with _engine.begin() as conn:
        for item in listings:
            result = conn.execute(
                text(_sql("listings", "listings_insert")),
                {
                    "phone_model_id": model_id,
                    "item_id":        item.get("item_id"),
                    "title":          item["title"],
                    "price":          item["price"],
                    "currency":       item.get("currency", "EUR"),
                    "condition":      item.get("condition"),
                    "end_time":       item.get("end_time"),
                    "listing_url":    item.get("listing_url"),
                },
            )
            if result.rowcount > 0:
                saved += 1
    return saved


def update_price_summary(model_id: int, history_days: int = HISTORY_DAYS) -> Optional[dict]:
    # Compute avg/median/min/max for the last history_days days and upsert into price_summaries.
    # Returns the summary dict, or None if there are no listings.
    with _engine.connect() as conn:
        rows = conn.execute(
            text(_sql("listings", "listings_select_prices")),
            {"model_id": model_id, "since": f"-{history_days} days"},
        ).fetchall()

    if not rows:
        return None

    prices = [r[0] for r in rows]
    today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary = {
        "avg_price":    round(statistics.mean(prices),   2),
        "median_price": round(statistics.median(prices), 2),
        "min_price":    round(min(prices),               2),
        "max_price":    round(max(prices),               2),
        "sample_count": len(prices),
    }

    with _engine.begin() as conn:
        conn.execute(
            text(_sql("summaries", "summaries_upsert")),
            {
                "model_id":      model_id,
                "computed_date": today,
                **summary,
            },
        )
    return summary

# ─────────────────────────────────────────────────────────────────────────────
# Read helpers  (used by this bot and the Kleinanzeigen bot)
# ─────────────────────────────────────────────────────────────────────────────

def get_latest_summary(
    brand: Optional[str] = None,
    model: Optional[str] = None,
) -> list[dict]:
    # Return the most recent price summary for every tracked model.
    # brand – filter to a single brand  (e.g. "Apple")
    # model – partial model name match  (e.g. "iPhone 14 Pro")
    sql    = _sql("summaries", "summaries_select_latest")
    params: dict = {}
    if brand:
        sql += " AND pm.brand = :brand"
        params["brand"] = brand
    if model:
        sql += " AND pm.model LIKE :model"
        params["model"] = f"%{model}%"
    sql += " ORDER BY pm.brand, pm.model"

    with _engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    return [dict(r._mapping) for r in rows]


def get_avg_price_for_model(model_name: str) -> Optional[float]:
    # Quick single-value lookup: latest avg sold price for a phone model.
    # Used by the Kleinanzeigen bot:
    #   avg = get_avg_price_for_model("iPhone 14 Pro")
    #   if avg and listing_price < avg * 0.75: send_whatsapp_alert(listing)
    results = get_latest_summary(model=model_name)
    return results[0]["avg_price"] if results else None
