"""
RSI wave status service.

Turns RSIWaveStrategy's bar-by-bar trace into (a) a live per-ticker
status + SL/target box, and (b) a whole-universe screener label - both
share the same fetch+walk so the screener and the detail view can
never disagree about what's happening for a given symbol.
"""

import ta

from analysis.rsi_wave_strategy import RSIWaveStrategy

ACTIONABLE_STATES = {
    "ENTRY_LONG": "LONG",
    "WAVE_LONG": "LONG",
    "ENTRY_SHORT": "SHORT",
    "WAVE_SHORT": "SHORT",
}


class RSIWaveStatusService:

    ATR_WINDOW = 14
    SUPPORT_RESISTANCE_WINDOW = 20

    @classmethod
    def analyse(cls, symbol, period="730d"):

        trace, df = RSIWaveStrategy.run_symbol(symbol, period=period)

        if trace is None:
            return None

        description, state = RSIWaveStrategy.describe(trace)
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
        }

    @staticmethod
    def _stop_target(direction, price, support, resistance, atr):

        if direction == "LONG":

            stop = max(support, price - 2 * atr) if atr else support
            stop = min(stop, price - 0.0001)

            risk = max(price - stop, 0.01)
            target1 = resistance if resistance > price else price + risk * 2
            target2 = price + risk * 3

        else:  # SHORT

            stop = min(resistance, price + 2 * atr) if atr else resistance
            stop = max(stop, price + 0.0001)

            risk = max(stop - price, 0.01)
            target1 = support if support < price else price - risk * 2
            target2 = price - risk * 3

        risk_reward = round(abs(target1 - price) / risk, 2) if risk else 0.0

        return {
            "stop": round(stop, 2),
            "target1": round(target1, 2),
            "target2": round(target2, 2),
            "risk": round(risk, 2),
            "risk_reward": risk_reward,
        }

    @classmethod
    def screen_states(cls, symbols, period="730d"):
        """
        One fetch+walk per symbol - returns
        {symbol: {"state":..., "price":..., "rsi":...}}. Shared by the
        scanner label screener and the notification-check fragment so
        both always agree, and carries enough context (price/RSI) for
        a notification message without a second fetch.
        """

        states = {}

        for symbol in symbols:

            try:
                trace, _ = RSIWaveStrategy.run_symbol(symbol, period=period)

                if trace:
                    description, state = RSIWaveStrategy.describe(trace)
                    last = trace[-1]
                    states[symbol] = {
                        "state": state,
                        "description": description,
                        "price": last["price"],
                        "rsi": round(last["rsi"], 2),
                    }
                else:
                    states[symbol] = {"state": "NONE", "description": "", "price": None, "rsi": None}

            except Exception:
                states[symbol] = {"state": "NONE", "description": "", "price": None, "rsi": None}

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
