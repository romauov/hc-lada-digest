import tempfile
import os

from shared.news_store import NewsStore
from shared.models import NewsItem


def _store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return NewsStore(path), path


def test_save_and_get_recent():
    store, path = _store()
    try:
        items = [
            NewsItem(url="http://a.com", title="News A", published_at="2026-05-31T10:00:00",
                     source="test", entity_id="lada"),
            NewsItem(url="http://b.com", title="News B", published_at="2026-05-31T11:00:00",
                     source="test", entity_id="cska"),
        ]
        assert store.save(items) == 2
        recent = store.get_recent(10)
        assert len(recent) == 2
        assert recent[0].title == "News B"
        assert recent[1].url == "http://a.com"
    finally:
        store.close()
        os.unlink(path)


def test_save_overwrites_by_url():
    store, path = _store()
    try:
        items = [
            NewsItem(url="http://a.com", title="Original", published_at="2026-05-31T10:00:00",
                     source="test", entity_id="lada", summary="old"),
        ]
        store.save(items)
        items[0].summary = "updated"
        items[0].is_rumour = True
        assert store.save(items) == 1

        recent = store.get_recent(10)
        assert len(recent) == 1
        assert recent[0].summary == "updated"
        assert recent[0].is_rumour is True
    finally:
        store.close()
        os.unlink(path)


def test_save_empty_list():
    store, path = _store()
    try:
        assert store.save([]) == 0
        assert len(store.get_recent(10)) == 0
    finally:
        store.close()
        os.unlink(path)


def test_get_by_date():
    store, path = _store()
    try:
        items = [
            NewsItem(url="http://a.com", title="News A", published_at="2026-05-31T10:00:00",
                     source="test", entity_id="lada"),
            NewsItem(url="http://b.com", title="News B", published_at="2026-06-01T10:00:00",
                     source="test", entity_id="cska"),
        ]
        store.save(items)
        day1 = store.get_by_date("2026-05-31")
        assert len(day1) == 1
        assert day1[0].url == "http://a.com"

        day2 = store.get_by_date("2026-06-01")
        assert len(day2) == 1
    finally:
        store.close()
        os.unlink(path)


def test_deferred_field_preserved():
    store, path = _store()
    try:
        items = [
            NewsItem(url="http://d.com", title="Deferred", published_at="2026-05-31T10:00:00",
                     source="test", entity_id="lada", deferred=True),
        ]
        store.save(items)
        recent = store.get_recent(10)
        assert recent[0].deferred is True
    finally:
        store.close()
        os.unlink(path)
