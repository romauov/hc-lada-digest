"""
Тесты ручного подтверждения изменений графа через Telegram.

Запуск: python -m pytest tests/test_approval.py -v
"""
import os
from datetime import date
from unittest.mock import patch

os.environ.setdefault("DATA_DIR", "/tmp/test_approval_data")
os.environ.setdefault("TG_BOT_TOKEN", "test-token")
os.environ.setdefault("TG_CHAT_ID", "123")
os.environ.setdefault("TG_ADMIN_ID", "123")
import shutil

from shared.models import Entity, KnowledgeGraph, Relation
from shared.approval import (
    PENDING_DIR, STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED,
    ChangeItem, itemize_changes, build_approved_graph,
    publish_proposal, poll_decision, record_decision, get_proposal,
    request_all_approvals,
)

TODAY = date.today().isoformat()


def make_graph(version=1, extra_entities=None, relations=None) -> KnowledgeGraph:
    entities = {
        "lada_hc": Entity(
            id="lada_hc", type="hockey_club", name="Лада",
            full_name="ХК Лада Тольятти", priority_score=1.0,
            search_queries=["ХК Лада"],
        ),
    }
    if extra_entities:
        entities.update(extra_entities)
    return KnowledgeGraph(
        root_id="lada_hc", created_at=TODAY, last_updated=TODAY,
        version=version, entities=entities,
        relations=list(relations or []),
    )


def make_player(eid="player_иван", name="Иван Петров", **kw) -> Entity:
    return Entity(
        id=eid, type="player", name=name,
        priority_score=0.5, search_queries=[],
        **kw,
    )


def setup_function(_):
    shutil.rmtree(PENDING_DIR, ignore_errors=True)


# ── itemize_changes ───────────────────────────────────────────────────────────

class TestItemizeChanges:
    def test_empty_diff_returns_no_items(self):
        g = make_graph()
        assert itemize_changes(g, g) == []

    def test_new_entity_produces_add_entity_item(self):
        before = make_graph()
        after  = make_graph(extra_entities={"player_иван": make_player()})
        after.relations.append(Relation(
            from_id="lada_hc", to_id="player_иван", type="HAS_PLAYER", confidence=0.9,
        ))
        items = itemize_changes(before, after)
        kinds = [i.kind for i in items]
        assert "add_entity" in kinds
        assert not any(i.kind == "add_relation" for i in items)  # связь учтена в add_entity

    def test_add_entity_payload_contains_entity_and_relation(self):
        before = make_graph()
        after  = make_graph(extra_entities={"player_иван": make_player()})
        after.relations.append(Relation(
            from_id="lada_hc", to_id="player_иван", type="HAS_PLAYER", confidence=0.9,
        ))
        item = next(i for i in itemize_changes(before, after) if i.kind == "add_entity")
        assert item.payload["entity"]["name"] == "Иван Петров"
        assert len(item.payload["relations"]) == 1
        assert "Иван Петров" in item.preview

    def test_new_relation_between_existing_entities(self):
        before = make_graph(extra_entities={"player_иван": make_player()})
        after  = make_graph(extra_entities={"player_иван": make_player()})
        after.relations.append(Relation(
            from_id="lada_hc", to_id="player_иван", type="HAS_PLAYER", confidence=0.9,
        ))
        items = itemize_changes(before, after)
        assert any(i.kind == "add_relation" for i in items)

    def test_removed_relation(self):
        rel = Relation(from_id="lada_hc", to_id="player_иван", type="HAS_PLAYER", confidence=0.9)
        before = make_graph(extra_entities={"player_иван": make_player()}, relations=[rel])
        after  = make_graph(extra_entities={"player_иван": make_player()})
        items = itemize_changes(before, after)
        assert any(i.kind == "remove_relation" for i in items)

    def test_changed_relation_produces_change_relation(self):
        rel_b = Relation(from_id="lada_hc", to_id="player_иван", type="HAS_PLAYER", confidence=0.9, is_rumour=True)
        rel_a = Relation(from_id="lada_hc", to_id="player_иван", type="HAS_PLAYER", confidence=1.0, is_rumour=False)
        before = make_graph(extra_entities={"player_иван": make_player()}, relations=[rel_b])
        after  = make_graph(extra_entities={"player_иван": make_player()}, relations=[rel_a])
        items = itemize_changes(before, after)
        assert any(i.kind == "change_relation" for i in items)

    def test_priority_change_does_not_produce_item(self):
        before = make_graph(extra_entities={"player_иван": make_player()})
        after  = make_graph(extra_entities={"player_иван": make_player()})
        after.entities["player_иван"].priority_score = 0.9
        after.entities["player_иван"].mention_streak = 2
        assert itemize_changes(before, after) == []

    def test_removed_entity(self):
        before = make_graph(extra_entities={"player_иван": make_player()})
        after  = make_graph()
        items = itemize_changes(before, after)
        assert any(i.kind == "remove_entity" for i in items)

    def test_changed_entity_name(self):
        before = make_graph(extra_entities={"player_иван": make_player()})
        after  = make_graph(extra_entities={"player_иван": make_player(name="Иван Петров II")})
        items = itemize_changes(before, after)
        assert any(i.kind == "update_entity" for i in items)


