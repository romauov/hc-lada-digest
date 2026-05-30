"""
Хранение графа и дайджестов на локальной файловой системе.
Данные монтируются в /data через Docker volume.
"""
import json
import logging
import os
import shutil
from datetime import date
from typing import Optional

from shared.models import KnowledgeGraph

logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", "/data")

GRAPH_KEY        = os.path.join(DATA_DIR, "knowledge_graph.json")
GRAPH_BACKUP_DIR = os.path.join(DATA_DIR, "backups")
DIGEST_DIR       = os.path.join(DATA_DIR, "digests")


def _ensure_dirs() -> None:
    os.makedirs(GRAPH_BACKUP_DIR, exist_ok=True)
    os.makedirs(DIGEST_DIR, exist_ok=True)


def load_graph() -> Optional[KnowledgeGraph]:
    _ensure_dirs()
    if not os.path.exists(GRAPH_KEY):
        logger.warning("Graph not found at %s", GRAPH_KEY)
        return None
    with open(GRAPH_KEY, encoding="utf-8") as f:
        data = json.load(f)
    logger.info("Graph loaded (version %s)", data["meta"]["version"])
    return KnowledgeGraph.from_dict(data)


def save_graph(graph: KnowledgeGraph, backup: bool = True) -> None:
    _ensure_dirs()
    data = json.dumps(graph.to_dict(), ensure_ascii=False, indent=2)

    with open(GRAPH_KEY, "w", encoding="utf-8") as f:
        f.write(data)
    logger.info("Graph saved (version %s)", graph.version)

    if backup:
        backup_path = os.path.join(
            GRAPH_BACKUP_DIR, f"knowledge_graph_{date.today().isoformat()}.json"
        )
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(data)
        logger.info("Graph backup saved: %s", backup_path)


def save_digest(text: str, digest_date: Optional[str] = None) -> str:
    _ensure_dirs()
    key = os.path.join(DIGEST_DIR, f"{digest_date or date.today().isoformat()}.txt")
    with open(key, "w", encoding="utf-8") as f:
        f.write(text)
    logger.info("Digest saved: %s", key)
    return key


def load_digest(digest_date: str) -> Optional[str]:
    path = os.path.join(DIGEST_DIR, f"{digest_date}.txt")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()
