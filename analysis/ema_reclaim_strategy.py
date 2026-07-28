"""
EMA20 reclaim strategy.

User's own real-chart observation (Gold spot, 1H, three separate
examples): after a real down move (price below EMA20, having pulled
well away from EMA200), a reversal that closes back above EMA20 for
TWO consecutive bars - not just one, which is a common fakeout - tends
to keep running until it reaches EMA200. The deeper the preceding down
move, the more reliable this reads: RSI on the same 1H bars typically
touched <=22 (oversold) somewhere during it, so that's tracked and
surfaced as context, not as a hard gate - backtesting this filter
(analysis/backtester.py's backtest_ema_reclaim) showed it doesn't
uniformly improve results across every instrument (helps Gold and
NASDAQ specifically, hurts a couple of others on small samples), so
gating on it would just be discarding real signals on unproven
grounds.

Backtested on ~2 years of real 1H data across 9 instruments (Global
Indices: major US/international equity indices, Gold, Silver, Oil,
EUR/USD, USD/JPY) before this was built: ~59% of confirmed reclaims
went on to touch EMA200 before falling back to the down-move's own
low. That win rate is real, but the average trade is close to
breakeven once the stop (the down-move's low) and target (EMA200) are
priced in - built anyway per explicit instruction, with the same
honest stats surfaced in Command Center as every other engine (see
dashboard/services/conviction_ranking.py's WIN_RATE_LOOKUP) rather
than overstating it.

LONG-only - the backtest only validated the down-move-reverses-up
case the user actually described and showed real charts for; the
mirrored SHORT case (uptrend reverses down, closes below EMA20 twice,
targets a fall to EMA200) was never backtested and isn't implemented
here.
"""

import ta

from providers.yahoo import YahooProvider


class EMAReclaimStrategy:

    MIN_HISTORY = 210

    RSI_DEEP_TOUCH = 22   # the level the user's own downtrends were observed touching - tracked as context, not a gate

    # Same tolerance RSIWaveStrategy uses before calling an active wave
    # "broken" - a single noisy bar dipping back under EMA20 right after
    # entry shouldn't reset the whole setup.
    WAVE_INVALIDATION_STREAK = 3

    @staticmethod
    def _prepare(df):

        close = df["Close"]

        return {
            "close": close,
            "low": df["Low"],
            "ema20": ta.trend.ema_indicator(close, window=20),
            "ema200": ta.trend.ema_indicator(close, window=200),
            "rsi": ta.momentum.rsi(close, window=14),
            "time": df.index,
        }

    @classmethod
    def walk(cls, ind, start, end):
        """
        Returns the full per-bar state trace: phase (the ongoing
        condition) and event (fires only on the bar a reclaim actually
        confirms, or the wave resolves).

        Phases: WATCHING (price at/above EMA20, no setup) -> PULLBACK
        (price below EMA20, tracking the down-move's low and whether
        RSI has touched RSI_DEEP_TOUCH) -> RECLAIM_ALERT (one bar
        closed back above EMA20, unconfirmed) -> IN_WAVE (a second
        consecutive close above EMA20 confirmed it, entry fires here)
        -> back to WATCHING once EMA200 is touched (target reached) or
        price closes back below EMA20 for WAVE_INVALIDATION_STREAK
        bars running (setup failed).
        """

        close, low, ema20, ema200, rsi, time_index = (
            ind["close"], ind["low"], ind["ema20"], ind["ema200"], ind["rsi"], ind["time"]
        )

        phase = "WATCHING"
        wave_low = None
        rsi_touched_deep = False
        below_streak = 0

        trace = []

        for i in range(start, end):

            price = float(close.iloc[i])
            l = float(low.iloc[i])
            e20 = float(ema20.iloc[i])
            e200 = float(ema200.iloc[i])
            r = float(rsi.iloc[i])
            above20 = price > e20

            event = None

            if phase == "WATCHING":

                if not above20:
                    phase = "PULLBACK"
                    wave_low = l
                    rsi_touched_deep = r <= cls.RSI_DEEP_TOUCH

            elif phase == "PULLBACK":

                if above20:
                    phase = "RECLAIM_ALERT"
                else:
                    wave_low = min(wave_low, l)
                    rsi_touched_deep = rsi_touched_deep or r <= cls.RSI_DEEP_TOUCH

            elif phase == "RECLAIM_ALERT":

                if above20:

                    if price < e200:
                        event = "ENTRY_LONG"
                        phase = "IN_WAVE"
                        below_streak = 0
                    else:
                        # Already back above EMA200 too by the
                        # confirming bar - the reversion already
                        # happened, nothing left to enter for.
                        phase = "WATCHING"
                        wave_low = None
                        rsi_touched_deep = False

                else:
                    # Failed reclaim - back below EMA20 on the bar
                    # that was supposed to confirm it.
                    phase = "PULLBACK"
                    wave_low = min(wave_low, l)
                    rsi_touched_deep = rsi_touched_deep or r <= cls.RSI_DEEP_TOUCH

            elif phase == "IN_WAVE":

                if price >= e200:
                    event = "TARGET_REACHED"
                    phase = "WATCHING"
                    wave_low = None
                    rsi_touched_deep = False
                    below_streak = 0
                elif not above20:
                    below_streak += 1
                    if below_streak >= cls.WAVE_INVALIDATION_STREAK:
                        phase = "PULLBACK"
                        wave_low = l
                        rsi_touched_deep = r <= cls.RSI_DEEP_TOUCH
                        below_streak = 0
                else:
                    below_streak = 0

            trace.append({
                "index": i, "phase": phase, "event": event,
                "price": price, "rsi": r, "ema20": e20, "ema200": e200,
                "wave_low": wave_low, "rsi_touched_deep": rsi_touched_deep,
                "time": time_index[i],
            })

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
        price = round(last["price"], 4)

        last_event_bar = next((bar for bar in reversed(trace) if bar["event"]), None)
        bars_since_event = len(trace) - 1 - trace.index(last_event_bar) if last_event_bar else None
        recent = bars_since_event is not None and bars_since_event <= 3

        rsi_note = " (RSI touched oversold during the down-move)" if last["rsi_touched_deep"] else ""

        if phase == "WATCHING":
            return f"⚪ Watching — price {price}, no down-move/reclaim setup active.", "WATCHING", None

        if phase == "PULLBACK":
            return (
                f"🟡 Down-move in progress, price {price} below EMA20{rsi_note} — "
                f"watching for a close back above EMA20.",
                "PULLBACK",
                None,
            )

        if phase == "RECLAIM_ALERT":
            return (
                f"🟠 First close back above EMA20 (price {price}){rsi_note} — "
                f"watching for a second consecutive close above EMA20 to confirm.",
                "RECLAIM_ALERT",
                None,
            )

        if phase == "IN_WAVE":

            if recent and last_event_bar["event"] == "ENTRY_LONG":
                return (
                    f"🟢 EMA20 reclaim confirmed {bars_since_event} bar(s) ago, price {price}{rsi_note} — "
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
        "PULLBACK": "🟡 Down-move — watching for EMA20 reclaim",
        "RECLAIM_ALERT": "🟠 Alert — 1st close above EMA20, needs confirmation",
        "ENTRY_LONG": "🟢 LONG entry — EMA20 reclaim confirmed",
        "IN_WAVE": "🔵 Riding to EMA200",
    }

    @classmethod
    def short_label(cls, trace):
        """One-line label for a screener table cell (no paragraph)."""

        _, state, _ = cls.describe(trace)

        return cls.STATE_LABELS.get(state, "⚪ Watching")