# ── build_approved_graph ──────────────────────────────────────────────────────

class TestBuildApprovedGraph:
    def test_approved_only_applies(self):
        before = make_graph()
        after  = make_graph(extra_entities={"player_иван": make_player()})
        after.version = 2
        items = itemize_changes(before, after)
        assert len(items) == 1
        decisions = {items[0].id: STATUS_APPROVED}
        result = build_approved_graph(before, items, decisions)
        assert "player_иван" in result.entities

    def test_rejected_is_skipped(self):
        before = make_graph()
        after  = make_graph(extra_entities={"player_иван": make_player()})
        after.version = 2
        items = itemize_changes(before, after)
        decisions = {items[0].id: STATUS_REJECTED}
        result = build_approved_graph(before, items, decisions)
        assert "player_иван" not in result.entities

    def test_mixed_decisions(self):
        before = make_graph(extra_entities={"player_иван": make_player()})
        before.relations.append(Relation(
            from_id="lada_hc", to_id="player_иван", type="HAS_PLAYER", confidence=0.9,
        ))
        after = make_graph(extra_entities={
            "player_иван": make_player(),
            "player_петр": make_player(eid="player_петр", name="Пётр Сидоров"),
        })
        after.relations.append(Relation(
            from_id="lada_hc", to_id="player_иван", type="HAS_PLAYER", confidence=0.9,
        ))
        after.relations.append(Relation(
            from_id="lada_hc", to_id="player_петр", type="HAS_PLAYER", confidence=0.8,
        ))
        after.version = 2
        items = itemize_changes(before, after)
        assert len(items) == 1
        result = build_approved_graph(before, items, {items[0].id: STATUS_APPROVED})
        assert "player_петр" in result.entities
        assert "player_иван" in result.entities

    def test_rejected_relation_change(self):
        rel_b = Relation(from_id="lada_hc", to_id="player_иван", type="HAS_PLAYER", confidence=0.9, is_rumour=True)
        rel_a = Relation(from_id="lada_hc", to_id="player_иван", type="HAS_PLAYER", confidence=1.0, is_rumour=False)
        before = make_graph(extra_entities={"player_иван": make_player()}, relations=[rel_b])
        after  = make_graph(extra_entities={"player_иван": make_player()}, relations=[rel_a])
        after.version = 2
        items = itemize_changes(before, after)
        assert len(items) == 1
        result = build_approved_graph(before, items, {items[0].id: STATUS_REJECTED})
        r = result.relations[0]
        assert r.confidence == 0.9 and r.is_rumour is True


# ── approval store / polling ──────────────────────────────────────────────────

