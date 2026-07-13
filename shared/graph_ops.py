import json
import logging
import re
from datetime import date

from shared.models import Entity, KnowledgeGraph, Relation

logger = logging.getLogger(__name__)

INVALID_NAME_PATTERNS = [
    r'^\d+$',
    r'^null$',
    r'хоккеист(ы)?$',
    r'^(главный\s+)?тренер$',
    r'^\d+\s+хоккеист',
    r'^форвард\s+из',
    r'^сын\s+',
    r'^экс[-\s]',
    r'^спортсмен',
    r'улица|проспект|переулок',
]

ALLOWED_RELATIONS_FOR_RIVAL_CLUBS = {
    "SIGNED_FROM",
    "SIGNED_TO",
    "PLAYER_MOVED_FROM",
    "PLAYER_MOVED_TO",
    "TRADED_FROM",
    "TRADED_TO",
    "HAS_FARM_CLUB",
    "FARM_OF",
}

MAX_NAME_LENGTH = 60


def _normalize_name(name: str) -> str:
    return re.sub(r'\s+', ' ', name.lower().strip()).replace('ё', 'е')


def _is_valid_entity_name(name: str) -> bool:
    if len(name) > MAX_NAME_LENGTH:
        return False
    for pattern in INVALID_NAME_PATTERNS:
        if re.search(pattern, name):
            return False
    return True


def parse_json(text: str) -> dict | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    logger.warning("Failed to parse LLM JSON: %s", text[:200])
    return None


def entity_exists(graph: KnowledgeGraph, name: str) -> tuple[bool, str | None]:
    name_lower = name.lower()
    for eid, entity in graph.entities.items():
        if entity.name.lower() == name_lower or entity.full_name.lower() == name_lower:
            return True, eid
    return False, None


def make_entity_id(name: str, type_: str) -> str:
    slug = re.sub(r'[^a-zа-я0-9]', '_', name.lower())[:30]
    return f"{type_[:6]}_{slug}"


def apply_new_entities(graph: KnowledgeGraph, new_entities: list[dict], source_url: str) -> KnowledgeGraph:
    name_index: dict[str, str] = {
        _normalize_name(e.name): eid for eid, e in graph.entities.items()
    }

    for ent in new_entities:
        name = ent.get("name", "").strip()
        if not name:
            continue

        if not _is_valid_entity_name(name):
            logger.info("Filtered A (invalid name): '%s'", name)
            continue

        norm = _normalize_name(name)
        if norm in name_index:
            logger.info("Filtered C (normalized dup): '%s' -> '%s'", name, name_index[norm])
            continue

        rel = ent.get("relation_to_root", "RELATED_TO")
        if ent.get("type") == "hockey_club" and rel not in ALLOWED_RELATIONS_FOR_RIVAL_CLUBS:
            logger.info("Filtered B (club wrong relation '%s'): '%s'", rel, name)
            continue

        eid = make_entity_id(name, ent.get("type", "other"))
        entity = Entity(
            id=eid,
            type=ent.get("type", "other"),
            name=name,
            priority_score=0.3,
            is_rumour=ent.get("is_rumour", False),
            search_queries=ent.get("search_queries") or [f"{name} хоккей"],
            sources=[source_url],
        )
        graph.entities[eid] = entity
        name_index[norm] = eid
        logger.info("New entity: %s (%s)", name, entity.type)

        graph.relations.append(Relation(
            from_id=graph.root_id,
            to_id=eid,
            type=rel,
            confidence=float(ent.get("confidence", 0.5)),
            since=date.today().isoformat(),
            is_rumour=ent.get("is_rumour", False),
        ))
    return graph


def apply_updated_relations(graph: KnowledgeGraph, updates: list[dict]) -> KnowledgeGraph:
    for upd in updates:
        exists, eid = entity_exists(graph, upd.get("entity_name", ""))
        if not exists:
            continue
        for rel in graph.relations:
            if rel.to_id == eid and rel.type == upd.get("relation_type"):
                action = upd.get("action", "confirm")
                confidence = float(upd.get("confidence", 0.5))
                is_rumour = upd.get("is_rumour", False)
                if action == "remove":
                    rel.confidence = max(0.0, rel.confidence - 0.2)
                    rel.is_rumour = is_rumour
                elif action in ("confirm", "add"):
                    rel.confidence = min(1.0, max(rel.confidence, confidence))
                    if rel.is_rumour and not is_rumour and confidence > 0.7:
                        rel.is_rumour = False
                        logger.info("Rumour confirmed: %s", upd.get("entity_name"))
    return graph
