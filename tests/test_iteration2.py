"""
Тесты итерации 2: graph_updater и digest_generator.
LLM-вызовы мокируются — тесты не требуют реальных токенов.

Запуск: python -m pytest tests/test_iteration2.py -v
"""
import pytest
from datetime import date
from unittest.mock import patch

from shared.models import Entity, KnowledgeGraph, NewsItem, Relation
from shared.graph_ops import (
    make_entity_id,
    entity_exists,
    apply_new_entities,
    apply_updated_relations,
)
from functions.graph_updater.handler import update_graph_from_news
from functions.digest_generator.handler import build_digest, _fallback_digest

TODAY = date.today().isoformat()


def make_graph() -> KnowledgeGraph:
    return KnowledgeGraph(
        root_id="lada_hc",
        created_at=TODAY, last_updated=TODAY, version=1,
        entities={
            "lada_hc": Entity(
                id="lada_hc", type="hockey_club", name="Лада",
                full_name="ХК Лада Тольятти", priority_score=1.0,
                search_queries=["ХК Лада"],
            ),
            "coach_1": Entity(
                id="coach_1", type="coach", name="Иван Иванов",
                priority_score=0.8, search_queries=["Иванов тренер Лада"],
            ),
        },
        relations=[
            Relation(from_id="lada_hc", to_id="coach_1", type="HAS_COACH", confidence=1.0),
        ],
    )


def make_news(**kwargs) -> NewsItem:
    defaults = dict(
        url="https://example.com/news/1", title="Тестовая новость",
        published_at=TODAY, source="sports_ru", entity_id="lada_hc",
        is_rumour=False, relevance_score=0.7, summary="Краткое содержание",
    )
    defaults.update(kwargs)
    return NewsItem(**defaults)


# ── utils ─────────────────────────────────────────────────────────────────────

class TestUtils:
    def testmake_entity_id_stable(self):
        assert make_entity_id("Пётр", "player") == make_entity_id("Пётр", "player")

    def testmake_entity_id_different_types(self):
        assert make_entity_id("Петров", "player") != make_entity_id("Петров", "coach")

    def testentity_exists(self):
        found, eid = entity_exists(make_graph(), "Лада")
        assert found and eid == "lada_hc"

    def testentity_exists_case_insensitive(self):
        found, _ = entity_exists(make_graph(), "лада")
        assert found

    def test_entity_not_exists(self):
        found, eid = entity_exists(make_graph(), "Ак Барс")
        assert not found and eid is None


# ── _apply_new_entities ───────────────────────────────────────────────────────

class TestApplyNewEntities:
    def _new_player(self, **kwargs):
        base = dict(name="Алексей Петров", type="player",
                    relation_to_root="HAS_PLAYER", confidence=0.9, is_rumour=False)
        base.update(kwargs)
        return base

    def test_adds_entity_and_relation(self):
        graph  = make_graph()
        before = len(graph.entities), len(graph.relations)
        apply_new_entities(graph, [self._new_player()], "http://x.com")
        assert len(graph.entities) == before[0] + 1
        assert len(graph.relations) == before[1] + 1

    def test_skips_existing(self):
        graph  = make_graph()
        before = len(graph.entities)
        apply_new_entities(graph, [self._new_player(name="Лада", type="hockey_club")], "x")
        assert len(graph.entities) == before

    def test_rumour_entity_flagged(self):
        graph = make_graph()
        apply_new_entities(graph, [self._new_player(name="Слух", is_rumour=True)], "x")
        ent = next(e for e in graph.entities.values() if e.name == "Слух")
        assert ent.is_rumour
        rel = next(r for r in graph.relations if r.to_id == ent.id)
        assert rel.is_rumour

    # ── Filter A: invalid names ───────────────────────────────────────────

    def test_skips_digits_only(self):
        graph = make_graph()
        before = len(graph.entities)
        apply_new_entities(graph, [dict(name="12345", type="player", relation_to_root="HAS_PLAYER")], "x")
        assert len(graph.entities) == before

    def test_skips_null_literal(self):
        graph = make_graph()
        before = len(graph.entities)
        apply_new_entities(graph, [dict(name="null", type="player", relation_to_root="HAS_PLAYER")], "x")
        assert len(graph.entities) == before

    def test_skips_hokkeist(self):
        graph = make_graph()
        before = len(graph.entities)
        apply_new_entities(graph, [dict(name="хоккеисты", type="player", relation_to_root="HAS_PLAYER")], "x")
        assert len(graph.entities) == before

    def test_skips_coach_generic(self):
        graph = make_graph()
        before = len(graph.entities)
        apply_new_entities(graph, [dict(name="главный тренер", type="coach", relation_to_root="HAS_COACH")], "x")
        assert len(graph.entities) == before

    def test_skips_name_over_60_chars(self):
        graph = make_graph()
        before = len(graph.entities)
        apply_new_entities(graph, [dict(name="А" * 61, type="player", relation_to_root="HAS_PLAYER")], "x")
        assert len(graph.entities) == before

    # ── Filter B: hockey_club with wrong relation ─────────────────────────

    def test_skips_hockey_club_wrong_relation(self):
        graph = make_graph()
        before = len(graph.entities)
        apply_new_entities(
            graph,
            [dict(name="Ак Барс", type="hockey_club", relation_to_root="RELATED_TO")],
            "x",
        )
        assert len(graph.entities) == before

    def test_adds_hockey_club_transfer_relation(self):
        graph = make_graph()
        before = len(graph.entities)
        apply_new_entities(
            graph,
            [dict(name="Ак Барс", type="hockey_club", relation_to_root="SIGNED_FROM")],
            "x",
        )
        assert len(graph.entities) == before + 1

    # ── Filter C: normalized duplicates ───────────────────────────────────

    def test_skips_normalized_dup_yo(self):
        graph = make_graph()
        apply_new_entities(
            graph,
            [dict(name="Игорь Швырёв", type="player", relation_to_root="HAS_PLAYER")],
            "x",
        )
        before = len(graph.entities)
        apply_new_entities(
            graph,
            [dict(name="Игорь Швырев", type="player", relation_to_root="HAS_PLAYER")],
            "x",
        )
        assert len(graph.entities) == before

    def test_skips_normalized_dup_spaces(self):
        graph = make_graph()
        apply_new_entities(
            graph,
            [dict(name="Петр", type="player", relation_to_root="HAS_PLAYER")],
            "x",
        )
        before = len(graph.entities)
        apply_new_entities(
            graph,
            [dict(name="Пётр ", type="player", relation_to_root="HAS_PLAYER")],
            "x",
        )
        assert len(graph.entities) == before

    def test_adds_valid_player_still_works(self):
        graph = make_graph()
        before = len(graph.entities)
        apply_new_entities(graph, [self._new_player()], "http://x.com")
        assert len(graph.entities) == before + 1


