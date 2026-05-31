"""
orchestrator — главная функция, управляет ежедневным пайплайном.

Итерации 1-5: RSS → анализ → граф → верификация → дайджест → Telegram
"""
import logging
import os
from datetime import date

from functions.orchestrator.pipeline import run_pipeline

logger = logging.getLogger(__name__)


def handler(event: dict, context) -> dict:
    return run_pipeline()


if __name__ == "__main__":
    log_dir = os.path.join(os.environ.get("DATA_DIR", "/data"), "logs")
    log_file = os.path.join(log_dir, f"orchestrator-{date.today().isoformat()}.log")
    os.makedirs(log_dir, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(fmt)
    root.addHandler(handler)
    if not root.handlers or not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(logging.StreamHandler())
    print(run_pipeline())
