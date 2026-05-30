"""
RSS-источники: Google News, KHL.ru, sports.ru.
"""
import logging
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser
import requests

from shared.models import Entity, NewsItem
from .base import BaseSource

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "HC-Digest-Bot/1.0"}
TIMEOUT = 15


def _fetch_feed(url: str) -> list:
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        resp.raise_for_status()
        return feedparser.parse(resp.content).entries
    except Exception as e:
        logger.warning("Feed error [%s]: %s", url[:60], e)
        return []


def _parse_date(entry) -> datetime | None:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def _matches_keywords(entry, keywords: list[str]) -> bool:
    text = f"{entry.get('title', '')} {entry.get('summary', '')}".lower()
    return any(kw.lower() in text for kw in keywords)


# ── Google News RSS ───────────────────────────────────────────────────────────

class GoogleNewsSource(BaseSource):
    """
    Поиск через Google News RSS.
    Бесплатно, широкий охват русскоязычных СМИ.
    """
    name = "google_news"
    URL  = "https://news.google.com/rss/search?q={query}&hl=ru&gl=RU&ceid=RU:ru"

    def fetch(self, entity: Entity) -> list[NewsItem]:
        found: dict[str, NewsItem] = {}

        for query in entity.search_queries:
            url     = self.URL.format(query=quote_plus(query))
            entries = _fetch_feed(url)

            for entry in entries:
                dt = _parse_date(entry)
                if not self.is_fresh(dt):
                    continue
                news_url = entry.get("link", "")
                if not news_url:
                    continue
                nid = self.make_news_id(news_url)
                if nid not in found:
                    found[nid] = self._make_item(
                        url=news_url,
                        title=entry.get("title", ""),
                        published_at=dt,
                        entity_id=entity.id,
                    )

        logger.debug("[google_news] %s → %d items", entity.name, len(found))
        return list(found.values())


# ── KHL.ru RSS ────────────────────────────────────────────────────────────────

class KHLSource(BaseSource):
    """
    Официальная RSS-лента KHL.ru.
    Фильтрация по ключевым словам сущности.
    """
    name = "khl"
    URL  = "https://www.khl.ru/news/rss/"

    def fetch(self, entity: Entity) -> list[NewsItem]:
        entries = _fetch_feed(self.URL)
        found: dict[str, NewsItem] = {}

        for entry in entries:
            if not _matches_keywords(entry, entity.search_queries):
                continue
            dt = _parse_date(entry)
            if not self.is_fresh(dt):
                continue
            news_url = entry.get("link", "")
            if not news_url:
                continue
            nid = self.make_news_id(news_url)
            if nid not in found:
                found[nid] = self._make_item(
                    url=news_url,
                    title=entry.get("title", ""),
                    published_at=dt,
                    entity_id=entity.id,
                )

        logger.debug("[khl] %s → %d items", entity.name, len(found))
        return list(found.values())


# ── sports.ru RSS ─────────────────────────────────────────────────────────────

class SportsRuRSSSource(BaseSource):
    """
    RSS-лента sports.ru (хоккей России).
    Фильтрация по ключевым словам сущности.
    """
    name = "sports_ru_rss"
    URL  = "https://www.sports.ru/rss/hockey_russia.xml"

    def fetch(self, entity: Entity) -> list[NewsItem]:
        entries = _fetch_feed(self.URL)
        found: dict[str, NewsItem] = {}

        for entry in entries:
            if not _matches_keywords(entry, entity.search_queries):
                continue
            dt = _parse_date(entry)
            if not self.is_fresh(dt):
                continue
            news_url = entry.get("link", "")
            if not news_url:
                continue
            nid = self.make_news_id(news_url)
            if nid not in found:
                found[nid] = self._make_item(
                    url=news_url,
                    title=entry.get("title", ""),
                    published_at=dt,
                    entity_id=entity.id,
                )

        logger.debug("[sports_ru_rss] %s → %d items", entity.name, len(found))
        return list(found.values())
