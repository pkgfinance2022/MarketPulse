"""
Stock news service.

Recent headlines for a single stock, via yfinance's own news feed - no
new dependency, same data source the rest of the app already uses.

Deliberately per-stock and on-demand, not part of the fundamental scan
itself: Yahoo's per-ticker news feed is a mix of genuinely
company-specific stories and broader sector/market news loosely
tagged to that symbol (a "relevant news" feed, not a strict filter) -
useful context once you've picked a stock to look at, but too noisy
and too expensive (one more fetch per symbol) to pull for the whole
200+ stock universe automatically.
"""

import yfinance as yf


class StockNewsService:

    @staticmethod
    def latest(symbol, limit=8):

        try:
            items = yf.Ticker(symbol).news
        except Exception:
            return []

        if not items:
            return []

        cleaned = []

        for item in items[:limit]:

            content = item.get("content", {})

            url = ""
            canonical = content.get("canonicalUrl")

            if isinstance(canonical, dict):
                url = canonical.get("url", "")

            provider = content.get("provider", {})
            publisher = provider.get("displayName", "") if isinstance(provider, dict) else ""

            cleaned.append(
                {
                    "title": content.get("title", ""),
                    "publisher": publisher,
                    "pub_date": content.get("pubDate", ""),
                    "summary": content.get("summary", ""),
                    "url": url,
                }
            )

        return cleaned
