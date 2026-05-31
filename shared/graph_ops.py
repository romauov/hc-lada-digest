import json
import logging
import re
from datetime import date

from shared.models import Entity, KnowledgeGraph, Relation

logger = logging.getLogger(__name__)


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
    for ent in new_entities:
        name = ent.get("name", "").strip()
        if not name:
            continue
        exists, _ = entity_exists(graph, name)
        if exists:
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
        logger.info("New entity: %s (%s)", name, entity.type)

        graph.relations.append(Relation(
            from_id=graph.root_id,
            to_id=eid,
            type=ent.get("relation_to_root", "RELATED_TO"),
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
