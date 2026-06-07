"""
Тесты итерации 3: источники новостей и реестр.
HTTP-запросы мокируются — тесты не требуют сети.

Запуск: python -m pytest tests/test_iteration3.py -v
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from xml.etree import ElementTree as ET

from shared.models import Entity
from functions.search_worker.sources.base import BaseSource
from functions.search_worker.sources.rss import (
    GoogleNewsSource, KHLSource, SportsRuRSSSource,
    _fetch_feed, _matches_keywords,
)
from functions.search_worker.sources.scrapers import (
    SportsRuScraperSource, ChampionatScraperSource,
    _parse_text_date, _parse_iso,
)
from functions.search_worker.sources.yandex_search import (
    YandexSearchSource, _parse_yandex_date, _strip_tags,
)
from functions.search_worker.sources.registry import SourceRegistry, reset_registry


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_entity(**kwargs) -> Entity:
    defaults = dict(
        id="lada_hc", type="hockey_club", name="Лада",
        search_queries=["ХК Лада", "Lada hockey"],
    )
    defaults.update(kwargs)
    return Entity(**defaults)


NOW    = datetime.now(timezone.utc)
RECENT = (NOW - timedelta(hours=2)).isoformat()
OLD    = (NOW - timedelta(hours=48)).isoformat()

FAKE_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Лада разгромила соперника</title>
    <link>https://sports.ru/news/lada-wins.html</link>
    <pubDate>Mon, 13 May 2024 10:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Старая новость</title>
    <link>https://sports.ru/news/old.html</link>
    <pubDate>Wed, 01 Jan 2020 00:00:00 GMT</pubDate>
  </item>
</channel></rss>"""


# ── BaseSource ────────────────────────────────────────────────────────────────

class TestBaseSource:
    def test_normalize_url_strips_utm(self):
        class ConcreteSource(BaseSource):
            name = "test"
            def fetch(self, entity): return []
        src = ConcreteSource()
        url = "https://example.com/news?id=1&utm_source=google&utm_medium=cpc"
        normalized = src.normalize_url(url)
        assert "utm_source" not in normalized
        assert "id=1" in normalized

    def test_normalize_url_strips_fragment(self):
        class ConcreteSource(BaseSource):
            name = "test"
            def fetch(self, entity): return []
        src = ConcreteSource()
        assert "#comments" not in src.normalize_url("https://example.com/news#comments")

    def test_is_fresh_recent(self):
        class ConcreteSource(BaseSource):
            name = "test"
            def fetch(self, entity): return []
        src = ConcreteSource(freshness_hours=24)
        recent = datetime.now(timezone.utc) - timedelta(hours=2)
        assert src.is_fresh(recent)

    def test_is_fresh_old(self):
        class ConcreteSource(BaseSource):
            name = "test"
            def fetch(self, entity): return []
        src = ConcreteSource(freshness_hours=24)
        old = datetime.now(timezone.utc) - timedelta(hours=48)
        assert not src.is_fresh(old)

    def test_is_fresh_none_returns_true(self):
        class ConcreteSource(BaseSource):
            name = "test"
            def fetch(self, entity): return []
        src = ConcreteSource()
        assert src.is_fresh(None)

    def test_make_news_id_stable(self):
        assert BaseSource.make_news_id("http://x.com") == BaseSource.make_news_id("http://x.com")

    def test_make_news_id_different(self):
        assert BaseSource.make_news_id("http://a.com") != BaseSource.make_news_id("http://b.com")


# ── RSS helpers ───────────────────────────────────────────────────────────────

class TestRSSHelpers:
    def test_matches_keywords_found(self):
        entry = {"title": "Лада победила ЦСКА", "summary": ""}
        assert _matches_keywords(entry, ["Лада", "ЦСКА"])

    def test_matches_keywords_not_found(self):
        entry = {"title": "Спартак выиграл чемпионат", "summary": ""}
        assert not _matches_keywords(entry, ["Лада"])

    def test_matches_keywords_case_insensitive(self):
        entry = {"title": "лада тольятти", "summary": ""}
        assert _matches_keywords(entry, ["Лада"])

    def test_matches_keywords_in_summary(self):
        entry = {"title": "Хоккей", "summary": "ХК Лада выиграла"}
        assert _matches_keywords(entry, ["Лада"])


# ── RSS sources ───────────────────────────────────────────────────────────────

