import json
import logging
import os
import re
from datetime import date

from shared.history import HistoryStore
from shared.models import Entity, KnowledgeGraph, Relation
from shared.openrouter import analyze_news, search_answer
from shared.classifiers import extract_from_bot_answer
from shared.storage import load_graph, save_graph
from shared.tg_format import extract_message

logger = logging.getLogger(__name__)

_store = HistoryStore(os.path.join(os.environ.get("DATA_DIR", "/data"), "history.db"))


def add_history(chat_id: str, role: str, text: str):
    _store.add_message(chat_id, role, text)


def history_context(chat_id: str) -> str:
    hist = _store.get_context(chat_id)
    if not hist:
        return ""
    lines = ["История диалога:"]
    for entry in hist:
        role = "Пользователь" if entry["role"] == "user" else "Бот"
        lines.append(f"{role}: {entry['text']}")
    return "\n".join(lines)


def reset_history(chat_id: str):
    _store.reset(chat_id)


GRAPH_SYSTEM = """Ты — эксперт по хоккейному клубу «Лада» Тольятти (ХК Лада, КХЛ).
ВНИМАНИЕ: «Лада» в твоём контексте — это ХОККЕЙНЫЙ КЛУБ, а не автомобиль Лада (АвтоВАЗ).
Игнорируй всё, что связано с автомобилями, автозапчастями или АвтоВАЗом (кроме спонсорства).

Учитывай историю диалога — пользователь может уточнять или исправлять предыдущие ответы.
Отвечай на вопрос пользователя на основе предоставленных фактов из графа знаний.
Если информации в графе достаточно — дай чёткий ответ со ссылкой на факты.
Если подходит несколько фактов — перечисли их все, не ограничивайся одним.

Отвечай ТОЛЬКО валидным JSON без пояснений и markdown:
- Если знаешь ответ: {"found": true, "answer": "твой ответ"}
- Если не знаешь:    {"found": false}
Никакого другого текста, только JSON."""

SEARCH_SYSTEM = """Ты — эксперт по хоккейному клубу «Лада» Тольятти (ХК Лада, КХЛ).
ВНИМАНИЕ: «Лада» в твоём контексте — это ХОККЕЙНЫЙ КЛУБ, а не автомобиль Лада (АвтоВАЗ).
Игнорируй всё, что связано с автомобилями, автозапчастями или АвтоВАЗом (кроме спонсорства).

Учитывай историю диалога — пользователь может уточнять или исправлять предыдущие ответы.
Выполни поиск в интернете и ответь на вопрос пользователя.
Используй только информацию из поиска. Укажи источники (ссылки) в конце ответа.
Если ничего не нашёл — так и скажи."""


def answer_from_graph(question: str, graph: KnowledgeGraph, chat_id: str) -> str | None:
    root = graph.entities.get(graph.root_id)
    known = "\n".join(
        f"- {e.name} ({e.type}): {e.full_name or e.name}"
        for e in graph.entities.values()
    )
    facts = "\n".join(
        f"- {graph.entities[r.from_id].name} →[{r.type}]→ {graph.entities[r.to_id].name} (conf: {r.confidence})"
        for r in graph.relations
        if r.from_id in graph.entities and r.to_id in graph.entities
    )
    hist = history_context(chat_id)
    prompt = f"""Клуб: ХК Лада Тольятти (хоккейный клуб, КХЛ). Не путать с автомобилем Лада.

Известные сущности:
{known}

Факты (связи между сущностями):
{facts}

{hist}

Дата: {date.today().isoformat()}

Вопрос пользователя:
{question}"""
    result = analyze_news(GRAPH_SYSTEM, prompt)
    if result:
        try:
            data = json.loads(result)
            if data.get("found") and data.get("answer"):
                return data["answer"]
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def search_and_answer(question: str, chat_id: str) -> str | None:
    hist = history_context(chat_id)
    prompt = f"""Контекст: ХК Лада Тольятти — хоккейный клуб, выступает в КХЛ.
«Лада» здесь — это ХОККЕЙНЫЙ КЛУБ, а не автомобиль. Игнорируй автомобильные темы.

{hist}

Дата: {date.today().isoformat()}

Вопрос пользователя:
{question}"""
    result = search_answer(SEARCH_SYSTEM, prompt)
    return extract_message(result) if result else None


def update_graph_from_answer(question: str, answer: str) -> None:
    graph = load_graph()
    if not graph:
        return
    extracted = extract_from_bot_answer(question, answer, graph.to_dict())
    if not extracted:
        return

    changed = False
    new_entities = extracted.get("new_entities") or []
    for ent in new_entities:
        name = ent.get("name", "").strip()
        if not name:
            continue
        name_lower = name.lower()
        exists = any(
            e.name.lower() == name_lower or e.full_name.lower() == name_lower
            for e in graph.entities.values()
        )
        if exists:
            continue
        eid = re.sub(r'[^a-zа-я0-9]', '_', name.lower())[:30]
        eid = f"{ent.get('type', 'other')[:6]}_{eid}"
        entity = Entity(
            id=eid,
            type=ent.get("type", "other"),
            name=name,
            priority_score=0.3,
            search_queries=ent.get("search_queries") or [f"{name} хоккей"],
        )
        graph.entities[eid] = entity
        logger.info("Bot added entity: %s (%s)", name, entity.type)
        graph.relations.append(Relation(
            from_id=graph.root_id,
            to_id=eid,
            type=ent.get("relation_to_root", "RELATED_TO"),
            confidence=float(ent.get("confidence", 0.5)),
            since=date.today().isoformat(),
            is_rumour=False,
        ))
        changed = True

    updates = extracted.get("updated_relations") or []
    for upd in updates:
        ename = upd.get("entity_name", "").strip()
        if not ename:
            continue
        ename_lower = ename.lower()
        eid = None
        for e in graph.entities.values():
            if e.name.lower() == ename_lower or e.full_name.lower() == ename_lower:
                eid = e.id
                break
        if not eid:
            continue
        for rel in graph.relations:
            if rel.to_id == eid and rel.type == upd.get("relation_type"):
                action = upd.get("action", "confirm")
                conf = float(upd.get("confidence", 0.5))
                if action == "remove":
                    rel.confidence = max(0.0, rel.confidence - 0.2)
                elif action in ("confirm", "add"):
                    rel.confidence = min(1.0, max(rel.confidence, conf))
                changed = True
                break

    if changed:
        graph.version += 1
        graph.last_updated = date.today().isoformat()
        save_graph(graph)
        logger.info("Graph updated after bot answer (version %d)", graph.version)
