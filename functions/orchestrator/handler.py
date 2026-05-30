"""
orchestrator — главная функция, управляет ежедневным пайплайном.

Итерации 1-5: RSS → анализ → граф → верификация → дайджест → Telegram
"""
import logging
import os
from datetime import date

from shared.models import KnowledgeGraph, NewsItem
from shared.priority import update_priorities
from shared.storage import load_graph, save_digest, save_graph
from shared.seen import SeenStore
from graph.seed import build_initial_graph
from shared.deferred import apply_deferred_to_pipeline
from shared.versioning import save_snapshot, compute_diff
from shared.monitoring import PipelineMetrics, PipelineRun, send_monitoring_report, send_critical_alert
from functions.search_worker.handler import search_entity_news
from functions.search_worker.sources.registry import get_registry, reset_registry
from functions.graph_updater.handler import update_graph_from_news, update_graph_with_fact_check
from functions.digest_generator.handler import build_digest, build_digest_with_fact_check
from functions.tg_sender.handler import send_digest

logger = logging.getLogger(__name__)

MAX_NEWS_IN_DIGEST = int(os.environ.get("MAX_NEWS_IN_DIGEST", "20"))
USE_LLM            = os.environ.get("USE_LLM",        "true").lower() == "true"
USE_FACT_CHECK     = os.environ.get("USE_FACT_CHECK",  "true").lower() == "true"


def _collect_news(graph, seen: SeenStore):
    reset_registry()
    news_by_entity: dict[str, list[NewsItem]] = {}
    mentioned_ids:  set[str]                  = set()
    total                                     = 0
    seen_this_run:  set[str]                  = set()

    for entity in graph.get_entities_by_priority():
        if total >= MAX_NEWS_IN_DIGEST:
            break
        items    = search_entity_news(entity)
        unique   = [i for i in items if not seen.is_seen(i.url) and i.url not in seen_this_run]
        seen_this_run.update(i.url for i in unique)
        remaining                 = MAX_NEWS_IN_DIGEST - total
        batch                     = unique[:remaining]
        news_by_entity[entity.id] = batch
        total                    += len(batch)
        if batch:
            mentioned_ids.add(entity.id)

    today = date.today().isoformat()
    seen.mark_many(seen_this_run, today)

    return news_by_entity, mentioned_ids


def _format_simple(news_by_entity, graph):
    today = date.today().strftime("%d.%m.%Y")
    lines = [f"📰 <b>Дайджест новостей — {today}</b>\n"]
    total = 0
    for entity_id, items in news_by_entity.items():
        if not items:
            continue
        entity = graph.entities.get(entity_id)
        lines.append(f"\n<b>{entity.name if entity else entity_id}</b>")
        for item in items:
            lines.append(f'• <a href="{item.url}">{item.title}</a>')
            total += 1
    lines.append(f"\n<i>Всего материалов: {total}</i>")
    return "\n".join(lines)


