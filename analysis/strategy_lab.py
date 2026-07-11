"""
Strategy lab.

Head-to-head comparison of the current AI-Score-based signal engine
against a few simple, independently-defined, well-known strategies -
trend-following, mean-reversion, breakout - over the same historical
data. The point isn't to prove any one of these is "the answer"; the
backtest of the current engine (see backtest_engine.py) already showed
it has no measurable short-term edge and its SELL signals were
inverted. This module exists to find out honestly whether any simpler,
well-known rule does better before building anything else on top of a
scorecard that hasn't earned it.

Reuses BacktestEngine._prepare_indicators (same EMA/RSI/ADX/ATR/etc,
same causal no-lookahead discipline, same forward-return windows) so
the comparison is apples-to-apples - only the signal RULE differs
between strategies, nothing else about the methodology.
"""

import pandas as pd

from analysis.backtest_engine import BacktestEngine
from providers.yahoo import YahooProvider


class StrategyLab:

    MIN_HISTORY = BacktestEngine.MIN_HISTORY
    FORWARD_DAYS = BacktestEngine.FORWARD_DAYS

    @staticmethod
    def trend_following(ind, i):
        """Long when price is stacked above EMA50 above EMA200, short the mirror image, flat otherwise."""

        price = float(ind["close"].iloc[i])
        ema50 = float(ind["ema50"].iloc[i])
        ema200 = float(ind["ema200"].iloc[i])

        if price > ema50 > ema200:
            return "LONG"

        if price < ema50 < ema200:
            return "SHORT"

        return "FLAT"

    @staticmethod
    def mean_reversion(ind, i):
        """Long when oversold (RSI<30), short when overbought (RSI>70), flat otherwise."""

        rsi = float(ind["rsi"].iloc[i])

        if rsi < 30:
            return "LONG"

        if rsi > 70:
            return "SHORT"

        return "FLAT"

    @staticmethod
    def breakout(ind, i):
        """Long on a fresh 20-day high, short on a fresh 20-day low, flat otherwise."""

        price = float(ind["close"].iloc[i])
        resistance = float(ind["resistance"].iloc[i])
        support = float(ind["support"].iloc[i])

        if price >= resistance:
            return "LONG"

        if price <= support:
            return "SHORT"

        return "FLAT"

    @classmethod
    def run_symbol(cls, symbol, strategy_name, period="10y"):

        signal_fn = cls.STRATEGIES[strategy_name]

        df = YahooProvider().history(symbol, interval="1d", period=period)

        min_len = cls.MIN_HISTORY + max(cls.FORWARD_DAYS) + 1

        if df.empty or len(df) < min_len:
            return []

        ind = BacktestEngine._prepare_indicators(df)
        close = ind["close"]

        last_usable = len(df) - max(cls.FORWARD_DAYS) - 1

        results = []

        for i in range(cls.MIN_HISTORY, last_usable):

            try:
                signal = signal_fn(ind, i)
                price = float(close.iloc[i])
            except Exception:
                continue

            row = {
                "Symbol": symbol,
                "Date": df.index[i],
                "Signal": signal,
            }

            for n in cls.FORWARD_DAYS:
                future_price = float(close.iloc[i + n])
                row[f"Fwd {n}D %"] = round((future_price / price - 1) * 100, 2)

            results.append(row)

        return results

    @classmethod
    def run(cls, symbols, strategy_name, period="10y"):

        all_rows = []

        for index, symbol in enumerate(symbols, start=1):

            print(f"  [{index}/{len(symbols)}] {strategy_name}: {symbol}...")

            try:
                all_rows.extend(cls.run_symbol(symbol, strategy_name, period=period))
            except Exception as ex:
                print(f"    Failed: {symbol} : {ex}")

        return pd.DataFrame(all_rows)


StrategyLab.STRATEGIES = {
    "Trend Following": StrategyLab.trend_following,
    "Mean Reversion": StrategyLab.mean_reversion,
    "Breakout": StrategyLab.breakout,
}
