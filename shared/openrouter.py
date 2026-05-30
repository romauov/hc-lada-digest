"""
Клиент OpenRouter (OpenAI-совместимый API).
Заменяет shared/yandex_gpt.py.

Документация: https://openrouter.ai/docs
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

MAX_RETRIES = 3
RETRY_DELAY = 2


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
        "Content-Type": "application/json",
    }


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
                    headers=_headers(),
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


def analyze_news(system_prompt: str, user_prompt: str) -> Optional[str]:
    return _call_openrouter(system_prompt, user_prompt, model=MODEL_LITE, temperature=0.1)


def generate_digest(system_prompt: str, user_prompt: str) -> Optional[str]:
    return _call_openrouter(system_prompt, user_prompt, model=MODEL_PRO, temperature=0.4, max_tokens=3000)


def search_answer(system_prompt: str, user_prompt: str) -> Optional[str]:
    return _call_openrouter(system_prompt, user_prompt, model=MODEL_SEARCH, temperature=0.3, max_tokens=4000)
