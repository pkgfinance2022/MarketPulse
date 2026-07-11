"""
Backtest engine.

Answers "is the Signal engine actually any good" by replaying the exact
live scoring/recommendation logic day-by-day over real historical data,
instead of trusting the hand-picked weights/thresholds on faith. At each
day it only uses data up to and including that day (no lookahead), asks
the same functions the live app uses what the signal would have been,
then checks what the price actually did afterward.

Deliberately reuses ScoringEngine / RiskEngine / RecommendationEngine
directly rather than re-implementing their math here - a backtest that
uses a parallel copy of the logic could silently drift from what the
live app actually does and end up validating the wrong thing.

Known limitation: Fundamental/Valuation scores need point-in-time
financials (historical P/E, ROE, etc.) that aren't available from
yfinance, which only exposes the current snapshot. Every symbol is
therefore scored with a non-equity "Market" label during backtesting so
ScoringEngine always uses MACRO_WEIGHTS (Technical/Momentum/Trend/Risk
only, Fundamental/Valuation held at neutral 50). This backtest
validates the technical/momentum/trend/risk half of the engine, not
the fundamental/valuation half - that half currently runs on faith.
"""

import pandas as pd
import ta

from analysis.recommendation_engine import RecommendationEngine
from analysis.risk_engine import RiskEngine
from analysis.scoring_engine import ScoringEngine
from providers.yahoo import YahooProvider


