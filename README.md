# HC News Digest

Сервис ежедневного дайджеста новостей о хоккейном клубе на базе Yandex Cloud.

## Архитектура

```
Trigger (таймер)
    ↓
orchestrator        — управляет пайплайном
    ↓
search_worker       — поиск новостей по RSS (Google News, KHL.ru)
    ↓
graph_updater       — анализ через YandexGPT, обновление графа знаний
    ↓
digest_generator    — генерация дайджеста через YandexGPT
    ↓
tg_sender           — отправка в Telegram
```

## Инфраструктура (Yandex Cloud)

| Сервис | Назначение |
|--------|------------|
| Cloud Functions | бизнес-логика |
| Object Storage | граф (JSON), архив дайджестов |
| Message Queue | очередь задач поиска |
| Lockbox | секреты и токены |
| Cloud Logging | логи |
| YandexGPT API | анализ и генерация |

## Структура репозитория

```
functions/
  orchestrator/     — точка входа, управление пайплайном
  search_worker/    — поиск новостей по RSS
  graph_updater/    — обновление графа знаний
  digest_generator/ — генерация дайджеста
  tg_sender/        — отправка в Telegram
shared/             — общие модели, утилиты
graph/              — начальный граф знаний (seed)
tests/              — тесты
infra/              — Terraform конфигурация YC
```

## Итерации разработки

- **Итерация 1** — скелет: RSS + граф без обновления + сырые заголовки в TG ← *текущая*
- **Итерация 2** — YandexGPT: анализ, обновление графа, дайджест
- **Итерация 3** — расширение источников: парсинг, Yandex Search API, Telegram-каналы
- **Итерация 4** — полировка: deferred news, версионирование, мониторинг
- **Итерация 5** — верификация фактов

## Локальный запуск

```bash
pip install -r requirements.txt
cp .env.example .env  # заполнить токены
python -m functions.orchestrator.handler
```
