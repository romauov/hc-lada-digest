"""
tg_sender — отправка дайджеста в Telegram.

Разбивает длинные сообщения на части (лимит TG — 4096 символов).
"""
import logging
import os

from shared.tg_format import sanitize_tg_html
from shared.tg_send import send_message as tg_send, split_message

logger = logging.getLogger(__name__)


def send_digest(digest_text: str, is_empty: bool = False) -> bool:
    """
    Отправляет дайджест в Telegram.
    Если новостей нет (is_empty=True) — отправляет уведомление админу.
    """
    token   = os.environ["TG_BOT_TOKEN"]
    chat_id = os.environ["TG_CHAT_ID"]
    admin_id = os.environ.get("TG_ADMIN_ID", chat_id)

    if is_empty:
        return tg_send(
            token, admin_id,
            "⚠️ <b>Дайджест</b>: за сегодня свежих новостей не найдено."
        )

    digest_text = sanitize_tg_html(digest_text)
    parts = split_message(digest_text)
    ok    = True
    for i, part in enumerate(parts, 1):
        suffix = f"\n\n<i>Часть {i}/{len(parts)}</i>" if len(parts) > 1 else ""
        ok = ok and tg_send(token, chat_id, part + suffix)

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
