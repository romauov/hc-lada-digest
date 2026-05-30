"""
Парсеры sports.ru и championat.com.

Стратегия:
  - Сначала ищем через поиск сайта или RSS
  - Затем загружаем полный текст статьи для извлечения заголовка и даты
  - Используем BeautifulSoup для парсинга HTML

Важно: парсеры хрупкие — при смене вёрстки могут сломаться.
Все ошибки логируются и не роняют пайплайн.
"""
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from shared.models import Entity, NewsItem
from .base import BaseSource

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


def _get(url: str) -> BeautifulSoup | None:
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.warning("Scrape error [%s]: %s", url[:80], e)
        return None


def _parse_iso(text: str) -> datetime | None:
    """Парсит ISO-дату из атрибутов datetime."""
    if not text:
        return None
    try:
        text = text.strip().rstrip("Z")
        if "+" in text:
            text = text.split("+")[0]
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ── sports.ru scraper ─────────────────────────────────────────────────────────

class SportsRuScraperSource(BaseSource):
    """
    Парсинг sports.ru — поиск через встроенный поиск сайта.
    Извлекаем список статей с датами и заголовками.
    """
    name    = "sports_ru"
    SEARCH  = "https://www.sports.ru/search/?query={query}&type=news"

    def fetch(self, entity: Entity) -> list[NewsItem]:
        found: dict[str, NewsItem] = {}

        for query in entity.search_queries[:3]:  # ограничиваем кол-во запросов
            url  = self.SEARCH.format(query=quote_plus(query))
            soup = _get(url)
            if soup is None:
                continue

            self._extract_search_results(soup, entity, found)

        logger.debug("[sports_ru] %s → %d items", entity.name, len(found))
        return list(found.values())

    def _extract_search_results(
        self,
        soup:      BeautifulSoup,
        entity:    Entity,
        found:     dict[str, NewsItem],
    ) -> None:
        # sports.ru search results — статьи в .news-list__item или article
        articles = (
            soup.select(".news-list__item") or
            soup.select("article.material") or
            soup.select(".search-results .item")
        )

        for article in articles:
            try:
                # заголовок + ссылка
                link_tag = article.select_one("a[href]")
                if not link_tag:
                    continue
                href  = urljoin("https://www.sports.ru", link_tag["href"])
                title = link_tag.get_text(strip=True)
                if not title or not href:
                    continue

                # дата публикации
                time_tag = article.select_one("time[datetime]")
                dt       = _parse_iso(time_tag["datetime"]) if time_tag else None

                if not self.is_fresh(dt):
                    continue

                nid = self.make_news_id(href)
                if nid not in found:
                    found[nid] = self._make_item(
                        url=href, title=title, published_at=dt, entity_id=entity.id,
                    )
            except Exception as e:
                logger.debug("Article parse error: %s", e)


# ── championat.com scraper ────────────────────────────────────────────────────

class ChampionatScraperSource(BaseSource):
    """
    Парсинг championat.com — поиск через Google News с фильтром по сайту,
    затем прямой парсинг страниц поиска.
    """
    name   = "championat"
    SEARCH = "https://www.championat.com/search/?query={query}"

    def fetch(self, entity: Entity) -> list[NewsItem]:
        found: dict[str, NewsItem] = {}

        for query in entity.search_queries[:3]:
            url  = self.SEARCH.format(query=quote_plus(query))
            soup = _get(url)
            if soup is None:
                continue
            self._extract_results(soup, entity, found)

        logger.debug("[championat] %s → %d items", entity.name, len(found))
        return list(found.values())

    def _extract_results(
        self,
        soup:   BeautifulSoup,
        entity: Entity,
        found:  dict[str, NewsItem],
    ) -> None:
        # championat.com: .search-results__item, .news-item, article
        articles = (
            soup.select(".search-results__item") or
            soup.select(".news-list .news-item") or
            soup.select("article")
        )

        for article in articles:
            try:
                link_tag = article.select_one("a[href]")
                if not link_tag:
                    continue
                href  = urljoin("https://www.championat.com", link_tag["href"])
                title = link_tag.get_text(strip=True)
                if not title or not href or "championat.com" not in href:
                    continue

                time_tag = article.select_one("time[datetime]")
                dt       = _parse_iso(time_tag["datetime"]) if time_tag else None

                # fallback: ищем дату в тексте рядом со статьёй
                if dt is None:
                    date_span = article.select_one(".date, .time, .news-item__date")
                    if date_span:
                        dt = _parse_text_date(date_span.get_text(strip=True))

                if not self.is_fresh(dt):
                    continue

                nid = self.make_news_id(href)
                if nid not in found:
                    found[nid] = self._make_item(
                        url=href, title=title, published_at=dt, entity_id=entity.id,
                    )
            except Exception as e:
                logger.debug("Article parse error: %s", e)


# ── helpers ───────────────────────────────────────────────────────────────────

_RU_MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}


def _parse_text_date(text: str) -> datetime | None:
    """Парсит русскоязычные даты вида '15 мар 2024' или '15.03.2024'."""
    text = text.lower().strip()

    # формат: 15.03.2024 или 15.03.24
    m = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{2,4})', text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            pass

    # формат: 15 мар 2024
    m = re.search(r'(\d{1,2})\s+([а-я]{3})\w*\s+(\d{4})', text)
    if m:
        day   = int(m.group(1))
        month = _RU_MONTHS.get(m.group(2)[:3])
        year  = int(m.group(3))
        if month:
            try:
                return datetime(year, month, day, tzinfo=timezone.utc)
            except ValueError:
                pass

    return None
