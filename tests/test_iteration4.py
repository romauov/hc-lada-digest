"""
Тесты итерации 4: deferred news, versioning, monitoring.

Запуск: python -m pytest tests/test_iteration4.py -v
"""
import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from shared.models import Entity, KnowledgeGraph, NewsItem, Relation, DeferredNews
from shared.deferred import (
    push_deferred, pop_deferred, should_use_deferred,
    apply_deferred_to_pipeline, DEFERRED_TTL_DAYS, MIN_NEWS_THRESHOLD,
)
from shared.versioning import compute_diff, GraphDiff
from shared.monitoring import PipelineMetrics, PipelineRun

TODAY     = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
OLD_DATE  = (date.today() - timedelta(days=DEFERRED_TTL_DAYS + 1)).isoformat()


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_graph(version=1, extra_entities=None) -> KnowledgeGraph:
    entities = {
        "lada_hc": Entity(
            id="lada_hc", type="hockey_club", name="Лада",
            priority_score=1.0, search_queries=["ХК Лада"],
        ),
    }
    if extra_entities:
        entities.update(extra_entities)
    return KnowledgeGraph(
        root_id="lada_hc", created_at=TODAY, last_updated=TODAY,
        version=version, entities=entities, relations=[],
    )


def make_news(url="https://example.com/1", entity_id="lada_hc") -> NewsItem:
    return NewsItem(
        url=url, title="Тест", published_at=TODAY,
        source="sports_ru", entity_id=entity_id,
    )


def make_deferred(url="https://example.com/1", deferred_at=None) -> DeferredNews:
    return DeferredNews(
        url=url, title="Отложенная новость",
        entity_id="lada_hc",
        deferred_at=deferred_at or TODAY,
        reason="low_priority_day",
        source="sports_ru",
        published_at=TODAY,
    )


# ── Deferred: push ────────────────────────────────────────────────────────────

class TestPushDeferred:
    def test_push_adds_to_graph(self):
        graph = make_graph()
        news  = [make_news("https://a.com/1"), make_news("https://a.com/2")]
        graph = push_deferred(graph, news)
        assert len(graph.deferred_news) == 2

    def test_push_stores_correct_fields(self):
        graph = make_graph()
        news  = [make_news("https://a.com/1")]
        graph = push_deferred(graph, news, reason="quota_exceeded")
        d     = graph.deferred_news[0]
        assert d.url    == "https://a.com/1"
        assert d.reason == "quota_exceeded"
        assert d.entity_id == "lada_hc"

    def test_push_empty_list(self):
        graph = make_graph()
        graph = push_deferred(graph, [])
        assert len(graph.deferred_news) == 0


# ── Deferred: pop ─────────────────────────────────────────────────────────────

class TestPopDeferred:
    def test_pop_returns_requested_count(self):
        graph = make_graph()
        graph.deferred_news = [
            make_deferred(f"https://a.com/{i}") for i in range(5)
        ]
        graph, items = pop_deferred(graph, needed=3)
        assert len(items) == 3

    def test_pop_removes_used_from_graph(self):
        graph = make_graph()
        graph.deferred_news = [make_deferred(f"https://a.com/{i}") for i in range(5)]
        graph, items = pop_deferred(graph, needed=3)
        assert len(graph.deferred_news) == 2

    def test_pop_removes_expired(self):
        graph = make_graph()
        graph.deferred_news = [
            make_deferred("https://a.com/fresh",  deferred_at=TODAY),
            make_deferred("https://a.com/expired", deferred_at=OLD_DATE),
        ]
        graph, items = pop_deferred(graph, needed=10)
        # expired удалён, fresh использован
        assert all(i.url != "https://a.com/expired" for i in items)
        assert len(graph.deferred_news) == 0

    def test_pop_newest_first(self):
        graph = make_graph()
        graph.deferred_news = [
            make_deferred("https://a.com/old",  deferred_at=YESTERDAY),
            make_deferred("https://a.com/new",  deferred_at=TODAY),
        ]
        _, items = pop_deferred(graph, needed=1)
        assert items[0].url == "https://a.com/new"

    def test_pop_empty_deferred(self):
        graph = make_graph()
        graph, items = pop_deferred(graph, needed=5)
        assert items == []


# ── should_use_deferred ───────────────────────────────────────────────────────

class TestShouldUseDeferred:
    def test_below_threshold_returns_true(self):
        assert should_use_deferred(MIN_NEWS_THRESHOLD - 1)

    def test_at_threshold_returns_false(self):
        assert not should_use_deferred(MIN_NEWS_THRESHOLD)

    def test_above_threshold_returns_false(self):
        assert not should_use_deferred(MIN_NEWS_THRESHOLD + 5)


# ── apply_deferred_to_pipeline ────────────────────────────────────────────────

class TestApplyDeferredPipeline:
    def test_adds_deferred_when_few_fresh(self):
        graph = make_graph()
        deferred_item = make_deferred("https://deferred.com/1")
        graph.deferred_news = [deferred_item]

        # мало свежих новостей → добираем из deferred
        fresh = [make_news(f"https://fresh.com/{i}") for i in range(MIN_NEWS_THRESHOLD - 1)]
        graph, final = apply_deferred_to_pipeline(graph, fresh, max_total=20)
        assert len(final) > len(fresh)
        assert any(n.url == "https://deferred.com/1" for n in final)

    def test_defers_excess_when_too_many_fresh(self):
        graph = make_graph()
        fresh = [make_news(f"https://fresh.com/{i}") for i in range(10)]
        graph, final = apply_deferred_to_pipeline(graph, fresh, max_total=5)
        assert len(final) == 5
        assert len(graph.deferred_news) == 5

    def test_no_change_when_fresh_in_range(self):
        graph = make_graph()
        fresh = [make_news(f"https://fresh.com/{i}") for i in range(MIN_NEWS_THRESHOLD + 2)]
        graph, final = apply_deferred_to_pipeline(graph, fresh, max_total=20)
        assert final == fresh
        assert len(graph.deferred_news) == 0


