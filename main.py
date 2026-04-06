#!/usr/bin/env python3
# eBay Sold-Price Pricer Bot
#
# Usage:
#   python main.py fetch      – scrape all phone models now, save to DB
#   python main.py schedule   – start the daily background scheduler
#   python main.py stats      – print current price table from the DB

import sys
import time
import logging

import json
from pathlib import Path

import schedule as sched

from database.alchemy.database import init_db, get_or_create_model, save_listings, update_price_summary, get_latest_summary
from bot.ebay_client import EbayFindingClient

_cfg         = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
PHONE_MODELS  = _cfg["phone_models"]
HISTORY_DAYS  = _cfg["history_days"]
SCHEDULE_TIME = _cfg["schedule_time"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────

# Scrape every configured phone model from eBay.de and save results to the DB
def cmd_fetch() -> None:
    init_db()
    client    = EbayFindingClient()
    total_new = 0

    for brand, models in PHONE_MODELS.items():
        for model in models:
            logger.info("Fetching: %s", model)
            # Build the eBay model-aspect filter value:
            # Apple models lack the brand prefix in config, so we prepend "Apple "
            modell = f"Apple {model}" if brand == "Apple" else model
            try:
                listings = client.fetch_sold_listings(model, modell=modell)
                model_id = get_or_create_model(brand, model)
                new      = save_listings(model_id, listings)
                summary  = update_price_summary(model_id, HISTORY_DAYS)
                total_new += new

                if summary:
                    logger.info(
                        "  %-40s  avg=€%-7.2f  median=€%-7.2f  n=%d  (+%d new)",
                        model,
                        summary["avg_price"],
                        summary["median_price"],
                        summary["sample_count"],
                        new,
                    )
                else:
                    logger.info("  %-40s  no data yet", model)

            except Exception as exc:
                logger.error("  Failed for %s: %s", model, exc)

    logger.info("Done. %d new listings saved to the database.", total_new)


# Print the latest avg/median/min/max price table for all models
def cmd_stats() -> None:
    init_db()
    rows = get_latest_summary()

    if not rows:
        print("No data yet. Run:  python main.py fetch")
        return

    header = f"{'Brand':<10} {'Model':<38} {'Avg €':>8} {'Median €':>10} {'Min €':>8} {'Max €':>8} {'n':>5}  {'Date':>10}"
    print()
    print(header)
    print("─" * len(header))

    current_brand = None
    for r in rows:
        if r["brand"] != current_brand:
            current_brand = r["brand"]
            print()
        print(
            f"{r['brand']:<10} {r['model']:<38} "
            f"{r['avg_price']:>8.2f} {r['median_price']:>10.2f} "
            f"{r['min_price']:>8.2f} {r['max_price']:>8.2f} "
            f"{r['sample_count']:>5}  {r['computed_date']:>10}"
        )
    print()


# Start the daily scheduler — runs fetch once immediately, then every day at SCHEDULE_TIME
def cmd_schedule() -> None:
    logger.info("Scheduler started – daily scrape at %s.", SCHEDULE_TIME)
    sched.every().day.at(SCHEDULE_TIME).do(cmd_fetch)

    cmd_fetch()  # run once immediately on start

    while True:
        sched.run_pending()
        time.sleep(60)


# ─────────────────────────────────────────────────────────────────────────────

_COMMANDS = {
    "fetch":    cmd_fetch,
    "stats":    cmd_stats,
    "schedule": cmd_schedule,
}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd not in _COMMANDS:
        print(f"Unknown command '{cmd}'. Available: {', '.join(_COMMANDS)}")
        sys.exit(1)
    _COMMANDS[cmd]()
