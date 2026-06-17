"""RSS feed reader — standalone, no crewai dependency."""
import time

import feedparser

from shared.sanitize import sanitize_rss_item

_MAX_RETRIES = 2
_RETRY_BACKOFF = 2  # seconds
_ITEMS_PER_FEED = 5

RSS_FEEDS = {
    "Reuters Markets":    "https://feeds.reuters.com/reuters/businessNews",
    "Reuters Technology": "https://feeds.reuters.com/reuters/technologyNews",
    "Yahoo Finance":      "https://finance.yahoo.com/news/rssindex",
    "MarketWatch":        "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    # Investing.com rimosso — violazione ToS, no licenza dati pubblica (policy ❌)
}


def fetch_rss_news(max_items_per_feed: int = _ITEMS_PER_FEED) -> str:
    """Fetch financial news from all RSS feeds. Returns formatted string."""
    items: list[str] = []
    failed: list[str] = []

    for source, url in RSS_FEEDS.items():
        entries = None
        for attempt in range(1 + _MAX_RETRIES):
            try:
                feed = feedparser.parse(url)
                if feed.entries:
                    entries = feed.entries
                    break
            except Exception:
                pass
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF)

        if entries:
            for entry in entries[:max_items_per_feed]:
                raw_title = entry.get("title", "")
                raw_summary = entry.get("summary", entry.get("description", ""))
                title, summary = sanitize_rss_item(raw_title, raw_summary)
                link = entry.get("link", "")
                items.append(f"[{source}] {title}\n{summary}\nURL: {link}")
        else:
            failed.append(source)

    if not items:
        raise RuntimeError(
            f"No articles retrieved from any RSS feed "
            f"({', '.join(failed)}). Check network connectivity."
        )

    return "\n\n---\n\n".join(items)


def get_feed_status() -> dict[str, bool]:
    """Return {source: ok} dict after a quick probe of each feed."""
    status: dict[str, bool] = {}
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            status[source] = bool(feed.entries)
        except Exception:
            status[source] = False
    return status
