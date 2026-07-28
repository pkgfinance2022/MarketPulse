"""
EMA20 reclaim status service.

Turns EMAReclaimStrategy's bar-by-bar trace into (a) a live per-ticker
status + SL/target box, and (b) a whole-universe screener label - same
shared-fetch-and-walk shape as RSIWaveStatusService, so the screener
and the detail view never disagree about what's happening for a given
symbol.

Stop/target here is NOT the generic ATR/support-resistance formula
RSIWaveStatusService uses - this strategy's own stop and target are
specific, real levels from its own trace (the down-move's low, and the
current EMA200 value), so those are used directly instead.
"""

from concurrent.futures import ThreadPoolExecutor

from analysis.ema_reclaim_strategy import DailyEMAReclaimStrategy, EMAReclaimStrategy

ACTIONABLE_STATES = {
    "ENTRY_LONG": "LONG",
    "IN_WAVE": "LONG",
}

SCREEN_WORKERS = 3   # kept low - same Streamlit Community Cloud thread-limit reasoning as RSIWaveStatusService


class EMAReclaimStatusService:

    STRATEGY = EMAReclaimStrategy

    @classmethod
    def analyse(cls, symbol, period="730d"):

        trace, df = cls.STRATEGY.run_symbol(symbol, period=period)

        if trace is None:
            return None

        description, state, event_time = cls.STRATEGY.describe(trace)
        last = trace[-1]

        direction = ACTIONABLE_STATES.get(state)
        stop_target = cls._stop_target(last) if direction else None

        return {
            "symbol": symbol,
            "df": df,
            "trace": trace,
            "state": state,
            "description": description,
            "price": last["price"],
            "rsi": round(last["rsi"], 2),
            "direction": direction,
            "stop_target": stop_target,
            "event_time": event_time,
        }

    @staticmethod
    def _price_round(value, price):
        """Same fix as ReversalPlaybook._price_round - round(x, 2) collapses distinct levels for anything priced under ~10 (major forex pairs)."""

        if value is None:
            return None

        decimals = 4 if abs(price) < 10 else 2

        return round(value, decimals)

    @classmethod
    def _stop_target(cls, last):

        price = last["price"]
        stop = last["wave_low"]
        target1 = last["ema200"]

        if stop is None or stop >= price:
            return None

        risk = price - stop
        risk_reward = round((target1 - price) / risk, 2) if risk and target1 > price else 0.0

        return {
            "stop": cls._price_round(stop, price),
            "target1": cls._price_round(target1, price),
            "target2": cls._price_round(target1, price),
            "risk": cls._price_round(risk, price),
            "risk_reward": risk_reward,
        }

    @classmethod
    def _screen_one(cls, symbol, period):

        try:
            trace, _ = cls.STRATEGY.run_symbol(symbol, period=period)

            if trace:
                description, state, event_time = cls.STRATEGY.describe(trace)
                last = trace[-1]
                return symbol, {
                    "state": state,
                    "description": description,
                    "price": last["price"],
                    "rsi": round(last["rsi"], 2),
                    "event_time": event_time,
                }

            return symbol, {"state": "NONE", "description": "", "price": None, "rsi": None, "event_time": None}

        except Exception:
            return symbol, {"state": "NONE", "description": "", "price": None, "rsi": None, "event_time": None}

    @classmethod
    def screen_states(cls, symbols, period="730d"):
        """Fetches every symbol concurrently (network-bound, not CPU-bound) - see RSIWaveStatusService.screen_states."""

        states = {}

        with ThreadPoolExecutor(max_workers=SCREEN_WORKERS) as executor:

            futures = [executor.submit(cls._screen_one, symbol, period) for symbol in symbols]

            for future in futures:
                symbol, info = future.result()
                states[symbol] = info

        return states


class DailyEMAReclaimStatusService(EMAReclaimStatusService):
    """Daily-bar variant - see DailyEMAReclaimStrategy for why this is a separate class rather than a parameter."""

    STRATEGY = DailyEMAReclaimStrategy
