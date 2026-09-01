"""
reset_data.py — одноразовый полный сброс данных.

Запускается из entrypoint при старте контейнера. После выполнения
удаляет сам себя, чтобы следующая очистка наступила только при
очередном явном развёртывании этого файла.

Что делает:
  - Бэкапит текущие данные в {DATA_DIR}/backups/reset_<дата>_<время>/
  - Пересоздаёт граф через build_initial_graph() (чистый, v1, 4 сущности)
  - Удаляет news.db, seen_urls.db (и WAL/SHM)
  - Очищает history.db (DELETE FROM conversation_history)
  - Обнуляет deferred_news в графе
  - Очищает digests/, snapshots/, backups/, logs/
  - Удаляет сам файл reset_data.py
"""
import json
import logging
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph.seed import build_initial_graph
from shared.storage import save_graph
from shared.models import KnowledgeGraph

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", "/data")

SUB_DIRS_TO_CLEAR = ["digests", "snapshots", "backups", "logs"]

DB_FILES = ["news.db", "news.db-wal", "news.db-shm", "news.db-journal",
            "seen_urls.db", "seen_urls.db-wal", "seen_urls.db-shm",
            "history.db", "history.db-wal", "history.db-shm"]


def _backup(data_dir: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    target = Path(data_dir) / "backups" / f"reset_{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    graph_path = Path(data_dir) / "knowledge_graph.json"
    if graph_path.exists():
        shutil.copy2(graph_path, target / graph_path.name)
        logger.info("Backup graph -> %s", target / graph_path.name)
    for name in DB_FILES:
        p = Path(data_dir) / name
        if p.exists():
            shutil.copy2(p, target / name)
            logger.info("Backup %s -> %s", name, target / name)
    return target


def _clear_subdirs(data_dir: str) -> None:
    for sub in SUB_DIRS_TO_CLEAR:
        d = Path(data_dir) / sub
        if d.exists() and d.is_dir():
            for item in d.iterdir():
                if item.name.startswith("reset_"):
                    continue
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
            logger.info("Cleared %s/", sub)


def _delete_db_files(data_dir: str) -> None:
    for name in DB_FILES:
        p = Path(data_dir) / name
        if p.exists():
            p.unlink()
            logger.info("Deleted %s", name)


def _clear_history_db(data_dir: str) -> None:
    path = Path(data_dir) / "history.db"
    if not path.exists():
        logger.info("history.db not present, skipping")
        return
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM conversation_history")
        conn.commit()
        logger.info("history.db conversation_history cleared")
    finally:
        conn.close()


def _reset_graph(data_dir: str) -> None:
    graph = build_initial_graph()
    graph_data = graph.to_dict()
    graph_data["deferred_news"] = []
    path = Path(data_dir) / "knowledge_graph.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(graph_data, f, ensure_ascii=False, indent=2)
    logger.info("Graph reset to base (v1, %d entities)", len(graph.entities))
    save_graph(graph, backup=False)


def reset_data(data_dir: str) -> int:
    if not os.path.isdir(data_dir):
        logger.error("DATA_DIR %s does not exist", data_dir)
        return 1
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    _backup(data_dir)
    _clear_subdirs(data_dir)
    _delete_db_files(data_dir)
    _clear_history_db(data_dir)
    _reset_graph(data_dir)
    _delete_self()
    logger.info("Reset complete. Data wiped, graph reset to base.")
    return 0


def _delete_self() -> None:
    self_path = Path(os.path.abspath(__file__))
    try:
        self_path.unlink()
        logger.info("Removed self: %s", self_path.name)
    except OSError as e:
        logger.warning("Could not remove self: %s", e)


if __name__ == "__main__":
    sys.exit(reset_data(os.environ.get("DATA_DIR", "/data")))
