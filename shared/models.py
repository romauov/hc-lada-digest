"""
Общие модели данных проекта.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Entity:
    """Сущность в графе знаний."""
    id: str
    type: str           # hockey_club, coach, player, sponsor, arena, farm_club, etc.
    name: str
    full_name: str = ""
    priority_score: float = 0.5
    last_mentioned: Optional[str] = None   # ISO date
    mention_streak: int = 0
    is_rumour: bool = False
    rumour_source: Optional[str] = None
    search_queries: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # любые доп. поля без изменения схемы

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "full_name": self.full_name,
            "priority_score": self.priority_score,
            "last_mentioned": self.last_mentioned,
            "mention_streak": self.mention_streak,
            "is_rumour": self.is_rumour,
            "rumour_source": self.rumour_source,
            "search_queries": self.search_queries,
            "sources": self.sources,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Entity":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Relation:
    """Связь между сущностями."""
    from_id: str
    to_id: str
    type: str           # HAS_COACH, HAS_PLAYER, SPONSORED_BY, PLAYS_AT, HAS_FARM, etc.
    confidence: float = 1.0
    since: Optional[str] = None
    until: Optional[str] = None
    is_rumour: bool = False

    def to_dict(self) -> dict:
        return {
            "from": self.from_id,
            "to": self.to_id,
            "type": self.type,
            "confidence": self.confidence,
            "since": self.since,
            "until": self.until,
            "is_rumour": self.is_rumour,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Relation":
        d = dict(data)
        d["from_id"] = d.pop("from")
        d["to_id"] = d.pop("to")
        return cls(**d)


@dataclass
class NewsItem:
    """Найденная новость."""
    url: str
    title: str
    published_at: str   # ISO datetime
    source: str
    entity_id: str      # к какой сущности относится
    summary: str = ""
    is_rumour: bool = False
    relevance_score: float = 0.5
    deferred: bool = False


@dataclass
class DeferredNews:
    """Отложенная новость для следующего дня."""
    url: str
    title: str
    entity_id: str
    deferred_at: str
    reason: str         # low_priority_day, quota_exceeded
    source: str = ""
    published_at: str = ""

    def to_dict(self) -> dict:
        return self.__dict__

    @classmethod
    def from_dict(cls, data: dict) -> "DeferredNews":
        return cls(**data)


@dataclass
class KnowledgeGraph:
    """Граф знаний."""
    root_id: str
    created_at: str
    last_updated: str
    version: int
    entities: dict[str, Entity]         # id -> Entity
    relations: list[Relation]
    deferred_news: list[DeferredNews] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "meta": {
                "root_id": self.root_id,
                "created_at": self.created_at,
                "last_updated": self.last_updated,
                "version": self.version,
            },
            "entities": {k: v.to_dict() for k, v in self.entities.items()},
            "relations": [r.to_dict() for r in self.relations],
            "deferred_news": [d.to_dict() for d in self.deferred_news],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeGraph":
        meta = data["meta"]
        entities = {k: Entity.from_dict(v) for k, v in data["entities"].items()}
        relations = [Relation.from_dict(r) for r in data["relations"]]
        deferred = [DeferredNews.from_dict(d) for d in data.get("deferred_news", [])]
        return cls(
            root_id=meta["root_id"],
            created_at=meta["created_at"],
            last_updated=meta["last_updated"],
            version=meta["version"],
            entities=entities,
            relations=relations,
            deferred_news=deferred,
        )

    def get_entities_by_priority(self) -> list[Entity]:
        """Сущности, отсортированные по приоритету (убывание)."""
        return sorted(self.entities.values(), key=lambda e: e.priority_score, reverse=True)
