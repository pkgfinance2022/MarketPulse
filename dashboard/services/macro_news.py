"""
Aggregates yfinance's per-ticker news feed (see stock_news_service.py)
across a small, fixed set of macro-relevant tickers into one "what's
moving markets today" headline feed, instead of per-stock news which
is already covered by StockNewsService for a single ticker you've
picked.

Deliberately a small, fixed ticker list (broad US equity benchmarks,
vol, gold, oil, dollar) rather than the whole universe - the same
"too noisy, too expensive to pull for 200+ symbols" reasoning
StockNewsService already documents, but for a handful of macro proxies
whose news feed tends to carry genuinely market-wide stories (Fed
policy, geopolitical events, macro data) rather than single-company
news.
"""

from datetime import datetime, timezone

from dashboard.services.stock_news_service import StockNewsService

MACRO_NEWS_TICKERS = ["^GSPC", "^NDX", "^VIX", "GC=F", "CL=F", "DX-Y.NYB"]


def _parse_pub_date(pub_date):

    if not pub_date:
        return datetime.min.replace(tzinfo=timezone.utc)

    try:
        return datetime.fromisoformat(str(pub_date).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def top_headlines(limit=10):
    """
    Fetches news for each ticker in MACRO_NEWS_TICKERS, dedupes by
    title (the same real-world story often gets tagged to more than
    one of these tickers), and returns the `limit` most recent, newest
    first.
    """

    seen_titles = set()
    combined = []

    for ticker in MACRO_NEWS_TICKERS:

        for item in StockNewsService.latest(ticker, limit=5):

            title = item.get("title", "").strip()

            if not title or title in seen_titles:
                continue

            seen_titles.add(title)
            combined.append(item)

    combined.sort(key=lambda item: _parse_pub_date(item.get("pub_date")), reverse=True)

    return combined[:limit]
