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

Backtested via analysis/backtester.py's backtest_rsi_divergence.
Result on a 19-symbol, 365-day sample: positive avg return, but 12/19
symbols individually positive vs 7 negative, and the strongest
performers had thin per-symbol samples (3-5 trades) - promising, but
not yet validated enough to wire into any live screener or alert.
"""

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

    # How far RSI has to recover off the second-leg extreme before
    # entering - backtested sweep of 1-8 points all gave a positive
    # average return (unlike waiting for a 40/60 cross, which didn't),
    # peaking around this value.
    CONFIRM_MARGIN = 5

    @staticmethod
    def _prepare(df):

        close = df["Close"]
        typical_price = (df["Open"] + df["High"] + df["Low"] + close) / 4

        return {
            "close": close,
            "high": df["High"],
            "low": df["Low"],
            "rsi": ta.momentum.rsi(typical_price, window=14),
            "time": df.index,
        }

    @classmethod
    def walk(cls, ind, start, end):

        close, rsi, time_index = ind["close"], ind["rsi"], ind["time"]

        phase = "WATCHING"

        # base_rsi/base_price: the first leg's low - keeps updating to
        # any new lower reading until RSI bounces away from it.
        # second_leg_rsi/second_leg_price: once bounced, the lowest
        # point of whatever comes next - deliberately allowed to be
        # HIGHER than the base (that's the divergence case), tracked
        # regardless of direction relative to the base.
        base_rsi = base_price = None
        bounced_from_base = False
        second_leg_rsi = second_leg_price = None
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
                    second_leg_rsi = second_leg_price = None
                    divergence_locked = False

                elif r >= cls.OVERBOUGHT_TOUCH:
                    phase = "BASE_SHORT"
                    base_rsi, base_price = r, price
                    bounced_from_base = False
                    second_leg_rsi = second_leg_price = None
                    divergence_locked = False

            elif phase == "BASE_LONG":

                if not bounced_from_base:

                    if r < base_rsi:
                        base_rsi, base_price = r, price
                    elif r >= base_rsi + cls.BOUNCE_MARGIN:
                        bounced_from_base = True

                elif second_leg_rsi is None or r < second_leg_rsi:
                    second_leg_rsi, second_leg_price = r, price
                    divergence_locked = second_leg_price <= base_price and second_leg_rsi > base_rsi + cls.MIN_DIVERGENCE_MARGIN

                elif divergence_locked and r >= second_leg_rsi + cls.CONFIRM_MARGIN:

                    event = "ENTRY_LONG_DIVERGENCE"
                    divergence_points = {
                        "base_rsi": base_rsi, "base_price": base_price,
                        "second_leg_rsi": second_leg_rsi, "second_leg_price": second_leg_price,
                    }
                    phase = "WATCHING"

                if phase == "BASE_LONG" and r >= cls.OVERBOUGHT_TOUCH:
                    # Whipsawed straight to the other extreme without
                    # ever confirming - start a fresh short-side base.
                    phase = "BASE_SHORT"
                    base_rsi, base_price = r, price
                    bounced_from_base = False
                    second_leg_rsi = second_leg_price = None
                    divergence_locked = False

            elif phase == "BASE_SHORT":

                if not bounced_from_base:

                    if r > base_rsi:
                        base_rsi, base_price = r, price
                    elif r <= base_rsi - cls.BOUNCE_MARGIN:
                        bounced_from_base = True

                elif second_leg_rsi is None or r > second_leg_rsi:
                    second_leg_rsi, second_leg_price = r, price
                    divergence_locked = second_leg_price >= base_price and second_leg_rsi < base_rsi - cls.MIN_DIVERGENCE_MARGIN

                elif divergence_locked and r <= second_leg_rsi - cls.CONFIRM_MARGIN:

                    event = "ENTRY_SHORT_DIVERGENCE"
                    divergence_points = {
                        "base_rsi": base_rsi, "base_price": base_price,
                        "second_leg_rsi": second_leg_rsi, "second_leg_price": second_leg_price,
                    }
                    phase = "WATCHING"

                if phase == "BASE_SHORT" and r <= cls.OVERSOLD_TOUCH:
                    # Whipsawed straight to the other extreme without
                    # ever confirming - start a fresh long-side base.
                    phase = "BASE_LONG"
                    base_rsi, base_price = r, price
                    bounced_from_base = False
                    second_leg_rsi = second_leg_price = None
                    divergence_locked = False

            trace.append({
                "index": i,
                "phase": phase,
                "event": event,
                "rsi": r,
                "price": price,
                "time": time_index[i],
                "divergence_points": divergence_points,
            })

            prev_rsi = r

        return trace

    @classmethod
    def run_symbol(cls, symbol, period="730d"):

        df = YahooProvider().history(symbol, interval="1h", period=period)

        if df.empty or len(df) < cls.MIN_HISTORY + 1:
            return None, None

        ind = cls._prepare(df)
        trace = cls.walk(ind, cls.MIN_HISTORY, len(df))

        return trace, df