class TestApprovalStore:
    def test_publish_writes_pending_file(self):
        with patch("shared.approval.send_message", return_value=True):
            item = ChangeItem(kind="add_entity", preview="+ Тест", payload={})
            pid = publish_proposal("123", item)
            data = get_proposal(pid)
            assert data is not None
            assert data["status"] == "pending"

    def test_publish_sends_message_with_buttons(self):
        with patch("shared.approval.send_message", return_value=True) as m:
            item = ChangeItem(kind="add_entity", preview="+ Тест", payload={})
            publish_proposal("123", item)
            kwargs = m.call_args.kwargs
            assert kwargs["reply_markup"]["inline_keyboard"]
            texts = [b["text"] for row in kwargs["reply_markup"]["inline_keyboard"] for b in row]
            assert any("✅" in t for t in texts)
            assert any("❌" in t for t in texts)

    def test_publish_send_failure_rejects(self):
        with patch("shared.approval.send_message", return_value=False):
            item = ChangeItem(kind="add_entity", preview="+ Тест", payload={})
            pid = publish_proposal("123", item)
            assert get_proposal(pid)["status"] == "rejected"

    def test_publish_no_admin_rejects(self):
        with patch("shared.approval.send_message", return_value=True):
            item = ChangeItem(kind="add_entity", preview="+ Тест", payload={})
            pid = publish_proposal("", item)
            assert get_proposal(pid)["status"] == "rejected"

    def test_record_decision_pending_only(self):
        item = ChangeItem(kind="add_entity", preview="+ Тест", payload={})
        with patch("shared.approval.send_message", return_value=True):
            pid = publish_proposal("123", item)
        assert record_decision(pid, STATUS_APPROVED) is True
        assert get_proposal(pid)["status"] == "approved"
        # второй раз — не перезаписываем
        assert record_decision(pid, STATUS_REJECTED) is False

    def test_record_decision_invalid_payload(self):
        assert record_decision("missing_id", STATUS_APPROVED) is False

    def test_poll_returns_approved(self):
        item = ChangeItem(kind="add_entity", preview="+ Тест", payload={})
        with patch("shared.approval.send_message", return_value=True):
            pid = publish_proposal("123", item)
        record_decision(pid, STATUS_APPROVED)
        assert poll_decision(pid, timeout=0.5) == STATUS_APPROVED

    def test_poll_times_out_to_expired(self):
        with patch("shared.approval.send_message", return_value=True):
            item = ChangeItem(kind="add_entity", preview="+ Тест", payload={})
            pid = publish_proposal("123", item)
        with patch("shared.approval.time.sleep", return_value=None):
            assert poll_decision(pid, timeout=0.1) == STATUS_EXPIRED

    def test_request_all_publications_and_waits(self):
        items = [
            ChangeItem(kind="add_entity", preview="+ A", payload={}),
            ChangeItem(kind="remove_entity", preview="- B", payload={}),
        ]
        expected = {items[0].id: STATUS_APPROVED, items[1].id: STATUS_REJECTED}
        with patch("shared.approval.send_message", return_value=True) as send_m, \
             patch("shared.approval.poll_decision", side_effect=lambda pid, t: expected[pid]):
            decisions = request_all_approvals("123", items, timeout=0.5)
        # каждое изменение опубликовано отдельным сообщением
        assert send_m.call_count == 2
        assert decisions == expected


# ── бот: обработка callback_query ────────────────────────────────────────────

class TestBotCallback:
    def test_callback_approve_records_and_replies(self):
        from unittest.mock import patch as p
        with p("shared.approval.send_message", return_value=True):
            item = ChangeItem(kind="add_entity", preview="+ Тест", payload={})
            pid = publish_proposal("123", item)

        from functions.tg_sender.bot import _handle_callback
        cb = {
            "id": "cb123",
            "from": {"id": 123},
            "message": {"message_id": 10, "chat": {"id": 123}},
            "data": f"approve:{pid}",
        }
        with p("functions.tg_sender.bot.answer_callback_query",
               return_value=True) as ans, \
             p("functions.tg_sender.bot.edit_message_text",
               return_value=True) as edit:
            _handle_callback(cb, "123")
            assert ans.call_count == 1
            assert edit.call_count == 1
        assert get_proposal(pid)["status"] == "approved"

    def test_callback_ignore_non_admin(self):
        from unittest.mock import patch as p
        with p("shared.approval.send_message", return_value=True):
            item = ChangeItem(kind="add_entity", preview="+ Тест", payload={})
            pid = publish_proposal("123", item)

        from functions.tg_sender.bot import _handle_callback
        cb = {"id": "cb999", "from": {"id": 999}, "message": {}, "data": f"reject:{pid}"}
        with p("functions.tg_sender.bot.answer_callback_query") as ans, \
             p("functions.tg_sender.bot.edit_message_text") as edit:
            _handle_callback(cb, "999")
            assert ans.call_count == 0
            assert edit.call_count == 0
        assert get_proposal(pid)["status"] == "pending"