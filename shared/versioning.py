"""Снапшоты и diff графа на локальной файловой системе."""
import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

from shared.models import KnowledgeGraph

logger = logging.getLogger(__name__)

DATA_DIR     = os.environ.get("DATA_DIR", "/data")
SNAPSHOT_DIR = os.path.join(DATA_DIR, "snapshots")


def _ensure_dirs() -> None:
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)


def save_snapshot(graph: KnowledgeGraph, label: Optional[str] = None) -> str:
    _ensure_dirs()
    today = date.today().isoformat()
    key   = os.path.join(SNAPSHOT_DIR, f"{today}_{label or f'v{graph.version}'}.json")
    try:
        data = json.dumps(graph.to_dict(), ensure_ascii=False, indent=2)
        with open(key, "w", encoding="utf-8") as f:
            f.write(data)
        logger.info("Snapshot saved: %s", key)
    except Exception as e:
        logger.warning("Snapshot save failed: %s", e)
    return key


def load_snapshot(key: str) -> Optional[KnowledgeGraph]:
    try:
        with open(key, encoding="utf-8") as f:
            return KnowledgeGraph.from_dict(json.load(f))
    except Exception:
        return None


def list_snapshots(limit: int = 30) -> list[dict]:
    _ensure_dirs()
    try:
        files = sorted(
            os.listdir(SNAPSHOT_DIR),
            key=lambda f: os.path.getmtime(os.path.join(SNAPSHOT_DIR, f)),
            reverse=True,
        )[:limit]
        return [
            {"key": f, "last_modified": os.path.getmtime(os.path.join(SNAPSHOT_DIR, f))}
            for f in files
        ]
    except Exception:
        return []


@dataclass
class GraphDiff:
    added_entities:    list
    removed_entities:  list
    changed_entities:  list
    added_relations:   list
    removed_relations: list
    version_from:      int
    version_to:        int

    def is_empty(self):
        return not any([self.added_entities, self.removed_entities,
                        self.changed_entities, self.added_relations, self.removed_relations])

    def summary(self):
        parts = []
        if self.added_entities:
            names = ", ".join(self.added_entities[:3]) + ("..." if len(self.added_entities)>3 else "")
            parts.append(f"+{len(self.added_entities)} сущностей ({names})")
        if self.removed_entities: parts.append(f"-{len(self.removed_entities)} сущностей")
        if self.changed_entities:  parts.append(f"~{len(self.changed_entities)} изменений")
        if self.added_relations:   parts.append(f"+{len(self.added_relations)} связей")
        if self.removed_relations: parts.append(f"-{len(self.removed_relations)} связей")
        return f"v{self.version_from}→v{self.version_to}: " + (", ".join(parts) if parts else "нет изменений")

    def to_tg_message(self):
        if self.is_empty(): return ""
        lines = [f"📊 <b>Граф обновлён</b> (v{self.version_from}→v{self.version_to})\n"]
        if self.added_entities:
            lines.append("<b>Новые сущности:</b>")
            for name in self.added_entities[:5]: lines.append(f"  + {name}")
            if len(self.added_entities)>5: lines.append(f"  ... и ещё {len(self.added_entities)-5}")
        if self.removed_entities:
            lines.append("<b>Удалены:</b>")
            for name in self.removed_entities[:3]: lines.append(f"  - {name}")
        if self.changed_entities:
            lines.append(f"<b>Обновлено:</b> {len(self.changed_entities)} сущностей")
        return "\n".join(lines)


def compute_diff(before: KnowledgeGraph, after: KnowledgeGraph) -> GraphDiff:
    before_ids = set(before.entities); after_ids = set(after.entities)
    added   = [after.entities[i].name for i in after_ids - before_ids]
    removed = [before.entities[i].name for i in before_ids - after_ids]
    changed = []
    for eid in before_ids & after_ids:
        b, a = before.entities[eid], after.entities[eid]
        chg  = {f: {"before": getattr(b,f), "after": getattr(a,f)}
                for f in ("name","type","is_rumour","priority_score") if getattr(b,f)!=getattr(a,f)}
        if chg: changed.append({"entity_id": eid, "name": a.name, "changes": chg})
    def rk(r): return (r.from_id, r.to_id, r.type)
    br = {rk(r):r for r in before.relations}; ar = {rk(r):r for r in after.relations}
    added_rels   = [{"from":k[0],"to":k[1],"type":k[2],"confidence":r.confidence} for k,r in ar.items() if k not in br]
    removed_rels = [{"from":k[0],"to":k[1],"type":k[2]} for k in br if k not in ar]
    return GraphDiff(added, removed, changed, added_rels, removed_rels, before.version, after.version)