def run_pipeline() -> dict:
    metrics = PipelineMetrics(llm_used=USE_LLM)

    with PipelineRun(metrics):
        logger.info("=== Pipeline started (LLM=%s, FactCheck=%s) ===", USE_LLM, USE_FACT_CHECK)

        # 1. Загрузить или создать граф
        graph = load_graph()
        if graph is None:
            logger.info("Graph not found — seeding initial graph")
            graph = build_initial_graph()
            save_graph(graph, backup=False)
            logger.info("Initial graph seeded (root=%s)", graph.root_id)

        metrics.entities_before = len(graph.entities)
        metrics.graph_version   = graph.version
        graph.entities          = update_priorities(graph.entities)

        # 2. Собрать новости
        seen = SeenStore(os.path.join(os.environ.get("DATA_DIR", "/data"), "seen_urls.db"))
        news_by_entity, mentioned_ids = _collect_news(graph, seen)
        seen.close()
        all_fresh            = [i for items in news_by_entity.values() for i in items]
        metrics.news_fresh   = len(all_fresh)
        metrics.source_stats = get_registry().get_summary()
        get_registry().log_summary()

        # 3. Deferred логика
        graph, final_news         = apply_deferred_to_pipeline(graph, all_fresh, MAX_NEWS_IN_DIGEST)
        metrics.news_deferred_in  = max(0, len(final_news) - len(all_fresh))
        metrics.news_deferred_out = len(graph.deferred_news)
        metrics.news_total        = len(final_news)

        # 4. Нет новостей
        if not final_news:
            send_digest("", is_empty=True)
            save_graph(graph)
            send_monitoring_report(metrics)
            return {"status": "ok", "news_count": 0}

        # 5. Снапшот до обновления
        save_snapshot(graph, label="before_update")
        graph_before   = graph
        contradictions = []

        if USE_LLM:
            try:
                if USE_FACT_CHECK:
                    # итерация 5: обновление + верификация фактов
                    graph, analysis_results, contradictions = update_graph_with_fact_check(
                        graph, final_news, mentioned_ids
                    )
                    # алерт при критических противоречиях
                    high = [c for c in contradictions if c.severity == "high" and not c.is_confirmed]
                    if high:
                        alert_lines = [f"🔴 {c.entity_name}: {c.new_claim}" for c in high[:3]]
                        send_critical_alert(
                            "Обнаружены противоречия с графом знаний:\n" + "\n".join(alert_lines)
                        )
                        metrics.add_warning(f"{len(high)} high-severity contradictions")
                else:
                    # итерация 2-4: только обновление графа
                    graph, analysis_results = update_graph_from_news(graph, final_news, mentioned_ids)

            except Exception as e:
                metrics.add_warning(f"graph_updater failed: {e}")
                analysis_results = [{"item": n, "analysis": {}} for n in final_news]

            diff = compute_diff(graph_before, graph)
            if not diff.is_empty():
                logger.info("Graph diff: %s", diff.summary())

            try:
                if USE_FACT_CHECK and contradictions:
                    digest_text = build_digest_with_fact_check(
                        analysis_results, graph, contradictions
                    )
                else:
                    digest_text = build_digest(analysis_results, graph)
                if not digest_text:
                    raise ValueError("empty digest")
            except Exception as e:
                metrics.add_warning(f"digest_generator failed: {e}, fallback")
                digest_text = _format_simple({n.entity_id: [n] for n in final_news}, graph)
        else:
            graph.version     += 1
            graph.last_updated = date.today().isoformat()
            analysis_results   = []
            digest_text        = _format_simple(news_by_entity, graph)

        metrics.entities_after = len(graph.entities)
        metrics.graph_version  = graph.version
        metrics.digest_length  = len(digest_text)

        # 6. Сохранить
        save_graph(graph)
        save_snapshot(graph, label="after_update")
        save_digest(digest_text)

        # 7. Отправить
        send_digest(digest_text)

    send_monitoring_report(metrics)
    logger.info("=== Pipeline finished: %d news, %d chars, %.1fs ===",
                metrics.news_total, metrics.digest_length, metrics.duration_seconds)
    return {
        "status":          metrics.status,
        "news_count":      metrics.news_total,
        "digest_length":   metrics.digest_length,
        "graph_version":   metrics.graph_version,
        "duration_s":      metrics.duration_seconds,
        "contradictions":  len(contradictions),
    }


def handler(event: dict, context) -> dict:
    return run_pipeline()


if __name__ == "__main__":
    log_dir = os.path.join(os.environ.get("DATA_DIR", "/data"), "logs")
    log_file = os.path.join(log_dir, f"orchestrator-{date.today().isoformat()}.log")
    os.makedirs(log_dir, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(fmt)
    root.addHandler(handler)
    if not root.handlers or not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(logging.StreamHandler())
    print(run_pipeline())
