import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

YANDEX_CHAT_URL = "https://llm.api.cloud.yandex.net/v1/chat/completions"
YANDEX_RESPONSES_URL = "https://ai.api.cloud.yandex.net/v1/responses"


def _yc_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Api-Key {api_key}",
        "Content-Type": "application/json",
    }


def _call_yandex_chat(
    system_prompt: str,
    user_prompt: str,
    yc_model: str,
    folder_id: str,
    api_key: str,
    temperature: float = 0.2,
    max_tokens: int = 2000,
) -> Optional[str]:
    model_uri = f"gpt://{folder_id}/{yc_model}"
    payload = {
        "model": model_uri,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        resp = requests.post(YANDEX_CHAT_URL, headers=_yc_headers(api_key), json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        logger.debug("Yandex chat response (%s)", yc_model)
        return text.strip()
    except Exception as e:
        logger.warning("Yandex chat error: %s", e)
        return None


def _call_yandex_search(
    system_prompt: str,
    user_prompt: str,
    yc_model: str,
    folder_id: str,
    api_key: str,
    temperature: float = 0.3,
    max_tokens: int = 4000,
) -> Optional[str]:
    model_uri = f"gpt://{folder_id}/{yc_model}"
    payload = {
        "model": model_uri,
        "input": f"{system_prompt}\n\n{user_prompt}",
        "tools": [{"type": "web_search", "search_context_size": "high"}],
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }
    try:
        resp = requests.post(YANDEX_RESPONSES_URL, headers=_yc_headers(api_key), json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("output", []):
            if item.get("type") == "message":
                for c in item.get("content", []):
                    if c.get("type") == "output_text":
                        text = c["text"].strip()
                        logger.debug("Yandex search response (%s)", yc_model)
                        return text
        logger.warning("Yandex search: unexpected response structure")
        return None
    except Exception as e:
        logger.warning("Yandex search error: %s", e)
        return None
