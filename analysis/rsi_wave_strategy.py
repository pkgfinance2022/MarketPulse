"""
RSI wave strategy.

Implements the specific pattern the user described from a real 1H WTI
Crude chart: RSI touches oversold (~20) -> that's just an alert, not an
entry. Watch what happens next:

  - If RSI's first touch of 65 already arrives at 80+ (jumped straight
    through without pausing near 65), the move already happened -
    don't chase it. ("TOO LATE")
  - If RSI's first touch of 65 lands in the 65-79 zone, that's a clean
    breakout - a valid entry. ("ENTRY - direct")
  - If RSI touches 65, falls back below it, then crosses 65 again
    (an RSI "double bottom" / failure-swing retest), that second cross
    is usually the higher-quality entry. ("ENTRY - retest")
  - After an entry, price tends to keep making a "wave" higher, with
    RSI periodically dipping (without a full reset to 20) as price
    pulls back toward EMA20/EMA200, then resuming - each of those
    dip-and-recross-65 moments is flagged as another entry inside the
    same wave. The wave is invalidated once price closes back below
    EMA200 (trend broken), which resets everything to watching.

Mirrored exactly for downtrends: touch ~80 is the alert, 35 is the
intent/confirmation level (100-65), 20 is the "too late" level (100-80).

This is deliberately NOT the same as the earlier, simpler
pullback_strategy.py (which required a strict EMA50>EMA200 trend
filter before counting any RSI touch). This version has no separate
trend gate on the alert itself - the RSI failure-swing pattern is
watched everywhere, and EMA200 is only used to invalidate an active
wave, matching what was actually described.
"""

import ta

from providers.yahoo import YahooProvider


