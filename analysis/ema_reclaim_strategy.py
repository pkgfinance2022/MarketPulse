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
EUR/USD, USD/JPY) before this was built. Real bug found AFTER first
shipping this, live: it fired on Russell 2000 while chopping sideways,
EMA20/EMA200 tangled only ~0.2% apart - not the deep, stretched
down-move every one of the user's own charts showed. Fixed with
MIN_DOWNTREND_DIVERGENCE_PCT (reusing ReversalPlaybook's own
"meaningfully far apart" threshold and concept) - the down-move has to
have pushed EMA20 at least this far below EMA200 at some point before
a reclaim counts as real, not just noise. Requiring that dropped trade
count sharply and even lowered the raw win rate, but nearly 9x'd the
average return - fewer, deeper, more real setups over more frequent,
shallow ones. Current, authoritative numbers live in
dashboard/services/conviction_ranking.py's WIN_RATE_LOOKUP - surfaced
in Command Center honestly, not overstated either direction.

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

    # Real, reported bug: a confirmed 2-candle reclaim fired on
    # Russell 2000 while it was chopping sideways, EMA20 and EMA200
    # tangled only ~0.2% apart - not the deep, stretched down-move
    # every one of the user's own charts showed (Gold, all with
    # EMA20/EMA200 several percent apart at the low). Requires the
    # down-move to have pushed EMA20 at least this far below EMA200 at
    # some point before a reclaim counts - same "meaningfully far
    # apart" concept and threshold ReversalPlaybook.FAR_THRESHOLD_PCT
    # already uses in this codebase, reused here rather than inventing
    # a new number. Calibrated against every real historical entry
    # this backtested: most Russell-2000-style chop sat under 1%,
    # genuine reversals mostly cleared 2%+.
    MIN_DOWNTREND_DIVERGENCE_PCT = 2.0

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
        max_divergence_pct = 0.0
        below_streak = 0

        trace = []

        def divergence_pct(e20, e200):
            return abs(e20 - e200) / e200 * 100 if e200 else 0.0

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
                    max_divergence_pct = divergence_pct(e20, e200)

            elif phase == "PULLBACK":

                if above20:
                    phase = "RECLAIM_ALERT"
                else:
                    wave_low = min(wave_low, l)
                    rsi_touched_deep = rsi_touched_deep or r <= cls.RSI_DEEP_TOUCH
                    max_divergence_pct = max(max_divergence_pct, divergence_pct(e20, e200))

            elif phase == "RECLAIM_ALERT":

                if above20:

                    if price >= e200:
                        # Already back above EMA200 too by the
                        # confirming bar - the reversion already
                        # happened, nothing left to enter for.
                        phase = "WATCHING"
                        wave_low = None
                        rsi_touched_deep = False
                        max_divergence_pct = 0.0
                    elif max_divergence_pct < cls.MIN_DOWNTREND_DIVERGENCE_PCT:
                        # Reclaim confirmed, but the preceding down-move
                        # never got deep enough - sideways chop (e.g.
                        # Russell 2000 tangled ~0.2% apart), not the
                        # real, stretched downtrend this setup needs.
                        # Reset silently - no signal, same as if nothing
                        # happened.
                        phase = "WATCHING"
                        wave_low = None
                        rsi_touched_deep = False
                        max_divergence_pct = 0.0
                    else:
                        event = "ENTRY_LONG"
                        phase = "IN_WAVE"
                        below_streak = 0

                else:
                    # Failed reclaim - back below EMA20 on the bar
                    # that was supposed to confirm it.
                    phase = "PULLBACK"
                    wave_low = min(wave_low, l)
                    rsi_touched_deep = rsi_touched_deep or r <= cls.RSI_DEEP_TOUCH
                    max_divergence_pct = max(max_divergence_pct, divergence_pct(e20, e200))

            elif phase == "IN_WAVE":

                if price >= e200:
                    event = "TARGET_REACHED"
                    phase = "WATCHING"
                    wave_low = None
                    rsi_touched_deep = False
                    max_divergence_pct = 0.0
                    below_streak = 0
                elif not above20:
                    below_streak += 1
                    if below_streak >= cls.WAVE_INVALIDATION_STREAK:
                        phase = "PULLBACK"
                        wave_low = l
                        rsi_touched_deep = r <= cls.RSI_DEEP_TOUCH
                        max_divergence_pct = divergence_pct(e20, e200)
                        below_streak = 0
                else:
                    below_streak = 0

            trace.append({
                "index": i, "phase": phase, "event": event,
                "price": price, "rsi": r, "ema20": e20, "ema200": e200,
                "wave_low": wave_low, "rsi_touched_deep": rsi_touched_deep,
                "max_divergence_pct": round(max_divergence_pct, 2),
                "time": time_index[i],
            })

        return trace

    @classmethod
    def run_symbol(cls, symbol, period="730d", interval="1h"):
        """
        Returns (trace, df) for a symbol, or (None, None) if there's
        not enough history. The state machine itself doesn't care what
        `interval` represents (1h or 1d bars) - MIN_HISTORY is a bar
        count, and EMA200 needs ~200 bars to be meaningful regardless
        of what each bar spans. interval="1d" is what the Daily variant
        (see DailyEMAReclaimStrategy below) uses.
        """

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


class DailyEMAReclaimStrategy(EMAReclaimStrategy):
    """
    Daily-bar variant - same state machine, same concept (2 consecutive
    closes back above EMA20 after a real down-move, targeting EMA200),
    just running on Daily bars instead of Hourly. Explicit follow-up
    request ("I also want the logic to run in macro daily") after the
    user showed a real Daily chart (Booking Holdings) where the same
    pattern was playing out on that timeframe.

    A separate class (not just a different `interval` argument at the
    call site) so every caller - the scan, the status service, the
    backtester, Telegram - has one unambiguous name for "the Daily
    one", matching how RSIWaveStrategy/ReversalPlaybook and
    DailyWeeklyReversalPlaybook are separate classes in this codebase
    even though the underlying concept is related.

    MIN_DOWNTREND_DIVERGENCE_PCT is NOT necessarily the same real
    number on Daily bars (20/200-day EMAs move on a very different
    scale than 20/200-HOUR EMAs) - see
    analysis/backtester.py's backtest_daily_ema_reclaim, which
    re-validates this threshold on Daily data rather than assuming the
    Hourly-calibrated number just carries over.
    """

    @classmethod
    def run_symbol(cls, symbol, period="730d", interval="1d"):
        return super().run_symbol(symbol, period=period, interval=interval)
