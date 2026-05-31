import logging
from datetime import date

from shared.models import KnowledgeGraph, NewsItem
from shared.priority import mark_mentioned, mark_not_mentioned, update_priorities
from shared.graph_ops import apply_new_entities, apply_updated_relations
from functions.graph_updater.analysis import analyze_news_item, check_contradictions

logger = logging.getLogger(__name__)


def update_graph_from_news(
    graph: KnowledgeGraph,
    all_news: list[NewsItem],
    mentioned_ids: set[str],
) -> tuple[KnowledgeGraph, list[dict]]:
    analysis_results = []

    for item in all_news:
        analysis = analyze_news_item(item, graph)
        if not analysis or not analysis.get("is_relevant", True):
            continue

        item.is_rumour = analysis.get("is_rumour", False)
        item.relevance_score = float(analysis.get("importance", 0.5))
        item.summary = analysis.get("summary", "")

        if analysis.get("new_entities"):
            graph = apply_new_entities(graph, analysis["new_entities"], item.url)

        if analysis.get("updated_relations"):
            graph = apply_updated_relations(graph, analysis["updated_relations"])

        if item.relevance_score > 0.6:
            contradictions = check_contradictions(item, graph)
            if contradictions:
                logger.warning("Contradictions in '%s': %s", item.title, contradictions)

        analysis_results.append({"item": item, "analysis": analysis})

    for eid, entity in graph.entities.items():
        graph.entities[eid] = (
            mark_mentioned(entity) if eid in mentioned_ids else mark_not_mentioned(entity)
        )

    graph.entities = update_priorities(graph.entities)
    graph.version += 1
    graph.last_updated = date.today().isoformat()

    return graph, analysis_results


def handler(event: dict, context) -> dict:
    from shared.models import KnowledgeGraph as KG, NewsItem as NI
    graph = KG.from_dict(event["graph"])
    mentioned = set(event.get("mentioned_ids", []))
    news = [NI(**n) for n in event.get("news", [])]
    updated, analysis = update_graph_from_news(graph, news, mentioned)
    return {"status": "ok", "graph": updated.to_dict(), "analysis_count": len(analysis)}


def update_graph_with_fact_check(
    graph: KnowledgeGraph,
    all_news: list[NewsItem],
    mentioned_ids: set[str],
) -> tuple[KnowledgeGraph, list[dict], list]:
    from shared.fact_checker import verify_all_news, format_fact_check_block

    graph, analysis_results = update_graph_from_news(graph, all_news, mentioned_ids)

    graph, _verif_results, contradictions = verify_all_news(all_news, graph)

    if contradictions:
        logger.warning(
            "Fact check: %d contradictions, %d high severity",
            len(contradictions),
            sum(1 for c in contradictions if c.severity == "high"),
        )

    return graph, analysis_results, contradictions
