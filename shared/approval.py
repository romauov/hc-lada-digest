"""
Ручное подтверждение изменений графа через Telegram.

Каждое отдельное изменение графа (новая сущность, новая/изменённая/удалённая
связь, обновление сущности) превращается в отдельный ChangeItem и отдельный
запрос на подтверждение с inline-кнопками ✅/❌.

Состояние запроса хранится в файле {DATA_DIR}/pending/approval_<id>.json,
который пишет пайплайн и обновляет бот при нажатии кнопки.
"""
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from shared.models import Entity, KnowledgeGraph, Relation
from shared.tg_send import send_message

logger = logging.getLogger(__name__)

DATA_DIR    = os.environ.get("DATA_DIR", "/data")
PENDING_DIR = os.path.join(DATA_DIR, "pending")

STATUS_PENDING  = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED  = "expired"

POLL_INTERVAL = 2.0

APPROVE_TEXT = "✅ Подтвердить"
REJECT_TEXT  = "❌ Отклонить"


def get_admin_id() -> str:
    return os.environ.get("TG_ADMIN_ID") or os.environ.get("TG_CHAT_ID", "")


def _bg_token() -> str:
    return os.environ.get("TG_BOT_TOKEN", "")


# ── ChangeItem ────────────────────────────────────────────────────────────────

CONTENT_FIELDS_ENTITY  = ("name", "type", "full_name", "is_rumour")
CONTENT_FIELDS_REL     = ("confidence", "is_rumour", "until")


@dataclass
class ChangeItem:
    kind: str                 # add_entity, remove_entity, update_entity, add_relation, remove_relation, change_relation
    preview: str
    payload: dict
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "preview": self.preview, "payload": self.payload}


def _rel_key(r: Relation) -> tuple:
    return (r.from_id, r.to_id, r.type)


def _name_lookup(before: KnowledgeGraph, after: KnowledgeGraph) -> dict[str, str]:
    names: dict[str, str] = {}
    for g in (before, after):
        for eid, ent in g.entities.items():
            names.setdefault(eid, ent.name)
    return names


# ── itemize_changes ───────────────────────────────────────────────────────────

def itemize_changes(before: KnowledgeGraph, after: KnowledgeGraph) -> list[ChangeItem]:
    """
    Превращает разницу between/after в список отдельных изменений графа.

    Учитываются только содержательные изменения (сущности, связи, слухи):
    приоритеты/метки упоминаний НЕ порождают запросы на подтверждение.
    """
    names   = _name_lookup(before, after)
    b_ids   = set(before.entities); a_ids = set(after.entities)
    b_rels  = {_rel_key(r): r for r in before.relations}
    a_rels  = {_rel_key(r): r for r in after.relations}
    items: list[ChangeItem] = []

    # ── новые сущности (+ их новые связи) ──
    for eid in a_ids - b_ids:
        ent = after.entities[eid]
        rels = [r for r in after.relations
                if (r.from_id == eid or r.to_id == eid) and _rel_key(r) not in b_rels]
        lines = [f"➕ Сущность «{ent.name}» (тип: {ent.type})"]
        for r in rels:
            lines.append(f"   → {_rel_name(g=after, names=names, r=r)}")
        items.append(ChangeItem(
            kind="add_entity",
            preview="\n".join(lines),
            payload={"entity": ent.to_dict(), "relations": [r.to_dict() for r in rels]},
        ))

    # ── удалённые сущности ──
    for eid in b_ids - a_ids:
        ent = before.entities[eid]
        items.append(ChangeItem(
            kind="remove_entity",
            preview=f"➖ Удалить сущность «{ent.name}» ({ent.type})",
            payload={"entity_id": eid},
        ))

    # ── изменённые сущности (только содержательные поля) ──
    for eid in b_ids & a_ids:
        b, a = before.entities[eid], after.entities[eid]
        diffs = {f: {"before": getattr(b, f), "after": getattr(a, f)}
                 for f in CONTENT_FIELDS_ENTITY if getattr(b, f) != getattr(a, f)}
        if not diffs:
            continue
        lines = [f"✏️ Обновить сущность «{a.name}»:"]
        for f, d in diffs.items():
            lines.append(f"   {f}: {d['before']} → {d['after']}")
        items.append(ChangeItem(
            kind="update_entity",
            preview="\n".join(lines),
            payload={"entity_id": eid, "diffs": diffs},
        ))

    # ── новые связи (вне новых сущностей) ──
    for key in a_rels:
        if key in b_rels:
            continue
        r = a_rels[key]
        if key[0] in (a_ids - b_ids) or key[1] in (a_ids - b_ids):
            continue  # уже учтена в add_entity
        items.append(ChangeItem(
            kind="add_relation",
            preview=f"➕ Новая связь: {_rel_name(after, names, r)}",
            payload={"relation": r.to_dict()},
        ))

    # ── удалённые связи ──
    for key in b_rels:
        if key in a_rels:
            continue
        r = b_rels[key]
        items.append(ChangeItem(
            kind="remove_relation",
            preview=f"➖ Удалить связь: {_rel_name(before, names, r)}",
            payload={"relation": r.to_dict()},
        ))

    # ── изменённые связи ──
    for key in b_rels:
        if key not in a_rels:
            continue
        b, a = b_rels[key], a_rels[key]
        diffs = {f: {"before": getattr(b, f), "after": getattr(a, f)}
                 for f in CONTENT_FIELDS_REL if getattr(b, f) != getattr(a, f)}
        if not diffs:
            continue
        lines = [f"🔁 Изменить связь: {_rel_name(after, names, a)}"]
        for f, d in diffs.items():
            lines.append(f"   {f}: {d['before']} → {d['after']}")
        items.append(ChangeItem(
            kind="change_relation",
            preview="\n".join(lines),
            payload={"from_id": key[0], "to_id": key[1], "type": key[2], "diffs": diffs},
        ))

    return items


