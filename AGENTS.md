# HC Lada Digest — агент-памятка

## Команды

```bash
# установка и первый запуск
cp .env.example .env   # заполнить OPENROUTER_API_KEY, TG_BOT_TOKEN, TG_CHAT_ID
docker compose build
docker compose up -d                                    # запуск (cron ежедневно в 9:00)

# ручной запуск пайплайна
docker compose run --rm digester python -m functions.orchestrator.handler

# тесты (мокируют LLM, не требуют токенов, без Docker)
pip install -r requirements.txt
python -m pytest tests/ -v
```

## Архитектура

- **OpenRouter**: `shared/openrouter.py` (клиент с runtime fallback).
- **Хранилище** — локальные JSON-файлы в `/data` (Docker volume), а не S3.
- **5 функций** в `functions/{orchestrator,search_worker,graph_updater,digest_generator,tg_sender}/handler.py`.
- **Ручное подтверждение графа**: при `GRAPH_APPROVAL=true` каждое содержательное изменение графа отдельно подтверждается админом через Telegram inline-кнопки ✅/❌. Запросы пишутся в `{DATA_DIR}/pending/approval_*.json` (`shared/approval.py`), бот обрабатывает `callback_query`. Пайплайн стартует в 9:00 и шлёт нерешённые запросы заново каждые `GRAPH_APPROVAL_REMIND_INTERVAL` (3600с) до часа `GRAPH_APPROVAL_END_HOUR` (18:00), по дедлайну нерешённые = отклонено. Применяется только одобренное (`build_approved_graph`). Приоритеты/метки упоминаний отдельные запросы НЕ порождают.
- **Orchestrator** (`functions/orchestrator/handler.py`) — единственная точка входа для пайплайна. Cron: ежедневно в **9:00**.
- **Auto-seed**: при отсутствии графа orchestrator сам создаёт начальный через `build_initial_graph()`.

## Ключевые детали

- **Модели**: `openai/gpt-4o-mini` для анализа/классификации, `openai/gpt-4o` для дайджеста.
- **Fallback**: при ошибке основной модели — `qwen/qwen2.5-72b-instruct`.
- **Telegram**: лимит 4096 символов на сообщение, авто-сплит по строкам.
- **Бот** (`functions/tg_sender/bot.py`): long-polling, команды `/start`, `/graph`, `/logs`.
- **Deferred news**: TTL 3 дня; активируются если свежих новостей < 3.
- **SourceRegistry**: синглтон, сбрасывать через `reset_registry()` перед каждым тестом.
- **Тесты**: LLM вызовы мокируются `@patch`. Не требуют Docker/токенов.
- **ML-сервис**: FastAPI + rubert-tiny2 (CPU) на порту 8001.
  - `POST /dedup` — семантическая дедупликация заголовков (порог DEDUP_THRESHOLD=0.92).
  - `POST /relevance` — оценка релевантности (score + is_relevant).
  - `GET /health` — проверка статуса.
  - Fallback: при недоступности ML дедупликация отключена, все новости считаются релевантными.
- **Фильтрация в SourceRegistry**: keyword prefilter → semantic dedup → relevance scoring → добор low_relevance при < MIN_NEWS_THRESHOLD (3).
- **No CI/linter/formatter/typecheck**: ни pyproject.toml, ни .github/workflows нет.
