"""
Клиент LLM с 3-tier fallback: YandexGPT → OpenRouter платные → OpenRouter бесплатные.
"""
import logging
import os
import time
from typing import Optional

import requests

from shared.monitoring import send_critical_alert

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

MODEL_LITE   = os.environ.get("LLM_LITE_MODEL",   "openai/gpt-4o-mini")
MODEL_PRO    = os.environ.get("LLM_PRO_MODEL",    "openai/gpt-4o")
MODEL_SEARCH = os.environ.get("LLM_SEARCH_MODEL", "perplexity/sonar")
FALLBACK     = os.environ.get("LLM_FALLBACK_MODEL", "qwen/qwen2.5-72b-instruct")

YC_API_KEY     = os.environ.get("YC_API_KEY", "")
YC_FOLDER_ID   = os.environ.get("YC_FOLDER_ID", "")
YC_MODEL_LITE  = os.environ.get("YC_MODEL_LITE", "yandexgpt-5-lite")
YC_MODEL_PRO   = os.environ.get("YC_MODEL_PRO", "yandexgpt-5.1")

YANDEX_CHAT_URL      = "https://llm.api.cloud.yandex.net/v1/chat/completions"
YANDEX_RESPONSES_URL = "https://ai.api.cloud.yandex.net/v1/responses"

MAX_RETRIES = 3
RETRY_DELAY = 2


def _yc_headers() -> dict:
    return {
        "Authorization": f"Api-Key {YC_API_KEY}",
        "Content-Type": "application/json",
    }


def _or_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
        "Content-Type": "application/json",
    }


def _call_yandex_chat(
    system_prompt: str,
    user_prompt:   str,
    yc_model:      str,
    temperature:   float = 0.2,
    max_tokens:    int = 2000,
) -> Optional[str]:
    if not YC_API_KEY or not YC_FOLDER_ID:
        return None
    model_uri = f"gpt://{YC_FOLDER_ID}/{yc_model}"
    payload = {
        "model": model_uri,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }
    try:
        resp = requests.post(YANDEX_CHAT_URL, headers=_yc_headers(), json=payload, timeout=60)
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
    user_prompt:   str,
    yc_model:      str,
    temperature:   float = 0.3,
    max_tokens:    int = 4000,
) -> Optional[str]:
    if not YC_API_KEY or not YC_FOLDER_ID:
        return None
    model_uri = f"gpt://{YC_FOLDER_ID}/{yc_model}"
    payload = {
        "model": model_uri,
        "input": f"{system_prompt}\n\n{user_prompt}",
        "tools": [{"type": "web_search", "search_context_size": "high"}],
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }
    try:
        resp = requests.post(YANDEX_RESPONSES_URL, headers=_yc_headers(), json=payload, timeout=60)
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


def _call_openrouter(
    system_prompt: str,
    user_prompt:   str,
    model:         str,
    temperature:   float = 0.2,
    max_tokens:    int = 2000,
) -> Optional[str]:
    models_to_try = [model, FALLBACK] if model != FALLBACK else [model]

    for attempt in range(1, MAX_RETRIES + 1):
        for m in models_to_try:
            payload = {
                "model": m,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                "temperature": temperature,
                "max_tokens":  max_tokens,
            }
            try:
                resp = requests.post(
                    OPENROUTER_URL,
                    headers=_or_headers(),
                    json=payload,
                    timeout=60,
                )
                if resp.status_code == 429:
                    logger.warning("OpenRouter 429, retry %d/%d", attempt, MAX_RETRIES)
                    time.sleep(RETRY_DELAY * attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                logger.debug("OpenRouter response (%d tokens using %s)",
                             data.get("usage", {}).get("total_tokens", 0), m)
                if m != model:
                    send_critical_alert(
                        f"⚠️ Fallback на запасную модель\n"
                        f"Была: {model}\n"
                        f"Используется: {m}"
                    )
                return text.strip()

            except (requests.ConnectionError, requests.Timeout) as e:
                logger.warning("OpenRouter network error (%s): %s", m, e)
                continue
            except requests.HTTPError as e:
                logger.error("OpenRouter HTTP error (%s): %s", m, e)
                continue
            except Exception as e:
                logger.error("OpenRouter unexpected error (%s): %s", m, e)
                return None

    return None


def _call_llm(
    system_prompt: str,
    user_prompt:   str,
    model:         str,
    temperature:   float = 0.2,
    max_tokens:    int = 2000,
    yc_model:      Optional[str] = None,
    use_search:    bool = False,
) -> Optional[str]:
    """3-tier: Yandex → OpenRouter paid → OpenRouter free."""
    if yc_model and YC_API_KEY and YC_FOLDER_ID:
        fn = _call_yandex_search if use_search else _call_yandex_chat
        result = fn(system_prompt, user_prompt, yc_model, temperature, max_tokens)
        if result:
            return result
        send_critical_alert("⚠️ Yandex API недоступен, переход на OpenRouter")

    return _call_openrouter(system_prompt, user_prompt, model, temperature, max_tokens)


def analyze_news(system_prompt: str, user_prompt: str) -> Optional[str]:
    return _call_llm(
        system_prompt, user_prompt, model=MODEL_LITE, temperature=0.1, yc_model=YC_MODEL_LITE,
    )


def generate_digest(system_prompt: str, user_prompt: str) -> Optional[str]:
    return _call_llm(
        system_prompt, user_prompt, model=MODEL_PRO, temperature=0.4, max_tokens=3000, yc_model=YC_MODEL_PRO,
    )


def search_answer(system_prompt: str, user_prompt: str) -> Optional[str]:
    return _call_llm(
        system_prompt, user_prompt, model=MODEL_SEARCH, temperature=0.3, max_tokens=4000, yc_model=YC_MODEL_PRO, use_search=True,
    )
