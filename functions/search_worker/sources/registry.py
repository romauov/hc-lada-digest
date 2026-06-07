"""
Реестр источников новостей.

Управляет порядком опроса источников, собирает статистику доступности,
реализует fallback при недоступности источника.
"""
import logging
import os
from dataclasses import dataclass, field

from shared.models import Entity, NewsItem
from shared import ml_client
from .base import BaseSource
from .rss import GoogleNewsSource, KHLSource, SportsRuRSSSource
from .scrapers import SportsRuScraperSource, ChampionatScraperSource
from .yandex_search import YandexSearchSource

logger = logging.getLogger(__name__)

FRESHNESS_HOURS = int(os.environ.get("FRESHNESS_HOURS", "24"))


@dataclass
class SourceStats:
    """Статистика по источнику за текущий запуск."""
    name:      str
    requests:  int = 0
    found:     int = 0
    errors:    int = 0
    disabled:  bool = False


class SourceRegistry:
    """
    Реестр источников с приоритетами.

    Порядок опроса (итерация 3):
      1. Google News RSS   — бесплатно, широкий охват
      2. KHL.ru RSS        — официальный источник
      3. sports.ru RSS     — быстро, надёжно
      4. sports.ru scraper — полный поиск по сайту
      5. championat scraper
      6. Yandex Search API — если настроен

    Источник отключается на текущий запуск при 3+ ошибках подряд.
    """

    def __init__(self):
        h = FRESHNESS_HOURS
        self._sources: list[BaseSource] = [
            GoogleNewsSource(h),
            KHLSource(h),
            SportsRuRSSSource(h),
            SportsRuScraperSource(h),
            ChampionatScraperSource(h),
            YandexSearchSource(h),      # включается только если есть ключи
        ]
        self._stats: dict[str, SourceStats] = {
            s.name: SourceStats(name=s.name) for s in self._sources
        }
        self._consecutive_errors: dict[str, int] = {s.name: 0 for s in self._sources}
        self._low_relevance: dict[str, list[NewsItem]] = {}

    # ── Опрос источников ──────────────────────────────────────────────────────

    def _keyword_prefilter(self, items: list[NewsItem], entity: Entity) -> list[NewsItem]:
        if not entity.search_queries:
            return items
        keywords = set()
        for phrase in entity.search_queries:
            for word in phrase.lower().split():
                keywords.add(word)
        if not keywords:
            return items
        result = []
        for item in items:
            text = (item.title + " " + item.summary).lower()
            if any(kw in text for kw in keywords):
                result.append(item)
        return result

    def fetch_all(self, entity: Entity) -> list[NewsItem]:
        """
        Опрашивает все доступные источники для сущности.
        Возвращает дедуплицированный список NewsItem.
        """
        all_items: dict[str, NewsItem] = {}   # url → NewsItem

        for source in self._sources:
            # ленивая инициализация — на случай источников добавленных после __init__
            if source.name not in self._stats:
                self._stats[source.name]             = SourceStats(name=source.name)
                self._consecutive_errors[source.name] = 0
            stats = self._stats[source.name]
            if stats.disabled:
                continue

            try:
                items = source.fetch(entity)
                stats.requests += 1
                stats.found    += len(items)
                self._consecutive_errors[source.name] = 0

                # дедупликация по нормализованному URL
                for item in items:
                    if item.url not in all_items:
                        all_items[item.url] = item

            except Exception as e:
                stats.errors += 1
                self._consecutive_errors[source.name] += 1
                logger.warning("[%s] fetch error for '%s': %s", source.name, entity.name, e)

                if self._consecutive_errors[source.name] >= 3:
                    stats.disabled = True
                    logger.error("[%s] disabled after 3 consecutive errors", source.name)

        items = list(all_items.values())

        # keyword prefilter (без ML, дешёвая фильтрация)
        items = self._keyword_prefilter(items, entity)

        if not items:
            return items

        # семантическая дедупликация
        if ml_client.ML_URL:
            titles = [i.title for i in items]
            indices = ml_client.semantic_dedup(titles)
            items = [items[i] for i in indices]

        # relevance scoring и разделение на high/low
        high: list[NewsItem] = []
        low: list[NewsItem] = []
        for item in items:
            item = ml_client.calc_relevance(item, entity.search_queries)
            if ml_client.ML_URL and item.relevance_score < ml_client.RELEVANCE_THRESHOLD:
                low.append(item)
            else:
                high.append(item)

        self._low_relevance[entity.id] = low
        return high

    def get_low_relevance(self, entity_id: str) -> list[NewsItem]:
        return self._low_relevance.get(entity_id, [])

    # ── Статистика ────────────────────────────────────────────────────────────

    def get_summary(self) -> dict:
        return {
            name: {
                "requests": s.requests,
                "found":    s.found,
                "errors":   s.errors,
                "disabled": s.disabled,
            }
            for name, s in self._stats.items()
        }

    def log_summary(self) -> None:
        logger.info("=== Source summary ===")
        for name, s in self._stats.items():
            status = "DISABLED" if s.disabled else "ok"
            logger.info("  %-20s requests=%-3d found=%-4d errors=%-2d [%s]",
                        name, s.requests, s.found, s.errors, status)


# Синглтон для переиспользования в рамках одного запуска Cloud Function
_registry: SourceRegistry | None = None


def get_registry() -> SourceRegistry:
    global _registry
    if _registry is None:
        _registry = SourceRegistry()
    return _registry


def reset_registry() -> None:
    """Сброс синглтона — используется в тестах."""
    global _registry
    _registry = None
