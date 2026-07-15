"""
Reversal playbook status service.

Live per-ticker status (for the detail box) and whole-universe
screening (for the scanner column) - mirrors rsi_wave_status.py's
shape so both features feel consistent in the dashboard.

v2: runs on 1H + Daily bars only (no more 15m confirmation step),
so this now costs 2 yfinance fetches per symbol (1H 730d + Daily 5y),
same order of magnitude as before.
"""

from concurrent.futures import ThreadPoolExecutor

from analysis.reversal_playbook import ReversalPlaybook

ACTIONABLE_STATES = {
    "BUY_SIGNAL": "LONG",
    "BUY_SIGNAL_PATH_C": "LONG",
    "SELL_SIGNAL": "SHORT",
    "SELL_SIGNAL_CONTINUATION": "SHORT",
}

SCREEN_WORKERS = 3   # yfinance calls are network-bound (waiting on Yahoo), not CPU-bound - a thread pool still helps at a small worker count. Kept low deliberately: Streamlit Community Cloud's free-tier container has a much lower OS thread limit than local dev - a higher count here caused "RuntimeError: can't start new thread" crashes in production that never showed up locally.


class ReversalStatusService:

    @classmethod
    def analyse(cls, symbol, period_1h="730d", period_daily="5y"):

        result = ReversalPlaybook.run_symbol(symbol, period_1h=period_1h, period_daily=period_daily)

        if result is None:
            return None

        description, state, levels, event_time = ReversalPlaybook.describe(result)

        last = result["trace"][-1]

        return {
            "symbol": symbol,
            "state": state,
            "description": description,
            "direction": ACTIONABLE_STATES.get(state),
            "price": float(last["price"]),
            "rsi": round(last["rsi"], 2),
            "stop_target": levels,
            "event_time": event_time,
        }

    @classmethod
    def _screen_one(cls, symbol, period_1h, period_daily):

        try:
            result = ReversalPlaybook.run_symbol(symbol, period_1h=period_1h, period_daily=period_daily)

            if result:
                description, state, _, event_time = ReversalPlaybook.describe(result)
                last = result["trace"][-1]
                return symbol, {
                    "state": state,
                    "description": description,
                    "price": float(last["price"]),
                    "rsi": round(last["rsi"], 2),
                    "event_time": event_time,
                }

            return symbol, {"state": "NONE", "description": "", "price": None, "rsi": None, "event_time": None}

        except Exception:
            return symbol, {"state": "NONE", "description": "", "price": None, "rsi": None, "event_time": None}

    @classmethod
    def screen_states(cls, symbols, period_1h="730d", period_daily="5y"):
        """
        Fetches every symbol concurrently (thread pool - these calls
        spend nearly all their time waiting on Yahoo's network
        response, not on CPU), instead of one at a time. This is the
        difference between a 140-symbol universe taking minutes vs.
        tens of seconds.
        """

        states = {}

        with ThreadPoolExecutor(max_workers=SCREEN_WORKERS) as executor:

            futures = [executor.submit(cls._screen_one, symbol, period_1h, period_daily) for symbol in symbols]

            for future in futures:
                symbol, info = future.result()
                states[symbol] = info

        return states

    @classmethod
    def screen(cls, symbols, period_1h="730d", period_daily="5y"):

        states = cls.screen_states(symbols, period_1h=period_1h, period_daily=period_daily)

        return {
            symbol: ReversalPlaybook.STATE_LABELS.get(info["state"], "⚪ Watching")
            for symbol, info in states.items()
        }
