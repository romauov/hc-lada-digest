"""
clean_graph.py — очистка графа знаний и news.db от мусорных сущностей.

Запуск:
  docker compose run --rm digester python scripts/clean_graph.py

Что делает:
  - Бэкапит граф в {DATA_DIR}/backups/
  - Удаляет мусорные сущности (player_null, player_хоккеисты и т.д.)
  - Перепривязывает новости чужих клубов к lada_hc
  - Сливает дублирующиеся сущности (бокун → даниил_бокун, и т.д.)
  - Чистит relations, ссылающиеся на удалённые сущности
  - Чистит news.db: DELETE для мусора, UPDATE для перепривязки
"""
import json
import logging
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ── Чужие клубы — новости перепривязываем к lada_hc ─────────────────────

CLUB_IDS_TO_REASSIGN = {
    "hockey_салават_юлаев",
    "hockey_локомотив",
    "hockey_металлург",
    "hockey_ак_барс",
    "hockey_акм",
    "hockey_авангард",
    "hockey_северсталь",
    "hockey_амур",
    "hockey_сочи",
}

# ── Мусор + сущности вне контекста — новости удаляются ──────────────────

JUNK_IDS_TO_DELETE = {
    # мусорные сущности
    "player_null",
    "player_хоккеисты",
    "player_ещ__пять_хоккеистов",
    "player_четыре_хоккеиста",
    "player_спортсмены",
    "player_форвард_из_канады",
    "player_сын_экс_защитника_сборной_бела",
    "player_сын_главного_тренера_анвара_га",
    "coach_главный_тренер",
    "coach_экс_тренеры_минского__динамо_",
    "coach_экс_специалист_минского__динам",
    "coach_экс_наставник_жлобинского__мет",
    "other_новый_худший_клуб",
    "other_тренерский_штаб",
    "hockey_команда_из_пермского_края",
    "arena_ботаническая_улица__5__тольятт",
    # сущности вне контекста Лады
    "other_нэшвилл",
    "other_вайсфельд",
    "other_кузнецов_евгений",
    "other_ринат_баширов",
    "player_радулов",
    "player_муиссу",
    "player_егор_климович",
    "player_егор_сурин",
    "player_сурин_егор",
    "other_григорий_панин",
    "other_панин_григорий",
    "player_вадим_шипачев",
    "player_вадим_шипач_в",
    "player_шипач_в_вадим",
}

# ── Дубли — слияние: старый_id → новый_id ─────────────────────────────

MERGE_MAP = {
    "player_бокун": "player_даниил_бокун",
    "player_романов": "player_иван_романов",
    "player_макаров": "player_николай_макаров",
    "player_земченок": "player_артем_земченок",
    "player_семин": "player_владислав_семин",
    "player_уильямс": "player_колби_уильямс",
    "player_коттон": "player_алекс_коттон",
    "player_рожков": "player_никита_рожков",
    "player_кугрышев": "player_дмитрий_кугрышев",
    "player_сетдиков": "player_никита_сетдиков",
    "player_алтыбармакян": "player_андрей_алтыбармакян",
    "player_савчук": "player_райли_савчук",
    "player_кинг": "player_бен_кинг",
    "player_артем_волков": "player_арт_м_волков",
    "player_швырев": "player_игорь_швыр_в",
    "player_подскребалин": "player_никита_подскребалин",
    "other__академия_михайлова_": "other_академия_михайлова",
    "coach_десятков": "coach_павел_десятков",
    "coach_зубов_павел": "coach_павел_зубов",
}

# ── Отношения, которые нужно удалить (типы чужих клубов) ──────────────

RELATION_TYPES_TO_REMOVE = {"OTHER", "OTHER_CLUB", "HAS_OPPONENT"}


def clean_graph(data_dir: str) -> int:
    graph_path = os.path.join(data_dir, "knowledge_graph.json")
    news_db_path = os.path.join(data_dir, "news.db")

    if not os.path.exists(graph_path):
        logger.error("Graph not found at %s", graph_path)
        return 1

    with open(graph_path, encoding="utf-8") as f:
        graph = json.load(f)

    entities = graph["entities"]
    relations = graph["relations"]
    meta = graph["meta"]

    # ── бэкап ──
    backup_dir = os.path.join(data_dir, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(
        backup_dir, f"knowledge_graph_before_clean_{date.today().isoformat()}.json"
    )
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)
    logger.info("Backup saved: %s", backup_path)

    # ── считаем статистику ──
    all_to_remove = set(CLUB_IDS_TO_REASSIGN) | JUNK_IDS_TO_DELETE
    merge_sources = set(MERGE_MAP.keys())
    total_remove = len(all_to_remove) + len(merge_sources)
    total_before = len(entities)

    # ── relations: удаляем связанные с удаляемыми ──
    relations_before = len(relations)
    removed_relation_ids = set()

    new_relations = []
    for r in relations:
        if r["from"] in all_to_remove or r["to"] in all_to_remove:
            removed_relation_ids.add(r.get("from", "") + "→" + r.get("to", ""))
            continue
        new_relations.append(r)
    relations = new_relations

    # ── relations: перенаправляем при слиянии ──
    for r in relations:
        if r["from"] in merge_sources:
            r["from"] = MERGE_MAP[r["from"]]
        if r["to"] in merge_sources:
            r["to"] = MERGE_MAP[r["to"]]

    # ── entities: удаляем ──
    for eid in all_to_remove:
        entities.pop(eid, None)
    for eid in merge_sources:
        entities.pop(eid, None)

    # ── increment version ──
    meta["version"] += 1
    meta["last_updated"] = date.today().isoformat()

    graph["entities"] = entities
    graph["relations"] = relations

    # ── сохраняем граф ──
    with open(graph_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)

    relations_removed = relations_before - len(relations)
    logger.info("Graph: %d → %d entities (removed %d + merged %d)",
                total_before, len(entities), len(all_to_remove), len(merge_sources))
    logger.info("Relations: %d → %d", relations_before, len(relations))

    # ── чистим news.db ──
    if os.path.exists(news_db_path):
        _clean_news_db(news_db_path)
    else:
        logger.info("news.db not found at %s, skipping", news_db_path)

    logger.info("Cleanup complete. Version: %s → %s", meta["version"] - 1, meta["version"])
    return 0


def _clean_news_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)

    # перепривязываем новости чужих клубов к lada_hc
    for eid in CLUB_IDS_TO_REASSIGN:
        cur = conn.execute("UPDATE news SET entity_id = 'lada_hc' WHERE entity_id = ?", (eid,))
        if cur.rowcount:
            logger.info("  news UPDATE %s → lada_hc (%d rows)", eid, cur.rowcount)

    # удаляем новости мусорных сущностей
    for eid in JUNK_IDS_TO_DELETE:
        cur = conn.execute("DELETE FROM news WHERE entity_id = ?", (eid,))
        if cur.rowcount:
            logger.info("  news DELETE %s (%d rows)", eid, cur.rowcount)

    # перепривязываем дубли
    for old_id, new_id in MERGE_MAP.items():
        cur = conn.execute("UPDATE news SET entity_id = ? WHERE entity_id = ?", (new_id, old_id))
        if cur.rowcount:
            logger.info("  news MERGE %s → %s (%d rows)", old_id, new_id, cur.rowcount)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    data_dir = os.environ.get("DATA_DIR", "/data")
    sys.exit(clean_graph(data_dir))
