import json
import logging
import os
import re
from typing import Optional

from shared.openrouter import _call_llm

logger = logging.getLogger(__name__)

MODEL_LITE = os.environ.get("LLM_LITE_MODEL", "openai/gpt-4o-mini")
YC_MODEL_LITE = os.environ.get("YC_MODEL_LITE", "yandexgpt-5-lite")

CLASSIFY_SYSTEM = (
    "Ты — классификатор для бота ХК Лада (хоккейный клуб, КХЛ).\n"
    "Определи, нужен ли веб-поиск (интернет) для ответа на вопрос пользователя.\n"
    "Учитывай историю диалога — пользователь может уточнять или исправлять предыдущие ответы.\n"
    "\n"
    "YES — нужен поиск:\n"
    "  • вопросы про свежие новости, результаты матчей, турнирную таблицу\n"
    "  • вопросы про трансферы, переходы, контракты\n"
    "  • пользователь исправляет или уточняет предыдущий ответ бота\n"
    "  • явная или неявная просьба что-то найти / проверить\n"
    "  • вопрос, на который граф знаний заведомо не может ответить\n"
    "\n"
    "NO — поиск не нужен:\n"
    "  • фактологический вопрос (кто тренер, где арена, когда основан клуб)\n"
    "  • простое согласие / отказ / «спасибо» / приветствие\n"
    "  • вопрос, на который уже ответил бот в истории\n"
    "\n"
    "Ответь ТОЛЬКО одним словом: YES или NO."
)

BOT_EXTRACT_SYSTEM = (
    "Ты — аналитик, извлекающий факты из диалога с ботом о ХК ЛАДА Тольятти (хоккейный клуб, КХЛ).\n"
    "ВНИМАНИЕ: «Лада» здесь — это хоккейный клуб, а НЕ автомобиль.\n"
    "\n"
    "Пользователь задал вопрос, бот ответил через веб-поиск. Извлеки новые сущности и связи,\n"
    "которые НЕ присутствуют в текущем графе знаний.\n"
    "\n"
    "Отвечай ТОЛЬКО валидным JSON без пояснений и markdown:\n"
    "{\n"
    '  "new_entities": [\n'
    "    {\n"
    '      "name": "Иван Петров",\n'
    '      "type": "sponsor",\n'
    '      "relation_to_root": "HAS_SPONSOR",\n'
    '      "confidence": 0.8,\n'
    '      "search_queries": ["Иван Петров спонсор Лада"]\n'
    "    }\n"
    "  ],\n"
    '  "updated_relations": [\n'
    "    {\n"
    '      "entity_name": "АвтоВАЗ",\n'
    '      "relation_type": "HAS_SPONSOR",\n'
    '      "action": "confirm",\n'
    '      "confidence": 0.9\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "\n"
    "Типы сущностей: hockey_club, coach, player, sponsor, arena, farm_club, official, league, other\n"
    'Если нет новых сущностей или изменений — верни {"new_entities": [], "updated_relations": []}'
)


def _parse_llm_json(text: str) -> Optional[dict]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None


def classify_need_search(question: str, history: str) -> bool:
    prompt = f"История диалога:\n{history}\n\nВопрос пользователя:\n{question}" if history else f"Вопрос пользователя:\n{question}"
    result = _call_llm(CLASSIFY_SYSTEM, prompt, model=MODEL_LITE, temperature=0.0, max_tokens=5, yc_model=YC_MODEL_LITE)
    if result and "YES" in result.upper():
        return True
    return False


def extract_from_bot_answer(question: str, answer: str, graph_json: dict) -> Optional[dict]:
    known = "\n".join(
        f"- {e.get('name', '?')} ({e.get('type', '?')})"
        for e in graph_json.get("entities", {}).values()
    )
    prompt = f"Известные сущности в графе:\n{known}\n\nВопрос: {question}\nОтвет бота: {answer}"
    result = _call_llm(BOT_EXTRACT_SYSTEM, prompt, model=MODEL_LITE, temperature=0.1, max_tokens=1000, yc_model=YC_MODEL_LITE)
    return _parse_llm_json(result) if result else None
