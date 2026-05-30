"""deferred.py — управление отложенными новостями."""
import logging
from datetime import date, timedelta
from shared.models import DeferredNews, KnowledgeGraph, NewsItem

logger = logging.getLogger(__name__)
DEFERRED_TTL_DAYS  = 3
MIN_NEWS_THRESHOLD = 3

def push_deferred(graph, items, reason="low_priority_day"):
    today = date.today().isoformat()
    for item in items:
        graph.deferred_news.append(DeferredNews(
            url=item.url, title=item.title, entity_id=item.entity_id,
            deferred_at=today, reason=reason, source=item.source,
            published_at=item.published_at,
        ))
    logger.info("Deferred %d items (%s)", len(items), reason)
    return graph

def pop_deferred(graph, needed):
    today  = date.today()
    cutoff = (today - timedelta(days=DEFERRED_TTL_DAYS)).isoformat()
    fresh  = [d for d in graph.deferred_news if d.deferred_at >= cutoff]
    fresh.sort(key=lambda d: d.deferred_at, reverse=True)
    to_use = fresh[:needed]
    graph.deferred_news = fresh[needed:]
    items = [NewsItem(url=d.url, title=d.title, published_at=d.published_at,
                      source=d.source, entity_id=d.entity_id) for d in to_use]
    if items:
        logger.info("Using %d deferred items", len(items))
    return graph, items

def should_use_deferred(fresh_count):
    return fresh_count < MIN_NEWS_THRESHOLD

def apply_deferred_to_pipeline(graph, fresh_news, max_total):
    fresh_count = len(fresh_news)
    if should_use_deferred(fresh_count) and graph.deferred_news:
        needed = min(max_total - fresh_count, len(graph.deferred_news))
        graph, extra = pop_deferred(graph, needed)
        logger.info("Pipeline: %d fresh + %d deferred = %d total", fresh_count, len(extra), fresh_count+len(extra))
        return graph, fresh_news + extra
    if fresh_count > max_total:
        graph = push_deferred(graph, fresh_news[max_total:], reason="quota_exceeded")
        return graph, fresh_news[:max_total]
    return graph, fresh_news
