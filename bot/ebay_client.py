import time
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

import json
from pathlib import Path

_cfg          = json.loads((Path(__file__).parent.parent / "config.json").read_text(encoding="utf-8"))
EBAY_BASE_URL = _cfg["ebay_base_url"]
HISTORY_DAYS  = _cfg["history_days"]

logger = logging.getLogger(__name__)

# German month abbreviation → number
_DE_MONTHS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "mär": 3, "apr": 4, "mai": 5,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "oct": 10,
    "nov": 11, "dez": 12, "dec": 12,
}


class EbayFindingClient:
    """
    Scrapes eBay.de sold/completed listings for a given keyword.
    No API key required.
    """

    ITEMS_PER_PAGE = 120
    MAX_PAGES      = 8
    REQUEST_DELAY  = 2.5   # seconds between pages (be polite, avoid blocks)

    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(self._HEADERS)
        self._cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)

    # ── Public ────────────────────────────────────────────────────────────────

    # Fetch all sold listings for a keyword, filtered to used condition and the exact model
    def fetch_sold_listings(self, keyword: str, modell: str = "") -> list[dict]:
        all_items: list[dict] = []

        for page in range(1, self.MAX_PAGES + 1):
            items, has_more = self._fetch_page(keyword, page, modell)
            all_items.extend(items)
            logger.debug("  page %d → %d items (total %d)", page, len(items), len(all_items))
            if not has_more:
                break
            time.sleep(self.REQUEST_DELAY)

        return all_items

    # ── Private ───────────────────────────────────────────────────────────────

    def _fetch_page(self, keyword: str, page: int, modell: str = "") -> tuple[list[dict], bool]:
        params = {
            "_nkw":             keyword,
            "LH_Sold":          "1",
            "LH_All":           "1",
            "LH_ItemCondition": "3000",  # Gebraucht only (excludes Neu, refurbished, parts)
            "_dcat":            "9355",  # Handys & Smartphones category (blocks accessories)
            "_ipg":             str(self.ITEMS_PER_PAGE),
            "_pgn":             str(page),
            "_sop":             "10",    # sort: most recently ended first
        }
        # Modell must be pre-encoded so requests double-encodes it — eBay requires this
        if modell:
            params["Modell"] = quote(modell)

        try:
            resp = self._session.get(EBAY_BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("eBay request failed for '%s' page %d: %s", keyword, page, exc)
            return [], False

        return self._parse_page(resp.text)

    def _parse_page(self, html: str) -> tuple[list[dict], bool]:
        soup = BeautifulSoup(html, "lxml")
        items: list[dict] = []
        hit_cutoff = False

        # Real sold listings always have id="item..." — skip ad/placeholder cards
        for li in soup.select("li.s-card"):
            if not str(li.get("id", "")).startswith("item"):
                continue
            item = self._parse_item(li)
            if item is None:
                continue

            # Enrich end_time with a proper ISO timestamp when possible
            end_dt = self._parse_date(item["end_time"])
            if end_dt is not None:
                if end_dt < self._cutoff:
                    hit_cutoff = True
                    continue
                item["end_time"] = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            items.append(item)

        has_next = bool(soup.select_one("a.pagination__next"))
        return items, has_next and not hit_cutoff

    @staticmethod
    def _parse_item(li) -> Optional[dict]:
        try:
            # Item ID is stored directly on the <li> as data-listingid
            item_id = li.get("data-listingid", "")

            # Title: first non-clipped span inside .s-card__title
            title_el = li.select_one(".s-card__title")
            if not title_el:
                return None
            title_span = title_el.find("span", class_=lambda c: c and "clipped" not in c)
            title = title_span.get_text(strip=True) if title_span else title_el.get_text(strip=True)
            if not title:
                return None

            # Price: span.s-card__price  →  "EUR 235,00"
            price_el = li.select_one("span.s-card__price")
            if not price_el:
                return None
            price = EbayFindingClient._parse_price(price_el.get_text(strip=True))
            if price is None or price <= 0:
                return None

            # Sold date: .s-card__caption span  →  "Verkauft  4. Apr 2026"
            date_el = li.select_one(".s-card__caption span")
            end_time = date_el.get_text(strip=True) if date_el else ""

            # URL: the non-image a.s-card__link in the header
            link_el = li.select_one(".su-card-container__header a.s-card__link")
            url = link_el["href"].split("?")[0] if link_el and link_el.get("href") else ""

            # Condition: first span in .s-card__subtitle  →  "Gebraucht |"
            cond_el = li.select_one(".s-card__subtitle span")
            condition = cond_el.get_text(strip=True).rstrip("|").strip() if cond_el else ""

            return {
                "item_id":     item_id,
                "title":       title,
                "price":       price,
                "currency":    "EUR",
                "condition":   condition,
                "end_time":    end_time,
                "listing_url": url,
            }
        except (AttributeError, KeyError, TypeError) as exc:
            logger.debug("Skipping item: %s", exc)
            return None

    @staticmethod
    def _parse_price(text: str) -> Optional[float]:
        """Parse German price strings like 'EUR 1.299,00' or '1.299,00 €'."""
        if not text:
            return None
        # Take lower bound of price ranges ("100,00 bis 200,00")
        text = re.split(r"\s+(?:bis|to)\s+", text, flags=re.IGNORECASE)[0]
        text = re.sub(r"[^\d,.]", "", text.strip())
        if not text:
            return None
        # German thousands separator: 1.299,00
        if re.search(r"\d\.\d{3},\d{2}$", text):
            text = text.replace(".", "").replace(",", ".")
        elif "," in text and "." in text:
            if text.rindex(",") > text.rindex("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            text = text.replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _parse_date(text: str) -> Optional[datetime]:
        """Parse German or English date text from eBay.de, e.g. '4. Apr. 2026'."""
        if not text:
            return None
        t = re.sub(r"verkauft\s*", "", text.lower()).strip()
        m = re.search(r"(\d{1,2})[.\s]+([a-zä]{3})[.\s]*(\d{4})", t)
        if m:
            day   = int(m.group(1))
            mon   = m.group(2).replace("ä", "a")[:3]
            year  = int(m.group(3))
            month = _DE_MONTHS.get(mon)
            if month:
                return datetime(year, month, day, 12, 0, 0, tzinfo=timezone.utc)
        return None
