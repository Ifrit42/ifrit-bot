# news_fetcher.py
import feedparser
from datetime import datetime

def fetch_crypto_news(limit: int = 10) -> list[dict]:
    """
    Returns up to `limit` latest Coindesk RSS entries as dicts:
      - title, url, description, published (ISO), published_parsed
    """
    feed_url = "https://www.coindesk.com/arc/outboundfeeds/rss/"
    feed     = feedparser.parse(feed_url)

    articles = []
    for entry in feed.entries[:limit]:
        # Some feeds may not have published_parsed, so guard it
        ts = None
        if getattr(entry, "published_parsed", None):
            ts = datetime(*entry.published_parsed[:6]).isoformat()
        
        articles.append({
            "title":       entry.title,
            "url":         entry.link,
            "description": entry.get("summary", "").replace("\n", " "),
            "published":   ts,
        })
    return articles