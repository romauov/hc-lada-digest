# HC News Digest

Сервис ежедневного дайджеста новостей о хоккейном клубе «Лада» (Тольятти).

Работает через OpenRouter (gpt-4o-mini для анализа, gpt-4o для генерации, fallback qwen). Разворачивается одной командой через Docker Compose.

## Архитектура

```
supercronic (cron 8:00)
    ↓
orchestrator        — управляет пайплайном
    ↓
search_worker       — поиск новостей по RSS (Google News, KHL.ru, sports.ru)
    ↓
graph_updater       — анализ через OpenRouter, обновление графа знаний
    ↓
digest_generator    — генерация дайджеста через OpenRouter
    ↓
tg_sender           — отправка в Telegram
```

## Быстрый старт

```bash
cp .env.example .env          # заполнить OPENROUTER_API_KEY, TG_BOT_TOKEN, TG_CHAT_ID
docker compose build
docker compose up -d           # запуск (cron ежедневно в 8:00)

# ручной запуск пайплайна
docker compose run --rm digester python -m functions.orchestrator.handler
```

## Структура репозитория

```
functions/
  orchestrator/     — точка входа, управление пайплайном
  search_worker/    — поиск новостей по RSS и парсинг
  graph_updater/    — анализ через LLM, обновление графа знаний
  digest_generator/ — генерация дайджеста через LLM
  tg_sender/        — отправка в Telegram
shared/             — общие модели, утилиты
graph/              — начальный граф знаний (seed)
tests/              — тесты (мокируют LLM, не требуют токенов)
```

## Тесты

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

LLM-вызовы мокируются — тесты не требуют токенов и Docker.

## Хранилище

Граф знаний, снапшоты и архив дайджестов хранятся в локальных JSON-файлах в директории `data/` (Docker volume). При первом запуске граф создаётся автоматически через `graph/seed.py`.

## Переменные окружения

| Переменная | Обязательная | Описание |
|---|---|---|
| `OPENROUTER_API_KEY` | да | Ключ OpenRouter |
| `TG_BOT_TOKEN` | да | Токен Telegram бота |
| `TG_CHAT_ID` | да | Чат для отправки дайджеста |
| `LLM_LITE_MODEL` | нет | Модель для анализа (по умолч. `openai/gpt-4o-mini`) |
| `LLM_PRO_MODEL` | нет | Модель для генерации (по умолч. `openai/gpt-4o`) |
| `LLM_FALLBACK_MODEL` | нет | Fallback модель (по умолч. `qwen/qwen2.5-72b-instruct`) |
| `USE_LLM` | нет | Включить LLM-анализ (`true`/`false`) |
| `FRESHNESS_HOURS` | нет | Свежесть новостей в часах (24) |
| `MAX_NEWS_IN_DIGEST` | нет | Максимум новостей в дайджесте (20) |
