import logging

from shared.graph_ops import parse_json
from shared.models import KnowledgeGraph, NewsItem
from shared.openrouter import analyze_news

logger = logging.getLogger(__name__)

EXTRACT_SYSTEM = """Ты — аналитик спортивных новостей о ХК ЛАДА Тольятти (хоккейный клуб, КХЛ).
ВНИМАНИЕ: «Лада» здесь — это хоккейный клуб, а НЕ автомобиль. Если новость про автомобили Лада, автозапчасти или АвтоВАЗ (не про спонсорство) — ставь "is_relevant": false.

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


def analyze_news_item(item: NewsItem, graph: KnowledgeGraph) -> dict | None:
    root = graph.entities.get(graph.root_id)
    root_name = root.name if root else "клуб"
    known = "\n".join(f"- {e.name} ({e.type})" for e in graph.entities.values())

    prompt = f"""Клуб: {root_name}

Известные сущности:
{known}

Заголовок: {item.title}
Источник: {item.source}
URL: {item.url}

Проанализируй и верни JSON."""

    resp = analyze_news(EXTRACT_SYSTEM, prompt)
    return parse_json(resp) if resp else None


def check_contradictions(item: NewsItem, graph: KnowledgeGraph) -> list[dict]:
    facts = [
        f"{graph.entities[r.from_id].name} —[{r.type}]→ {graph.entities[r.to_id].name} (conf: {r.confidence})"
        for r in graph.relations
        if r.from_id in graph.entities and r.to_id in graph.entities and not r.is_rumour
    ]
    if not facts:
        return []

    root = graph.entities.get(graph.root_id)
    root_name = root.name if root else "клуб"

    prompt = f"""Клуб: {root_name}
Известные факты:
{chr(10).join(facts)}

Новость: {item.title}
Источник: {item.source}"""

    resp = analyze_news(VERIFY_SYSTEM, prompt)
    if not resp:
        return []
    parsed = parse_json(resp)
    return parsed.get("contradictions", []) if parsed and parsed.get("has_contradiction") else []
