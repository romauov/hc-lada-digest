"""
Клиент LLM с 3-tier fallback: OpenRouter → YandexGPT → OpenRouter бесплатные.
"""
import logging
import os
import re
import time
from typing import Optional

import requests

from shared.monitoring import send_critical_alert
from shared.llm_yandex import _call_yandex_chat, _call_yandex_search
from shared.tg_format import extract_message

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

MAX_RETRIES = 3
RETRY_DELAY = 2


def _or_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
        "Content-Type": "application/json",
    }


def _call_openrouter(
    system_prompt: str,
    user_prompt: str,
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 2000,
) -> Optional[str]:
    models_to_try = [model, FALLBACK] if model != FALLBACK else [model]

    for attempt in range(1, MAX_RETRIES + 1):
        for m in models_to_try:
            payload = {
                "model": m,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
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
                citations = data.get("citations") or data["choices"][0]["message"].get("citations")
                if citations:
                    logger.info("OpenRouter citations found: %d urls", len(citations))
                    links = []
                    for i, url in enumerate(citations, 1):
                        links.append(f"[{i}]({url})")
                    text = text.strip() + "\n\n**Источники:**\n" + ", ".join(links)
                else:
                    logger.info("OpenRouter citations not found in response keys: %s",
                                list(data.keys()))
                    urls = re.findall(r'(?<!\]\()https?://[^\s\[\])>"]+', text)
                    if urls:
                        seen = []
                        for u in urls:
                            if u not in seen:
                                seen.append(u)
                        links = [f"[{i}]({u})" for i, u in enumerate(seen, 1)]
                        text = text.strip() + "\n\n**Источники:**\n" + ", ".join(links)
                logger.debug("OpenRouter response (%d tokens using %s)",
                             data.get("usage", {}).get("total_tokens", 0), m)
                if m != model:
                    send_critical_alert(
                        f"\u26a0\ufe0f \u0424\u043e\u043b\u043b\u0431\u044d\u043a \u043d\u0430 "
                        f"\u0437\u0430\u043f\u0430\u0441\u043d\u0443\u044e \u043c\u043e\u0439\u0435\u043b\u044c\n"
                        f"\u0411\u044b\u043b\u0430: {model}\n"
                        f"\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0435\u0442\u0441\u044f: {m}"
                    )
                return extract_message(text.strip())

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
    user_prompt: str,
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    yc_model: Optional[str] = None,
    use_search: bool = False,
) -> Optional[str]:
    """3-tier: OpenRouter → Yandex → OpenRouter free."""
    result = _call_openrouter(system_prompt, user_prompt, model, temperature, max_tokens)
    if result:
        return extract_message(result)

    if yc_model and YC_API_KEY and YC_FOLDER_ID:
        fn = _call_yandex_search if use_search else _call_yandex_chat
        result = fn(system_prompt, user_prompt, yc_model, YC_FOLDER_ID, YC_API_KEY, temperature, max_tokens)
        if result:
            return extract_message(result)
        send_critical_alert("⚠️ Yandex API недоступен, использован OpenRouter")

    return None


def analyze_news(system_prompt: str, user_prompt: str) -> Optional[str]:
    return _call_llm(
        system_prompt, user_prompt, model=MODEL_LITE, temperature=0.1, yc_model=YC_MODEL_LITE,
    )


def generate_digest(system_prompt: str, user_prompt: str) -> Optional[str]:
    return _call_llm(
        system_prompt, user_prompt, model=MODEL_PRO, temperature=0.4, max_tokens=3000, yc_model=YC_MODEL_PRO,
    )


def search_answer(system_prompt: str, user_prompt: str) -> Optional[str]:
    return _call_openrouter(system_prompt, user_prompt, model=MODEL_SEARCH, temperature=0.3, max_tokens=4000)
