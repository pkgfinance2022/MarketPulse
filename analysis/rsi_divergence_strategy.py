"""
RSI early-cross divergence strategy.

A deliberately earlier, riskier sibling of RSIWaveStrategy. That
strategy waits for RSI to travel all the way from an oversold base to
a full cross above 65 before calling it an entry - a late, confirmed
signal. This one asks: what about the moment RSI first crosses back
above 40, coming out of an oversold base? That's much earlier (and
more prone to getting rejected back down at 50/60/65 before ever
reaching a Wave entry), so it's tagged and reported as its own thing,
not folded into the Wave engine's own states.

The pattern, bar by bar:
  1. RSI touches oversold (<= OVERSOLD_TOUCH) - this is the base.
  2. Price/RSI often "fight" below 40 for a while - noisy, indecisive,
     sometimes carving out a second, shallower low before actually
     turning up.
  3. RSI crosses above 40. This is the signal.

The interesting part is whether step 2 formed a classic bullish RSI
divergence on the way there: price makes an equal-or-lower low on its
second dip while RSI makes a HIGHER low - the textbook "failure swing"
setup. When that structure is present, the 40-cross is tagged
DIVERGENCE (higher-confidence read); when RSI just fell straight from
the oversold touch to the 40-cross with no second, higher low to
compare against, it's tagged NO_DIVERGENCE (still reported - "it is
also pass on" - but flagged as the more speculative case).

Divergence detection, mechanically: once oversold, the running lowest
RSI/price point is the "base." If RSI later bounces away from that
base by BOUNCE_MARGIN points and then turns back down again (a genuine
second leg, not just noise), the base is locked in as prior_low and a
new current_low starts tracking the second leg's own bottom. Whichever
point is lowest when RSI crosses 40 is what gets compared against
prior_low for the divergence check. If RSI never bounces away from the
base before crossing 40, there's no second leg to compare - NO_DIVERGENCE.

Mirrored for downtrends: oversold becomes overbought, 40 becomes 60,
"higher low" becomes "lower high."

This is a prototype for backtesting the idea, not wired into any live
screener/Telegram alert yet - see analysis/backtester.py's
backtest_rsi_divergence for how it's being evaluated.
"""

import ta

from analysis.rsi_wave_strategy import RSIWaveStrategy
from providers.yahoo import YahooProvider


class RSIDivergenceStrategy:

    MIN_HISTORY = 210

    OVERSOLD_TOUCH = RSIWaveStrategy.OVERSOLD_TOUCH      # 25 - same base condition as the Wave engine
    OVERBOUGHT_TOUCH = RSIWaveStrategy.OVERBOUGHT_TOUCH  # 75

    CROSS_LONG = 40
    CROSS_SHORT = 60

    # How far RSI has to bounce away from the current low before a
    # later pullback counts as a genuine "second leg" (and the bounce
    # peak locks in prior_low) rather than just noise sitting at the
    # bottom. Untested magic number - exactly the kind of thing the
    # backtest below exists to sanity-check.
    BOUNCE_MARGIN = 5

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

        trace = []
        prev_rsi = float(rsi.iloc[start - 1])

        for i in range(start, end):

            r = float(rsi.iloc[i])
            price = float(close.iloc[i])
            event = None

            if phase == "WATCHING":

                if r <= cls.OVERSOLD_TOUCH:
                    phase = "BASE_LONG"
                    base_rsi, base_price = r, price
                    bounced_from_base = False
                    second_leg_rsi = second_leg_price = None

                elif r >= cls.OVERBOUGHT_TOUCH:
                    phase = "BASE_SHORT"
                    base_rsi, base_price = r, price
                    bounced_from_base = False
                    second_leg_rsi = second_leg_price = None

            elif phase == "BASE_LONG":

                if not bounced_from_base:

                    if r < base_rsi:
                        base_rsi, base_price = r, price
                    elif r >= base_rsi + cls.BOUNCE_MARGIN:
                        bounced_from_base = True

                elif second_leg_rsi is None or r < second_leg_rsi:
                    second_leg_rsi, second_leg_price = r, price

                if r >= cls.CROSS_LONG and prev_rsi < cls.CROSS_LONG:

                    if bounced_from_base and second_leg_rsi is not None:
                        divergence = second_leg_price <= base_price and second_leg_rsi > base_rsi
                        event = "ENTRY_LONG_DIVERGENCE" if divergence else "ENTRY_LONG_NO_DIVERGENCE"
                    else:
                        event = "ENTRY_LONG_NO_DIVERGENCE"

                    phase = "WATCHING"

                elif r >= cls.OVERBOUGHT_TOUCH:
                    # Whipsawed straight to the other extreme without
                    # ever crossing 40 - start a fresh short-side base.
                    phase = "BASE_SHORT"
                    base_rsi, base_price = r, price
                    bounced_from_base = False
                    second_leg_rsi = second_leg_price = None

            elif phase == "BASE_SHORT":

                if not bounced_from_base:

                    if r > base_rsi:
                        base_rsi, base_price = r, price
                    elif r <= base_rsi - cls.BOUNCE_MARGIN:
                        bounced_from_base = True

                elif second_leg_rsi is None or r > second_leg_rsi:
                    second_leg_rsi, second_leg_price = r, price

                if r <= cls.CROSS_SHORT and prev_rsi > cls.CROSS_SHORT:

                    if bounced_from_base and second_leg_rsi is not None:
                        divergence = second_leg_price >= base_price and second_leg_rsi < base_rsi
                        event = "ENTRY_SHORT_DIVERGENCE" if divergence else "ENTRY_SHORT_NO_DIVERGENCE"
                    else:
                        event = "ENTRY_SHORT_NO_DIVERGENCE"

                    phase = "WATCHING"

                elif r <= cls.OVERSOLD_TOUCH:
                    # Whipsawed straight to the other extreme without
                    # ever crossing 60 - start a fresh long-side base.
                    phase = "BASE_LONG"
                    base_rsi, base_price = r, price
                    bounced_from_base = False
                    second_leg_rsi = second_leg_price = None

            trace.append({
                "index": i,
                "phase": phase,
                "event": event,
                "rsi": r,
                "price": price,
                "time": time_index[i],
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
