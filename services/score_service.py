"""
Score Service

Builds a simple technical score (0-100) for each asset.
"""

from core.asset import Asset


class ScoreService:

    @staticmethod
    def build(asset: Asset):

        score = 0
        reasons = []

        d1 = asset.indicators.d1
        h1 = asset.indicators.h1
        m15 = asset.indicators.m15

        required = [
            d1.rsi14,
            d1.ema20,
            d1.ema200,
        ]

        if any(v is None for v in required):
            asset.scores["score"] = 0
            asset.scores["rating"] = "No Data"
            asset.scores["reasons"] = ["Missing indicators"]
            return
        # -----------------------------
        # Daily Trend (30)
        # -----------------------------
        if d1.trend == "Bullish":
            score += 30
            reasons.append("Daily trend bullish")
        elif d1.trend == "Neutral":
            score += 15

        # -----------------------------
        # Hourly Trend (20)
        # -----------------------------
        if h1.trend == "Bullish":
            score += 20
            reasons.append("Hourly trend bullish")
        elif h1.trend == "Neutral":
            score += 10

        # -----------------------------
        # 15m Trend (10)
        # -----------------------------
        if m15.trend == "Bullish":
            score += 10
            reasons.append("Intraday trend bullish")
        elif m15.trend == "Neutral":
            score += 5

        # -----------------------------
        # Daily RSI (20)
        # -----------------------------
        if 50 <= d1.rsi14 <= 65:
            score += 20
            reasons.append("Healthy RSI")

        elif 40 <= d1.rsi14 < 50:
            score += 10

        elif 65 < d1.rsi14 <= 75:
            score += 10

        # -----------------------------
        # EMA Alignment (20)
        # -----------------------------
        if d1.ema20 > d1.ema50 > d1.ema200:
            score += 20
            reasons.append("EMA alignment bullish")

        elif d1.ema20 > d1.ema200:
            score += 10

        asset.scores["overall"] = score
        asset.scores["reasons"] = reasons