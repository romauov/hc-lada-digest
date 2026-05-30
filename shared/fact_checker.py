"""
fact_checker.py — верификация фактов из новостей против графа знаний.

Логика:
  1. Граф содержит подтверждённые факты (confidence >= FACT_THRESHOLD)
  2. Каждая важная новость проверяется на противоречия с этими фактами
  3. Противоречия классифицируются по severity: high / medium / low
  4. Результат влияет на:
     - is_rumour флаг сущности/связи
     - confidence связи (снижается при противоречии)
     - отдельный блок в дайджесте "⚠️ Требует проверки"
     - алерт админу при severity=high

Пример:
  Граф: Лада —[HAS_COACH]→ Петров (confidence=1.0, since=2023-09-01)
  Новость: "Петров покинул Ладу и возглавил Ак Барс"
  → Contradiction(severity=high, known="тренер Лады с 2023", claim="возглавил Ак Барс")
  → confidence снижается до 0.5, is_rumour=True до подтверждения из 2+ источников
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from shared.models import Entity, KnowledgeGraph, NewsItem, Relation
from shared.openrouter import analyze_news

logger = logging.getLogger(__name__)

# Минимальный confidence чтобы факт считался подтверждённым
FACT_THRESHOLD = 0.75
# Минимальная важность новости чтобы запускать верификацию
VERIFY_MIN_IMPORTANCE = 0.5
# Сколько источников нужно чтобы подтвердить спорный факт
CONFIRMATION_SOURCES_NEEDED = 2


# ── Модели ────────────────────────────────────────────────────────────────────

@dataclass
class Contradiction:
    entity_name:  str
    relation_type: str
    known_fact:   str
    new_claim:    str
    severity:     str        # high | medium | low
    source_url:   str = ""
    source_name:  str = ""
    confirmed_by: list[str] = field(default_factory=list)   # URLs подтверждающих источников

    @property
    def is_confirmed(self) -> bool:
        return len(self.confirmed_by) >= CONFIRMATION_SOURCES_NEEDED

    def to_dict(self) -> dict:
        return {
            "entity_name":   self.entity_name,
            "relation_type": self.relation_type,
            "known_fact":    self.known_fact,
            "new_claim":     self.new_claim,
            "severity":      self.severity,
            "source_url":    self.source_url,
            "source_name":   self.source_name,
            "confirmed_by":  self.confirmed_by,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Contradiction":
        return cls(**d)


@dataclass
class VerificationResult:
    news_item:      NewsItem
    contradictions: list[Contradiction] = field(default_factory=list)
    is_clean:       bool = True         # нет противоречий
    skip_reason:    str = ""            # почему верификация пропущена

    @property
    def has_high_severity(self) -> bool:
        return any(c.severity == "high" for c in self.contradictions)

    def to_digest_block(self) -> str:
        """Форматирует блок для дайджеста если есть противоречия."""
        if self.is_clean or not self.contradictions:
            return ""
        lines = []
        for c in self.contradictions:
            icon = "🔴" if c.severity == "high" else ("🟡" if c.severity == "medium" else "🔵")
            lines.append(
                f'{icon} <b>{c.entity_name}</b>: {c.new_claim} '
                f'<i>(известно: {c.known_fact})</i> — '
                f'<a href="{self.news_item.url}">источник</a>'
            )
        return "\n".join(lines)


# ── Промпты ───────────────────────────────────────────────────────────────────

VERIFY_SYSTEM = """Ты — аналитик фактов о хоккейном клубе. Проверяй новости на противоречия с известными фактами.

Отвечай ТОЛЬКО валидным JSON без пояснений:
{
  "has_contradiction": false,
  "contradictions": [
    {
      "entity_name": "Игорь Петров",
      "relation_type": "HAS_COACH",
      "known_fact": "тренер Лады с сентября 2023",
      "new_claim": "возглавил Ак Барс",
      "severity": "high"
    }
  ]
}

Severity:
  high   — прямое противоречие подтверждённому факту (тренер X теперь в клубе Y)
  medium — возможное противоречие или неточность (другая дата, другая роль)
  low    — косвенное или спорное расхождение

Не считай противоречием:
  - добавление новой информации (у клуба появился новый игрок — это не противоречит старым)
  - уточнение деталей
  - события в будущем ("планирует перейти" — это слух, не факт)"""


CONFIRM_SYSTEM = """Ты — аналитик фактов о хоккейном клубе.
Подтверждает ли эта новость указанное изменение факта?

