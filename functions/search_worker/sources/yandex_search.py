"""
Yandex Search API — платный, но мощный источник.
Документация: https://cloud.yandex.ru/docs/search-api/

Использует XML API Яндекс.Поиска.
Требует: YANDEX_SEARCH_USER, YANDEX_SEARCH_KEY в окружении.
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import requests
from xml.etree import ElementTree as ET

from shared.models import Entity, NewsItem
from .base import BaseSource

logger = logging.getLogger(__name__)

YANDEX_SEARCH_URL = "https://yandex.ru/search/xml"
TIMEOUT           = 15


class YandexSearchSource(BaseSource):
    """
    Поиск через Yandex XML Search API.
    Ищет свежие новости (фильтр по дате через within= параметр).
    """
    name = "yandex_search"

    def __init__(self, freshness_hours: int = 24):
        super().__init__(freshness_hours)
        self.user    = os.environ.get("YANDEX_SEARCH_USER", "")
        self.api_key = os.environ.get("YANDEX_SEARCH_KEY", "")
        self.enabled = bool(self.user and self.api_key)
        if not self.enabled:
            logger.debug("YandexSearch disabled: YANDEX_SEARCH_USER/KEY not set")

    def fetch(self, entity: Entity) -> list[NewsItem]:
        if not self.enabled:
            return []

        found: dict[str, NewsItem] = {}

        for query in entity.search_queries[:2]:   # API лимиты — не более 2 запросов на сущность
            items = self._search(query, entity.id)
            for item in items:
                nid = self.make_news_id(item.url)
                if nid not in found:
                    found[nid] = item

        logger.debug("[yandex_search] %s → %d items", entity.name, len(found))
        return list(found.values())

    def _search(self, query: str, entity_id: str) -> list[NewsItem]:
        """Выполняет один поисковый запрос, возвращает список NewsItem."""
        # within=1 — за последние сутки
        params = {
            "user":      self.user,
            "key":       self.api_key,
            "query":     query,
            "within":    "1",       # последние сутки
            "sortby":    "rlv",     # по релевантности
            "maxpassages": "0",
            "results":   "10",
            "lr":        "225",     # Россия
        }

        try:
            resp = requests.get(
                YANDEX_SEARCH_URL,
                params=params,
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            return self._parse_xml(resp.text, entity_id)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 402:
                logger.warning("YandexSearch: quota exceeded")
            else:
                logger.warning("YandexSearch HTTP error: %s", e)
            return []
        except Exception as e:
            logger.warning("YandexSearch error: %s", e)
            return []

    def _parse_xml(self, xml_text: str, entity_id: str) -> list[NewsItem]:
        """Парсит XML-ответ Yandex Search API."""
        items = []
        try:
            root = ET.fromstring(xml_text)
            for doc in root.findall(".//doc"):
                url_el   = doc.find("url")
                title_el = doc.find("title")
                date_el  = doc.find("modtime")  # формат: YYYYMMDDTHHMMSS

                if url_el is None or title_el is None:
                    continue

                url   = url_el.text or ""
                title = _strip_tags(title_el.text or "")
                dt    = _parse_yandex_date(date_el.text if date_el is not None else None)

                if not self.is_fresh(dt):
                    continue

                items.append(self._make_item(
                    url=url, title=title, published_at=dt, entity_id=entity_id,
                ))
        except ET.ParseError as e:
            logger.warning("YandexSearch XML parse error: %s", e)
        return items


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_yandex_date(text: str | None) -> datetime | None:
    """Парсит дату в формате Яндекс: 20240315T143022 или 2024-03-15T14:30:22."""
    if not text:
        return None
    text = text.strip()
    # компактный формат
    m = re.match(r'(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})', text)
    if m:
        try:
            return datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5)), int(m.group(6)),
                tzinfo=timezone.utc,
            )
        except ValueError:
            pass
    # ISO формат
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _strip_tags(text: str) -> str:
    """Убирает HTML-теги из заголовков в XML-ответе."""
    return re.sub(r'<[^>]+>', '', text).strip()
