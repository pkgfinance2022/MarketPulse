"""
RSI regular-divergence strategy.

Enters on a classic bullish/bearish "regular" RSI divergence - price
makes an equal-or-lower low while RSI makes a meaningfully higher low
(mirrored: equal-or-higher high with a lower high, for shorts) - a
pattern independently verified against real chart examples (AAPL,
TSLA, GC=F, BTC-USD all matched the textbook shape exactly: two swing
points in price and RSI, connected trendlines sloping opposite ways).

The pattern, bar by bar:
  1. RSI touches oversold (<= OVERSOLD_TOUCH) or overbought
     (>= OVERBOUGHT_TOUCH) - this is the base, and keeps updating to
     whatever the deepest point turns out to be.
  2. RSI bounces away from the base by BOUNCE_MARGIN points - the base
     is locked in, and a second leg starts.
  3. The second leg's own lowest/highest point is tracked as it forms.
  4. Once RSI turns back off that second-leg extreme, check whether it
     actually diverges from the base (price equal-or-worse, RSI
     meaningfully better by MIN_DIVERGENCE_MARGIN). If not, there's no
     valid setup here - keep watching, no entry.
  5. If it does diverge, wait for RSI to recover CONFIRM_MARGIN points
     off that second-leg extreme before entering - not the instant it
     turns (too early, mostly noise: backtested at 22.4% win rate),
     and not waiting for a much later confirmation like RSI Wave's own
     65-cross (by then price has already run well past the actual
     divergence point). This margin is what separates this from an
     earlier, weaker version that wagered fully on a 40-cross instead -
     that version showed a flat-to-negative average return; requiring
     divergence AND a modest recovery off the second leg is what turned
     it consistently positive across a range of confirmation margins
     (backtested 1-8 points, positive avg return throughout, peaking
     near CONFIRM_MARGIN=5).

Mirrored for downtrends: oversold becomes overbought, "higher low"
becomes "lower high."

Stop/target reuses RSIWaveStatusService._stop_target exactly (same
risk model RSI Wave already uses) - tested tightening the stop to the
second-leg swing point directly, which made results worse, so left as
the existing ATR/support-resistance formula.

Backtested via analysis/backtester.py's backtest_rsi_divergence. Live
on Global Indices (Command Center + Telegram) since that first pass.

REAL BUG FOUND AND FIXED (explicit user correction): the second leg's
own low/high was tracked with no floor/ceiling of its own - as long as
it was "higher than base + MIN_DIVERGENCE_MARGIN", a second leg that
only made it back to RSI 45 counted as a valid bullish divergence, even
though 45 is nowhere near oversold. "Divergence in the middle has less
value" - added SECOND_LEG_OVERSOLD (30) / SECOND_LEG_OVERBOUGHT (60) so
the second leg has to actually retest the extreme zone before a
divergence locks in, not just be numerically higher/lower than the
base. See DailyRSIDivergenceStrategy for the Daily-bar variant.
"""

import pandas as pd
import ta

from analysis.rsi_wave_strategy import RSIWaveStrategy
from providers.yahoo import YahooProvider


