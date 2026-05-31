"""Conversation history — SQLite-хранилище истории диалогов."""
import logging
import sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)

CONTEXT_LIMIT = 10


class HistoryStore:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS conversation_history ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  chat_id TEXT NOT NULL,"
            "  role TEXT NOT NULL,"
            "  text TEXT NOT NULL,"
            "  created_at TEXT NOT NULL"
            ")"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add_message(self, chat_id: str, role: str, text: str) -> None:
        now = datetime.utcnow().isoformat()
        self._conn.execute(
            "INSERT INTO conversation_history (chat_id, role, text, created_at) VALUES (?, ?, ?, ?)",
            (chat_id, role, text, now),
        )
        self._conn.commit()

    def get_context(self, chat_id: str, limit: int = CONTEXT_LIMIT) -> list[dict]:
        cur = self._conn.execute(
            "SELECT role, text FROM conversation_history"
            " WHERE chat_id = ?"
            " ORDER BY id ASC"
            " LIMIT ?",
            (chat_id, limit),
        )
        return [{"role": row[0], "text": row[1]} for row in cur.fetchall()]

    def reset(self, chat_id: str) -> None:
        self._conn.execute(
            "DELETE FROM conversation_history WHERE chat_id = ?", (chat_id,)
        )
        self._conn.commit()

    def count(self, chat_id: str) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM conversation_history WHERE chat_id = ?", (chat_id,)
        )
        return cur.fetchone()[0]
