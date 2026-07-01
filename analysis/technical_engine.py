"""
Technical analysis engine.

This engine converts raw price history and calculated indicators into a
single technical intelligence payload used by scoring and recommendations.
"""

import pandas as pd
import ta


class TechnicalEngine:

    @staticmethod
    def _latest(series, default=0.0):

        if series is None or series.empty:
            return default

        value = series.iloc[-1]

        if pd.isna(value):
            return default

        return round(float(value), 2)

    @staticmethod
    def _score(price, ema20, ema50, ema200, rsi, macd, adx):

        score = 0

        if price and ema20 and price > ema20:
            score += 20

        if ema20 and ema50 and ema20 > ema50:
            score += 20

        if ema50 and ema200 and ema50 > ema200:
            score += 20

        if 45 <= rsi <= 65:
            score += 20
        elif 35 <= rsi < 45 or 65 < rsi <= 75:
            score += 10

        if macd > 0:
            score += 10

        if adx >= 25:
            score += 10

        return max(0, min(100, int(round(score))))

    @classmethod
    def analyse(cls, asset):

        d1 = asset.indicators.d1
        df = asset.data_1d

        price = asset.summary.price or 0.0
        ema20 = d1.ema20 or 0.0
        ema50 = d1.ema50 or 0.0
        ema200 = d1.ema200 or 0.0
        rsi = d1.rsi14 or 0.0
        trend = d1.trend or "Neutral"

        result = {
            "technical_score": 0,
            "trend": trend,
            "momentum": d1.momentum or "Neutral",
            "breakout": False,
            "ema_alignment": bool(ema20 and ema50 and ema200 and ema20 > ema50 > ema200),
            "rsi": rsi,
            "macd": "Neutral",
            "macd_value": 0.0,
            "adx": 0.0,
            "atr": 0.0,
            "support": 0.0,
            "resistance": 0.0,
            "stop_loss": 0.0,
        }

        if df.empty:
            result["technical_score"] = cls._score(
                price,
                ema20,
                ema50,
                ema200,
                rsi,
                0.0,
                0.0,
            )
            return result

        close = df["Close"]
        high = df["High"]
        low = df["Low"]

        macd_series = ta.trend.macd_diff(close)
        adx_series = ta.trend.adx(high, low, close, window=14)
        atr_series = ta.volatility.average_true_range(high, low, close, window=14)

        macd = cls._latest(macd_series)
        adx = cls._latest(adx_series)
        atr = cls._latest(atr_series)
        support = round(float(low.tail(20).min()), 2)
        resistance = round(float(high.tail(20).max()), 2)

        result["macd_value"] = macd
        result["macd"] = "Bullish" if macd > 0 else "Bearish" if macd < 0 else "Neutral"
        result["adx"] = adx
        result["atr"] = atr
        result["support"] = support
        result["resistance"] = resistance
        result["breakout"] = bool(price and resistance and price > resistance)

        stop_loss = support

        if price and atr:
            stop_loss = max(support, price - (2 * atr))

        result["stop_loss"] = round(stop_loss, 2)
        result["technical_score"] = cls._score(
            price,
            ema20,
            ema50,
            ema200,
            rsi,
            macd,
            adx,
        )

        return result
