"""
Pullback-in-trend strategy.

Tests a specific trend + oscillator combination: within an established
uptrend, wait for RSI to dip into oversold territory (a pullback/
correction), then enter LONG only once RSI recovers back above a
threshold - confirmation the correction is over and the uptrend is
resuming. Mirrored for downtrends (RSI spikes overbought, then drops
back below a threshold to confirm the downtrend is resuming).

This differs from both pure trend-following (always in the market
while the trend holds, no entry timing) and pure mean-reversion
(buys/sells purely on RSI extremes regardless of the prevailing trend)
tested in strategy_lab.py - it combines a trend FILTER with an
oscillator-based ENTRY TIMING signal, only firing on the RSI recovery
CROSS, not on the touch itself.

Runs on 1H bars, since yfinance only exposes ~730 days of hourly
history (about 2 years). This backtest does NOT cover the 2020/2022
bear markets - only the recent ~2-year window, which still includes
at least one real drawdown-and-recovery.
"""

import pandas as pd
import ta

from providers.yahoo import YahooProvider


class PullbackStrategy:

    MIN_HISTORY = 210
    FORWARD_BARS = (5, 10, 20, 40)

    OVERSOLD_TOUCH = 25
    RECOVERY_LEVEL = 65

    OVERBOUGHT_TOUCH = 75
    DOWN_RECOVERY_LEVEL = 35

    @staticmethod
    def _prepare(df):

        close = df["Close"]

        return {
            "close": close,
            "ema50": ta.trend.ema_indicator(close, window=50),
            "ema200": ta.trend.ema_indicator(close, window=200),
            "rsi": ta.momentum.rsi(close, window=14),
        }

    @classmethod
    def _trend(cls, ema50, ema200):
        """
        Trend is defined by the EMA50/EMA200 relationship alone, NOT
        current price vs EMA50 - a pullback (which is exactly what
        pushes RSI into oversold territory) temporarily pulls price
        below its faster average without breaking the slower trend
        structure. Requiring price > EMA50 too would make "bullish
        trend" and "RSI oversold" almost mutually exclusive by
        construction, since a dip sharp enough to hit RSI 25 usually
        also dips price under EMA50.
        """

        if ema50 > ema200:
            return "Bullish"

        if ema50 < ema200:
            return "Bearish"

        return "Neutral"

    @classmethod
    def walk(cls, ind, start, end):
        """
        Walks bars [start, end) in order, maintaining "armed" state for
        the pullback setup (has RSI touched the extreme zone yet, while
        still in the same trend). Returns the FULL per-bar state trace
        (not just the signal label) so callers can both backtest (only
        needs `signal`) and show live status (needs the final
        trend/rsi/armed state to describe what's happening right now).
        """

        ema50, ema200, rsi = ind["ema50"], ind["ema200"], ind["rsi"]

        armed_long = False
        armed_short = False

        trace = []

        prev_rsi = float(rsi.iloc[start - 1])

        for i in range(start, end):

            e50 = float(ema50.iloc[i])
            e200 = float(ema200.iloc[i])
            r = float(rsi.iloc[i])

            trend = cls._trend(e50, e200)

            signal = "FLAT"

            if trend == "Bullish":

                armed_short = False

                if r <= cls.OVERSOLD_TOUCH:
                    armed_long = True

                if armed_long and r >= cls.RECOVERY_LEVEL and prev_rsi < cls.RECOVERY_LEVEL:
                    signal = "LONG"
                    armed_long = False

            elif trend == "Bearish":

                armed_long = False

                if r >= cls.OVERBOUGHT_TOUCH:
                    armed_short = True

                if armed_short and r <= cls.DOWN_RECOVERY_LEVEL and prev_rsi > cls.DOWN_RECOVERY_LEVEL:
                    signal = "SHORT"
                    armed_short = False

            else:
                # Trend context is gone - invalidate any in-progress setup.
                armed_long = False
                armed_short = False

            trace.append(
                {
                    "index": i,
                    "signal": signal,
                    "trend": trend,
                    "rsi": r,
                    "armed_long": armed_long,
                    "armed_short": armed_short,
                }
            )

            prev_rsi = r

        return trace

    @classmethod
    def generate_signals(cls, ind, start, end):
        """Backtest-only convenience: just the per-bar signal labels."""

        return [bar["signal"] for bar in cls.walk(ind, start, end)]

    @classmethod
    def run_symbol(cls, symbol, period="730d"):

        df = YahooProvider().history(symbol, interval="1h", period=period)

        min_len = cls.MIN_HISTORY + max(cls.FORWARD_BARS) + 1

        if df.empty or len(df) < min_len:
            return []

        ind = cls._prepare(df)
        close = ind["close"]

        last_usable = len(df) - max(cls.FORWARD_BARS) - 1

        signals = cls.generate_signals(ind, cls.MIN_HISTORY, last_usable)

        results = []

        for offset, signal in enumerate(signals):

            i = cls.MIN_HISTORY + offset
            price = float(close.iloc[i])

            row = {
                "Symbol": symbol,
                "Date": df.index[i],
                "Signal": signal,
            }

            for n in cls.FORWARD_BARS:
                future_price = float(close.iloc[i + n])
                row[f"Fwd {n}B %"] = round((future_price / price - 1) * 100, 2)

            results.append(row)

        return results

    @classmethod
    def run(cls, symbols, period="730d"):

        all_rows = []

        for index, symbol in enumerate(symbols, start=1):

            print(f"[{index}/{len(symbols)}] Pullback strategy: {symbol}...")

            try:
                all_rows.extend(cls.run_symbol(symbol, period=period))
            except Exception as ex:
                print(f"  Failed: {symbol} : {ex}")

        return pd.DataFrame(all_rows)

    @staticmethod
    def summarize(df):
        """
        Same shape as BacktestEngine.summarize - per-signal count,
        average forward return, win rate, and edge over the all-bars
        baseline, for every forward horizon.
        """

        if df.empty:
            return pd.DataFrame()

        forward_cols = [c for c in df.columns if c.startswith("Fwd ")]

        baseline = {c: df[c].mean() for c in forward_cols}

        rows = []

        for signal, group in df.groupby("Signal"):

            row = {"Signal": signal, "Count": len(group)}

            for c in forward_cols:
                row[f"{c} Avg"] = round(group[c].mean(), 3)
                row[f"{c} Win %"] = round((group[c] > 0).mean() * 100, 1)
                row[f"{c} Edge"] = round(group[c].mean() - baseline[c], 3)

            rows.append(row)

        summary = pd.DataFrame(rows).sort_values("Count", ascending=False)

        baseline_row = {"Signal": "ALL BARS (baseline)", "Count": len(df)}

        for c in forward_cols:
            baseline_row[f"{c} Avg"] = round(baseline[c], 3)
            baseline_row[f"{c} Win %"] = round((df[c] > 0).mean() * 100, 1)
            baseline_row[f"{c} Edge"] = 0.0

        summary = pd.concat([summary, pd.DataFrame([baseline_row])], ignore_index=True)

        return summary
