"""
Тесты для итерации 1.
Запуск: python -m pytest tests/ -v
"""
import pytest
from datetime import date, timedelta

from shared.models import Entity, KnowledgeGraph, Relation, DeferredNews
from shared.priority import compute_priority, mark_mentioned, mark_not_mentioned, update_priorities


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_entity(type_="hockey_club", last_mentioned=None, streak=0, id_="test_entity") -> Entity:
    return Entity(
        id=id_,
        type=type_,
        name="Test",
        last_mentioned=last_mentioned,
        mention_streak=streak,
        search_queries=["test query"],
    )


# ── Тесты моделей ─────────────────────────────────────────────────────────────

class TestModels:
    def test_entity_serialization(self):
        e = make_entity()
        d = e.to_dict()
        e2 = Entity.from_dict(d)
        assert e.id == e2.id
        assert e.type == e2.type

    def test_relation_serialization(self):
        r = Relation(from_id="a", to_id="b", type="HAS_COACH", confidence=0.9)
        d = r.to_dict()
        r2 = Relation.from_dict(d)
        assert r.from_id == r2.from_id
        assert r.confidence == r2.confidence

    def test_graph_roundtrip(self):
        today = date.today().isoformat()
        graph = KnowledgeGraph(
            root_id="root",
            created_at=today,
            last_updated=today,
            version=1,
            entities={"root": make_entity()},
            relations=[Relation(from_id="root", to_id="root", type="SELF")],
        )
        d  = graph.to_dict()
        g2 = KnowledgeGraph.from_dict(d)
        assert g2.root_id == "root"
        assert g2.version == 1
        assert "root" in g2.entities


# ── Тесты приоритизации ───────────────────────────────────────────────────────

class TestPriority:
    def test_coach_higher_than_arena(self):
        coach = make_entity(type_="coach", last_mentioned=date.today().isoformat())
        arena = make_entity(type_="arena", last_mentioned=date.today().isoformat())
        assert compute_priority(coach) > compute_priority(arena)

    def test_recent_mention_boosts_score(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        month_ago = (date.today() - timedelta(days=31)).isoformat()
        e_recent = make_entity(last_mentioned=yesterday)
        e_old    = make_entity(last_mentioned=month_ago)
        assert compute_priority(e_recent) > compute_priority(e_old)

    def test_streak_boosts_score(self):
        today = date.today().isoformat()
        e_no_streak  = make_entity(type_="player", last_mentioned=today, streak=0)
        e_has_streak = make_entity(type_="player", last_mentioned=today, streak=3)
        assert compute_priority(e_has_streak) > compute_priority(e_no_streak)

    def test_streak_capped(self):
        today = date.today().isoformat()
        e10 = make_entity(type_="player", last_mentioned=today, streak=10)
        e50 = make_entity(type_="player", last_mentioned=today, streak=50)
        # разница должна быть минимальной — бонус ограничен
        assert abs(compute_priority(e10) - compute_priority(e50)) < 0.01

    def test_root_always_max(self):
        root = make_entity(type_="hockey_club", id_="lada_hc")
        assert compute_priority(root) == 1.5

    def test_hockey_club_type_capped(self):
        today = date.today().isoformat()
        club = make_entity(type_="hockey_club", last_mentioned=today, streak=29)
        assert compute_priority(club) <= 0.6

    def test_hockey_club_streak_ignored(self):
        today = date.today().isoformat()
        e0 = make_entity(type_="hockey_club", last_mentioned=today, streak=0)
        e5 = make_entity(type_="hockey_club", last_mentioned=today, streak=5)
        assert compute_priority(e0) == compute_priority(e5)

    def test_other_type_capped(self):
        today = date.today().isoformat()
        e = make_entity(type_="other", last_mentioned=today, streak=10)
        assert compute_priority(e) <= 0.3

    def test_coach_scoring_unchanged(self):
        today = date.today().isoformat()
        coach = make_entity(type_="coach", last_mentioned=today)
        assert compute_priority(coach) == 0.96

    def test_mark_mentioned_increments_streak(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        e = make_entity(last_mentioned=yesterday, streak=2)
        e = mark_mentioned(e)
        assert e.mention_streak == 3
        assert e.last_mentioned == date.today().isoformat()

    def test_mark_mentioned_resets_streak_on_gap(self):
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        e = make_entity(last_mentioned=week_ago, streak=5)
        e = mark_mentioned(e)
        assert e.mention_streak == 1

    def test_mark_mentioned_idempotent_same_day(self):
        today = date.today().isoformat()
        e = make_entity(last_mentioned=today, streak=3)
        e = mark_mentioned(e)
        assert e.mention_streak == 3  # не изменился

    def test_mark_not_mentioned_resets_streak(self):
        e = make_entity(streak=5)
        e = mark_not_mentioned(e)
        assert e.mention_streak == 0

    def test_update_priorities_all_entities(self):
        entities = {
            "club":  make_entity(type_="hockey_club"),
            "arena": make_entity(type_="arena"),
        }
        updated = update_priorities(entities)
        for e in updated.values():
            assert e.priority_score > 0

    def test_get_entities_by_priority(self):
        today = date.today().isoformat()
        graph = KnowledgeGraph(
            root_id="team",
            created_at=today,
            last_updated=today,
            version=1,
            entities={
                "coach": make_entity(type_="coach", last_mentioned=today),
                "arena": make_entity(type_="arena", last_mentioned=today),
            },
            relations=[],
        )
        sorted_entities = graph.get_entities_by_priority()
        assert sorted_entities[0].type == "coach"
