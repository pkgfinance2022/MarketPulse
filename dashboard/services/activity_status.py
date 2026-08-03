"""
Activity score status service.

Stateless volatility-expansion read (see analysis/activity_score.py) -
no bar-by-bar phase machine to walk, just a live snapshot off the
latest fetched bars. Same screen_states shape as every other status
service in this app so it plugs into the existing scan/table pattern
without a special case.
"""

from concurrent.futures import ThreadPoolExecutor

from analysis.activity_score import STATE_LABELS, compute_activity
from providers.yahoo import YahooProvider

SCREEN_WORKERS = 3   # kept low - Streamlit Community Cloud's free-tier container has a much lower OS thread limit than local dev


class ActivityStatusService:

    @classmethod
    def analyse(cls, symbol, period="730d", interval="1h"):

        df = YahooProvider().history(symbol, interval=interval, period=period)

        result = compute_activity(df)

        if result is None:
            return None

        return {
            "symbol": symbol,
            "ratio": result["ratio"],
            "state": result["state"],
            "label": result["label"],
            "price": float(df["Close"].iloc[-1]) if not df.empty else None,
        }

    @classmethod
    def _screen_one(cls, symbol, period, interval):

        try:
            info = cls.analyse(symbol, period=period, interval=interval)

            if info:
                return symbol, info

            return symbol, {"state": "NONE", "label": STATE_LABELS["NONE"], "ratio": None, "price": None}

        except Exception:
            return symbol, {"state": "NONE", "label": STATE_LABELS["NONE"], "ratio": None, "price": None}

    @classmethod
    def screen_states(cls, symbols, period="730d", interval="1h"):

        states = {}

        with ThreadPoolExecutor(max_workers=SCREEN_WORKERS) as executor:

            futures = [executor.submit(cls._screen_one, symbol, period, interval) for symbol in symbols]

            for future in futures:
                symbol, info = future.result()
                states[symbol] = info

        return states