# ── Versioning: compute_diff ──────────────────────────────────────────────────

class TestComputeDiff:
    def test_no_changes(self):
        g = make_graph()
        diff = compute_diff(g, g)
        assert diff.is_empty()

    def test_added_entity(self):
        before = make_graph()
        after  = make_graph(extra_entities={
            "new_player": Entity(
                id="new_player", type="player", name="Иван Новиков",
                priority_score=0.5, search_queries=[],
            )
        })
        diff = compute_diff(before, after)
        assert "Иван Новиков" in diff.added_entities
        assert not diff.is_empty()

    def test_removed_entity(self):
        before = make_graph(extra_entities={
            "old_coach": Entity(
                id="old_coach", type="coach", name="Старый Тренер",
                priority_score=0.8, search_queries=[],
            )
        })
        after = make_graph()
        diff  = compute_diff(before, after)
        assert "Старый Тренер" in diff.removed_entities

    def test_changed_entity(self):
        before = make_graph()
        after  = make_graph()
        after.entities["lada_hc"].priority_score = 1.4   # изменился score
        diff   = compute_diff(before, after)
        assert any(c["entity_id"] == "lada_hc" for c in diff.changed_entities)

    def test_added_relation(self):
        before = make_graph()
        after  = make_graph()
        after.relations.append(
            Relation(from_id="lada_hc", to_id="lada_hc", type="SELF_REF", confidence=1.0)
        )
        diff = compute_diff(before, after)
        assert len(diff.added_relations) == 1

    def test_diff_summary_readable(self):
        before = make_graph()
        after  = make_graph(version=2, extra_entities={
            "new_e": Entity(id="new_e", type="player", name="Новый", priority_score=0.5, search_queries=[])
        })
        after.version = 2
        diff    = compute_diff(before, after)
        summary = diff.summary()
        assert "v1" in summary and "v2" in summary
        assert "Новый" in summary

    def test_tg_message_format(self):
        before = make_graph()
        after  = make_graph(extra_entities={
            "p1": Entity(id="p1", type="player", name="Игрок А", priority_score=0.5, search_queries=[]),
        })
        after.version = 2
        diff = compute_diff(before, after)
        msg  = diff.to_tg_message()
        assert "<b>" in msg
        assert "Игрок А" in msg


# ── Monitoring: PipelineMetrics ───────────────────────────────────────────────

class TestPipelineMetrics:
    def test_initial_status_ok(self):
        m = PipelineMetrics()
        assert m.status == "ok"

    def test_add_error_sets_error_status(self):
        m = PipelineMetrics()
        m.add_error("something broke")
        assert m.status == "error"
        assert "something broke" in m.errors

    def test_add_warning_sets_warn_status(self):
        m = PipelineMetrics()
        m.add_warning("something slow")
        assert m.status == "warn"
        assert "something slow" in m.warnings

    def test_error_overrides_warn(self):
        m = PipelineMetrics()
        m.add_warning("minor")
        m.add_error("critical")
        assert m.status == "error"

    def test_new_entities_count(self):
        m = PipelineMetrics(entities_before=5, entities_after=8)
        assert m.new_entities_count() == 3

    def test_new_entities_count_no_change(self):
        m = PipelineMetrics(entities_before=5, entities_after=5)
        assert m.new_entities_count() == 0

    def test_tg_report_ok(self):
        m = PipelineMetrics(
            status="ok", news_total=12, news_fresh=10,
            entities_before=4, entities_after=6,
            graph_version=5, digest_length=800,
            started_at="2026-05-13T08:00:00.000000+00:00",
            finished_at="2026-05-13T08:00:45.000000+00:00",
        )
        report = m.to_tg_report()
        assert "✅" in report
        assert "12" in report    # news_total
        assert "45.0с" in report  # duration

    def test_tg_report_error(self):
        m = PipelineMetrics()
        m.add_error("graph not found")
        report = m.to_tg_report()
        assert "❌" in report
        assert "graph not found" in report

    def test_tg_report_shows_deferred(self):
        m = PipelineMetrics(news_total=5, news_fresh=2, news_deferred_in=3)
        report = m.to_tg_report()
        assert "отложенных" in report


# ── PipelineRun context manager ───────────────────────────────────────────────

class TestPipelineRun:
    def test_sets_timestamps(self):
        m = PipelineMetrics()
        with PipelineRun(m):
            pass
        assert m.started_at
        assert m.finished_at

    def test_captures_exception(self):
        m = PipelineMetrics()
        try:
            with PipelineRun(m):
                raise ValueError("boom")
        except ValueError:
            pass
        assert m.status == "error"
        assert "ValueError" in m.errors[0]

    def test_does_not_suppress_exception(self):
        m = PipelineMetrics()
        with pytest.raises(RuntimeError):
            with PipelineRun(m):
                raise RuntimeError("propagate me")

    def test_duration_calculated(self):
        import time
        m = PipelineMetrics()
        with PipelineRun(m):
            time.sleep(0.05)
        assert m.duration_seconds >= 0.04
