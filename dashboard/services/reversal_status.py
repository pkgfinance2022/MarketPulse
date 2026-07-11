"""
Reversal playbook status service.

Live per-ticker status (for the detail box) and whole-universe
screening (for the scanner column) - mirrors rsi_wave_status.py's
shape so both features feel consistent in the dashboard.

v2: runs on 1H + Daily bars only (no more 15m confirmation step),
so this now costs 2 yfinance fetches per symbol (1H 730d + Daily 5y),
same order of magnitude as before.
"""

from analysis.reversal_playbook import ReversalPlaybook

ACTIONABLE_STATES = {
    "BUY_SIGNAL": "LONG",
    "SELL_SIGNAL": "SHORT",
    "SELL_SIGNAL_CONTINUATION": "SHORT",
}


class ReversalStatusService:

    @classmethod
    def analyse(cls, symbol, period_1h="730d", period_daily="5y"):

        result = ReversalPlaybook.run_symbol(symbol, period_1h=period_1h, period_daily=period_daily)

        if result is None:
            return None

        description, state, levels = ReversalPlaybook.describe(result)

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
    def screen_states(cls, symbols, period_1h="730d", period_daily="5y"):

        states = {}

        for symbol in symbols:

            try:
                result = ReversalPlaybook.run_symbol(symbol, period_1h=period_1h, period_daily=period_daily)

                if result:
                    description, state, _ = ReversalPlaybook.describe(result)
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
    def screen(cls, symbols, period_1h="730d", period_daily="5y"):

        states = cls.screen_states(symbols, period_1h=period_1h, period_daily=period_daily)

        return {
            symbol: ReversalPlaybook.STATE_LABELS.get(info["state"], "⚪ Watching")
            for symbol, info in states.items()
        }
