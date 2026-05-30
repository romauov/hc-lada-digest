"""
telegram bot — интерактивный режим.
Принимает сообщения от админа, отвечает на вопросы о ХК Лада.
Использует граф знаний + Perplexity через OpenRouter для веб-поиска.
"""
import logging
import os
import time
from datetime import date

import requests

from shared.models import KnowledgeGraph
from shared.storage import load_graph, save_graph
from shared.openrouter import analyze_news, search_answer

logger = logging.getLogger(__name__)

TG_API   = "https://api.telegram.org/bot{token}"
TOKEN    = os.environ["TG_BOT_TOKEN"]
ADMIN_ID = os.environ.get("TG_ADMIN_ID", "")

LOG_DIR  = os.path.join(os.environ.get("DATA_DIR", "/data"), "logs")

def _today_log() -> str:
    return os.path.join(LOG_DIR, f"bot-{date.today().isoformat()}.log")

POLL_TIMEOUT = 30
SLEEP_ON_ERR = 5

GRAPH_SYSTEM = """Ты — эксперт по хоккейному клубу «Лада» Тольятти (ХК Лада, КХЛ).
ВНИМАНИЕ: «Лада» в твоём контексте — это ХОККЕЙНЫЙ КЛУБ, а не автомобиль Лада (АвтоВАЗ).
Игнорируй всё, что связано с автомобилями, автозапчастями или АвтоВАЗом (кроме спонсорства).

Отвечай на вопрос пользователя на основе предоставленных фактов из графа знаний.
Если информации в графе достаточно — дай чёткий ответ со ссылкой на факты.
Если не хватает — скажи только: UNKNOWN"""

SEARCH_SYSTEM = """Ты — эксперт по хоккейному клубу «Лада» Тольятти (ХК Лада, КХЛ).
ВНИМАНИЕ: «Лада» в твоём контексте — это ХОККЕЙНЫЙ КЛУБ, а не автомобиль Лада (АвтоВАЗ).
Игнорируй всё, что связано с автомобилями, автозапчастями или АвтоВАЗом (кроме спонсорства).

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


def _answer_from_graph(question: str, graph: KnowledgeGraph) -> str | None:
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
    prompt = f"""Клуб: ХК Лада Тольятти (хоккейный клуб, КХЛ). Не путать с автомобилем Лада.

Известные сущности:
{known}

Факты (связи между сущностями):
{facts}

Дата: {date.today().isoformat()}

Вопрос пользователя:
{question}"""
    result = analyze_news(GRAPH_SYSTEM, prompt)
    if result and "UNKNOWN" not in result:
        return result
    return None


def _search_and_answer(question: str) -> str | None:
    prompt = f"""Контекст: ХК Лада Тольятти — хоккейный клуб, выступает в КХЛ.
«Лада» здесь — это ХОККЕЙНЫЙ КЛУБ, а не автомобиль. Игнорируй автомобильные темы.

Дата: {date.today().isoformat()}

Вопрос пользователя:
{question}"""
    return search_answer(SEARCH_SYSTEM, prompt)


def _handle_message(text: str, chat_id: str) -> str | None:
    cmd = text.split()[0]

    if cmd in ("/start", "/help"):
        return (
            "Привет! Я бот ХК Лада.\n\n"
            "<b>Команды:</b>\n"
            "/graph — граф знаний клуба\n"
            "/logs — файл с логами бота\n"
            "/help — эта справка\n\n"
            "<b>Вопросы:</b>\n"
            "Просто напиши вопрос о клубе — отвечу из графа знаний "
            "или найду в интернете."
        )

    if text.startswith("/graph"):
        graph = load_graph()
        if not graph:
            return "Граф знаний не найден."
        return _format_graph(graph)

    if text.startswith("/logs"):
        sent = False
        for f in (_today_log(), os.path.join(LOG_DIR, f"orchestrator-{date.today().isoformat()}.log")):
            if os.path.exists(f) and os.path.getsize(f) > 0:
                _send_file(chat_id, f)
                sent = True
        return None if sent else "Логов нет."

    graph = load_graph()
    if graph:
        answer = _answer_from_graph(text, graph)
        if answer:
            return answer

    logger.info("Graph could not answer, searching via Perplexity: %s", text[:80])
    return _search_and_answer(text)


def poll():
    if not ADMIN_ID:
        logger.error("TG_ADMIN_ID not set — bot disabled")
        return

    logger.info("Bot started for admin %s", ADMIN_ID)
    offset = 0

    while True:
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
            logger.error("poll error: %s", e)
            time.sleep(SLEEP_ON_ERR)


if __name__ == "__main__":
    os.makedirs(LOG_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler = logging.FileHandler(_today_log(), encoding="utf-8")
    handler.setFormatter(fmt)
    root.addHandler(handler)
    if not root.handlers or not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(logging.StreamHandler())
    poll()
