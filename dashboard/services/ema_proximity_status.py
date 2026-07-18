"""
EMA proximity watchlist status service.

Screens every asset across all four sources (Global Indices, US
Stocks, Indian Stocks, Crypto) for Weekly-200 / Monthly-50 EMA
proximity - see analysis/ema_proximity.py for why those specific
periods. Stateless (nothing to track between runs), so this is a
plain screen, not a wave/divergence-style state machine.
"""

from concurrent.futures import ThreadPoolExecutor

from analysis.ema_proximity import check_proximity

SCREEN_WORKERS = 3   # kept low - see rsi_wave_status.py's own reasoning (Streamlit Community Cloud's thread limit)


class EMAProximityStatusService:

    @staticmethod
    def _screen_one(symbol):

        try:
            return symbol, check_proximity(symbol)
        except Exception:
            return symbol, {"weekly": None, "monthly": None}

    @classmethod
    def screen_states(cls, symbols):

        states = {}

        with ThreadPoolExecutor(max_workers=SCREEN_WORKERS) as executor:

            futures = [executor.submit(cls._screen_one, symbol) for symbol in symbols]

            for future in futures:
                symbol, info = future.result()
                states[symbol] = info

        return states
