# HC Lada Digest — агент-памятка

## Команды

```bash
# установка и первый запуск
cp .env.example .env   # заполнить OPENROUTER_API_KEY, TG_BOT_TOKEN, TG_CHAT_ID
docker compose build
docker compose up -d                                    # запуск (cron ежедневно в 8:00)

# ручной запуск пайплайна
docker compose run --rm digester python -m functions.orchestrator.handler

# тесты (мокируют LLM, не требуют токенов, без Docker)
pip install -r requirements.txt
python -m pytest tests/ -v
```

## Архитектура

- **OpenRouter** вместо YandexGPT: `shared/openrouter.py` (клиент с runtime fallback).
- **Хранилище** — локальные JSON-файлы в `/data` (Docker volume), а не S3.
- **5 функций** в `functions/{orchestrator,search_worker,graph_updater,digest_generator,tg_sender}/handler.py`.
- **Orchestrator** (`functions/orchestrator/handler.py:181`) — единственная точка входа для пайплайна.
- **Auto-seed**: при отсутствии графа orchestrator сам создаёт начальный через `build_initial_graph()`.

## Ключевые детали

- **Модели**: `openai/gpt-4o-mini` для анализа/классификации, `openai/gpt-4o` для дайджеста.
- **Fallback**: при ошибке основной модели — `qwen/qwen2.5-72b-instruct`.
- **Telegram**: лимит 4096 символов на сообщение, авто-сплит по строкам.
- **Бот** (`functions/tg_sender/bot.py`): long-polling, команды `/start`, `/graph`, `/logs`.
- **Deferred news**: TTL 3 дня; активируются если свежих новостей < 3.
- **SourceRegistry**: синглтон, сбрасывать через `reset_registry()` перед каждым тестом.
- **Тесты**: LLM вызовы мокируются `@patch`. Не требуют Docker/токенов.
- **No CI/linter/formatter/typecheck**: ни pyproject.toml, ни .github/workflows нет.
