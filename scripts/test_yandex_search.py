#!/usr/bin/env python3
"""Test YandexGPT with web search via Responses API + web_search tool."""
import os
import sys

from dotenv import load_dotenv
import requests


load_dotenv()


def main():
    key = os.environ.get("YC_API_KEY")
    folder = os.environ.get("YC_FOLDER_ID")

    if not key or not folder:
        print("❌ YC_API_KEY и YC_FOLDER_ID не заданы в .env")
        sys.exit(1)

    model = f"gpt://{folder}/yandexgpt/rc"
    url = "https://ai.api.cloud.yandex.net/v1/responses"

    query = " ".join(sys.argv[1:]) or "последние новости ХК Лада Тольятти"

    payload = {
        "model": model,
        "input": query,
        "tools": [{"type": "web_search", "search_context_size": "high"}],
        "temperature": 0.3,
        "max_output_tokens": 2000,
    }

    headers = {
        "Authorization": f"Api-Key {key}",
        "Content-Type": "application/json",
    }

    print(f"🔍 Поиск: {query}\n")

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=45)
        resp.raise_for_status()
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        if isinstance(e, requests.HTTPError) and e.response is not None:
            print(f"   Статус: {e.response.status_code}")
            print(f"   Тело: {e.response.text[:500]}")
        sys.exit(1)

    data = resp.json()

    # вытаскиваем текст ответа
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    print(c["text"])
                    # источники
                    anns = c.get("annotations", [])
                    if anns:
                        print("\n📚 Источники:")
                        for a in anns:
                            if a.get("url"):
                                print(f"   • {a.get('title', a['url'])}")
                                print(f"     {a['url']}")
                    return

    print("✅ Ответ получен, но структура не распознана")
    print(resp.text[:1000])


if __name__ == "__main__":
    main()
