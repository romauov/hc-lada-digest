import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

from shared.models import NewsItem

logger = logging.getLogger(__name__)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS news (
    url TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    published_at TEXT,
    source TEXT,
    entity_id TEXT,
    summary TEXT DEFAULT '',
    is_rumour INTEGER DEFAULT 0,
    relevance_score REAL DEFAULT 0.0,
    deferred INTEGER DEFAULT 0,
    processed_at TEXT DEFAULT (datetime('now'))
)
"""


class NewsStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(CREATE_SQL)
        return self._conn

    def save(self, items: list[NewsItem]) -> int:
        conn = self._connect()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        saved = 0
        for item in items:
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO news
                    (url, title, published_at, source, entity_id, summary,
                     is_rumour, relevance_score, deferred, processed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item.url,
                        item.title,
                        item.published_at,
                        item.source,
                        item.entity_id,
                        item.summary,
                        1 if item.is_rumour else 0,
                        item.relevance_score,
                        1 if item.deferred else 0,
                        now,
                    ),
                )
                saved += 1
            except Exception as e:
                logger.warning("Failed to save news %s: %s", item.url[:60], e)
        conn.commit()
        return saved

    def get_recent(self, limit: int = 50) -> list[NewsItem]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT url, title, published_at, source, entity_id, summary, "
            "is_rumour, relevance_score, deferred "
            "FROM news ORDER BY processed_at DESC, published_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            NewsItem(
                url=r[0],
                title=r[1],
                published_at=r[2] or "",
                source=r[3] or "",
                entity_id=r[4] or "",
                summary=r[5] or "",
                is_rumour=bool(r[6]),
                relevance_score=r[7],
                deferred=bool(r[8]),
            )
            for r in rows
        ]

    def get_by_date(self, date_str: str) -> list[NewsItem]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT url, title, published_at, source, entity_id, summary, "
            "is_rumour, relevance_score, deferred "
            "FROM news WHERE published_at LIKE ? "
            "ORDER BY relevance_score DESC",
            (date_str + "%",),
        ).fetchall()
        return [
            NewsItem(
                url=r[0],
                title=r[1],
                published_at=r[2] or "",
                source=r[3] or "",
                entity_id=r[4] or "",
                summary=r[5] or "",
                is_rumour=bool(r[6]),
                relevance_score=r[7],
                deferred=bool(r[8]),
            )
            for r in rows
        ]

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
