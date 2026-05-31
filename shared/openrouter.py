"""
Клиент LLM с 3-tier fallback: YandexGPT → OpenRouter платные → OpenRouter бесплатные.
Ре-экспорт из shared.llm_providers для обратной совместимости.
"""
from shared.llm_providers import *  # noqa: F401, F403