class RSIDivergenceStrategy:

    MIN_HISTORY = 210

    OVERSOLD_TOUCH = RSIWaveStrategy.OVERSOLD_TOUCH      # 25 - same base condition as the Wave engine
    OVERBOUGHT_TOUCH = RSIWaveStrategy.OVERBOUGHT_TOUCH  # 75

    # How far RSI has to bounce away from the base before a later
    # pullback counts as a genuine "second leg" rather than just noise
    # sitting at the bottom.
    BOUNCE_MARGIN = 5

    # A second-leg low that's only 0.1 RSI points "higher" than the
    # base isn't a real divergence, it's rounding noise - require a
    # meaningful gap before calling it the textbook higher-low/
    # lower-high pattern.
    MIN_DIVERGENCE_MARGIN = 3

    # Explicit real-world correction: a second leg that bottoms out at,
    # say, RSI 45 is technically "higher" than an oversold base, but
    # that's divergence forming in the middle of the range - much
    # weaker than one that still retests oversold/overbought territory
    # before turning. Require the second leg itself to still sit in the
    # zone for the divergence to count at all.
    SECOND_LEG_OVERSOLD = 30
    SECOND_LEG_OVERBOUGHT = 60

    # How far above (long) / below (short) the base price the second
    # leg is still allowed to sit and count as "equal-or-lower" - 0
    # means strictly enforce a real lower low/higher high (this class's
    # own 1H-calibrated behavior). Daily/Weekly bars swing far enough
    # that a real divergence often shows up as a FLAT-to-slightly-higher
    # price with clearly rising RSI lows (real example: MSFT Weekly,
    # price ~$356 -> ~$373, a real divergence despite the ~5% higher
    # second touch) rather than a strict lower low - see
    # StockRSIDivergenceStrategy, which overrides this.
    PRICE_TOLERANCE_PCT = 0.0

    # How far RSI has to recover off the second-leg extreme before
    # entering - backtested sweep of 1-8 points all gave a positive
    # average return (unlike waiting for a 40/60 cross, which didn't),
    # peaking around this value.
    CONFIRM_MARGIN = 5

    # How close the second-leg swing point has to sit to the 200 EMA
    # to count as "taking the 200 EMA as support/resistance" - an extra
    # confluence note on top of the divergence itself (not a
    # requirement to enter, just a higher-confidence flag), matching
    # the same idea RSI Wave and Reversal Playbook already use EMA200
    # for.
    EMA200_CONFLUENCE_PCT = 0.5

    @staticmethod
    def _prepare(df):

        close = df["Close"]
        typical_price = (df["Open"] + df["High"] + df["Low"] + close) / 4

        return {
            "close": close,
            "high": df["High"],
            "low": df["Low"],
            "ema200": ta.trend.ema_indicator(close, window=200),
            "rsi": ta.momentum.rsi(typical_price, window=28),
            "time": df.index,
        }

    @classmethod
    def walk(cls, ind, start, end):

        close, rsi, ema200, time_index = ind["close"], ind["rsi"], ind["ema200"], ind["time"]

        phase = "WATCHING"

        # base_rsi/base_price: the first leg's low - keeps updating to
        # any new lower reading until RSI bounces away from it.
        # second_leg_rsi/second_leg_price: once bounced, the lowest
        # point of whatever comes next - deliberately allowed to be
        # HIGHER than the base (that's the divergence case), tracked
        # regardless of direction relative to the base. second_leg_ema200
        # is the 200 EMA reading at that same second-leg bar, purely for
        # the support/resistance confluence check at entry time.
        base_rsi = base_price = None
        bounced_from_base = False
        second_leg_rsi = second_leg_price = second_leg_ema200 = None
        divergence_locked = False

        trace = []
        prev_rsi = float(rsi.iloc[start - 1])

        for i in range(start, end):

            r = float(rsi.iloc[i])
            price = float(close.iloc[i])
            event = None
            divergence_points = None

            if phase == "WATCHING":

                if r <= cls.OVERSOLD_TOUCH:
                    phase = "BASE_LONG"
                    base_rsi, base_price = r, price
                    bounced_from_base = False
                    second_leg_rsi = second_leg_price = second_leg_ema200 = None
                    divergence_locked = False

                elif r >= cls.OVERBOUGHT_TOUCH:
                    phase = "BASE_SHORT"
                    base_rsi, base_price = r, price
                    bounced_from_base = False
                    second_leg_rsi = second_leg_price = second_leg_ema200 = None
                    divergence_locked = False

            elif phase == "BASE_LONG":

                if not bounced_from_base:

                    if r < base_rsi:
                        base_rsi, base_price = r, price
                    elif r >= base_rsi + cls.BOUNCE_MARGIN:
                        bounced_from_base = True

                elif second_leg_rsi is None or r < second_leg_rsi:
                    second_leg_rsi, second_leg_price = r, price
                    second_leg_ema200 = float(ema200.iloc[i]) if pd.notna(ema200.iloc[i]) else None
                    divergence_locked = (
                        second_leg_price <= base_price * (1 + cls.PRICE_TOLERANCE_PCT / 100)
                        and second_leg_rsi > base_rsi + cls.MIN_DIVERGENCE_MARGIN
                        and second_leg_rsi <= cls.SECOND_LEG_OVERSOLD
                    )

                elif divergence_locked and r >= second_leg_rsi + cls.CONFIRM_MARGIN:

                    event = "ENTRY_LONG_DIVERGENCE"
                    divergence_points = {
                        "base_rsi": base_rsi, "base_price": base_price,
                        "second_leg_rsi": second_leg_rsi, "second_leg_price": second_leg_price,
                        "ema200_support": cls._near_ema200(second_leg_price, second_leg_ema200),
                    }
                    phase = "WATCHING"

                if phase == "BASE_LONG" and r >= cls.OVERBOUGHT_TOUCH:
                    # Whipsawed straight to the other extreme without
                    # ever confirming - start a fresh short-side base.
                    phase = "BASE_SHORT"
                    base_rsi, base_price = r, price
                    bounced_from_base = False
                    second_leg_rsi = second_leg_price = second_leg_ema200 = None
                    divergence_locked = False

            elif phase == "BASE_SHORT":

                if not bounced_from_base:

                    if r > base_rsi:
                        base_rsi, base_price = r, price
                    elif r <= base_rsi - cls.BOUNCE_MARGIN:
                        bounced_from_base = True

                elif second_leg_rsi is None or r > second_leg_rsi:
                    second_leg_rsi, second_leg_price = r, price
                    second_leg_ema200 = float(ema200.iloc[i]) if pd.notna(ema200.iloc[i]) else None
                    divergence_locked = (
                        second_leg_price >= base_price * (1 - cls.PRICE_TOLERANCE_PCT / 100)
                        and second_leg_rsi < base_rsi - cls.MIN_DIVERGENCE_MARGIN
                        and second_leg_rsi >= cls.SECOND_LEG_OVERBOUGHT
                    )

                elif divergence_locked and r <= second_leg_rsi - cls.CONFIRM_MARGIN:

                    event = "ENTRY_SHORT_DIVERGENCE"
                    divergence_points = {
                        "base_rsi": base_rsi, "base_price": base_price,
                        "second_leg_rsi": second_leg_rsi, "second_leg_price": second_leg_price,
                        "ema200_support": cls._near_ema200(second_leg_price, second_leg_ema200),
                    }
                    phase = "WATCHING"

                if phase == "BASE_SHORT" and r <= cls.OVERSOLD_TOUCH:
                    # Whipsawed straight to the other extreme without
                    # ever confirming - start a fresh long-side base.
                    phase = "BASE_LONG"
                    base_rsi, base_price = r, price
                    bounced_from_base = False
                    second_leg_rsi = second_leg_price = second_leg_ema200 = None
                    divergence_locked = False

            trace.append({
                "index": i,
                "phase": phase,
                "event": event,
                "rsi": r,
                "price": price,
                "time": time_index[i],
                "divergence_points": divergence_points,
                "divergence_locked": divergence_locked,
            })

            prev_rsi = r

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
    def _near_ema200(cls, price, ema200_value):
        """
        True if the second-leg swing point sat within
        EMA200_CONFLUENCE_PCT of the 200 EMA - "taking the 200 EMA as
        support/resistance" at the exact moment the divergence formed,
        not just somewhere nearby in time.
        """

        if price is None or ema200_value is None or ema200_value == 0:
            return False

        return abs(price - ema200_value) / ema200_value * 100 <= cls.EMA200_CONFLUENCE_PCT

    @classmethod
    def describe(cls, trace):
        """
        Plain-English read of the CURRENT state, for a live screener/
        status box - mirrors RSIWaveStrategy.describe()'s own pattern.
        "Div1"/"Div2" (bullish/bearish) rather than "Path D" - Reversal
        Playbook already has an unrelated Path D, and these are a
        different engine entirely.
        """

        if not trace:
            return "Not enough 1H history to evaluate this instrument yet.", "NONE", None

        last = trace[-1]
        phase = last["phase"]
        rsi = round(last["rsi"], 2)

        last_event_bar = next((bar for bar in reversed(trace) if bar["event"]), None)
        bars_since_event = len(trace) - 1 - trace.index(last_event_bar) if last_event_bar else None
        recent = bars_since_event is not None and bars_since_event <= 3

        if recent and last_event_bar["event"] == "ENTRY_LONG_DIVERGENCE":
            ema_note = " Also taking the 200 EMA as support — double confirmation." if (last_event_bar["divergence_points"] or {}).get("ema200_support") else ""
            return (
                f"🟢 Div1 (bullish) entry {bars_since_event} bar(s) ago — RSI divergence confirmed, RSI {rsi}.{ema_note}",
                "ENTRY_LONG_DIVERGENCE",
                last_event_bar["time"],
            )

        if recent and last_event_bar["event"] == "ENTRY_SHORT_DIVERGENCE":
            ema_note = " Also rejecting the 200 EMA as resistance — double confirmation." if (last_event_bar["divergence_points"] or {}).get("ema200_support") else ""
            return (
                f"🔴 Div2 (bearish) entry {bars_since_event} bar(s) ago — RSI divergence confirmed, RSI {rsi}.{ema_note}",
                "ENTRY_SHORT_DIVERGENCE",
                last_event_bar["time"],
            )

        if phase == "BASE_LONG" and last["divergence_locked"]:
            return (
                f"🟡 Div1 forming (bullish) — RSI divergence detected, RSI {rsi}, watching for confirmation.",
                "DIVERGENCE_FORMING_LONG",
                None,
            )

        if phase == "BASE_SHORT" and last["divergence_locked"]:
            return (
                f"🟠 Div2 forming (bearish) — RSI divergence detected, RSI {rsi}, watching for confirmation.",
                "DIVERGENCE_FORMING_SHORT",
                None,
            )

        return f"⚪ Watching — RSI {rsi}, no divergence setup active.", "WATCHING", None

    STATE_LABELS = {
        "NONE": "⚪ No data",
        "WATCHING": "⚪ Watching",
        "DIVERGENCE_FORMING_LONG": "🟡 Div1 forming (bullish)",
        "DIVERGENCE_FORMING_SHORT": "🟠 Div2 forming (bearish)",
        "ENTRY_LONG_DIVERGENCE": "🟢 Div1 (bullish) entry",
        "ENTRY_SHORT_DIVERGENCE": "🔴 Div2 (bearish) entry",
    }


