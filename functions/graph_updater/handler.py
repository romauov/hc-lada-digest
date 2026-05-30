"""
graph_updater — анализирует новости через YandexGPT и обновляет граф знаний.

Что делает:
  1. Для каждой новости определяет: важность, тип (факт/слух), новые сущности
  2. Обновляет priority_score сущностей (mark_mentioned / mark_not_mentioned)
  3. Добавляет новые сущности и связи найденные в новостях
  4. Обновляет confidence существующих связей при появлении противоречий
"""
import json
import logging
import re
from datetime import date

from shared.models import Entity, KnowledgeGraph, NewsItem, Relation
from shared.priority import mark_mentioned, mark_not_mentioned, update_priorities
from shared.openrouter import analyze_news

logger = logging.getLogger(__name__)

# ── Промпты ───────────────────────────────────────────────────────────────────

EXTRACT_SYSTEM = """Ты — аналитик спортивных новостей. Извлекай структурированную информацию из новостей о хоккейном клубе.

Отвечай ТОЛЬКО валидным JSON без пояснений и markdown.

Формат:
{
  "is_relevant": true,
  "is_rumour": false,
  "importance": 0.8,
  "new_entities": [
    {
      "name": "Иван Петров",
      "type": "player",
      "relation_to_root": "HAS_PLAYER",
      "confidence": 0.9,
      "is_rumour": false,
      "search_queries": ["Иван Петров хоккей", "Петров Лада КХЛ"]
    }
  ],
  "updated_relations": [
    {
      "entity_name": "Игорь Сидоров",
      "relation_type": "HAS_COACH",
      "action": "add|remove|confirm",
      "confidence": 0.7,
      "is_rumour": true
    }
  ],
  "summary": "Краткое резюме в 1-2 предложения"
}

Типы сущностей: hockey_club, coach, player, sponsor, arena, farm_club, official, league, other
importance: 1.0=трансфер/отставка, 0.5=результат матча, 0.3=упоминание без события
is_rumour=true если: "возможно", "по слухам", "источники сообщают", "может перейти", "якобы" """

VERIFY_SYSTEM = """Ты — аналитик фактов о хоккейном клубе.
Определи: противоречит ли новость известным фактам в графе?

Отвечай ТОЛЬКО валидным JSON:
{
  "has_contradiction": false,
  "contradictions": [
    {
      "entity_name": "Игорь Петров",
      "known_fact": "тренер Лады с 2023 года",
      "new_claim": "перешёл в Ак Барс",
      "severity": "high"
    }
  ]
}"""


# ── Вспомогательные ───────────────────────────────────────────────────────────

def _parse_json(text: str) -> dict | None:
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


def _entity_exists(graph: KnowledgeGraph, name: str) -> tuple[bool, str | None]:
    name_lower = name.lower()
    for eid, entity in graph.entities.items():
        if entity.name.lower() == name_lower or entity.full_name.lower() == name_lower:
            return True, eid
    return False, None


def _make_entity_id(name: str, type_: str) -> str:
    slug = re.sub(r'[^a-zа-я0-9]', '_', name.lower())[:30]
    return f"{type_[:6]}_{slug}"


# ── Анализ одной новости ──────────────────────────────────────────────────────

def _analyze_news_item(item: NewsItem, graph: KnowledgeGraph) -> dict | None:
    root      = graph.entities.get(graph.root_id)
    root_name = root.name if root else "клуб"
    known     = "\n".join(f"- {e.name} ({e.type})" for e in graph.entities.values())

    prompt = f"""Клуб: {root_name}

Известные сущности:
{known}

Заголовок: {item.title}
Источник: {item.source}
URL: {item.url}

Проанализируй и верни JSON."""

    resp = analyze_news(EXTRACT_SYSTEM, prompt)
    return _parse_json(resp) if resp else None


def _check_contradictions(item: NewsItem, graph: KnowledgeGraph) -> list[dict]:
    facts = [
        f"{graph.entities[r.from_id].name} —[{r.type}]→ {graph.entities[r.to_id].name} (conf: {r.confidence})"
        for r in graph.relations
        if r.from_id in graph.entities and r.to_id in graph.entities and not r.is_rumour
    ]
    if not facts:
        return []

    root      = graph.entities.get(graph.root_id)
    root_name = root.name if root else "клуб"

    prompt = f"""Клуб: {root_name}
Известные факты:
{chr(10).join(facts)}

Новость: {item.title}
Источник: {item.source}"""

    resp = analyze_news(VERIFY_SYSTEM, prompt)
    if not resp:
        return []
    parsed = _parse_json(resp)
    return parsed.get("contradictions", []) if parsed and parsed.get("has_contradiction") else []


