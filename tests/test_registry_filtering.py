"""
Тесты фильтрации в SourceRegistry: _keyword_prefilter и fetch_all с замоканным ML.

Не требуют Docker/ML-сервиса — ML-зависимости замоканы.
"""
from unittest.mock import MagicMock, patch

import pytest

from functions.search_worker.sources.base import BaseSource
from functions.search_worker.sources.registry import SourceRegistry, reset_registry
from shared.models import Entity, NewsItem


def _make_item(title: str, url: str = None, summary: str = "") -> NewsItem:
    return NewsItem(
        url=url or f"https://example.com/{hash(title)}",
        title=title,
        published_at="2026-01-01",
        source="test",
        entity_id="e1",
        summary=summary,
    )


class FakeSource(BaseSource):
    name = "fake"
    def __init__(self, items: list[NewsItem]):
        super().__init__(0)
        self._items = items
    def fetch(self, entity):
        return self._items


@pytest.fixture(autouse=True)
def reset():
    reset_registry()
    yield
    reset_registry()


class TestKeywordPrefilter:
    def test_matching_title(self):
        entity = Entity(id="e1", type="club", name="Test", search_queries=["Лада", "Тольятти"])
        items = [_make_item("Лада выиграла матч")]
        registry = SourceRegistry()
        result = registry._keyword_prefilter(items, entity)
        assert len(result) == 1

    def test_matching_summary(self):
        entity = Entity(id="e1", type="club", name="Test", search_queries=["Тольятти"])
        items = [_make_item("Победа в КХЛ", summary="Команда из Тольятти одержала победу")]
        registry = SourceRegistry()
        result = registry._keyword_prefilter(items, entity)
        assert len(result) == 1

    def test_no_match(self):
        entity = Entity(id="e1", type="club", name="Test", search_queries=["Лада"])
        items = [_make_item("ЦСКА выиграл кубок")]
        registry = SourceRegistry()
        result = registry._keyword_prefilter(items, entity)
        assert len(result) == 0

    def test_empty_search_queries(self):
        entity = Entity(id="e1", type="club", name="Test", search_queries=[])
        items = [_make_item("Любая новость")]
        registry = SourceRegistry()
        result = registry._keyword_prefilter(items, entity)
        assert len(result) == 1

    def test_case_insensitive(self):
        entity = Entity(id="e1", type="club", name="Test", search_queries=["лада"])
        items = [_make_item("ЛАДА выиграла")]
        registry = SourceRegistry()
        result = registry._keyword_prefilter(items, entity)
        assert len(result) == 1

    def test_partial_word_match(self):
        entity = Entity(id="e1", type="club", name="Test", search_queries=["Лада"])
        items = [_make_item("Тольяттилада победа")]
        registry = SourceRegistry()
        result = registry._keyword_prefilter(items, entity)
        assert len(result) == 1


@patch("shared.ml_client.requests.post")
class TestFetchAllWithML:
    def test_semantic_dedup_and_relevance_called(self, mock_post):
        import shared.ml_client as mc
        mc.ML_URL = "http://ml:8001"
        mock_post.return_value.ok = True
        mock_post.return_value.json.side_effect = [
            {"indices_to_keep": [0, 1]},
            {"is_relevant": True, "score": 0.85},
            {"is_relevant": False, "score": 0.3},
        ]
        entity = Entity(id="e1", type="club", name="Test", search_queries=["test", "title"])
        source = FakeSource([_make_item("test title 1"), _make_item("test title 2")])
        registry = SourceRegistry()
        registry._sources = [source]
        result = registry.fetch_all(entity)
        assert len(result) == 1
        assert mock_post.call_count == 3

    def test_all_relevant(self, mock_post):
        import shared.ml_client as mc
        mc.ML_URL = "http://ml:8001"
        mock_post.return_value.ok = True
        mock_post.return_value.json.side_effect = [
            {"indices_to_keep": [0, 1]},
            {"is_relevant": True, "score": 0.9},
            {"is_relevant": True, "score": 0.8},
        ]
        entity = Entity(id="e1", type="club", name="Test", search_queries=["title"])
        source = FakeSource([_make_item("title 1"), _make_item("title 2")])
        registry = SourceRegistry()
        registry._sources = [source]
        result = registry.fetch_all(entity)
        assert len(result) == 2
        low = registry.get_low_relevance("e1")
        assert len(low) == 0

    def test_low_relevance_stored(self, mock_post):
        import shared.ml_client as mc
        mc.ML_URL = "http://ml:8001"
        mock_post.return_value.ok = True
        mock_post.return_value.json.side_effect = [
            {"indices_to_keep": [0, 1, 2]},
            {"is_relevant": True, "score": 0.9},
            {"is_relevant": False, "score": 0.3},
            {"is_relevant": False, "score": 0.4},
        ]
        entity = Entity(id="e1", type="club", name="Test", search_queries=["title"])
        source = FakeSource([_make_item("title 1"), _make_item("title 2"), _make_item("title 3")])
        registry = SourceRegistry()
        registry._sources = [source]
        result = registry.fetch_all(entity)
        assert len(result) == 1
        low = registry.get_low_relevance("e1")
        assert len(low) == 2

    def test_no_low_relevance_for_other_entity(self, mock_post):
        import shared.ml_client as mc
        mc.ML_URL = "http://ml:8001"
        mock_post.return_value.ok = True
        mock_post.return_value.json.side_effect = [
            {"indices_to_keep": [0]},
            {"is_relevant": False, "score": 0.3},
        ]
        entity = Entity(id="e1", type="club", name="Test", search_queries=["title"])
        source = FakeSource([_make_item("title 1")])
        registry = SourceRegistry()
        registry._sources = [source]
        registry.fetch_all(entity)
        low_other = registry.get_low_relevance("e2")
        assert low_other == []


class TestFetchAllNoML:
    def test_no_ml_url_passes_all(self):
        import shared.ml_client as mc
        mc.ML_URL = ""
        entity = Entity(id="e1", type="club", name="Test", search_queries=["title"])
        source = FakeSource([_make_item("title 1"), _make_item("title 2")])
        registry = SourceRegistry()
        registry._sources = [source]
        result = registry.fetch_all(entity)
        assert len(result) == 2
        for item in result:
            assert item.relevance_score == 1.0