Отвечай ТОЛЬКО валидным JSON:
{
  "confirms": true,
  "confidence": 0.9,
  "explanation": "Новость прямо сообщает об уходе тренера"
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
    return None


def _build_facts_block(graph: KnowledgeGraph) -> str:
    """Строит текстовое описание подтверждённых фактов для промпта."""
    facts = []
    for rel in graph.relations:
        if rel.confidence < FACT_THRESHOLD or rel.is_rumour:
            continue
        from_e = graph.entities.get(rel.from_id)
        to_e   = graph.entities.get(rel.to_id)
        if not from_e or not to_e:
            continue
        since = f" с {rel.since}" if rel.since else ""
        facts.append(
            f"- {from_e.name} —[{rel.type}]→ {to_e.name}{since} "
            f"(confidence: {rel.confidence:.2f})"
        )
    return "\n".join(facts) if facts else "(нет подтверждённых фактов)"


# ── Основные функции ──────────────────────────────────────────────────────────

def verify_news_item(item: NewsItem, graph: KnowledgeGraph) -> VerificationResult:
    """
    Проверяет одну новость на противоречия с графом.
    Пропускает проверку если новость неважная или граф пуст.
    """
    result = VerificationResult(news_item=item)

    # пропускаем неважные новости
    if item.relevance_score < VERIFY_MIN_IMPORTANCE:
        result.skip_reason = f"low importance ({item.relevance_score:.2f})"
        return result

    facts_block = _build_facts_block(graph)
    if facts_block == "(нет подтверждённых фактов)":
        result.skip_reason = "no confirmed facts in graph"
        return result

    root      = graph.entities.get(graph.root_id)
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

    parsed = _parse_json(response)
    if not parsed or not parsed.get("has_contradiction"):
        return result   # is_clean=True

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
    """
    Проверяет подтверждает ли новая новость уже найденное противоречие.
    Возвращает True если подтверждение засчитано.
    """
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

    parsed = _parse_json(response)
    if parsed and parsed.get("confirms") and float(parsed.get("confidence", 0)) >= 0.7:
        if confirming_item.url not in contradiction.confirmed_by:
            contradiction.confirmed_by.append(confirming_item.url)
        return True
    return False


def apply_contradictions_to_graph(
    graph:           KnowledgeGraph,
    contradictions:  list[Contradiction],
) -> KnowledgeGraph:
    """
    Применяет найденные противоречия к графу:
      - снижает confidence связей
      - помечает неподтверждённые изменения как слухи
      - подтверждённые изменения обновляют граф
    """
    for c in contradictions:
        # находим сущность
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
                # подтверждено из нескольких источников → обновляем граф
                logger.info(
                    "Confirmed change: %s [%s] (confirmed by %d sources)",
                    c.entity_name, c.relation_type, len(c.confirmed_by),
                )
                rel.confidence  = max(0.1, rel.confidence - 0.4)
                rel.is_rumour   = False   # это уже факт, просто изменившийся
                rel.until       = date.today().isoformat()
            else:
                # пока только один источник → помечаем как спорное
                rel.confidence = max(0.3, rel.confidence - 0.2)
                rel.is_rumour  = True
                logger.info(
                    "Relation flagged as rumour: %s [%s] conf→%.2f",
                    c.entity_name, c.relation_type, rel.confidence,
                )

    return graph


# ── Пакетная верификация ──────────────────────────────────────────────────────

def verify_all_news(
    news_items: list[NewsItem],
    graph:      KnowledgeGraph,
) -> tuple[KnowledgeGraph, list[VerificationResult], list[Contradiction]]:
    """
    Верифицирует все новости, обновляет граф при подтверждённых изменениях.

    Возвращает:
      - обновлённый граф
      - список результатов верификации
      - список всех найденных противоречий (для дайджеста и алертов)
    """
    results:          list[VerificationResult] = []
    all_contradictions: list[Contradiction]   = []
    # накапливаем противоречия и ищем подтверждения в следующих новостях
    pending: list[Contradiction] = []

    for item in news_items:
        # пробуем подтвердить уже найденные противоречия
        for contradiction in pending:
            if item.entity_id == contradiction.entity_name or True:  # проверяем все
                try_confirm_contradiction(contradiction, item, graph)

        # верифицируем текущую новость
        result = verify_news_item(item, graph)
        results.append(result)

        if not result.is_clean:
            all_contradictions.extend(result.contradictions)
            pending.extend(result.contradictions)

    # применяем противоречия к графу
    if all_contradictions:
        graph = apply_contradictions_to_graph(graph, all_contradictions)
        logger.info(
            "Fact check complete: %d contradictions (%d high, %d confirmed)",
            len(all_contradictions),
            sum(1 for c in all_contradictions if c.severity == "high"),
            sum(1 for c in all_contradictions if c.is_confirmed),
        )

    return graph, results, all_contradictions


def format_fact_check_block(contradictions: list[Contradiction]) -> str:
    """
    Формирует блок '⚠️ Требует проверки' для дайджеста.
    Возвращает пустую строку если противоречий нет.
    """
    if not contradictions:
        return ""

    high   = [c for c in contradictions if c.severity == "high"]
    medium = [c for c in contradictions if c.severity == "medium"]
    other  = [c for c in contradictions if c.severity == "low"]

    lines = ["⚠️ <b>Требует проверки</b>"]
    for c in high + medium + other:
        icon      = "🔴" if c.severity == "high" else ("🟡" if c.severity == "medium" else "🔵")
        confirmed = " ✓ подтверждено" if c.is_confirmed else ""
        lines.append(
            f'{icon} <b>{c.entity_name}</b>: {c.new_claim}'
            f'{confirmed} — <a href="{c.source_url}">{c.source_name}</a>\n'
            f'   <i>Известно: {c.known_fact}</i>'
        )
    return "\n".join(lines)
