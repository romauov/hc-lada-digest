import logging
from datetime import date

from shared.models import KnowledgeGraph
from shared.fact_checker.models import Contradiction

logger = logging.getLogger(__name__)


def apply_contradictions_to_graph(
    graph: KnowledgeGraph,
    contradictions: list[Contradiction],
) -> KnowledgeGraph:
    for c in contradictions:
        entity_id = None
        for eid, entity in graph.entities.items():
            if entity.name.lower() == c.entity_name.lower():
                entity_id = eid
                break
        if not entity_id:
            continue

        for rel in graph.relations:
            if rel.to_id != entity_id and rel.from_id != entity_id:
                continue
            if rel.type != c.relation_type:
                continue

            if c.is_confirmed:
                logger.info(
                    "Confirmed change: %s [%s] (confirmed by %d sources)",
                    c.entity_name, c.relation_type, len(c.confirmed_by),
                )
                rel.confidence = max(0.1, rel.confidence - 0.4)
                rel.is_rumour = False
                rel.until = date.today().isoformat()
            else:
                rel.confidence = max(0.3, rel.confidence - 0.2)
                rel.is_rumour = True
                logger.info(
                    "Relation flagged as rumour: %s [%s] conf→%.2f",
                    c.entity_name, c.relation_type, rel.confidence,
                )

    return graph