class RSIWaveStrategy:

    MIN_HISTORY = 210

    # "~20"/"~80" was meant with tolerance, not an exact wall - a real
    # trade got missed because RSI bottomed at 22.3, 2.3 points short of
    # a hard 20 cutoff. Loosened to 25/75 so genuinely deep oversold/
    # overbought reads aren't excluded on a technicality.
    OVERSOLD_TOUCH = 25
    OVERBOUGHT_TOUCH = 75

    INTENT_LONG = 65
    INTENT_SHORT = 35

    TOO_LATE_LONG = 80
    TOO_LATE_SHORT = 20

    # Entries fire right as price is reclaiming EMA200, so it sits only
    # fractionally above/below it at that moment - a single noisy bar
    # flipping back across the line would otherwise kill the wave
    # within an hour of every single entry. Require it to stay on the
    # wrong side for several consecutive bars before calling it a real
    # trend break, not a same-bar touch.
    WAVE_INVALIDATION_STREAK = 3

    @staticmethod
    def _prepare(df):

        close = df["Close"]

        # RSI on OHLC4 (typical price), not raw Close - see
        # analysis/reversal_playbook.py's _prepare_1h for the real-data
        # case that motivated this. EMA/price levels still use Close.
        typical_price = (df["Open"] + df["High"] + df["Low"] + close) / 4

        return {
            "close": close,
            "ema20": ta.trend.ema_indicator(close, window=20),
            "ema200": ta.trend.ema_indicator(close, window=200),
            "rsi": ta.momentum.rsi(typical_price, window=14),
            "time": df.index,
        }

    @classmethod
    def walk(cls, ind, start, end):
        """
        Returns the full per-bar state trace: phase (the ongoing
        condition) and event (fires only on the specific bar something
        notable happens - an entry, or a "too late" skip).
        """

        close, ema200, rsi, time_index = ind["close"], ind["ema200"], ind["rsi"], ind["time"]

        phase = "WATCHING"
        pulled_back_long = False
        pulled_back_short = False
        below_ema_streak = 0
        above_ema_streak = 0

        trace = []

        prev_rsi = float(rsi.iloc[start - 1])

        for i in range(start, end):

            r = float(rsi.iloc[i])
            price = float(close.iloc[i])
            e200 = float(ema200.iloc[i])

            event = None

            if phase == "WATCHING":

                if r <= cls.OVERSOLD_TOUCH:
                    phase = "ALERT_LONG"
                elif r >= cls.OVERBOUGHT_TOUCH:
                    phase = "ALERT_SHORT"

            elif phase == "ALERT_LONG":

                if r >= cls.INTENT_LONG and prev_rsi < cls.INTENT_LONG:

                    if r >= cls.TOO_LATE_LONG:
                        event = "TOO_LATE_LONG"
                        phase = "WATCHING"
                    else:
                        event = "ENTRY_LONG_DIRECT"
                        phase = "WAVE_LONG"
                        pulled_back_long = False
                        below_ema_streak = 0

            elif phase == "WAVE_LONG":

                if r < cls.INTENT_LONG:
                    pulled_back_long = True

                if pulled_back_long and r >= cls.INTENT_LONG and prev_rsi < cls.INTENT_LONG:
                    event = "ENTRY_LONG_RETEST"
                    pulled_back_long = False

                if price < e200:
                    below_ema_streak += 1
                else:
                    below_ema_streak = 0

                if below_ema_streak >= cls.WAVE_INVALIDATION_STREAK:
                    phase = "WATCHING"
                    below_ema_streak = 0
                elif r <= cls.OVERSOLD_TOUCH:
                    phase = "ALERT_LONG"
                    pulled_back_long = False
                    below_ema_streak = 0

            elif phase == "ALERT_SHORT":

                if r <= cls.INTENT_SHORT and prev_rsi > cls.INTENT_SHORT:

                    if r <= cls.TOO_LATE_SHORT:
                        event = "TOO_LATE_SHORT"
                        phase = "WATCHING"
                    else:
                        event = "ENTRY_SHORT_DIRECT"
                        phase = "WAVE_SHORT"
                        pulled_back_short = False
                        above_ema_streak = 0

            elif phase == "WAVE_SHORT":

                if r > cls.INTENT_SHORT:
                    pulled_back_short = True

                if pulled_back_short and r <= cls.INTENT_SHORT and prev_rsi > cls.INTENT_SHORT:
                    event = "ENTRY_SHORT_RETEST"
                    pulled_back_short = False

                if price > e200:
                    above_ema_streak += 1
                else:
                    above_ema_streak = 0

                if above_ema_streak >= cls.WAVE_INVALIDATION_STREAK:
                    phase = "WATCHING"
                    above_ema_streak = 0
                elif r >= cls.OVERBOUGHT_TOUCH:
                    phase = "ALERT_SHORT"
                    pulled_back_short = False
                    above_ema_streak = 0

            trace.append(
                {
                    "index": i,
                    "phase": phase,
                    "event": event,
                    "rsi": r,
                    "price": price,
                    "time": time_index[i],
                }
            )

            prev_rsi = r

        return trace

    @classmethod
    def run_symbol(cls, symbol, period="730d"):
        """Returns (trace, df) for a symbol, or (None, None) if there's not enough history."""

        df = YahooProvider().history(symbol, interval="1h", period=period)

        if df.empty or len(df) < cls.MIN_HISTORY + 1:
            return None, None

        ind = cls._prepare(df)
        trace = cls.walk(ind, cls.MIN_HISTORY, len(df))

        return trace, df

    @classmethod
    def describe(cls, trace):
        """Plain-English read of the CURRENT state, for a live screener/status box."""

        if not trace:
            return "Not enough 1H history to evaluate this instrument yet.", "NONE", None

        last = trace[-1]
        phase = last["phase"]
        rsi = round(last["rsi"], 2)

        last_event_bar = next(
            (bar for bar in reversed(trace) if bar["event"]),
            None,
        )

        bars_since_event = (
            len(trace) - 1 - trace.index(last_event_bar)
            if last_event_bar
            else None
        )

        recent = bars_since_event is not None and bars_since_event <= 3

        if phase == "WATCHING":

            if recent and last_event_bar["event"] in ("TOO_LATE_LONG", "TOO_LATE_SHORT"):
                direction = "up" if last_event_bar["event"] == "TOO_LATE_LONG" else "down"
                return (
                    f"🟤 RSI just ran straight through without pausing (already moved {direction}, RSI {rsi}) — "
                    f"too late to chase this one. Watching for the next setup.",
                    "TOO_LATE",
                    last_event_bar["time"],
                )

            return f"⚪ Watching — RSI {rsi}, not at an extreme, no setup active.", "WATCHING", None

        if phase == "ALERT_LONG":
            return (
                f"🟡 Alert (LONG) — RSI touched ≤{cls.OVERSOLD_TOUCH} (oversold) earlier, "
                f"now recovering to {rsi}. Watching for a clean cross above {cls.INTENT_LONG} to confirm.",
                "ALERT_LONG",
                last_event_bar["time"] if last_event_bar else None,
            )

        if phase == "ALERT_SHORT":
            return (
                f"🟠 Alert (SHORT) — RSI touched ≥{cls.OVERBOUGHT_TOUCH} (overbought) earlier, "
                f"now cooling to {rsi}. Watching for a clean cross below {cls.INTENT_SHORT} to confirm.",
                "ALERT_SHORT",
                last_event_bar["time"] if last_event_bar else None,
            )

        if phase == "WAVE_LONG":

            if recent and last_event_bar["event"] in ("ENTRY_LONG_DIRECT", "ENTRY_LONG_RETEST"):
                kind = "retest — higher quality" if last_event_bar["event"] == "ENTRY_LONG_RETEST" else "direct"
                bars_ago = bars_since_event
                return (
                    f"🟢 LONG entry {bars_ago} bar(s) ago ({kind}), RSI {rsi}. Riding the wave — "
                    f"price still above EMA200.",
                    "ENTRY_LONG",
                    last_event_bar["time"],
                )

            return (
                f"🔵 LONG wave in progress — RSI {rsi}, price above EMA200. "
                f"Watching for the next pullback-and-resume.",
                "WAVE_LONG",
                last_event_bar["time"] if last_event_bar else None,
            )

        if phase == "WAVE_SHORT":

            if recent and last_event_bar["event"] in ("ENTRY_SHORT_DIRECT", "ENTRY_SHORT_RETEST"):
                kind = "retest — higher quality" if last_event_bar["event"] == "ENTRY_SHORT_RETEST" else "direct"
                bars_ago = bars_since_event
                return (
                    f"🔴 SHORT entry {bars_ago} bar(s) ago ({kind}), RSI {rsi}. Riding the wave — "
                    f"price still below EMA200.",
                    "ENTRY_SHORT",
                    last_event_bar["time"],
                )

            return (
                f"🟣 SHORT wave in progress — RSI {rsi}, price below EMA200. "
                f"Watching for the next pullback-and-resume.",
                "WAVE_SHORT",
                last_event_bar["time"] if last_event_bar else None,
            )

        return f"⚪ Watching — RSI {rsi}.", "WATCHING", None

    # Each state gets its own emoji - ALERT_LONG/ALERT_SHORT previously
    # shared the same yellow circle, making them indistinguishable at a
    # glance in the scanner. LONG family reads yellow/green/blue, SHORT
    # family reads orange/red/purple; TOO_LATE gets its own brown so it
    # doesn't collide with either alert color.
    STATE_LABELS = {
        "NONE": "⚪ No data",
        "WATCHING": "⚪ Watching",
        "TOO_LATE": "🟤 Too late — skip",
        "ALERT_LONG": "🟡 Alert (LONG) — watching for 65 cross",
        "ALERT_SHORT": "🟠 Alert (SHORT) — watching for 35 cross",
        "ENTRY_LONG": "🟢 LONG entry — in wave",
        "WAVE_LONG": "🔵 LONG wave — watching for pullback",
        "ENTRY_SHORT": "🔴 SHORT entry — in wave",
        "WAVE_SHORT": "🟣 SHORT wave — watching for pullback",
    }

    @classmethod
    def short_label(cls, trace):
        """One-line label for a screener table cell (no paragraph)."""

        _, state, _ = cls.describe(trace)

        return cls.STATE_LABELS.get(state, "⚪ Watching")
