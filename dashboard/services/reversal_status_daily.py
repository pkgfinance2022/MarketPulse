"""
Daily+Weekly reversal playbook status service.

Mirrors dashboard/services/reversal_status.py exactly, wired to the
Daily+Weekly engine (analysis/reversal_playbook_daily.py) instead of
the 1H+Daily one. Kept as a separate service (not a parameterized
version of ReversalStatusService) so the two engines stay fully
independent - the Daily+Weekly one is additive, not a replacement.
"""

from analysis.reversal_playbook_daily import DailyWeeklyReversalPlaybook

ACTIONABLE_STATES = {
    "BUY_SIGNAL": "LONG",
    "SELL_SIGNAL": "SHORT",
    "SELL_SIGNAL_CONTINUATION": "SHORT",
}


class DailyReversalStatusService:

    @classmethod
    def analyse(cls, symbol, period_daily="10y", period_weekly="15y"):

        result = DailyWeeklyReversalPlaybook.run_symbol(symbol, period_daily=period_daily, period_weekly=period_weekly)

        if result is None:
            return None

        description, state, levels = DailyWeeklyReversalPlaybook.describe(result)

        last = result["trace"][-1]

        return {
            "symbol": symbol,
            "state": state,
            "description": description,
            "direction": ACTIONABLE_STATES.get(state),
            "price": float(last["price"]),
            "rsi": round(last["rsi"], 2),
            "stop_target": levels,
        }

    @classmethod
    def screen_states(cls, symbols, period_daily="10y", period_weekly="15y"):

        states = {}

        for symbol in symbols:

            try:
                result = DailyWeeklyReversalPlaybook.run_symbol(symbol, period_daily=period_daily, period_weekly=period_weekly)

                if result:
                    description, state, _ = DailyWeeklyReversalPlaybook.describe(result)
                    last = result["trace"][-1]
                    states[symbol] = {
                        "state": state,
                        "description": description,
                        "price": float(last["price"]),
                        "rsi": round(last["rsi"], 2),
                    }
                else:
                    states[symbol] = {"state": "NONE", "description": "", "price": None, "rsi": None}

            except Exception:
                states[symbol] = {"state": "NONE", "description": "", "price": None, "rsi": None}

        return states

    @classmethod
    def screen(cls, symbols, period_daily="10y", period_weekly="15y"):

        states = cls.screen_states(symbols, period_daily=period_daily, period_weekly=period_weekly)

        return {
            symbol: DailyWeeklyReversalPlaybook.STATE_LABELS.get(info["state"], "⚪ Watching")
            for symbol, info in states.items()
        }
