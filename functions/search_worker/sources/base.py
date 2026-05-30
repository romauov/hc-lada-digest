"""
Базовый класс для всех источников новостей.
Каждый источник реализует метод fetch(entity) -> list[NewsItem].
"""
import hashlib
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

from shared.models import Entity, NewsItem

logger = logging.getLogger(__name__)


class BaseSource(ABC):
    """Абстрактный источник новостей."""

    name: str = "base"

    def __init__(self, freshness_hours: int = 24):
        self.freshness_hours = freshness_hours

    @abstractmethod
    def fetch(self, entity: Entity) -> list[NewsItem]:
        """Ищет свежие новости по сущности. Возвращает список NewsItem."""
        ...

    # ── Утилиты для наследников ───────────────────────────────────────────────

    def is_fresh(self, dt: datetime | None) -> bool:
        if dt is None:
            return True  # не можем определить дату — берём
        threshold = datetime.now(timezone.utc) - timedelta(hours=self.freshness_hours)
        return dt >= threshold

    @staticmethod
    def make_news_id(url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()[:12]

    @staticmethod
    def normalize_url(url: str) -> str:
        """Убирает UTM-метки и якоря для стабильной дедупликации."""
        from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
        parsed = urlparse(url)
        # оставляем только чистый путь без якоря и UTM
        params = {k: v for k, v in parse_qs(parsed.query).items()
                  if not k.startswith("utm_")}
        clean = parsed._replace(
            query=urlencode(params, doseq=True),
            fragment="",
        )
        return urlunparse(clean)

    def _make_item(
        self,
        url:          str,
        title:        str,
        published_at: datetime | None,
        entity_id:    str,
        source:       str | None = None,
    ) -> NewsItem:
        return NewsItem(
            url=self.normalize_url(url),
            title=title.strip(),
            published_at=published_at.isoformat() if published_at else "",
            source=source or self.name,
            entity_id=entity_id,
        )
