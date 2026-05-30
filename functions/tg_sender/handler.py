"""
tg_sender — отправка дайджеста в Telegram.

Разбивает длинные сообщения на части (лимит TG — 4096 символов).
Совместим с Yandex Cloud Functions.
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)

TG_API_BASE  = "https://api.telegram.org/bot{token}"
TG_CHAR_LIMIT = 4096
PARSE_MODE    = "HTML"


def _send_message(token: str, chat_id: str, text: str) -> bool:
    url  = f"{TG_API_BASE.format(token=token)}/sendMessage"
    resp = requests.post(url, json={
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": PARSE_MODE,
        "disable_web_page_preview": True,
    }, timeout=10)
    if not resp.ok:
        logger.error("TG send error: %s %s", resp.status_code, resp.text)
    return resp.ok


def _split_message(text: str, limit: int = TG_CHAR_LIMIT) -> list[str]:
    """Разбивает текст на части, не разрывая строки."""
    parts  = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit:
            if current:
                parts.append(current.rstrip())
            current = line
        else:
            current += line
    if current:
        parts.append(current.rstrip())
    return parts or [""]


def send_digest(digest_text: str, is_empty: bool = False) -> bool:
    """
    Отправляет дайджест в Telegram.
    Если новостей нет (is_empty=True) — отправляет уведомление админу.
    """
    token   = os.environ["TG_BOT_TOKEN"]
    chat_id = os.environ["TG_CHAT_ID"]
    admin_id = os.environ.get("TG_ADMIN_ID", chat_id)

    if is_empty:
        return _send_message(
            token, admin_id,
            "⚠️ <b>Дайджест</b>: за сегодня свежих новостей не найдено."
        )

    parts = _split_message(digest_text)
    ok    = True
    for i, part in enumerate(parts, 1):
        suffix = f"\n\n<i>Часть {i}/{len(parts)}</i>" if len(parts) > 1 else ""
        ok = ok and _send_message(token, chat_id, part + suffix)

    return ok


# ── Cloud Function handler ────────────────────────────────────────────────────

def handler(event: dict, context) -> dict:
    """
    event["digest"]   — текст дайджеста (str)
    event["is_empty"] — True если новостей не было (bool, optional)
    """
    digest   = event.get("digest", "")
    is_empty = event.get("is_empty", False)

    if not digest and not is_empty:
        return {"status": "error", "message": "No digest in event"}

    ok = send_digest(digest, is_empty=is_empty)
    return {"status": "ok" if ok else "error"}
