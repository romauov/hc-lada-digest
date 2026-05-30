"""Seen URLs — SQLite-хранилище виденных URL новостей."""
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)


class SeenStore:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS seen_urls (url TEXT PRIMARY KEY, first_seen TEXT)"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def is_seen(self, url: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM seen_urls WHERE url = ?", (url,))
        return cur.fetchone() is not None

    def mark_seen(self, url: str, first_seen: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_urls (url, first_seen) VALUES (?, ?)",
            (url, first_seen),
        )
        self._conn.commit()

    def mark_many(self, urls: set[str], first_seen: str) -> None:
        rows = [(u, first_seen) for u in urls]
        self._conn.executemany(
            "INSERT OR IGNORE INTO seen_urls (url, first_seen) VALUES (?, ?)", rows
        )
        self._conn.commit()

    def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM seen_urls")
        return cur.fetchone()[0]
