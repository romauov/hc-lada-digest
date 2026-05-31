"""
Тесты итерации 5: fact_checker.
LLM-вызовы мокируются.

Запуск: python -m pytest tests/test_iteration5.py -v
"""
import pytest
from datetime import date
from unittest.mock import patch

from shared.models import Entity, KnowledgeGraph, NewsItem, Relation
from shared.fact_checker import (
    Contradiction, VerificationResult,
    verify_news_item, apply_contradictions_to_graph,
    verify_all_news, format_fact_check_block,
    _build_facts_block, FACT_THRESHOLD, CONFIRMATION_SOURCES_NEEDED,
)

TODAY = date.today().isoformat()


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_graph(with_coach=True) -> KnowledgeGraph:
    entities = {
        "lada_hc": Entity(
            id="lada_hc", type="hockey_club", name="Лада",
            full_name="ХК Лада Тольятти", priority_score=1.0,
            search_queries=["ХК Лада"],
        ),
    }
    relations = []
    if with_coach:
        entities["coach_1"] = Entity(
            id="coach_1", type="coach", name="Иван Петров",
            priority_score=0.8, search_queries=["Петров тренер Лада"],
        )
        relations.append(Relation(
            from_id="lada_hc", to_id="coach_1",
            type="HAS_COACH", confidence=1.0,
            since="2023-09-01", is_rumour=False,
        ))
    return KnowledgeGraph(
        root_id="lada_hc", created_at=TODAY, last_updated=TODAY,
        version=1, entities=entities, relations=relations,
    )


def make_news(importance=0.8, title="Тест", url="https://example.com/1") -> NewsItem:
    return NewsItem(
        url=url, title=title, published_at=TODAY,
        source="sports_ru", entity_id="lada_hc",
        relevance_score=importance, summary=title,
    )


def make_contradiction(severity="high", confirmed_by=None) -> Contradiction:
    return Contradiction(
        entity_name="Иван Петров",
        relation_type="HAS_COACH",
        known_fact="тренер Лады с 2023",
        new_claim="перешёл в Ак Барс",
        severity=severity,
        source_url="https://example.com/1",
        source_name="sports_ru",
        confirmed_by=confirmed_by or [],
    )


# ── Contradiction model ───────────────────────────────────────────────────────

class TestContradiction:
    def test_not_confirmed_by_default(self):
        c = make_contradiction()
        assert not c.is_confirmed

    def test_confirmed_when_enough_sources(self):
        urls = [f"https://source{i}.com" for i in range(CONFIRMATION_SOURCES_NEEDED)]
        c    = make_contradiction(confirmed_by=urls)
        assert c.is_confirmed

    def test_not_confirmed_one_source_short(self):
        c = make_contradiction(confirmed_by=["https://one.com"])
        assert not c.is_confirmed  # нужно CONFIRMATION_SOURCES_NEEDED

    def test_serialization_roundtrip(self):
        c  = make_contradiction(severity="medium")
        c2 = Contradiction.from_dict(c.to_dict())
        assert c.entity_name   == c2.entity_name
        assert c.severity      == c2.severity
        assert c.confirmed_by  == c2.confirmed_by

    def test_digest_block_format(self):
        result = VerificationResult(
            news_item=make_news(),
            contradictions=[make_contradiction(severity="high")],
            is_clean=False,
        )
        block = result.to_digest_block()
        assert "🔴" in block
        assert "Иван Петров" in block
        assert "перешёл в Ак Барс" in block
        assert "тренер Лады с 2023" in block

    def test_digest_block_empty_when_clean(self):
        result = VerificationResult(news_item=make_news(), is_clean=True)
        assert result.to_digest_block() == ""


# ── _build_facts_block ────────────────────────────────────────────────────────

class TestBuildFactsBlock:
    def test_includes_confirmed_facts(self):
        graph = make_graph()
        block = _build_facts_block(graph)
        assert "Иван Петров" in block
        assert "HAS_COACH" in block

    def test_excludes_low_confidence(self):
        graph = make_graph()
        graph.relations[0].confidence = FACT_THRESHOLD - 0.1
        block = _build_facts_block(graph)
        assert "Иван Петров" not in block

    def test_excludes_rumours(self):
        graph = make_graph()
        graph.relations[0].is_rumour = True
        block = _build_facts_block(graph)
        assert "Иван Петров" not in block

    def test_empty_graph_message(self):
        graph = make_graph(with_coach=False)
        block = _build_facts_block(graph)
        assert "нет подтверждённых фактов" in block


# ── verify_news_item ──────────────────────────────────────────────────────────

