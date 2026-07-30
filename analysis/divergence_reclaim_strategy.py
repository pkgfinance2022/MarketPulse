"""
Divergence Reclaim strategy - a new, standalone engine (deliberately
NOT a change to RSI Divergence or EMA Reclaim - explicit instruction:
"why mix with other working algo, this should be new path, new type
of table, and even new type of alerts").

The user's own description of how they actually read a chart, combining
two things this app already has as SEPARATE engines:

  1. RSI Divergence's own "setup" (see analysis/rsi_divergence_strategy.py):
     RSI touches oversold, bounces, and a second leg forms that still
     retests the oversold zone (SECOND_LEG_OVERSOLD) while price makes
     an equal-or-lower low - the same zone-retest rule just added there
     ("divergence in the middle has less value").
  2. EMA Reclaim's own idea of price closing back above EMA20 after a
     down-move, targeting a MOVING EMA200 (not a fixed R:R exit) - but
     simplified to a single close, not two, and gated on RSI already
     being back above RECLAIM_RSI_LONG (40) on that same bar, per the
     user's own words: "when next time the price close above 20 ema,
     and rsi is also above 40 like, I know that this can be good."

Once EMA200 is reached, any further continuation (crossing EMA200,
RSI crossing 65, making higher highs) is exactly what
analysis/reversal_playbook.py's Path A/B/C already tracks - not
duplicated here, this engine's job ends at "target reached."

LONG-only for now, matching EMA Reclaim's own precedent (only the
down-move-reverses-up case was described with real chart examples;
the mirrored short case isn't implemented until that's actually shown
and validated).
"""

import ta

from providers.yahoo import YahooProvider


