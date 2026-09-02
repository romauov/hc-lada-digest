# HC News Digest

Сервис ежедневного дайджеста новостей о хоккейном клубе «Лада» (Тольятти).

- **LLM**: OpenRouter (Tier 1) → OpenRouter free (qwen)
- **ML**: rubert-tiny2 для семантической дедупликации и фильтра релевантности
- **Хранилище**: локальные JSON + SQLite на Docker volume
- **Бот**: Telegram bot с long-polling, команды `/start`, `/graph`, `/digest`, `/logs`

## Архитектура

```
supercronic (cron 9:00)          Telegram bot (long-polling)
    ↓                                    ↑
orchestrator — управляет пайплайном       │
    ↓                                     │
search_worker — RSS + scrapers            │
    ↓                                     │
  ┌→ ml:8001 — rubert-tiny2 (CPU)        │
  │  /dedup, /relevance, /health          │
  └←————————————————————————————           │
    ↓                                     │
graph_updater — LLM-анализ, граф          │
    ↓                                     │
digest_generator — генерация дайджеста    │
    ↓                                     │
tg_sender → Telegram                      │
    ↓                                     │
monitoring → Telegram (админу) ───────────┘
```

При `GRAPH_APPROVAL=true` каждое содержательное изменение графа отдельно подтверждается админом через inline-кнопки ✅/❌. Пайплайн стартует в 9:00, нерешённые запросы пересылаются каждый час до 18:00, по дедлайну = отклонено.

## Сервисы

Все три сервиса запускаются одной командой `docker compose up -d`:

| Сервис | Образ | Назначение |
|---|---|---|
| `digester` | `Dockerfile` | Пайплайн по crontab (ежедневно 9:00) |
| `bot` | `Dockerfile` | Telegram bot с long-polling |
| `ml` | `Dockerfile.ml` | FastAPI + rubert-tiny2 (CPU) |

## Быстрый старт

```bash
cp .env.example .env          # заполнить токены
docker compose build
docker compose up -d           # запуск всех сервисов

# ручной запуск пайплайна
docker compose run --rm digester python -m functions.orchestrator.handler
```

Полный сброс данных (БД, граф, логи) выполняется вручную: `docker compose run --rm digester python scripts/reset_data.py`.

## Команды бота

Бот слушает только `TG_ADMIN_ID`:

- `/start`, `/help` — приветствие и справка
- `/graph` — текущий граф знаний
- `/digest` — сегодняшний дайджест
- `/logs` — лог-файл бота
- Любой текст → веб-поиск через Perplexity Sonar + обновление графа

## Структура репозитория

```
functions/
  orchestrator/     — точка входа, управление пайплайном
  search_worker/    — поиск новостей по RSS и парсинг
  graph_updater/    — анализ через LLM, обновление графа знаний
  digest_generator/ — генерация дайджеста через LLM
  tg_sender/        — отправка в Telegram + интерактивный бот
ml_service/         — FastAPI + rubert-tiny2 (семантическая дедупликация)
shared/             — общие модели, утилиты, клиенты
Graph/               — начальный граф знаний (seed)
scripts/             — служебные скрипты (clean_graph, reset_data, entrypoint)
tests/               — тесты (мокируют LLM, не требуют токенов)
```

## ML-сервис

`ml_service/main.py` — FastAPI с предобученным `cointegrated/rubert-tiny2`:

- **`POST /dedup`** — семантическая дедупликация заголовков (порог 0.92)
- **`POST /relevance`** — оценка релевантности (score + is_relevant)
- **`GET /health`** — статус сервиса

При недоступности ML-сервиса пайплайн работает без дедупликации и считает все новости релевантными.

Порядок фильтрации в `SourceRegistry`:
1. Keyword prefilter (без ML, дешёвая фильтрация по словам из search_queries)
2. Semantic dedup (ML)
3. Relevance scoring → high/low (ML, порог 0.65)
4. Добор из low_relevance при < 3 новостях

## Тесты

```bash
# основные тесты (без ML)
pip install -r requirements.txt
python -m pytest tests/ -v --ignore=tests/test_ml_service.py

# тесты ML (требуют torch + numpy)
pip install torch --index-url https://download.pytorch.org/whl/cpu numpy
python -m pytest tests/test_ml_service.py tests/test_ml_client.py tests/test_registry_filtering.py -v

# все тесты
python -m pytest tests/ -v
```

LLM-вызовы мокируются — тесты не требуют токенов и Docker.

## Хранилище

Граф знаний, снапшоты, дайджесты — JSON в `/data`. SQLite БД:
- `/data/pending/` — ожидающие подтверждения изменения графа (`approval_*.json`)
- `/data/seen_urls.db` — виденные URL (дедупликация)
- `/data/news.db` — все собранные новости
- `/data/history.db` — история диалогов бота

При отсутствии графа он создаётся автоматически (`build_initial_graph()`).

## Переменные окружения

| Переменная | Обязательная | Описание |
|---|---|---|
| `OPENROUTER_API_KEY` | да | Ключ OpenRouter |
| `TG_BOT_TOKEN` | да | Токен Telegram бота |
| `TG_CHAT_ID` | да | Чат для отправки дайджеста |
| `TG_ADMIN_ID` | да | ID администратора (для команд бота) |
| `LLM_LITE_MODEL` | нет | Модель анализа (по умолч. `openai/gpt-4o-mini`) |
| `LLM_PRO_MODEL` | нет | Модель генерации (по умолч. `openai/gpt-4o`) |
| `LLM_FALLBACK_MODEL` | нет | Fallback (по умолч. `qwen/qwen2.5-72b-instruct`) |
| `USE_LLM` | нет | Включить LLM-анализ (`true`/`false`) |
| `GRAPH_APPROVAL` | нет | Ручное подтверждение изменений графа (по умолч. `true`) |
| `GRAPH_APPROVAL_END_HOUR` | нет | Час дедлайна для подтверждений (18). Старт в 9:00, нерешённые пересылаются раз в час до дедлайна, затем = отклонено |
| `GRAPH_APPROVAL_REMIND_INTERVAL` | нет | Секунд между напоминаниями (3600) |
| `FRESHNESS_HOURS` | нет | Свежесть новостей в часах (24) |
| `MAX_NEWS_IN_DIGEST` | нет | Максимум новостей в дайджесте (20) |
| `YANDEX_SEARCH_USER` | нет | Логин Yandex Search API (источник новостей) |
| `YANDEX_SEARCH_KEY` | нет | Ключ Yandex Search API |
| `ML_URL` | нет | URL ML-сервиса (`http://ml:8001`, пусто → ML отключён) |
| `DEDUP_THRESHOLD` | нет | Порог дедупликации (0.92) |
| `RELEVANCE_THRESHOLD` | нет | Порог релевантности (0.65) |
| `EMBED_MODEL` | нет | Модель эмбеддингов (`cointegrated/rubert-tiny2`) |