class BacktestEngine:

    # Need EMA200 to be based on real history before trusting a signal.
    MIN_HISTORY = 200

    FORWARD_DAYS = (5, 10, 20)

    @staticmethod
    def _trend_label(price, ema20, ema200):

        if price and ema20 and ema200 and price > ema20 > ema200:
            return "Bullish"

        if price and ema20 and ema200 and price < ema20 < ema200:
            return "Bearish"

        return "Neutral"

    @staticmethod
    def _prepare_indicators(df):
        """
        Computes every indicator ONCE, vectorized over the whole
        series, instead of recomputing from scratch on a growing
        window for every single day (which was O(n^2) and made a full
        10y/many-symbol run impractically slow).

        This is safe: EMA/RSI/MACD/ADX/ATR/rolling-min/max are all
        causal filters - the value at position i only ever depends on
        data at or before i, never on anything after it. Computing them
        once over the full series and reading position i gives the
        exact same number as computing them on data[:i+1] and reading
        the last value, since both use the identical i+1 past data
        points. No lookahead is introduced.
        """

        close, high, low = df["Close"], df["High"], df["Low"]

        return {
            "close": close,
            "ema20": ta.trend.ema_indicator(close, window=20),
            "ema50": ta.trend.ema_indicator(close, window=50),
            "ema200": ta.trend.ema_indicator(close, window=200),
            "rsi": ta.momentum.rsi(close, window=14),
            "macd": ta.trend.macd_diff(close),
            "adx": ta.trend.adx(high, low, close, window=14),
            "atr": ta.volatility.average_true_range(high, low, close, window=14),
            "support": low.rolling(20).min(),
            "resistance": high.rolling(20).max(),
            "change_pct": close.pct_change() * 100,
        }

    @classmethod
    def _score_day(cls, ind, i):
        """
        Builds the same flat row ScoringEngine / RiskEngine /
        RecommendationEngine already work with in the live app, reading
        only index i of each precomputed (causal) indicator series -
        nothing from the future.
        """

        price = float(ind["close"].iloc[i])

        ema20 = float(ind["ema20"].iloc[i])
        ema50 = float(ind["ema50"].iloc[i])
        ema200 = float(ind["ema200"].iloc[i])
        rsi = float(ind["rsi"].iloc[i])
        macd = float(ind["macd"].iloc[i])
        adx = float(ind["adx"].iloc[i])
        atr = float(ind["atr"].iloc[i])

        support = round(float(ind["support"].iloc[i]), 2)
        resistance = round(float(ind["resistance"].iloc[i]), 2)
        stop_loss = max(support, price - 2 * atr) if atr else support

        change_pct = float(ind["change_pct"].iloc[i]) if i >= 1 else 0.0

        trend_label = cls._trend_label(price, ema20, ema200)

        row = {
            # Forces ScoringEngine.MACRO_WEIGHTS - see module docstring.
            "Market": "Index",
            "Price": price,
            "EMA20": ema20,
            "EMA50": ema50,
            "EMA200": ema200,
            "Resistance": resistance,
            "Support": support,
            "RSI": rsi,
            "MACD": macd,
            "ADX": adx,
            "Change %": change_pct,
            "15m Trend": trend_label,
            "1H Trend": trend_label,
            "1D Trend": trend_label,
            "Stop Loss": round(stop_loss, 2),
        }

        risk = RiskEngine.analyse(row)
        row["Risk Score"] = risk["risk_score"]
        row["Risk Reward"] = risk["risk_reward"]

        ScoringEngine.score(row)

        signal = RecommendationEngine._signal(
            row["AI Score"],
            row["Trend Score"],
            row["Risk Score"],
            row["Risk Reward"],
        )

        confidence = RecommendationEngine._confidence(
            [
                row["Technical Score"],
                50,  # Fundamental - not backtestable, see docstring
                50,  # Valuation - not backtestable, see docstring
                row["Momentum Score"],
                row["Trend Score"],
                row["Risk Score"],
            ],
            row["AI Score"],
        )

        return signal, confidence, price

    @classmethod
    def run_symbol(cls, symbol, period="10y"):
        """
        Returns one dict per day a signal was generated:
        {Symbol, Date, Signal, Confidence, "Fwd 5D %", "Fwd 10D %", "Fwd 20D %"}
        """

        df = YahooProvider().history(symbol, interval="1d", period=period)

        min_len = cls.MIN_HISTORY + max(cls.FORWARD_DAYS) + 1

        if df.empty or len(df) < min_len:
            return []

        ind = cls._prepare_indicators(df)
        close = ind["close"]

        last_usable = len(df) - max(cls.FORWARD_DAYS) - 1

        results = []

        for i in range(cls.MIN_HISTORY, last_usable):

            try:
                signal, confidence, price = cls._score_day(ind, i)
            except Exception:
                continue

            row = {
                "Symbol": symbol,
                "Date": df.index[i],
                "Signal": signal,
                "Confidence": confidence,
            }

            for n in cls.FORWARD_DAYS:
                future_price = float(close.iloc[i + n])
                row[f"Fwd {n}D %"] = round((future_price / price - 1) * 100, 2)

            results.append(row)

        return results

    @classmethod
    def run(cls, symbols, period="10y"):

        all_rows = []

        for index, symbol in enumerate(symbols, start=1):

            print(f"[{index}/{len(symbols)}] Backtesting {symbol}...")

            try:
                all_rows.extend(cls.run_symbol(symbol, period=period))
            except Exception as ex:
                print(f"  Failed: {symbol} : {ex}")

        return pd.DataFrame(all_rows)

    @staticmethod
    def summarize(df):
        """
        Per-signal aggregate stats (count, average forward return, win
        rate, and the edge over just holding on every day regardless of
        signal) so it's obvious whether a signal actually beats doing
        nothing.
        """

        if df.empty:
            return pd.DataFrame()

        forward_cols = [c for c in df.columns if c.startswith("Fwd ")]

        baseline = {c: df[c].mean() for c in forward_cols}

        rows = []

        for signal, group in df.groupby("Signal"):

            row = {"Signal": signal, "Count": len(group)}

            for c in forward_cols:
                row[f"{c} Avg"] = round(group[c].mean(), 2)
                row[f"{c} Win %"] = round((group[c] > 0).mean() * 100, 1)
                row[f"{c} Edge"] = round(group[c].mean() - baseline[c], 2)

            rows.append(row)

        summary = pd.DataFrame(rows).sort_values("Count", ascending=False)

        baseline_row = {"Signal": "ALL DAYS (baseline)", "Count": len(df)}

        for c in forward_cols:
            baseline_row[f"{c} Avg"] = round(baseline[c], 2)
            baseline_row[f"{c} Win %"] = round((df[c] > 0).mean() * 100, 1)
            baseline_row[f"{c} Edge"] = 0.0

        summary = pd.concat([summary, pd.DataFrame([baseline_row])], ignore_index=True)

        return summary
