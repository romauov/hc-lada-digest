import logging
import os

import requests

logger = logging.getLogger(__name__)

TG_API_BASE = "https://api.telegram.org/bot{token}"
TG_CHAR_LIMIT = 4096
PARSE_MODE = "HTML"


def send_message(
    token: str,
    chat_id: str,
    text: str,
    parse_mode: str = PARSE_MODE,
    disable_preview: bool = True,
    reply_markup: dict | None = None,
) -> bool:
    url = f"{TG_API_BASE.format(token=token)}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if disable_preview:
        payload["disable_web_page_preview"] = True
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if not resp.ok:
            logger.error("TG send error: %s %s", resp.status_code, resp.text)
        return resp.ok
    except Exception as e:
        logger.error("TG send exception: %s", e)
        return False


def answer_callback_query(token: str, callback_query_id: str, text: str = "") -> bool:
    url = f"{TG_API_BASE.format(token=token)}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id, "text": text}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if not resp.ok:
            logger.error("TG answerCallbackQuery error: %s %s", resp.status_code, resp.text)
        return resp.ok
    except Exception as e:
        logger.error("TG answerCallbackQuery exception: %s", e)
        return False


def edit_message_text(
    token: str,
    chat_id: str,
    message_id: int,
    text: str,
    parse_mode: str = PARSE_MODE,
) -> bool:
    url = f"{TG_API_BASE.format(token=token)}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if not resp.ok:
            logger.error("TG editMessageText error: %s %s", resp.status_code, resp.text)
        return resp.ok
    except Exception as e:
        logger.error("TG editMessageText exception: %s", e)
        return False


def send_document(token: str, chat_id: str, file_path: str) -> bool:
    url = f"{TG_API_BASE.format(token=token)}/sendDocument"
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(url, data={"chat_id": chat_id}, files={"document": f}, timeout=30)
        if not resp.ok:
            logger.error("TG send file error: %s %s", resp.status_code, resp.text)
        return resp.ok
    except Exception as e:
        logger.error("TG send file exception: %s", e)
        return False


def split_message(text: str, limit: int = TG_CHAR_LIMIT) -> list[str]:
    parts = []
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
