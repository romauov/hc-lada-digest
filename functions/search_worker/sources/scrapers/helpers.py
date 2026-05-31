import logging
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}
TIMEOUT = 20

_RU_MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}


def _get(url: str) -> BeautifulSoup | None:
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.warning("Scrape error [%s]: %s", url[:80], e)
        return None


def _parse_iso(text: str) -> datetime | None:
    if not text:
        return None
    try:
        text = text.strip().rstrip("Z")
        if "+" in text:
            text = text.split("+")[0]
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_text_date(text: str) -> datetime | None:
    text = text.lower().strip()

    m = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{2,4})', text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            pass

    m = re.search(r'(\d{1,2})\s+([а-я]{3})\w*\s+(\d{4})', text)
    if m:
        day = int(m.group(1))
        month = _RU_MONTHS.get(m.group(2)[:3])
        year = int(m.group(3))
        if month:
            try:
                return datetime(year, month, day, tzinfo=timezone.utc)
            except ValueError:
                pass

    return None
