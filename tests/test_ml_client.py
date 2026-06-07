import os
from unittest.mock import MagicMock, patch

import pytest

from shared import ml_client


@pytest.fixture(autouse=True)
def clear_env():
    ml_client.ML_URL = "http://ml:8001"
    yield
    ml_client.ML_URL = ""


class TestSemanticDedup:
    def test_normal_path(self):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"indices_to_keep": [0, 2]}
        with patch("shared.ml_client.requests.post", return_value=mock_resp) as mock_post:
            result = ml_client.semantic_dedup(["a", "b", "a"], threshold=0.9)
            assert result == [0, 2]
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert kwargs["json"]["titles"] == ["a", "b", "a"]

    def test_timeout_fallback(self):
        with patch("shared.ml_client.requests.post", side_effect=Exception("timeout")):
            result = ml_client.semantic_dedup(["a", "b", "a"])
            assert result == [0, 1, 2]

    def test_empty_titles(self):
        result = ml_client.semantic_dedup([])
        assert result == []

    def test_ml_url_empty(self):
        ml_client.ML_URL = ""
        result = ml_client.semantic_dedup(["a", "b"])
        assert result == [0, 1]


class TestIsRelevant:
    def test_relevant(self):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"is_relevant": True, "score": 0.85}
        with patch("shared.ml_client.requests.post", return_value=mock_resp):
            ok, score = ml_client.is_relevant("Лада выиграла", ["ХК Лада", "Лада"])
            assert ok is True
            assert score == 0.85

    def test_not_relevant(self):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"is_relevant": False, "score": 0.3}
        with patch("shared.ml_client.requests.post", return_value=mock_resp):
            ok, score = ml_client.is_relevant("Погода в Москве", ["ХК Лада"])
            assert ok is False
            assert score == 0.3

    def test_unavailable_fallback(self):
        with patch("shared.ml_client.requests.post", side_effect=Exception("timeout")):
            ok, score = ml_client.is_relevant("Лада выиграла", ["ХК Лада"])
            assert ok is True
            assert score == 1.0

    def test_ml_url_empty(self):
        ml_client.ML_URL = ""
        ok, score = ml_client.is_relevant("Лада выиграла", ["ХК Лада"])
        assert ok is True
        assert score == 1.0


class TestCalcRelevance:
    def test_sets_score(self):
        from shared.models import NewsItem

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"is_relevant": True, "score": 0.75}
        item = NewsItem(url="http://x", title="Лада выиграла", published_at="", source="test", entity_id="e1")
        with patch("shared.ml_client.requests.post", return_value=mock_resp):
            result = ml_client.calc_relevance(item, ["Лада"])
            assert result.relevance_score == 0.75

    def test_keeps_low_score(self):
        from shared.models import NewsItem

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"is_relevant": False, "score": 0.3}
        item = NewsItem(url="http://x", title="Погода", published_at="", source="test", entity_id="e1")
        with patch("shared.ml_client.requests.post", return_value=mock_resp):
            result = ml_client.calc_relevance(item, ["Лада"])
            assert result.relevance_score == 0.3
