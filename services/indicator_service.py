"""
Indicator Service
"""

import ta

from core.asset import Asset


class IndicatorService:

    @staticmethod
    def _calculate(df, indicator):

        if df.empty:
            return

        close = df["Close"]

        indicator.ema20 = round(
            ta.trend.ema_indicator(close, window=20).iloc[-1],
            2,
        )

        indicator.ema50 = round(
            ta.trend.ema_indicator(close, window=50).iloc[-1],
            2,
        )

        indicator.ema200 = round(
            ta.trend.ema_indicator(close, window=200).iloc[-1],
            2,
        )

        indicator.rsi14 = round(
            ta.momentum.rsi(close, window=14).iloc[-1],
            2,
        )

        price = float(close.iloc[-1])

        if price > indicator.ema20 > indicator.ema200:
            indicator.trend = "Bullish"

        elif price < indicator.ema20 < indicator.ema200:
            indicator.trend = "Bearish"

        else:
            indicator.trend = "Neutral"

        if indicator.rsi14 >= 70:
            indicator.momentum = "Overbought"

        elif indicator.rsi14 <= 30:
            indicator.momentum = "Oversold"

        elif indicator.rsi14 >= 55:
            indicator.momentum = "Strong"

        elif indicator.rsi14 <= 45:
            indicator.momentum = "Weak"

        else:
            indicator.momentum = "Neutral"

    @classmethod
    def build(cls, asset: Asset):

        cls._calculate(
            asset.data_15m,
            asset.indicators.m15,
        )

        cls._calculate(
            asset.data_1h,
            asset.indicators.h1,
        )

        cls._calculate(
            asset.data_1d,
            asset.indicators.d1,
        )