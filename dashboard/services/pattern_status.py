"""
Chart pattern status service.

Unlike RSI Wave/Divergence, candlestick/chart patterns aren't a
persistent state machine - each pattern either fired on a given daily
bar or it didn't. This just runs the validated detectors (see
analysis/candlestick_patterns.py's own docstrings for why only these
two made the cut - Evening Star, Dark Cloud Cover, Morning Star, and
Double Top all backtested net-negative on real Global Indices data)
against the most recent bars and reports whichever one fired most
recently, if any.

Global Indices only (explicit scope) - see dashboard/app.py's wiring.
Both surviving patterns are bullish-only (LONG) - nothing here fires
short.

Stop/target: Double Bottom carries its own measured-move levels
(backtested to genuinely outperform the generic formula for this
pattern specifically). Piercing Pattern uses the same shared
ATR/support-resistance formula RSI Wave uses - that's what it was
actually validated with.
"""

from concurrent.futures import ThreadPoolExecutor

import ta

from analysis.candlestick_patterns import find_double_bottom, find_piercing_pattern
from dashboard.services.rsi_wave_status import RSIWaveStatusService
from providers.yahoo import YahooProvider

SCREEN_WORKERS = 3

RECENT_BARS = 2   # a pattern confirmed more than 2 daily bars ago isn't "just happened" anymore

PATTERN_STATE_LABELS = {
    "NONE": "⚪ No data",
    "WATCHING": "⚪ Watching",
    "PIERCING_PATTERN": "🟢 Piercing Pattern — bullish entry",
    "DOUBLE_BOTTOM": "🟢 Double Bottom — bullish entry",
}


class ChartPatternStatusService:

    ATR_WINDOW = RSIWaveStatusService.ATR_WINDOW
    SUPPORT_RESISTANCE_WINDOW = RSIWaveStatusService.SUPPORT_RESISTANCE_WINDOW

    @staticmethod
    def _detect(df):
        """
        Runs both validated detectors and returns whichever pattern's
        most recent confirmation is the freshest, or (None, None).
        """

        piercing = find_piercing_pattern(df)
        double_bottom = find_double_bottom(df)

        candidates = []

        if piercing:
            last = piercing[-1]
            if len(df) - 1 - last["index"] <= RECENT_BARS:
                candidates.append(("PIERCING_PATTERN", last))

        if double_bottom:
            last = double_bottom[-1]
            if len(df) - 1 - last["index"] <= RECENT_BARS:
                candidates.append(("DOUBLE_BOTTOM", last))

        if not candidates:
            return None, None

        candidates.sort(key=lambda c: c[1]["index"], reverse=True)
        return candidates[0]

    @classmethod
    def analyse(cls, symbol, period="2y"):

        df = YahooProvider().history(symbol, interval="1d", period=period)

        if df.empty or len(df) < 60:
            return None

        state, event = cls._detect(df)
        last_close = float(df["Close"].iloc[-1])

        if state is None:
            return {
                "symbol": symbol, "state": "WATCHING", "description": "No pattern confirmed recently.",
                "price": last_close, "stop_target": None, "event_time": None,
            }

        idx = event["index"]
        price = float(df["Close"].iloc[idx])

        if state == "DOUBLE_BOTTOM":
            # Carries its own measured-move levels.
            risk = abs(price - event["stop"])
            stop_target = {
                "stop": round(event["stop"], 4), "target1": round(event["target"], 4),
                "risk_reward": round(abs(event["target"] - price) / risk, 2) if risk else 0.0,
            }
        else:
            # Piercing Pattern - same shared formula it was validated with.
            high, low, close = df["High"], df["Low"], df["Close"]
            atr = ta.volatility.average_true_range(high, low, close, window=cls.ATR_WINDOW)
            support = low.rolling(cls.SUPPORT_RESISTANCE_WINDOW).min()
            resistance = high.rolling(cls.SUPPORT_RESISTANCE_WINDOW).max()
            atr_val = float(atr.iloc[idx]) if not (atr.empty or atr.isna().iloc[idx]) else 0.0
            sup_val = float(support.iloc[idx]) if not support.isna().iloc[idx] else price
            res_val = float(resistance.iloc[idx]) if not resistance.isna().iloc[idx] else price
            stop_target = RSIWaveStatusService._stop_target("LONG", price, sup_val, res_val, atr_val)

        return {
            "symbol": symbol, "state": state,
            "description": f"{PATTERN_STATE_LABELS[state]} confirmed {event['time'].strftime('%b %d')}.",
            "price": price, "stop_target": stop_target, "event_time": event["time"],
        }

    @classmethod
    def _screen_one(cls, symbol, period):

        try:
            result = cls.analyse(symbol, period=period)

            if result is None:
                return symbol, {"state": "NONE", "description": "", "price": None, "event_time": None}

            return symbol, result

        except Exception:
            return symbol, {"state": "NONE", "description": "", "price": None, "event_time": None}

    @classmethod
    def screen_states(cls, symbols, period="2y"):

        states = {}

        with ThreadPoolExecutor(max_workers=SCREEN_WORKERS) as executor:

            futures = [executor.submit(cls._screen_one, symbol, period) for symbol in symbols]

            for future in futures:
                symbol, info = future.result()
                states[symbol] = info

        return states
