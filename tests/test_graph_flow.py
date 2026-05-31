"""Tests for bot graph-answer flow: classify_need_search + answer_from_graph."""
import os
from datetime import date
from unittest.mock import patch

os.environ.setdefault("DATA_DIR", "/tmp/test_data")

from shared.models import Entity, KnowledgeGraph
from shared.classifiers import classify_need_search
from functions.tg_sender.answers import answer_from_graph

TODAY = date.today().isoformat()


def make_graph() -> KnowledgeGraph:
    return KnowledgeGraph(
        root_id="lada_hc",
        created_at=TODAY,
        last_updated=TODAY,
        version=1,
        entities={
            "lada_hc": Entity(
                id="lada_hc", type="hockey_club", name="Лада",
                full_name="ХК Лада Тольятти", priority_score=1.0,
            ),
            "coach_1": Entity(
                id="coach_1", type="coach", name="Иван Иванов",
                priority_score=0.8,
            ),
        },
        relations=[],
    )


class TestClassifyNeedSearch:
    @patch("shared.classifiers._call_llm", return_value="YES")
    def test_unknown_entity_triggers_search(self, _):
        g = make_graph()
        assert classify_need_search(
            "какие хк есть в системе полипласта?",
            "",
            g.entities,
        ) is True

    @patch("shared.classifiers._call_llm", return_value="NO")
    def test_known_entity_no_search(self, _):
        g = make_graph()
        assert classify_need_search(
            "кто тренер лады?",
            "",
            g.entities,
        ) is False

    @patch("shared.classifiers._call_llm", return_value="NO")
    def test_no_entities_factual_question(self, _):
        assert classify_need_search(
            "где находится арена лады?",
            "",
        ) is False


class TestAnswerFromGraph:
    @patch("functions.tg_sender.answers.analyze_news",
           return_value='{"found": false}')
    def test_unknown_returns_none_triggers_search(self, _):
        g = make_graph()
        result = answer_from_graph(
            "какие хк есть в системе полипласта?",
            g,
            "123",
        )
        assert result is None

    @patch("functions.tg_sender.answers.analyze_news",
           return_value='{"found": true, "answer": "Тренер — Иван Иванов"}')
    def test_known_returns_answer(self, _):
        g = make_graph()
        result = answer_from_graph(
            "кто тренер лады?",
            g,
            "123",
        )
        assert result == "Тренер — Иван Иванов"

    @patch("functions.tg_sender.answers.analyze_news",
           return_value="К сожалению, я не знаю")
    def test_non_json_refusal_returns_none(self, _):
        g = make_graph()
        result = answer_from_graph(
            "какие хк есть в системе полипласта?",
            g,
            "123",
        )
        assert result is None
