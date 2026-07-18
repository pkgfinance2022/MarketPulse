"""
RSI wave status service.

Turns RSIWaveStrategy's bar-by-bar trace into (a) a live per-ticker
status + SL/target box, and (b) a whole-universe screener label - both
share the same fetch+walk so the screener and the detail view can
never disagree about what's happening for a given symbol.
"""

from concurrent.futures import ThreadPoolExecutor

import ta

from analysis.rsi_wave_strategy import RSIWaveStrategy

ACTIONABLE_STATES = {
    "ENTRY_LONG": "LONG",
    "WAVE_LONG": "LONG",
    "ENTRY_SHORT": "SHORT",
    "WAVE_SHORT": "SHORT",
}

SCREEN_WORKERS = 3   # kept low - Streamlit Community Cloud's free-tier container has a much lower OS thread limit than local dev; a higher count caused "can't start new thread" crashes in production


class RSIWaveStatusService:

    ATR_WINDOW = 14
    SUPPORT_RESISTANCE_WINDOW = 20

    @classmethod
    def analyse(cls, symbol, period="730d"):

        trace, df = RSIWaveStrategy.run_symbol(symbol, period=period)

        if trace is None:
            return None

        description, state, event_time = RSIWaveStrategy.describe(trace)
        last = trace[-1]

        direction = ACTIONABLE_STATES.get(state)

        stop_target = None

        if direction:

            close, high, low = df["Close"], df["High"], df["Low"]

            atr = ta.volatility.average_true_range(high, low, close, window=cls.ATR_WINDOW)
            support = low.rolling(cls.SUPPORT_RESISTANCE_WINDOW).min()
            resistance = high.rolling(cls.SUPPORT_RESISTANCE_WINDOW).max()

            stop_target = cls._stop_target(
                direction,
                last["price"],
                float(support.iloc[-1]),
                float(resistance.iloc[-1]),
                float(atr.iloc[-1]) if not (atr.empty or atr.isna().iloc[-1]) else 0.0,
            )

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
        """
        Same fix as ReversalPlaybook._price_round - round(x, 2) collapses
        distinct levels to the same value for anything priced under ~10
        (major forex pairs, mostly) since a whole cent there is ~100
        pips. Confirmed on EUR/USD: a real stop/target gap of ~50 pips
        (1.14377 vs 1.13843) both rounded to 1.14, making them
        indistinguishable and corrupting the simulated outcome.
        """

        if value is None:
            return None

        decimals = 4 if abs(price) < 10 else 2

        return round(value, decimals)

    @classmethod
    def _stop_target(cls, direction, price, support, resistance, atr):

        if direction == "LONG":

            stop = max(support, price - 2 * atr) if atr else support
            stop = min(stop, price - 0.0001)

            risk = max(price - stop, price * 0.0001)
            target1 = resistance if resistance > price else price + risk * 2
            target2 = price + risk * 3

        else:  # SHORT

            stop = min(resistance, price + 2 * atr) if atr else resistance
            stop = max(stop, price + 0.0001)

            risk = max(stop - price, price * 0.0001)
            target1 = support if support < price else price - risk * 2
            target2 = price - risk * 3

        risk_reward = round(abs(target1 - price) / risk, 2) if risk else 0.0

        return {
            "stop": cls._price_round(stop, price),
            "target1": cls._price_round(target1, price),
            "target2": cls._price_round(target2, price),
            "risk": cls._price_round(risk, price),
            "risk_reward": risk_reward,
        }

    @classmethod
    def _screen_one(cls, symbol, period):

        try:
            trace, _ = RSIWaveStrategy.run_symbol(symbol, period=period)

            if trace:
                description, state, event_time = RSIWaveStrategy.describe(trace)
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
        """
        Fetches every symbol concurrently (thread pool - network-bound,
        not CPU-bound). Returns
        {symbol: {"state":..., "price":..., "rsi":...}}. Shared by the
        scanner label screener and the notification-check fragment so
        both always agree, and carries enough context (price/RSI) for
        a notification message without a second fetch.
        """

        states = {}

        with ThreadPoolExecutor(max_workers=SCREEN_WORKERS) as executor:

            futures = [executor.submit(cls._screen_one, symbol, period) for symbol in symbols]

            for future in futures:
                symbol, info = future.result()
                states[symbol] = info

        return states

    @classmethod
    def screen(cls, symbols, period="730d"):
        """
        One fetch+walk per symbol - returns {symbol: short_label}.
        Deliberately not called on every 45s auto-refresh tick (too
        many yfinance fetches); only at region-load time.
        """

        states = cls.screen_states(symbols, period=period)

        return {
            symbol: RSIWaveStrategy.STATE_LABELS.get(info["state"], "⚪ Watching")
            for symbol, info in states.items()
        }
