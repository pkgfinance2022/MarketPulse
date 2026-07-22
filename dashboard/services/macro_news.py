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

MAX_AGE_DAYS = 2   # older than this isn't "what's moving markets today" anymore


def _parse_pub_date(pub_date):

    if not pub_date:
        return datetime.min.replace(tzinfo=timezone.utc)

    try:
        return datetime.fromisoformat(str(pub_date).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _is_recent(item):
    """A missing/unparseable pub_date can't be confirmed recent, so it's treated as stale, same as a genuinely old one."""

    age = datetime.now(timezone.utc) - _parse_pub_date(item.get("pub_date"))

    return age.days < MAX_AGE_DAYS


def scan(limit=10, limit_per_ticker=3):
    """
    Fetches news for each ticker in MACRO_NEWS_TICKERS once, returning
    both a deduped flat feed (the same real-world story often gets
    tagged to more than one of these tickers, so "headlines" dedupes
    by title) and a per-ticker grouping ("by_ticker") that does NOT
    dedupe - the same story showing up under more than one mover is
    useful signal there, not noise.

    The per-ticker view exists so a caller can connect today's actual
    biggest mover back to the headline(s) tagged to it specifically
    ("NASDAQ is up - here's the headline actually filed against
    NASDAQ today"), instead of the flat feed's "here are 10 recent
    macro headlines, good luck guessing which one explains today's
    move."

    Anything older than MAX_AGE_DAYS is dropped before dedup/sorting -
    a multi-day-old story isn't "what's moving markets today" anymore,
    and letting it sit in the feed just crowds out genuinely fresh
    headlines once the market's been quiet for a day or two.
    """

    seen_titles = set()
    combined = []
    by_ticker = {}

    for ticker in MACRO_NEWS_TICKERS:

        ticker_items = sorted(
            (item for item in StockNewsService.latest(ticker, limit=5) if _is_recent(item)),
            key=lambda item: _parse_pub_date(item.get("pub_date")),
            reverse=True,
        )
        by_ticker[ticker] = ticker_items[:limit_per_ticker]

        for item in ticker_items:

            title = item.get("title", "").strip()

            if not title or title in seen_titles:
                continue

            seen_titles.add(title)
            combined.append(item)

    combined.sort(key=lambda item: _parse_pub_date(item.get("pub_date")), reverse=True)

    return {"headlines": combined[:limit], "by_ticker": by_ticker}
