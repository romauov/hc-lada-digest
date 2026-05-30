"""
search_worker — точка входа для поиска новостей.

Итерация 3: делегирует поиск реестру источников.
Прежний интерфейс (search_entity_news) сохранён для совместимости с оркестратором.
"""
import logging

from shared.models import Entity, NewsItem
from .sources import get_registry

logger = logging.getLogger(__name__)


def search_entity_news(entity: Entity) -> list[NewsItem]:
    """
    Ищет свежие новости для сущности по всем доступным источникам.
    Возвращает дедуплицированный список NewsItem.
    """
    registry = get_registry()
    items    = registry.fetch_all(entity)
    logger.info("'%s': %d news items total", entity.name, len(items))
    return items


def handler(event: dict, context) -> dict:
    """
    Yandex Cloud Functions entry point.
    event["entity"] — сериализованная сущность (dict)
    """
    entity_data = event.get("entity")
    if not entity_data:
        return {"status": "error", "message": "No entity in event"}

    entity = Entity.from_dict(entity_data)
    items  = search_entity_news(entity)

    registry = get_registry()
    registry.log_summary()

    return {
        "status":    "ok",
        "entity_id": entity.id,
        "stats":     registry.get_summary(),
        "news": [
            {
                "url":          n.url,
                "title":        n.title,
                "published_at": n.published_at,
                "source":       n.source,
                "entity_id":    n.entity_id,
            }
            for n in items
        ],
    }
