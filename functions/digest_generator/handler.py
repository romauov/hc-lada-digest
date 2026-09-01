"""
digest_generator — генерирует структурированный дайджест через LLM.

Категории дайджеста:
  🏒 РЕЗУЛЬТАТЫ И МАТЧИ
  🔄 ТРАНСФЕРЫ И КАДРЫ
  🏗 ИНФРАСТРУКТУРА
  💬 СЛУХИ
  📌 ПРОЧЕЕ
"""
import logging
from datetime import date

from shared.models import KnowledgeGraph, NewsItem
from shared.openrouter import generate_digest

logger = logging.getLogger(__name__)

DIGEST_SYSTEM = """Ты — редактор спортивного дайджеста. Составляй краткий, информативный дайджест новостей о хоккейном клубе.

Правила:
- Используй только предоставленные новости, не добавляй факты от себя
- Каждый пункт: одно предложение + ссылка в формате <a href="URL">читать</a>
- Слухи выноси в отдельный раздел с пометкой "(не подтверждено)"
- Если в категории нет новостей — не включай её в дайджест
- Пиши живо, но по делу
- Отвечай ТОЛЬКО текстом дайджеста в HTML-форматировании для Telegram, без пояснений

Категории (используй только нужные):
🏒 РЕЗУЛЬТАТЫ И МАТЧИ — итоги игр, счета, турнирное положение
🔄 ТРАНСФЕРЫ И КАДРЫ — переходы игроков, тренерские назначения/отставки
🏗 ИНФРАСТРУКТУРА — арена, финансы, спонсоры, организационные изменения
💬 СЛУХИ — неподтверждённая информация
📌 ПРОЧЕЕ — всё остальное"""


def _build_prompt(news_with_analysis: list[dict], graph: KnowledgeGraph, today: str) -> str:
    root      = graph.entities.get(graph.root_id)
    root_name = root.full_name if root else "Хоккейный клуб"

    facts   = []
    rumours = []
    for entry in news_with_analysis:
        item: NewsItem = entry["item"]
        line = f'- {item.summary or item.title} | {item.source} | {item.url}'
        (rumours if item.is_rumour else facts).append(line)

    return f"""Клуб: {root_name}
Дата: {today}

ПОДТВЕРЖДЁННЫЕ НОВОСТИ:
{chr(10).join(facts) if facts else "(нет)"}

СЛУХИ:
{chr(10).join(rumours) if rumours else "(нет)"}

Составь дайджест согласно инструкции."""


def _fallback_digest(news_with_analysis: list[dict], graph: KnowledgeGraph, today: str) -> str:
    """Простой дайджест без LLM на случай недоступности модели."""
    root      = graph.entities.get(graph.root_id)
    root_name = root.name if root else "Клуб"
    display   = date.fromisoformat(today).strftime("%d.%m.%Y")

    lines   = [f"📰 <b>Дайджест {root_name} — {display}</b>\n"]
    facts   = [e["item"] for e in news_with_analysis if not e["item"].is_rumour]
    rumours = [e["item"] for e in news_with_analysis if e["item"].is_rumour]

    if facts:
        lines.append("<b>Новости</b>")
        for item in facts:
            lines.append(f'• {item.summary or item.title} — <a href="{item.url}">читать</a>')

    if rumours:
        lines.append("\n<b>💬 Слухи</b>")
        for item in rumours:
            lines.append(f'• {item.summary or item.title} <i>(не подтверждено)</i> — <a href="{item.url}">читать</a>')

    lines.append(f"\n<i>Материалов: {len(facts) + len(rumours)}</i>")
    return "\n".join(lines)


def build_digest(
    news_with_analysis: list[dict],
    graph: KnowledgeGraph,
    today: str | None = None,
) -> str:
    """
    Генерирует дайджест через LLM.
    При ошибке LLM возвращает fallback-дайджест.
    """
    today = today or date.today().isoformat()
    if not news_with_analysis:
        return ""

    response = generate_digest(DIGEST_SYSTEM, _build_prompt(news_with_analysis, graph, today))
    if response:
        logger.info("Digest generated (%d chars)", len(response))
        return response

    logger.warning("LLM unavailable — fallback digest")
    return _fallback_digest(news_with_analysis, graph, today)


# ── Cloud Function handler ────────────────────────────────────────────────────

def handler(event: dict, context) -> dict:
    from shared.models import KnowledgeGraph as KG, NewsItem as NI
    graph = KG.from_dict(event["graph"])
    today = event.get("today")
    news_with_analysis = [
        {"item": NI(**e["item"]), "analysis": e.get("analysis", {})}
        for e in event.get("news_with_analysis", [])
    ]
    digest = build_digest(news_with_analysis, graph, today)
    return {"status": "ok", "digest": digest}


# ── Дайджест с блоком верификации (итерация 5) ────────────────────────────────

def build_digest_with_fact_check(
    news_with_analysis: list[dict],
    graph:              KnowledgeGraph,
    contradictions:     list,
    today:              str | None = None,
) -> str:
    """
    Генерирует дайджест и добавляет в конец блок '⚠️ Требует проверки'
    если найдены противоречия с графом знаний.
    """
    from shared.fact_checker import format_fact_check_block

    digest = build_digest(news_with_analysis, graph, today)

    fact_block = format_fact_check_block(contradictions)
    if fact_block:
        digest = digest + "\n\n" + fact_block

    return digest