class DivergenceReclaimStrategy:

    MIN_HISTORY = 210

    OVERSOLD_TOUCH = 25          # first-leg base condition - same deep touch RSI Divergence/RSI Wave use
    SECOND_LEG_OVERSOLD = 30     # second leg must still retest this zone to count as real divergence
    BOUNCE_MARGIN = 5            # how far RSI has to bounce off the base before a second leg starts forming
    MIN_DIVERGENCE_MARGIN = 3    # second leg RSI must clear the base by this much
    RECLAIM_RSI_LONG = 40        # RSI must already be back above this on the bar price recrosses EMA20

    # Same tolerance EMAReclaimStrategy uses - a single noisy bar back
    # under EMA20 right after entry shouldn't reset the whole wave.
    WAVE_INVALIDATION_STREAK = 3

    @staticmethod
    def _prepare(df):

        close = df["Close"]

        return {
            "close": close,
            "low": df["Low"],
            "ema20": ta.trend.ema_indicator(close, window=20),
            "ema200": ta.trend.ema_indicator(close, window=200),
            "rsi": ta.momentum.rsi(close, window=28),
            "time": df.index,
        }

    @classmethod
    def walk(cls, ind, start, end):
        """
        Phases: WATCHING (price at/above EMA20) -> PULLBACK (price
        below EMA20 at some point; tracks the RSI base/second-leg
        divergence sub-state the same way RSIDivergenceStrategy does,
        but scoped to this one down-move) -> IN_WAVE (entry fires the
        bar price closes back above EMA20 with a locked-in divergence
        AND RSI already past RECLAIM_RSI_LONG) -> back to WATCHING once
        EMA200 is reached (target) or price fails the reclaim for
        WAVE_INVALIDATION_STREAK bars running.

        Deliberately does NOT reset to WATCHING just because price
        pokes back above EMA20 without RSI clearing 40 yet - it keeps
        waiting (still inside PULLBACK) for RSI to catch up, since the
        user's own read was "once price closes above EMA20 AND rsi is
        also above 40", not necessarily on the exact same tick.
        """

        close, low, ema20, ema200, rsi, time_index = (
            ind["close"], ind["low"], ind["ema20"], ind["ema200"], ind["rsi"], ind["time"]
        )

        phase = "WATCHING"

        wave_low = None
        base_rsi = base_price = None
        bounced_from_base = False
        second_leg_rsi = second_leg_price = None
        divergence_locked = False
        below_streak = 0

        trace = []

        for i in range(start, end):

            price = float(close.iloc[i])
            l = float(low.iloc[i])
            e20 = float(ema20.iloc[i])
            e200 = float(ema200.iloc[i])
            r = float(rsi.iloc[i])

            event = None

            if phase == "WATCHING":

                if price < e20:
                    phase = "PULLBACK"
                    wave_low, base_rsi, base_price, bounced_from_base, second_leg_rsi, second_leg_price, divergence_locked = (
                        l, None, None, False, None, None, False
                    )

            elif phase == "PULLBACK":

                wave_low = min(wave_low, l)

                if base_rsi is None:
                    if r <= cls.OVERSOLD_TOUCH:
                        base_rsi, base_price = r, price

                elif not bounced_from_base:
                    if r < base_rsi:
                        base_rsi, base_price = r, price
                    elif r >= base_rsi + cls.BOUNCE_MARGIN:
                        bounced_from_base = True

                elif second_leg_rsi is None or r < second_leg_rsi:
                    second_leg_rsi, second_leg_price = r, price
                    divergence_locked = (
                        second_leg_price <= base_price
                        and second_leg_rsi > base_rsi + cls.MIN_DIVERGENCE_MARGIN
                        and second_leg_rsi <= cls.SECOND_LEG_OVERSOLD
                    )

                if divergence_locked and price > e20 and r > cls.RECLAIM_RSI_LONG:
                    event = "ENTRY_LONG"
                    phase = "IN_WAVE"
                    below_streak = 0

                elif price >= e200:
                    # Ran all the way back to/through EMA200 without
                    # ever confirming a divergence-based reclaim -
                    # nothing left to enter for.
                    phase = "WATCHING"

            elif phase == "IN_WAVE":

                if price >= e200:
                    event = "TARGET_REACHED"
                    phase = "WATCHING"
                    below_streak = 0
                elif price < e20:
                    below_streak += 1
                    if below_streak >= cls.WAVE_INVALIDATION_STREAK:
                        phase = "PULLBACK"
                        wave_low, base_rsi, base_price, bounced_from_base, second_leg_rsi, second_leg_price, divergence_locked = (
                            l, None, None, False, None, None, False
                        )
                        below_streak = 0
                else:
                    below_streak = 0

            trace.append({
                "index": i, "phase": phase, "event": event,
                "price": price, "rsi": r, "ema20": e20, "ema200": e200,
                "wave_low": wave_low, "divergence_locked": divergence_locked,
                "time": time_index[i],
            })

        return trace

    @classmethod
    def run_symbol(cls, symbol, period="730d", interval="1h"):

        df = YahooProvider().history(symbol, interval=interval, period=period)

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
        price = round(last["price"], 4)

        last_event_bar = next((bar for bar in reversed(trace) if bar["event"]), None)
        bars_since_event = len(trace) - 1 - trace.index(last_event_bar) if last_event_bar else None
        recent = bars_since_event is not None and bars_since_event <= 3

        if phase == "WATCHING":
            return f"⚪ Watching — price {price}, no down-move/divergence setup active.", "WATCHING", None

        if phase == "PULLBACK":

            if last["divergence_locked"]:
                return (
                    f"🟡 RSI divergence formed at the low, price {price} — watching for a close back above "
                    f"EMA20 with RSI above {cls.RECLAIM_RSI_LONG} to confirm.",
                    "DIVERGENCE_FORMING",
                    None,
                )

            return f"⚪ Down-move in progress, price {price}, no divergence confirmed yet.", "PULLBACK", None

        if phase == "IN_WAVE":

            if recent and last_event_bar["event"] == "ENTRY_LONG":
                return (
                    f"🟢 Divergence Reclaim confirmed {bars_since_event} bar(s) ago, price {price} — "
                    f"targeting EMA200 ({round(last['ema200'], 4)}).",
                    "ENTRY_LONG",
                    last_event_bar["time"],
                )

            return (
                f"🔵 Riding toward EMA200 ({round(last['ema200'], 4)}) since the confirmed reclaim, price {price}.",
                "IN_WAVE",
                last_event_bar["time"] if last_event_bar else None,
            )

        return f"⚪ Watching — price {price}.", "WATCHING", None

    STATE_LABELS = {
        "NONE": "⚪ No data",
        "WATCHING": "⚪ Watching",
        "PULLBACK": "⚪ Down-move — no divergence yet",
        "DIVERGENCE_FORMING": "🟡 Divergence formed — watching for EMA20 reclaim",
        "ENTRY_LONG": "🟢 LONG entry — Divergence Reclaim confirmed",
        "IN_WAVE": "🔵 Riding to EMA200",
    }