# ── Применение изменений ──────────────────────────────────────────────────────

def _apply_new_entities(graph: KnowledgeGraph, new_entities: list[dict], source_url: str) -> KnowledgeGraph:
    for ent in new_entities:
        name = ent.get("name", "").strip()
        if not name:
            continue
        exists, _ = _entity_exists(graph, name)
        if exists:
            continue

        eid = _make_entity_id(name, ent.get("type", "other"))
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


def _apply_updated_relations(graph: KnowledgeGraph, updates: list[dict]) -> KnowledgeGraph:
    for upd in updates:
        exists, eid = _entity_exists(graph, upd.get("entity_name", ""))
        if not exists:
            continue
        for rel in graph.relations:
            if rel.to_id == eid and rel.type == upd.get("relation_type"):
                action     = upd.get("action", "confirm")
                confidence = float(upd.get("confidence", 0.5))
                is_rumour  = upd.get("is_rumour", False)
                if action == "remove":
                    rel.confidence = max(0.0, rel.confidence - 0.2)
                    rel.is_rumour  = is_rumour
                elif action in ("confirm", "add"):
                    rel.confidence = min(1.0, max(rel.confidence, confidence))
                    if rel.is_rumour and not is_rumour and confidence > 0.7:
                        rel.is_rumour = False
                        logger.info("Rumour confirmed: %s", upd.get("entity_name"))
    return graph


# ── Главная функция ───────────────────────────────────────────────────────────

def update_graph_from_news(
    graph:         KnowledgeGraph,
    all_news:      list[NewsItem],
    mentioned_ids: set[str],
) -> tuple[KnowledgeGraph, list[dict]]:
    """
    Обновляет граф на основе найденных новостей.
    Возвращает (обновлённый граф, список аналитических данных для digest_generator).
    """
    analysis_results = []

    for item in all_news:
        analysis = _analyze_news_item(item, graph)
        if not analysis or not analysis.get("is_relevant", True):
            continue

        item.is_rumour       = analysis.get("is_rumour", False)
        item.relevance_score = float(analysis.get("importance", 0.5))
        item.summary         = analysis.get("summary", "")

        if analysis.get("new_entities"):
            graph = _apply_new_entities(graph, analysis["new_entities"], item.url)

        if analysis.get("updated_relations"):
            graph = _apply_updated_relations(graph, analysis["updated_relations"])

        # проверка противоречий для важных новостей
        if item.relevance_score > 0.6:
            contradictions = _check_contradictions(item, graph)
            if contradictions:
                logger.warning("Contradictions in '%s': %s", item.title, contradictions)

        analysis_results.append({"item": item, "analysis": analysis})

    # обновляем streak и приоритеты
    for eid, entity in graph.entities.items():
        graph.entities[eid] = (
            mark_mentioned(entity) if eid in mentioned_ids else mark_not_mentioned(entity)
        )

    graph.entities    = update_priorities(graph.entities)
    graph.version    += 1
    graph.last_updated = date.today().isoformat()

    return graph, analysis_results


# ── Cloud Function handler ────────────────────────────────────────────────────

def handler(event: dict, context) -> dict:
    from shared.models import KnowledgeGraph as KG, NewsItem as NI
    graph     = KG.from_dict(event["graph"])
    mentioned = set(event.get("mentioned_ids", []))
    news      = [NI(**n) for n in event.get("news", [])]
    updated, analysis = update_graph_from_news(graph, news, mentioned)
    return {"status": "ok", "graph": updated.to_dict(), "analysis_count": len(analysis)}


# ── Интеграция верификации фактов (итерация 5) ────────────────────────────────

def update_graph_with_fact_check(
    graph:         KnowledgeGraph,
    all_news:      list[NewsItem],
    mentioned_ids: set[str],
) -> tuple[KnowledgeGraph, list[dict], list]:
    """
    Расширенная версия update_graph_from_news с верификацией фактов.
    Вызывается из оркестратора когда USE_FACT_CHECK=true.

    Возвращает (граф, analysis_results, contradictions).
    """
    from shared.fact_checker import verify_all_news, format_fact_check_block

    # сначала стандартное обновление графа
    graph, analysis_results = update_graph_from_news(graph, all_news, mentioned_ids)

    # затем верификация фактов по тем же новостям
    graph, _verif_results, contradictions = verify_all_news(all_news, graph)

    if contradictions:
        logger.warning(
            "Fact check: %d contradictions, %d high severity",
            len(contradictions),
            sum(1 for c in contradictions if c.severity == "high"),
        )

    return graph, analysis_results, contradictions
