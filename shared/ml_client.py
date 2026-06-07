import logging
import os

import requests

from shared.models import NewsItem

logger = logging.getLogger(__name__)

ML_URL = os.environ.get("ML_URL", "")
DEDUP_THRESHOLD = float(os.environ.get("DEDUP_THRESHOLD", "0.92"))
RELEVANCE_THRESHOLD = float(os.environ.get("RELEVANCE_THRESHOLD", "0.65"))

_REQUEST_TIMEOUT = 5


def semantic_dedup(titles: list[str], threshold: float | None = None) -> list[int]:
    if not ML_URL or not titles:
        return list(range(len(titles)))

    thresh = threshold if threshold is not None else DEDUP_THRESHOLD
    try:
        resp = requests.post(
            f"{ML_URL}/dedup",
            json={"titles": titles, "threshold": thresh},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["indices_to_keep"]
    except Exception:
        logger.warning("ML dedup unavailable, fallback to no dedup")
        return list(range(len(titles)))


def is_relevant(title: str, anchor_phrases: list[str]) -> tuple[bool, float]:
    if not ML_URL:
        return True, 1.0

    try:
        resp = requests.post(
            f"{ML_URL}/relevance",
            json={"title": title, "anchor_phrases": anchor_phrases},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["is_relevant"], data["score"]
    except Exception:
        logger.warning("ML relevance unavailable, fallback to relevant")
        return True, 1.0


def calc_relevance(item: NewsItem, anchor_phrases: list[str]) -> NewsItem:
    ok, score = is_relevant(item.title, anchor_phrases)
    item.relevance_score = score
    return item