# ── apply_updated_relations ──────────────────────────────────────────────────

class TestApplyUpdatedRelations:
    def _upd(self, action, confidence=0.9, is_rumour=False):
        return dict(entity_name="Иван Иванов", relation_type="HAS_COACH",
                    action=action, confidence=confidence, is_rumour=is_rumour)

    def test_confirm_raises_confidence(self):
        graph = make_graph()
        graph.relations[0].confidence = 0.5
        apply_updated_relations(graph, [self._upd("confirm", 0.95)])
        assert graph.relations[0].confidence >= 0.9

    def test_remove_lowers_confidence(self):
        graph    = make_graph()
        original = graph.relations[0].confidence
        apply_updated_relations(graph, [self._upd("remove", 0.3)])
        assert graph.relations[0].confidence < original

    def test_rumour_confirmed(self):
        graph = make_graph()
        graph.relations[0].is_rumour = True
        apply_updated_relations(graph, [self._upd("confirm", 0.95)])
        assert not graph.relations[0].is_rumour


# ── update_graph_from_news ────────────────────────────────────────────────────

BASIC_LLM = '{"is_relevant":true,"is_rumour":false,"importance":0.8,"new_entities":[],"updated_relations":[],"summary":"Клуб выиграл"}'
NEW_ENT_LLM = '{"is_relevant":true,"is_rumour":false,"importance":0.9,"new_entities":[{"name":"Сергей Новиков","type":"player","relation_to_root":"HAS_PLAYER","confidence":0.95,"is_rumour":false,"search_queries":["Новиков Лада"]}],"updated_relations":[],"summary":"Подписан игрок"}'

class TestUpdateGraph:
    @patch("functions.graph_updater.analysis.analyze_news", return_value=BASIC_LLM)
    def test_version_incremented(self, _):
        g, _ = update_graph_from_news(make_graph(), [make_news()], {"lada_hc"})
        assert g.version == 2

    @patch("functions.graph_updater.analysis.analyze_news", return_value=BASIC_LLM)
    def test_mentioned_streak_updated(self, _):
        g, _ = update_graph_from_news(make_graph(), [make_news()], {"lada_hc"})
        assert g.entities["lada_hc"].mention_streak >= 1

    @patch("functions.graph_updater.analysis.analyze_news", return_value=BASIC_LLM)
    def test_unmentioned_streak_reset(self, _):
        graph = make_graph()
        graph.entities["coach_1"].mention_streak = 5
        g, _ = update_graph_from_news(graph, [make_news()], {"lada_hc"})
        assert g.entities["coach_1"].mention_streak == 0

    @patch("functions.graph_updater.analysis.analyze_news", return_value=None)
    def test_llm_failure_safe(self, _):
        g, results = update_graph_from_news(make_graph(), [make_news()], set())
        assert g.version == 2 and results == []

    @patch("functions.graph_updater.analysis.analyze_news", return_value=NEW_ENT_LLM)
    def test_new_entity_added(self, _):
        g, _ = update_graph_from_news(make_graph(), [make_news()], {"lada_hc"})
        assert any(e.name == "Сергей Новиков" for e in g.entities.values())


# ── digest_generator ─────────────────────────────────────────────────────────

class TestDigestGenerator:
    def test_fallback_contains_news(self):
        result = _fallback_digest([{"item": make_news(), "analysis": {}}], make_graph(), TODAY)
        assert "Тестовая новость" in result or "Краткое содержание" in result

    def test_fallback_separates_rumours(self):
        rumour = make_news(is_rumour=True, url="http://x.com/r", title="Слух о переходе")
        result = _fallback_digest(
            [{"item": make_news(), "analysis": {}}, {"item": rumour, "analysis": {}}],
            make_graph(), TODAY,
        )
        assert "Слухи" in result and "не подтверждено" in result

    def test_empty_news_returns_empty(self):
        assert build_digest([], make_graph(), TODAY) == ""

    @patch("functions.digest_generator.handler.generate_digest", return_value=None)
    def test_fallback_on_llm_failure(self, _):
        result = build_digest([{"item": make_news(), "analysis": {}}], make_graph(), TODAY)
        assert result

    @patch("functions.digest_generator.handler.generate_digest",
           return_value="🏒 РЕЗУЛЬТАТЫ\n• Лада победила 3:1")
    def test_llm_result_returned(self, _):
        result = build_digest([{"item": make_news(), "analysis": {}}], make_graph(), TODAY)
        assert "РЕЗУЛЬТАТЫ" in result
