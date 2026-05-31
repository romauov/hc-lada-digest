import logging
from urllib.parse import quote_plus, urljoin

from shared.models import Entity, NewsItem
from functions.search_worker.sources.base import BaseSource
from functions.search_worker.sources.scrapers.helpers import _get, _parse_iso, _parse_text_date

logger = logging.getLogger(__name__)


class ChampionatScraperSource(BaseSource):
    name = "championat"
    SEARCH = "https://www.championat.com/search/?query={query}"

    def fetch(self, entity: Entity) -> list[NewsItem]:
        found: dict[str, NewsItem] = {}

        for query in entity.search_queries[:3]:
            url = self.SEARCH.format(query=quote_plus(query))
            soup = _get(url)
            if soup is None:
                continue
            self._extract_results(soup, entity, found)

        logger.debug("[championat] %s → %d items", entity.name, len(found))
        return list(found.values())

    def _extract_results(
        self,
        soup: object,
        entity: Entity,
        found: dict[str, NewsItem],
    ) -> None:
        articles = (
            soup.select(".search-results__item") or
            soup.select(".news-list .news-item") or
            soup.select("article")
        )

        for article in articles:
            try:
                link_tag = article.select_one("a[href]")
                if not link_tag:
                    continue
                href = urljoin("https://www.championat.com", link_tag["href"])
                title = link_tag.get_text(strip=True)
                if not title or not href or "championat.com" not in href:
                    continue

                time_tag = article.select_one("time[datetime]")
                dt = _parse_iso(time_tag["datetime"]) if time_tag else None

                if dt is None:
                    date_span = article.select_one(".date, .time, .news-item__date")
                    if date_span:
                        dt = _parse_text_date(date_span.get_text(strip=True))

                if not self.is_fresh(dt):
                    continue

                nid = self.make_news_id(href)
                if nid not in found:
                    found[nid] = self._make_item(
                        url=href, title=title, published_at=dt, entity_id=entity.id,
                    )
            except Exception as e:
                logger.debug("Article parse error: %s", e)
