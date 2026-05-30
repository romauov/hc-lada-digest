"""
seed.py — создаёт начальный граф знаний для ХК Лада Тольятти
и загружает его в Object Storage.

Запуск: python -m graph.seed
"""
import logging
import sys
from datetime import date

from shared.models import Entity, KnowledgeGraph, Relation
from shared.storage import save_graph

logger = logging.getLogger(__name__)


def build_initial_graph() -> KnowledgeGraph:
    today = date.today().isoformat()

    entities = {
        "lada_hc": Entity(
            id="lada_hc",
            type="hockey_club",
            name="Лада",
            full_name="ХК Лада Тольятти",
            priority_score=1.0,
            search_queries=[
                "ХК Лада Тольятти",
                "хоккей Лада Тольятти",
                "Lada Togliatti hockey",
                "Лада КХЛ",
            ],
            sources=["khl.ru", "sports.ru", "championat.com"],
        ),
        "arena_lada": Entity(
            id="arena_lada",
            type="arena",
            name="Лада-Арена",
            full_name="Лада-Арена Тольятти",
            priority_score=0.3,
            search_queries=["Лада-Арена Тольятти", "Lada Arena"],
        ),
        "sponsor_avtovaz": Entity(
            id="sponsor_avtovaz",
            type="sponsor",
            name="АвтоВАЗ",
            full_name="ПАО АвтоВАЗ",
            priority_score=0.5,
            search_queries=["АвтоВАЗ хоккей", "АвтоВАЗ Лада спонсор"],
        ),
        "league_khl": Entity(
            id="league_khl",
            type="league",
            name="КХЛ",
            full_name="Kontinental Hockey League",
            priority_score=0.4,
            search_queries=["КХЛ новости", "KHL news"],
        ),
        # Тренерский штаб и игроки добавятся автоматически
        # после первых итераций поиска (итерация 2, graph_updater)
    }

    relations = [
        Relation(from_id="lada_hc", to_id="arena_lada",     type="PLAYS_AT",     confidence=1.0),
        Relation(from_id="lada_hc", to_id="sponsor_avtovaz", type="SPONSORED_BY",  confidence=1.0),
        Relation(from_id="lada_hc", to_id="league_khl",      type="MEMBER_OF",     confidence=1.0),
    ]

    return KnowledgeGraph(
        root_id="lada_hc",
        created_at=today,
        last_updated=today,
        version=1,
        entities=entities,
        relations=relations,
    )


def main():
    logging.basicConfig(level=logging.INFO)
    logger.info("Building initial knowledge graph...")
    graph = build_initial_graph()
    logger.info("Entities: %d, Relations: %d", len(graph.entities), len(graph.relations))
    save_graph(graph, backup=False)
    logger.info("Graph uploaded to Object Storage ✓")


if __name__ == "__main__":
    main()