def _rel_name(g: KnowledgeGraph, names: dict[str, str], r: Relation) -> str:
    frm = names.get(r.from_id, r.from_id)
    to  = names.get(r.to_id, r.to_id)
    return f"{frm} —[{r.type}]→ {to}"


# ── build_approved_graph ──────────────────────────────────────────────────────

def build_approved_graph(
    graph_before: KnowledgeGraph,
    items: list[ChangeItem],
    decisions: dict[str, str],
) -> KnowledgeGraph:
    """
    Строит граф из graph_before, применяя только одобренные изменения.
    """
    result = KnowledgeGraph.from_dict(graph_before.to_dict())

    for item in items:
        if decisions.get(item.id) != STATUS_APPROVED:
            continue
        try:
            _apply_item(result, item)
        except Exception as e:
            logger.error("Failed to apply approved item %s (%s): %s", item.id, item.kind, e)
    return result


def _apply_item(graph: KnowledgeGraph, item: ChangeItem) -> None:
    p = item.payload

    if item.kind == "add_entity":
        if p["entity"]["id"] in graph.entities:
            return
        graph.entities[p["entity"]["id"]] = Entity.from_dict(p["entity"])
        existing = {_rel_key(r) for r in graph.relations}
        for rd in p["relations"]:
            r = Relation.from_dict(rd)
            if _rel_key(r) not in existing:
                graph.relations.append(r)
                existing.add(_rel_key(r))

    elif item.kind == "update_entity":
        ent = graph.entities.get(p["entity_id"])
        if ent is None:
            return
        for f, d in p["diffs"].items():
            setattr(ent, f, d["after"])

    elif item.kind == "remove_entity":
        eid = p["entity_id"]
        graph.entities.pop(eid, None)
        graph.relations = [r for r in graph.relations
                           if r.from_id != eid and r.to_id != eid]

    elif item.kind in ("add_relation", "remove_relation"):
        r = Relation.from_dict(p["relation"])
        existing = {_rel_key(x) for x in graph.relations}
        if item.kind == "add_relation" and _rel_key(r) not in existing:
            graph.relations.append(r)
        elif item.kind == "remove_relation":
            graph.relations = [x for x in graph.relations if _rel_key(x) != _rel_key(r)]

    elif item.kind == "change_relation":
        key = (p["from_id"], p["to_id"], p["type"])
        for r in graph.relations:
            if _rel_key(r) == key:
                for f, d in p["diffs"].items():
                    setattr(r, f, d["after"])
                break


# ── Ручное подтверждение через Telegram ──────────────────────────────────────

def _proposal_path(proposal_id: str) -> str:
    return os.path.join(PENDING_DIR, f"approval_{proposal_id}.json")


