"""
Fundamentals Provider

Fetches company fundamental ratios from Yahoo Finance. These change at
most quarterly, so results are cached for a day to keep repeated scans
fast and avoid hammering the API.
"""

import yfinance as yf

from core.cache import DiskCache


class FundamentalsProvider:

    CACHE_TTL = 24 * 60 * 60  # 1 day

    FIELDS = [
        "trailingPE",
        "forwardPE",
        "priceToBook",
        "pegRatio",
        "enterpriseToEbitda",
        "returnOnEquity",
        "returnOnAssets",
        "revenueGrowth",
        "earningsGrowth",
        "earningsQuarterlyGrowth",
        "debtToEquity",
        "profitMargins",
        "trailingEps",
        "forwardEps",
    ]

    def __init__(self):

        self._cache = DiskCache("fundamentals", ttl_seconds=self.CACHE_TTL)

    def get(self, symbol):

        cached = self._cache.get(symbol)

        if cached is not None:
            return cached

        data = {field: None for field in self.FIELDS}

        try:

            info = yf.Ticker(symbol).info or {}

            for field in self.FIELDS:
                data[field] = info.get(field)

        except Exception:
            # Network issue, delisted ticker, rate limit, etc. Fall back
            # to neutral/unknown rather than blowing up the whole scan.
            pass

        self._cache.set(symbol, data)

        return data
