import logging
import os
import time
import traceback
from datetime import date

import requests

from shared.storage import load_graph, load_digest
from shared.tg_format import sanitize_tg_html, extract_message, md_to_tg, format_graph_summary
from shared.tg_send import send_message, send_document
from shared.classifiers import classify_need_search
from functions.tg_sender.answers import (
    add_history, history_context, reset_history,
    answer_from_graph, search_and_answer, update_graph_from_answer,
)

logger = logging.getLogger(__name__)

TG_API = "https://api.telegram.org/bot{token}"
TOKEN = os.environ["TG_BOT_TOKEN"]
ADMIN_ID = os.environ.get("TG_ADMIN_ID", "")

LOG_DIR = os.path.join(os.environ.get("DATA_DIR", "/data"), "logs")
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


def _send(chat_id: str, text: str) -> bool:
    text = extract_message(text)
    text = md_to_tg(text)
    text = sanitize_tg_html(text)
    return send_message(TOKEN, chat_id, text[:4000])


def _send_file(chat_id: str, file_path: str) -> bool:
    return send_document(TOKEN, chat_id, file_path)


def _handle_message(text: str, chat_id: str) -> str | None:
    cmd = text.split()[0]

    if cmd in ("/start", "/help"):
        reset_history(chat_id)
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
        reset_history(chat_id)
        graph = load_graph()
        if not graph:
            return "Граф знаний не найден."
        _send(chat_id, format_graph_summary(graph))
        import json, tempfile
        j = json.dumps(graph.to_dict(), ensure_ascii=False, indent=2)
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        tmp.write(j)
        tmp.close()
        _send_file(chat_id, tmp.name)
        os.unlink(tmp.name)
        return None

    if text.startswith("/digest"):
        reset_history(chat_id)
        today = date.today().isoformat()
        digest = load_digest(today)
        if digest:
            return digest
        return "Дайджест за сегодня ещё не сформирован."

    if text.startswith("/logs"):
        reset_history(chat_id)
        sent = False
        for f in (_today_log(), os.path.join(LOG_DIR, f"orchestrator-{date.today().isoformat()}.log")):
            if os.path.exists(f) and os.path.getsize(f) > 0:
                _send_file(chat_id, f)
                sent = True
        return None if sent else "Логов нет."

    add_history(chat_id, "user", text)

    graph = load_graph()
    entities = graph.entities if graph else None

    if classify_need_search(text, history_context(chat_id), entities):
        logger.info("Search triggered by LLM classifier: %s", text[:80])
        answer = search_and_answer(text, chat_id)
        if answer:
            answer = extract_message(answer)
            add_history(chat_id, "assistant", answer)
            update_graph_from_answer(text, answer)
        logger.info("search_and_answer returned %s chars", len(answer) if answer else 0)
        return answer

    if graph:
        answer = answer_from_graph(text, graph, chat_id)
        if answer:
            add_history(chat_id, "assistant", answer)
            logger.info("answer_from_graph returned %s chars", len(answer))
            return answer

    logger.info("Graph could not answer, searching via web search: %s", text[:80])
    answer = search_and_answer(text, chat_id)
    if answer:
        answer = extract_message(answer)
        add_history(chat_id, "assistant", answer)
        update_graph_from_answer(text, answer)
    logger.info("search_and_answer (fallback) returned %s chars", len(answer) if answer else 0)
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