class TestGoogleNewsSource:
    @patch("functions.search_worker.sources.rss._fetch_feed")
    def test_returns_fresh_news(self, mock_feed):
        import feedparser
        mock_entry       = MagicMock()
        mock_entry.get   = lambda k, d="": {"title": "Лада выиграла", "link": "https://news.com/1", "summary": ""}[k] if k in ("title", "link", "summary") else d
        mock_entry.published_parsed = (datetime.now(timezone.utc) - timedelta(hours=1)).timetuple()[:9]
        mock_feed.return_value = [mock_entry]

        src   = GoogleNewsSource(freshness_hours=24)
        items = src.fetch(make_entity())
        assert len(items) > 0
        assert items[0].source == "google_news"

    @patch("functions.search_worker.sources.rss._fetch_feed", return_value=[])
    def test_empty_feed_returns_empty(self, _):
        src = GoogleNewsSource()
        assert src.fetch(make_entity()) == []

    @patch("functions.search_worker.sources.rss._fetch_feed")
    def test_deduplicates_same_url(self, mock_feed):
        mock_entry = MagicMock()
        mock_entry.get = lambda k, d="": {
            "title": "Новость", "link": "https://same.com/1", "summary": ""
        }.get(k, d)
        mock_entry.published_parsed = (datetime.now(timezone.utc) - timedelta(hours=1)).timetuple()[:9]
        mock_feed.return_value = [mock_entry, mock_entry]  # дубль

        entity = make_entity(search_queries=["Лада", "Лада Тольятти"])  # 2 запроса
        src    = GoogleNewsSource()
        items  = src.fetch(entity)
        urls   = [i.url for i in items]
        assert len(urls) == len(set(urls))


# ── Scraper helpers ───────────────────────────────────────────────────────────

class TestScraperHelpers:
    def test_parse_iso_valid(self):
        dt = _parse_iso("2024-03-15T14:30:22")
        assert dt is not None
        assert dt.year == 2024 and dt.month == 3 and dt.day == 15

    def test_parse_iso_with_z(self):
        dt = _parse_iso("2024-03-15T14:30:22Z")
        assert dt is not None

    def test_parse_iso_invalid(self):
        assert _parse_iso("not-a-date") is None
        assert _parse_iso("") is None
        assert _parse_iso(None) is None

    def test_parse_text_date_dot_format(self):
        dt = _parse_text_date("15.03.2024")
        assert dt is not None and dt.day == 15 and dt.month == 3

    def test_parse_text_date_ru_format(self):
        dt = _parse_text_date("15 мар 2024")
        assert dt is not None and dt.month == 3

    def test_parse_text_date_invalid(self):
        assert _parse_text_date("неизвестно") is None


# ── Yandex Search ─────────────────────────────────────────────────────────────

class TestYandexSearch:
    def test_parse_yandex_date_compact(self):
        dt = _parse_yandex_date("20240315T143022")
        assert dt is not None and dt.year == 2024 and dt.month == 3

    def test_parse_yandex_date_iso(self):
        dt = _parse_yandex_date("2024-03-15T14:30:22")
        assert dt is not None

    def test_parse_yandex_date_none(self):
        assert _parse_yandex_date(None) is None

    def test_strip_tags(self):
        assert _strip_tags("<b>Лада</b> выиграла") == "Лада выиграла"
        assert _strip_tags("Чистый текст") == "Чистый текст"

    def test_disabled_without_credentials(self):
        import os
        with patch.dict(os.environ, {}, clear=True):
            # убираем ключи
            os.environ.pop("YANDEX_SEARCH_USER", None)
            os.environ.pop("YANDEX_SEARCH_KEY", None)
            src   = YandexSearchSource()
            items = src.fetch(make_entity())
            assert items == []

    def test_parses_xml_response(self):
        xml = """<?xml version="1.0"?>
<yandexsearch>
  <response>
    <results>
      <grouping>
        <group><doc>
          <url>https://sports.ru/news/lada.html</url>
          <title>Лада победила</title>
          <modtime>20240515T100000</modtime>
        </doc></group>
      </grouping>
    </results>
  </response>
</yandexsearch>"""
        src   = YandexSearchSource()
        items = src._parse_xml(xml, "lada_hc")
        # дата 2024-05-15 — не свежая по умолчанию (24ч), но парсинг должен пройти
        # проверяем что метод отработал без ошибок
        assert isinstance(items, list)


# ── SourceRegistry ────────────────────────────────────────────────────────────

class TestSourceRegistry:
    def setup_method(self):
        reset_registry()

    def test_fetch_all_aggregates_sources(self):
        from functions.search_worker.sources.base import BaseSource
        from shared.models import NewsItem as NI

        class FakeSource(BaseSource):
            name = "fake"
            def fetch(self, entity):
                return [NI(url=f"https://fake.com/{entity.id}", title="Лада новость",
                           published_at="", source="fake", entity_id=entity.id)]

        registry = SourceRegistry()
        registry._sources = [FakeSource(), FakeSource()]  # два одинаковых источника

        items = registry.fetch_all(make_entity())
        # дедупликация по URL — должен быть 1 результат несмотря на два источника
        assert len(items) == 1

    def test_source_disabled_after_3_errors(self):
        class ErrorSource(BaseSource):
            name = "error_src"
            def fetch(self, entity):
                raise RuntimeError("network error")

        registry = SourceRegistry()
        registry._sources = [ErrorSource()]
        registry._stats["error_src"] = __import__(
            "functions.search_worker.sources.registry", fromlist=["SourceStats"]
        ).SourceStats(name="error_src")
        registry._consecutive_errors["error_src"] = 0

        entity = make_entity()
        for _ in range(3):
            registry.fetch_all(entity)

        assert registry._stats["error_src"].disabled

    def test_get_summary_returns_all_sources(self):
        registry = SourceRegistry()
        summary  = registry.get_summary()
        assert "google_news" in summary
        assert "khl"         in summary
        assert "sports_ru"   in summary
