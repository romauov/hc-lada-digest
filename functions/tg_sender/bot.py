"""
telegram bot — интерактивный режим.
Принимает сообщения от админа, отвечает на вопросы о ХК Лада.
Использует граф знаний + YandexGPT/OpenRouter для веб-поиска.
"""
import logging
import os
import re
import time
import traceback
from datetime import date

import requests

from shared.models import KnowledgeGraph
from shared.storage import load_graph, save_graph
from shared.openrouter import analyze_news, search_answer, classify_need_search, extract_from_bot_answer
from shared.models import Entity, Relation
from shared.storage import load_digest
from shared.tg_format import sanitize_tg_html
from shared.history import HistoryStore

logger = logging.getLogger(__name__)

TG_API   = "https://api.telegram.org/bot{token}"
TOKEN    = os.environ["TG_BOT_TOKEN"]
ADMIN_ID = os.environ.get("TG_ADMIN_ID", "")

LOG_DIR  = os.path.join(os.environ.get("DATA_DIR", "/data"), "logs")
_log_date = ""

def _today_log() -> str:
    return os.path.join(LOG_DIR, f"bot-{date.today().isoformat()}.log")


def _init_logger():
    global _log_date
    os.makedirs(LOG_DIR, exist_ok=True)
    _log_date = date.today().isoformat()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler = logging.FileHandler(_today_log(), encoding="utf-8")
    handler.setFormatter(fmt)
    root.addHandler(handler)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(logging.StreamHandler())


