"""
Dashboard Statistics
"""

import pandas as pd


class DashboardStats:

    @staticmethod
    def summary(df: pd.DataFrame):

        if df.empty:
            return {
                "assets": 0,
                "bullish": 0,
                "bearish": 0,
                "neutral": 0,
                "avg_score": 0,
                "avg_rsi": 0,
                "avg_change": 0,
                "buy": 0,
                "hold": 0,
                "sell": 0,
            }

        bullish = int((df["Trend"] == "Bullish").sum())
        bearish = int((df["Trend"] == "Bearish").sum())
        neutral = int((df["Trend"] == "Neutral").sum())

        return {
            "assets": len(df),
            "bullish": bullish,
            "bearish": bearish,
            "neutral": neutral,
            "avg_score": float(round(df["AI Score"].mean(), 1)),
            "avg_rsi": float(round(df["RSI"].mean(), 1)),
            "avg_change": float(round(df["Change %"].mean(), 2)),
            "buy": int((df["Signal"] == "BUY").sum()),
            "hold": int((df["Signal"] == "HOLD").sum()),
            "sell": int((df["Signal"] == "SELL").sum()),
        }

    @staticmethod
    def top_opportunities(df: pd.DataFrame):

        if df.empty:
            return df

        return (
            df.sort_values("AI Score", ascending=False)
              .head(10)[
                  [
                      "Ticker",
                      "Name",
                      "Signal",
                      "AI Score",
                      "Trend",
                      "RSI",
                  ]
              ]
        )
