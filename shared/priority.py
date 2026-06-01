"""
Логика динамической приоритизации сущностей графа.
"""
from datetime import date, datetime, timedelta
from shared.models import Entity

# Базовые веса по типу сущности
BASE_WEIGHTS: dict[str, float] = {
    "hockey_club": 1.0,
    "coach":        0.8,
    "player":       0.6,
    "sponsor":      0.5,
    "arena":        0.3,
    "farm_club":    0.4,
    "official":     0.5,   # президент, GM и т.д.
    "league":       0.4,
    "other":        0.2,
}

MAX_STREAK_BONUS = 0.3
STREAK_STEP      = 0.1
RECENCY_WEIGHTS  = [
    (1,   1.2),   # вчера
    (3,   1.0),   # 2–3 дня
    (7,   0.8),   # до недели
    (30,  0.5),   # до месяца
    (999, 0.3),   # давно
]


def _recency_factor(last_mentioned: str | None) -> float:
    if not last_mentioned:
        return 0.5
    try:
        last = date.fromisoformat(last_mentioned)
    except ValueError:
        return 0.5
    days_ago = (date.today() - last).days
    for threshold, factor in RECENCY_WEIGHTS:
        if days_ago <= threshold:
            return factor
    return 0.3


def _streak_bonus(streak: int) -> float:
    return min(streak * STREAK_STEP, MAX_STREAK_BONUS)


def compute_priority(entity: Entity) -> float:
    """
    priority = base_weight × recency_factor + streak_bonus

    Результат зажат в [0.0, 1.5] — намеренно допускаем > 1.0
    для сущностей с высоким streak, чтобы они явно выделялись.
    """
    base    = BASE_WEIGHTS.get(entity.type, BASE_WEIGHTS["other"])
    recency = _recency_factor(entity.last_mentioned)
    streak  = _streak_bonus(entity.mention_streak)
    score   = base * recency + streak
    return round(min(score, 1.5), 4)


def update_priorities(entities: dict[str, Entity]) -> dict[str, Entity]:
    """Пересчитывает priority_score для всех сущностей."""
    for entity in entities.values():
        entity.priority_score = compute_priority(entity)
    return entities


def mark_mentioned(entity: Entity, today: str | None = None) -> Entity:
    """
    Вызывается когда сущность упомянута в свежих новостях.
    Обновляет last_mentioned и увеличивает streak.
    """
    today = today or date.today().isoformat()
    if entity.last_mentioned == today:
        return entity  # уже отмечена сегодня

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    if entity.last_mentioned == yesterday:
        entity.mention_streak += 1
    else:
        entity.mention_streak = 1  # streak сброшен

    entity.last_mentioned = today
    return entity


def mark_not_mentioned(entity: Entity) -> Entity:
    """
    Вызывается когда за день новостей по сущности не найдено.
    Обнуляет streak.
    """
    entity.mention_streak = 0
    return entity