def get_proposal(proposal_id: str) -> dict | None:
    path = _proposal_path(proposal_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def record_decision(proposal_id: str, decision: str) -> bool:
    """Записывает решение бота в файл предложения. Возвращает True если записано."""
    if decision not in (STATUS_APPROVED, STATUS_REJECTED):
        return False
    data = get_proposal(proposal_id)
    if data is None or data.get("status") != STATUS_PENDING:
        return False
    data["status"] = decision
    data["decided_at"] = datetime.now().isoformat()
    try:
        with open(_proposal_path(proposal_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error("Failed to record decision for %s: %s", proposal_id, e)
        return False


def publish_proposal(admin_id: str, item: ChangeItem) -> str:
    """Публикует запрос: файл + сообщение с кнопками. Возвращает proposal_id."""
    os.makedirs(PENDING_DIR, exist_ok=True)
    data = {
        "id": item.id,
        "status": STATUS_PENDING,
        "created_at": datetime.now().isoformat(),
        "kind": item.kind,
        "preview": item.preview,
        "payload": item.payload,
    }
    with open(_proposal_path(item.id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    if not admin_id:
        logger.error("Approval requested but TG_ADMIN_ID/TG_CHAT_ID not set — rejecting")
        record_decision(item.id, STATUS_REJECTED)
        return item.id

    reply_markup = _remind_markup(item)
    header = "🛡 <b>Подтверждение изменения графа</b>\n\n"
    ok = send_message(_bg_token(), admin_id, header + item.preview, reply_markup=reply_markup)
    if not ok:
        logger.error("Failed to send approval message for %s — rejecting", item.id)
        record_decision(item.id, STATUS_REJECTED)
    return item.id


def poll_decision(proposal_id: str, timeout: float) -> str:
    """Ждёт решения по proposal_id до timeout. Возвращает approved/rejected/expired."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = get_proposal(proposal_id)
        if data:
            status = data.get("status")
            if status in (STATUS_APPROVED, STATUS_REJECTED):
                return status
        time.sleep(POLL_INTERVAL)
    data = get_proposal(proposal_id)
    if data and data.get("status") in (STATUS_APPROVED, STATUS_REJECTED):
        return data["status"]
    return STATUS_EXPIRED


def _deadline_ts(end_hour: int, min_wait: float = 60.0) -> float:
    """Timestamp дедлайна — сегодня в end_hour:00. Если уже позже — через min_wait."""
    now = time.time()
    deadline = datetime.now().replace(hour=end_hour, minute=0, second=0, microsecond=0).timestamp()
    if deadline <= now:
        deadline = now + min_wait
    return deadline


def _remind_markup(item: ChangeItem) -> dict:
    return {
        "inline_keyboard": [[
            {"text": APPROVE_TEXT, "callback_data": f"approve:{item.id}"},
            {"text": REJECT_TEXT,  "callback_data": f"reject:{item.id}"},
        ]]
    }


def remind_proposal(admin_id: str, item: ChangeItem) -> None:
    """Отправляет новое напоминание по нерешённому изменению (файл не перезаписывает)."""
    if not admin_id:
        return
    header = "🛡 <b>Подтверждение изменения графа</b>\n⏰ <i>Напоминание — ждём вашего решения</i>\n\n"
    send_message(_bg_token(), admin_id, header + item.preview, reply_markup=_remind_markup(item))


def request_all_approvals(
    admin_id: str,
    items: list[ChangeItem],
    end_hour: int = 18,
    remind_interval: float = 3600.0,
) -> dict[str, str]:
    """
    Публикует ВСЕ запросы сразу, затем до дедлайна (end_hour:00) раз в remind_interval
    пересылает напоминания по нерешённым изменениям. По дедлайну нерешённые = expired.
    """
    published = [(item, publish_proposal(admin_id, item)) for item in items]
    deadline = _deadline_ts(end_hour)
    next_remind = time.time() + remind_interval
    decisions: dict[str, str] = {item.id: STATUS_PENDING for item, _ in published}

    while time.time() < deadline:
        for item, pid in published:
            if decisions[item.id] != STATUS_PENDING:
                continue
            data = get_proposal(pid)
            if data and data.get("status") in (STATUS_APPROVED, STATUS_REJECTED):
                decisions[item.id] = data["status"]
                logger.info("Approval %s [%s]: %s", pid, item.kind, decisions[item.id])
        if all(s != STATUS_PENDING for s in decisions.values()):
            break
        if time.time() >= next_remind:
            for item, _ in published:
                if decisions[item.id] == STATUS_PENDING:
                    remind_proposal(admin_id, item)
                    logger.info("Reminder sent for %s [%s]", item.id, item.kind)
            next_remind = time.time() + remind_interval
        time.sleep(POLL_INTERVAL)

    for item, pid in published:
        if decisions[item.id] == STATUS_PENDING:
            decisions[item.id] = STATUS_EXPIRED
            logger.info("Approval %s [%s]: %s (deadline)", pid, item.kind, STATUS_EXPIRED)
    return decisions