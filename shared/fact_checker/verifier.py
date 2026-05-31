import logging

from shared.models import KnowledgeGraph, NewsItem
from shared.openrouter import analyze_news
from shared.graph_ops import parse_json
from shared.fact_checker.models import Contradiction, VerificationResult, CONFIRMATION_SOURCES_NEEDED
from shared.fact_checker.prompts import VERIFY_SYSTEM, CONFIRM_SYSTEM

logger = logging.getLogger(__name__)

FACT_THRESHOLD = 0.75
VERIFY_MIN_IMPORTANCE = 0.5


def _build_facts_block(graph: KnowledgeGraph) -> str:
    facts = []
    for rel in graph.relations:
        if rel.confidence < FACT_THRESHOLD or rel.is_rumour:
            continue
        from_e = graph.entities.get(rel.from_id)
        to_e = graph.entities.get(rel.to_id)
        if not from_e or not to_e:
            continue
        since = f" с {rel.since}" if rel.since else ""
        facts.append(
            f"- {from_e.name} —[{rel.type}]→ {to_e.name}{since} "
            f"(confidence: {rel.confidence:.2f})"
        )
    return "\n".join(facts) if facts else "(нет подтверждённых фактов)"


def verify_news_item(item: NewsItem, graph: KnowledgeGraph) -> VerificationResult:
    result = VerificationResult(news_item=item)

    if item.relevance_score < VERIFY_MIN_IMPORTANCE:
        result.skip_reason = f"low importance ({item.relevance_score:.2f})"
        return result

    facts_block = _build_facts_block(graph)
    if facts_block == "(нет подтверждённых фактов)":
        result.skip_reason = "no confirmed facts in graph"
        return result

    root = graph.entities.get(graph.root_id)
    root_name = root.name if root else "клуб"

    prompt = f"""Клуб: {root_name}

Подтверждённые факты:
{facts_block}

Новость для проверки:
Заголовок: {item.title}
Источник:  {item.source}
URL:       {item.url}
Краткое содержание: {item.summary or item.title}

Найди противоречия."""

    response = analyze_news(VERIFY_SYSTEM, prompt)
    if not response:
        result.skip_reason = "LLM unavailable"
        return result

    parsed = parse_json(response)
    if not parsed or not parsed.get("has_contradiction"):
        return result

    result.is_clean = False
    for c in parsed.get("contradictions", []):
        result.contradictions.append(Contradiction(
            entity_name=c.get("entity_name", ""),
            relation_type=c.get("relation_type", ""),
            known_fact=c.get("known_fact", ""),
            new_claim=c.get("new_claim", ""),
            severity=c.get("severity", "low"),
            source_url=item.url,
            source_name=item.source,
        ))

    logger.info(
        "Contradictions in '%s': %d (%s)",
        item.title[:60],
        len(result.contradictions),
        ", ".join(c.severity for c in result.contradictions),
    )
    return result


def try_confirm_contradiction(
    contradiction: Contradiction,
    confirming_item: NewsItem,
    graph: KnowledgeGraph,
) -> bool:
    prompt = f"""Известное противоречие:
  Сущность: {contradiction.entity_name}
  Известный факт: {contradiction.known_fact}
  Новое утверждение: {contradiction.new_claim}

Новость для проверки:
  Заголовок: {confirming_item.title}
  Источник:  {confirming_item.source}
  Содержание: {confirming_item.summary or confirming_item.title}

Подтверждает ли эта новость изменение?"""

    response = analyze_news(CONFIRM_SYSTEM, prompt)
    if not response:
        return False

    parsed = parse_json(response)
    if parsed and parsed.get("confirms") and float(parsed.get("confidence", 0)) >= 0.7:
        if confirming_item.url not in contradiction.confirmed_by:
            contradiction.confirmed_by.append(confirming_item.url)
        return True
    return False


def verify_all_news(
    news_items: list[NewsItem],
    graph: KnowledgeGraph,
) -> tuple[KnowledgeGraph, list[VerificationResult], list[Contradiction]]:
    results: list[VerificationResult] = []
    all_contradictions: list[Contradiction] = []
    pending: list[Contradiction] = []

    for item in news_items:
        for contradiction in pending:
            if item.entity_id == contradiction.entity_name or True:
                try_confirm_contradiction(contradiction, item, graph)

        result = verify_news_item(item, graph)
        results.append(result)

        if not result.is_clean:
            all_contradictions.extend(result.contradictions)
            pending.extend(result.contradictions)

    if all_contradictions:
        from shared.fact_checker.applier import apply_contradictions_to_graph
        graph = apply_contradictions_to_graph(graph, all_contradictions)
        logger.info(
            "Fact check complete: %d contradictions (%d high, %d confirmed)",
            len(all_contradictions),
            sum(1 for c in all_contradictions if c.severity == "high"),
            sum(1 for c in all_contradictions if c.is_confirmed),
        )

    return graph, results, all_contradictions