class DailyRSIDivergenceStrategy(RSIDivergenceStrategy):
    """
    Same state machine, Daily bars instead of Hourly - explicit
    follow-up request after the user showed the same price-down/RSI-up
    divergence shape playing out on a Daily BTC-USD chart, alongside a
    1H Japan 225 example. MIN_HISTORY is a bar count (needs ~200 bars
    for EMA200 confluence), timeframe-agnostic - only run_symbol's
    default interval needs overriding, same pattern as
    DailyEMAReclaimStrategy.

    Macro-scoped (Global Indices/currencies/commodities) - explicit
    instruction: "for macro like indices, currencies, hourly and daily
    make sense" - keeps the same 1H-calibrated thresholds (25/75 base,
    30/60 second-leg zone, strict equal-or-lower price) as the parent
    class. See StockRSIDivergenceStrategy for the separately-calibrated
    stock version (which needed real loosening - macro's own RSI swings
    on Daily bars are still deep enough for these thresholds to fire;
    see that class's docstring for why stocks are different).
    """

    @classmethod
    def run_symbol(cls, symbol, period="730d", interval="1d"):
        return super().run_symbol(symbol, period=period, interval=interval)


class StockRSIDivergenceStrategy(RSIDivergenceStrategy):
    """
    Daily-bar variant, recalibrated specifically for individual stocks
    - explicit instruction: "for stocks, daily and weekly is good...
    for macro hourly and daily make sense" (i.e. stocks use THIS
    timeframe pair with THESE thresholds, macro keeps its own).

    Real bug found via a live example (MSFT): the base/1H-calibrated
    thresholds (RSI must touch <=25/>=75) never fired at all on MSFT's
    real Daily/Weekly RSI, which only reached the low-to-mid 30s during
    a real, sustained down-move/consolidation before a +15-18% rally -
    individual stocks simply don't swing RSI as violently as an index
    or FX pair on these slower timeframes. Loosened to 40/60 (base) and
    45/55 (second-leg zone).

    Also relaxed PRICE_TOLERANCE_PCT to 5% - MSFT's real Weekly second
    leg was priced ~5% ABOVE the first leg (a flat-to-slightly-higher
    consolidation with clearly rising RSI lows), not the stricter
    equal-or-lower shape the base class requires - real divergence on
    these timeframes doesn't always show up as a textbook lower low.
    """

    OVERSOLD_TOUCH = 40
    OVERBOUGHT_TOUCH = 60
    SECOND_LEG_OVERSOLD = 45
    SECOND_LEG_OVERBOUGHT = 55
    PRICE_TOLERANCE_PCT = 5.0

    @classmethod
    def run_symbol(cls, symbol, period="730d", interval="1d"):
        return super().run_symbol(symbol, period=period, interval=interval)


class WeeklyStockRSIDivergenceStrategy(StockRSIDivergenceStrategy):
    """
    Same recalibrated thresholds as StockRSIDivergenceStrategy, Weekly
    bars instead of Daily - explicit instruction that stocks need BOTH
    ("daily and weekly is good"). Weekly bars need a much longer period
    to reach MIN_HISTORY (210 bars ~= 4 years of weekly data) - "730d"
    (the Hourly/Daily default) would return well under 210 weekly bars
    and always fail the history check, so this overrides period too,
    not just interval.
    """

    @classmethod
    def run_symbol(cls, symbol, period="10y", interval="1wk"):
        return super().run_symbol(symbol, period=period, interval=interval)
