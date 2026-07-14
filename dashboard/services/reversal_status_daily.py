"""
Daily+Weekly reversal playbook status service.

Mirrors dashboard/services/reversal_status.py exactly, wired to the
Daily+Weekly engine (analysis/reversal_playbook_daily.py) instead of
the 1H+Daily one. Kept as a separate service (not a parameterized
version of ReversalStatusService) so the two engines stay fully
independent - the Daily+Weekly one is additive, not a replacement.
"""

from concurrent.futures import ThreadPoolExecutor

from analysis.reversal_playbook_daily import DailyWeeklyReversalPlaybook

ACTIONABLE_STATES = {
    "BUY_SIGNAL": "LONG",
    "SELL_SIGNAL": "SHORT",
    "SELL_SIGNAL_CONTINUATION": "SHORT",
}

SCREEN_WORKERS = 3   # kept low - Streamlit Community Cloud's free-tier container has a much lower OS thread limit than local dev; a higher count caused "can't start new thread" crashes in production


class DailyReversalStatusService:

    @classmethod
    def analyse(cls, symbol, period_daily="10y", period_weekly="15y"):

        result = DailyWeeklyReversalPlaybook.run_symbol(symbol, period_daily=period_daily, period_weekly=period_weekly)

        if result is None:
            return None

        description, state, levels, event_time = DailyWeeklyReversalPlaybook.describe(result)
        weekly_description, weekly_state, weekly_event_time = DailyWeeklyReversalPlaybook.weekly_describe(result)

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
            "weekly_state": weekly_state,
            "weekly_description": weekly_description,
            "weekly_event_time": weekly_event_time,
        }

    @classmethod
    def _screen_one(cls, symbol, period_daily, period_weekly):

        try:
            result = DailyWeeklyReversalPlaybook.run_symbol(symbol, period_daily=period_daily, period_weekly=period_weekly)

            if result:
                description, state, _, event_time = DailyWeeklyReversalPlaybook.describe(result)
                weekly_description, weekly_state, weekly_event_time = DailyWeeklyReversalPlaybook.weekly_describe(result)
                last = result["trace"][-1]
                return symbol, {
                    "state": state,
                    "description": description,
                    "price": float(last["price"]),
                    "rsi": round(last["rsi"], 2),
                    "event_time": event_time,
                    "weekly_state": weekly_state,
                    "weekly_description": weekly_description,
                    "weekly_event_time": weekly_event_time,
                }

            return symbol, {"state": "NONE", "description": "", "price": None, "rsi": None, "event_time": None, "weekly_state": "NONE", "weekly_description": "", "weekly_event_time": None}

        except Exception:
            return symbol, {"state": "NONE", "description": "", "price": None, "rsi": None, "event_time": None, "weekly_state": "NONE", "weekly_description": "", "weekly_event_time": None}

    @classmethod
    def screen_states(cls, symbols, period_daily="10y", period_weekly="15y"):
        """
        Fetches every symbol concurrently (thread pool - network-bound,
        not CPU-bound), same reasoning as ReversalStatusService.
        """

        states = {}

        with ThreadPoolExecutor(max_workers=SCREEN_WORKERS) as executor:

            futures = [executor.submit(cls._screen_one, symbol, period_daily, period_weekly) for symbol in symbols]

            for future in futures:
                symbol, info = future.result()
                states[symbol] = info

        return states

    @classmethod
    def screen(cls, symbols, period_daily="10y", period_weekly="15y"):

        states = cls.screen_states(symbols, period_daily=period_daily, period_weekly=period_weekly)

        return {
            symbol: DailyWeeklyReversalPlaybook.STATE_LABELS.get(info["state"], "⚪ Watching")
            for symbol, info in states.items()
        }