def _rotate_log():
    global _log_date
    today = date.today().isoformat()
    if _log_date == today:
        return
    _log_date = today
    root = logging.getLogger()
    for h in root.handlers[:]:
        if isinstance(h, logging.FileHandler):
            h.close()
            root.removeHandler(h)
    handler = logging.FileHandler(_today_log(), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(handler)
    logger.info("Log rotated to %s", _today_log())

POLL_TIMEOUT = 30
SLEEP_ON_ERR = 5

_store = HistoryStore(os.path.join(os.environ.get("DATA_DIR", "/data"), "history.db"))


def _add_history(chat_id: str, role: str, text: str):
    _store.add_message(chat_id, role, text)


def _history_context(chat_id: str) -> str:
    hist = _store.get_context(chat_id)
    if not hist:
        return ""
    lines = ["История диалога:"]
    for entry in hist:
        role = "Пользователь" if entry["role"] == "user" else "Бот"
        lines.append(f"{role}: {entry['text']}")
    return "\n".join(lines)



def _reset_history(chat_id: str):
    _store.reset(chat_id)


GRAPH_SYSTEM = """Ты — эксперт по хоккейному клубу «Лада» Тольятти (ХК Лада, КХЛ).
ВНИМАНИЕ: «Лада» в твоём контексте — это ХОККЕЙНЫЙ КЛУБ, а не автомобиль Лада (АвтоВАЗ).
Игнорируй всё, что связано с автомобилями, автозапчастями или АвтоВАЗом (кроме спонсорства).

Учитывай историю диалога — пользователь может уточнять или исправлять предыдущие ответы.
Отвечай на вопрос пользователя на основе предоставленных фактов из графа знаний.
Если информации в графе достаточно — дай чёткий ответ со ссылкой на факты.
Если не хватает — скажи только: UNKNOWN"""

SEARCH_SYSTEM = """Ты — эксперт по хоккейному клубу «Лада» Тольятти (ХК Лада, КХЛ).
ВНИМАНИЕ: «Лада» в твоём контексте — это ХОККЕЙНЫЙ КЛУБ, а не автомобиль Лада (АвтоВАЗ).
Игнорируй всё, что связано с автомобилями, автозапчастями или АвтоВАЗом (кроме спонсорства).

Учитывай историю диалога — пользователь может уточнять или исправлять предыдущие ответы.
Выполни поиск в интернете и ответь на вопрос пользователя.
Используй только информацию из поиска. Укажи источники (ссылки) в конце ответа.
Если ничего не нашёл — так и скажи."""


def _send_file(chat_id: str, file_path: str) -> bool:
    url = f"{TG_API.format(token=TOKEN)}/sendDocument"
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(url, data={"chat_id": chat_id}, files={"document": f}, timeout=30)
        if not resp.ok:
            logger.error("send file error: %s %s", resp.status_code, resp.text)
        return resp.ok
    except Exception as e:
        logger.error("send file exception: %s", e)
        return False


def _send(chat_id: str, text: str) -> bool:
    text = sanitize_tg_html(text)
    url = f"{TG_API.format(token=TOKEN)}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": text[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
        if not resp.ok:
            logger.error("send error: %s %s", resp.status_code, resp.text)
        return resp.ok
    except Exception as e:
        logger.error("send exception: %s", e)
        return False


def _format_graph(graph: KnowledgeGraph) -> str:
    by_type = {}
    for e in graph.entities.values():
        by_type.setdefault(e.type, []).append(e.name)
    lines = [
        f"📊 <b>Граф знаний</b>",
        f"Версия: {graph.version} | {graph.last_updated}",
        f"",
    ]
    for t, names in sorted(by_type.items(), key=lambda x: -len(x[1])):
        icon = {"hockey_club": "🏒", "coach": "👤", "player": "👤", "sponsor": "🏢",
                "arena": "🏟", "farm_club": "🏒", "official": "👔", "league": "🏆",
                "other": "📌"}.get(t, "📌")
        lines.append(f"{icon} {t}: {len(names)}")
    lines.append(f"")
    lines.append(f"Связей: {len(graph.relations)}")
    top = sorted(graph.entities.values(), key=lambda e: e.priority_score, reverse=True)[:5]
    lines.append(f"")
    lines.append(f"<b>Топ по приоритету:</b>")
    for e in top:
        mentioned = e.last_mentioned or "никогда"
        lines.append(f"• {e.name} ({e.priority_score}) — {mentioned}")
    return "\n".join(lines)


def _answer_from_graph(question: str, graph: KnowledgeGraph, chat_id: str) -> str | None:
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
    history = _history_context(chat_id)
    prompt = f"""Клуб: ХК Лада Тольятти (хоккейный клуб, КХЛ). Не путать с автомобилем Лада.

Известные сущности:
{known}

Факты (связи между сущностями):
{facts}

{history}

Дата: {date.today().isoformat()}

Вопрос пользователя:
{question}"""
    result = analyze_news(GRAPH_SYSTEM, prompt)
    if result and "UNKNOWN" not in result:
        return result
    return None


def _search_and_answer(question: str, chat_id: str) -> str | None:
    history = _history_context(chat_id)
    prompt = f"""Контекст: ХК Лада Тольятти — хоккейный клуб, выступает в КХЛ.
«Лада» здесь — это ХОККЕЙНЫЙ КЛУБ, а не автомобиль. Игнорируй автомобильные темы.

{history}

Дата: {date.today().isoformat()}

Вопрос пользователя:
{question}"""
    return search_answer(SEARCH_SYSTEM, prompt)


def _update_graph_from_answer(question: str, answer: str) -> None:
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


def _handle_message(text: str, chat_id: str) -> str | None:
    cmd = text.split()[0]

    if cmd in ("/start", "/help"):
        _reset_history(chat_id)
        return (
            "Привет! Я бот ХК Лада.\n\n"
            "<b>Команды:</b>\n"
            "/graph — граф знаний клуба\n"
            "/digest — сегодняшний дайджест\n"
            "/logs — файл с логами бота\n"
            "/help — эта справка\n\n"
            "<b>Вопросы:</b>\n"
            "Просто напиши вопрос о клубе — отвечу из графа знаний "
            "или найду в интернете."
        )

    if text.startswith("/graph"):
        _reset_history(chat_id)
        graph = load_graph()
        if not graph:
            return "Граф знаний не найден."
        _send(chat_id, _format_graph(graph))
        import json, tempfile
        j = json.dumps(graph.to_dict(), ensure_ascii=False, indent=2)
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        tmp.write(j)
        tmp.close()
        _send_file(chat_id, tmp.name)
        os.unlink(tmp.name)
        return None

    if text.startswith("/digest"):
        _reset_history(chat_id)
        today = date.today().isoformat()
        digest = load_digest(today)
        if digest:
            return digest
        return "Дайджест за сегодня ещё не сформирован."

    if text.startswith("/logs"):
        _reset_history(chat_id)
        sent = False
        for f in (_today_log(), os.path.join(LOG_DIR, f"orchestrator-{date.today().isoformat()}.log")):
            if os.path.exists(f) and os.path.getsize(f) > 0:
                _send_file(chat_id, f)
                sent = True
        return None if sent else "Логов нет."

    _add_history(chat_id, "user", text)

    if classify_need_search(text, _history_context(chat_id)):
        logger.info("Search triggered by LLM classifier: %s", text[:80])
        answer = _search_and_answer(text, chat_id)
        if answer:
            _add_history(chat_id, "assistant", answer)
            _update_graph_from_answer(text, answer)
        return answer

    graph = load_graph()
    if graph:
        answer = _answer_from_graph(text, graph, chat_id)
        if answer:
            _add_history(chat_id, "assistant", answer)
            return answer

    logger.info("Graph could not answer, searching via web search: %s", text[:80])
    answer = _search_and_answer(text, chat_id)
    if answer:
        _add_history(chat_id, "assistant", answer)
        _update_graph_from_answer(text, answer)
    return answer


def poll():
    if not ADMIN_ID:
        logger.error("TG_ADMIN_ID not set — bot disabled")
        return

    logger.info("Bot started for admin %s", ADMIN_ID)
    offset = 0

    while True:
        _rotate_log()
        try:
            url = f"{TG_API.format(token=TOKEN)}/getUpdates"
            resp = requests.get(url, params={
                "offset": offset,
                "timeout": POLL_TIMEOUT,
                "allowed_updates": ["message"],
            }, timeout=POLL_TIMEOUT + 5)
            resp.raise_for_status()
            data = resp.json()

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg:
                    continue

                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "").strip()

                if chat_id != ADMIN_ID:
                    logger.info("Ignored message from %s", chat_id)
                    continue

                if not text:
                    continue

                logger.info("Question from admin: %s", text[:100])
                answer = _handle_message(text, chat_id)
                if answer:
                    _send(chat_id, answer)

        except requests.Timeout:
            pass
        except Exception as e:
            logger.error("poll error (%s): %s\n%s", type(e).__name__, e, traceback.format_exc())
            time.sleep(SLEEP_ON_ERR)


if __name__ == "__main__":
    _init_logger()
    poll()
