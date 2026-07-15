"""
Yahoo Finance Provider
"""

import threading
import time

import pandas as pd
import yfinance as yf

# Short-lived, thread-safe fetch cache (mirrors the pattern already used
# in dashboard/services/universe_cache.py) - the same (symbol, interval,
# period) combo often gets fetched several times within a few seconds
# by completely independent call sites (e.g. the Algo Test tab's detail
# box and its backtest report both analyse the same ticker/timeframe
# right after each other). Without this, one "Test" click could fire
# 2-3x the necessary Yahoo requests, which is exactly what tipped Yahoo
# into rate-limiting the app (YFRateLimitError) in production. The TTL
# is well under every existing refresh cadence elsewhere in the app
# (45s Global Indices live, 60s Movers, hourly background scans), so
# nothing that actually wants fresh-every-tick data goes stale because
# of this.
_cache_lock = threading.Lock()
_cache = {}
CACHE_TTL_SECONDS = 30


class YahooProvider:

    def history(
        self,
        symbol,
        interval="1d",
        period="1y",
    ):
        """
        Uses yf.Ticker(symbol).history(), NOT yf.download() - every
        call site here fetches one symbol at a time, but yf.download()
        always routes through yfinance's own internal multi-threaded
        bulk-download path (via the `multitasking` package) regardless
        of how many tickers you pass it, spawning its OWN thread pool
        on top of whatever ThreadPoolExecutor calls this from. That
        compounded with this app's own thread usage to exceed
        Streamlit Cloud's container thread limit ("RuntimeError: can't
        start new thread"). Ticker.history() is a single direct
        request with no internal threading at all.
        """

        cache_key = (symbol, interval, period)

        with _cache_lock:
            cached = _cache.get(cache_key)

        if cached is not None and (time.time() - cached[0]) < CACHE_TTL_SECONDS:
            return cached[1]

        df = yf.Ticker(symbol).history(
            interval=interval,
            period=period,
            auto_adjust=True,
        )

        if not df.empty:

            # Flatten MultiIndex columns if yfinance returns them
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # Keep only the columns we use
            expected = ["Open", "High", "Low", "Close", "Volume"]

            for col in expected:
                if col not in df.columns:
                    df[col] = None

            df = df[expected]

        with _cache_lock:
            _cache[cache_key] = (time.time(), df)

        return df