class TestVerifyNewsItem:
    def test_skips_low_importance(self):
        graph  = make_graph()
        item   = make_news(importance=0.1)
        result = verify_news_item(item, graph)
        assert result.is_clean
        assert "low importance" in result.skip_reason

    def test_skips_empty_graph(self):
        graph  = make_graph(with_coach=False)
        item   = make_news(importance=0.9)
        result = verify_news_item(item, graph)
        assert result.is_clean
        assert "no confirmed facts" in result.skip_reason

    CONTRADICTION_RESPONSE = """{
        "has_contradiction": true,
        "contradictions": [{
            "entity_name": "Иван Петров",
            "relation_type": "HAS_COACH",
            "known_fact": "тренер Лады с 2023",
            "new_claim": "перешёл в Ак Барс",
            "severity": "high"
        }]
    }"""

    NO_CONTRADICTION_RESPONSE = '{"has_contradiction": false, "contradictions": []}'

    @patch("shared.fact_checker.verifier.analyze_news", return_value=NO_CONTRADICTION_RESPONSE)
    def test_clean_news_returns_clean(self, _):
        result = verify_news_item(make_news(importance=0.9), make_graph())
        assert result.is_clean
        assert result.contradictions == []

    @patch("shared.fact_checker.verifier.analyze_news", return_value=CONTRADICTION_RESPONSE)
    def test_contradiction_detected(self, _):
        result = verify_news_item(make_news(importance=0.9), make_graph())
        assert not result.is_clean
        assert len(result.contradictions) == 1
        assert result.contradictions[0].severity == "high"
        assert result.has_high_severity

    @patch("shared.fact_checker.verifier.analyze_news", return_value=None)
    def test_llm_failure_returns_clean(self, _):
        result = verify_news_item(make_news(importance=0.9), make_graph())
        assert result.is_clean
        assert "LLM unavailable" in result.skip_reason


# ── apply_contradictions_to_graph ────────────────────────────────────────────

class TestApplyContradictions:
    def test_unconfirmed_lowers_confidence_and_flags_rumour(self):
        graph      = make_graph()
        before_conf = graph.relations[0].confidence
        c           = make_contradiction(severity="high")  # не подтверждено
        graph       = apply_contradictions_to_graph(graph, [c])
        assert graph.relations[0].confidence < before_conf
        assert graph.relations[0].is_rumour

    def test_confirmed_sets_until_date(self):
        graph = make_graph()
        urls  = [f"https://s{i}.com" for i in range(CONFIRMATION_SOURCES_NEEDED)]
        c     = make_contradiction(confirmed_by=urls)
        graph = apply_contradictions_to_graph(graph, [c])
        assert graph.relations[0].until == TODAY

    def test_confirmed_not_marked_as_rumour(self):
        graph = make_graph()
        urls  = [f"https://s{i}.com" for i in range(CONFIRMATION_SOURCES_NEEDED)]
        c     = make_contradiction(confirmed_by=urls)
        graph = apply_contradictions_to_graph(graph, [c])
        assert not graph.relations[0].is_rumour

    def test_unknown_entity_ignored(self):
        graph = make_graph()
        c     = make_contradiction()
        c.entity_name = "Неизвестный Игрок"
        before_conf   = graph.relations[0].confidence
        graph         = apply_contradictions_to_graph(graph, [c])
        assert graph.relations[0].confidence == before_conf  # не тронуто

    def test_confidence_floor(self):
        graph = make_graph()
        graph.relations[0].confidence = 0.31
        c     = make_contradiction()  # -0.2 от 0.31 → должно быть >= 0.3
        graph = apply_contradictions_to_graph(graph, [c])
        assert graph.relations[0].confidence >= 0.3


# ── verify_all_news ───────────────────────────────────────────────────────────

class TestVerifyAllNews:
    CLEAN_RESPONSE = '{"has_contradiction": false, "contradictions": []}'

    @patch("shared.fact_checker.verifier.analyze_news", return_value=CLEAN_RESPONSE)
    def test_clean_batch_no_contradictions(self, _):
        graph  = make_graph()
        news   = [make_news(importance=0.9, url=f"https://a.com/{i}") for i in range(3)]
        g2, results, contradictions = verify_all_news(news, graph)
        assert contradictions == []
        assert all(r.is_clean for r in results)

    CONTRA_RESPONSE = """{
        "has_contradiction": true,
        "contradictions": [{
            "entity_name": "Иван Петров",
            "relation_type": "HAS_COACH",
            "known_fact": "тренер Лады",
            "new_claim": "ушёл из клуба",
            "severity": "medium"
        }]
    }"""

    @patch("shared.fact_checker.verifier.analyze_news", return_value=CONTRA_RESPONSE)
    def test_contradiction_propagates_to_graph(self, _):
        graph  = make_graph()
        before = graph.relations[0].confidence
        news   = [make_news(importance=0.9)]
        g2, _, contradictions = verify_all_news(news, graph)
        assert len(contradictions) == 1
        assert g2.relations[0].confidence < before


# ── format_fact_check_block ───────────────────────────────────────────────────

class TestFormatFactCheckBlock:
    def test_empty_list_returns_empty(self):
        assert format_fact_check_block([]) == ""

    def test_high_severity_shows_red(self):
        block = format_fact_check_block([make_contradiction(severity="high")])
        assert "🔴" in block
        assert "Требует проверки" in block

    def test_medium_severity_shows_yellow(self):
        block = format_fact_check_block([make_contradiction(severity="medium")])
        assert "🟡" in block

    def test_confirmed_shows_checkmark(self):
        urls = [f"https://s{i}.com" for i in range(CONFIRMATION_SOURCES_NEEDED)]
        c    = make_contradiction(confirmed_by=urls)
        block = format_fact_check_block([c])
        assert "✓ подтверждено" in block

    def test_source_link_present(self):
        c     = make_contradiction()
        block = format_fact_check_block([c])
        assert "https://example.com/1" in block
        assert "sports_ru" in block

    def test_multiple_severities_sorted(self):
        high   = make_contradiction(severity="high")
        low    = make_contradiction(severity="low")
        low.source_url = "https://low.com"
        block  = format_fact_check_block([low, high])  # low идёт первым в списке
        # но в блоке high должен быть выше
        assert block.index("🔴") < block.index("🔵")
