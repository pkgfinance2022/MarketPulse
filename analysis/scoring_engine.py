"""
Canonical scoring engine for MarketPulse.

Every score is normalized to 0-100, then combined with the Sprint 2 weights.
"""


class ScoringEngine:

    WEIGHTS = {
        "Technical": 0.30,
        "Fundamental": 0.25,
        "Momentum": 0.15,
        "Trend": 0.15,
        "Risk": 0.10,
        "News": 0.05,
    }

    @classmethod
    def configure(cls, weights):

        total = sum(weights.values())

        if not total:
            raise ValueError("Scoring weights cannot be empty")

        cls.WEIGHTS = {
            key: value / total
            for key, value in weights.items()
        }

    @staticmethod
    def _num(value, default=0.0):

        if value is None:
            return default

        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clamp(value):

        return max(0, min(100, int(round(value))))

    @classmethod
    def technical_score(cls, stock):

        price = cls._num(stock.get("Price"))
        ema20 = cls._num(stock.get("EMA20"))
        ema50 = cls._num(stock.get("EMA50"))
        ema200 = cls._num(stock.get("EMA200"))
        rsi = cls._num(stock.get("RSI"))
        macd = cls._num(stock.get("MACD"))

        score = 0

        if price and ema20 and price > ema20:
            score += 20

        if ema20 and ema50 and ema20 > ema50:
            score += 20

        if ema50 and ema200 and ema50 > ema200:
            score += 20

        if 45 <= rsi <= 65:
            score += 25
        elif 35 <= rsi < 45 or 65 < rsi <= 75:
            score += 15

        if macd > 0:
            score += 15

        return cls._clamp(score)

    @classmethod
    def momentum_score(cls, stock):

        rsi = cls._num(stock.get("RSI"))
        macd = cls._num(stock.get("MACD"))
        adx = cls._num(stock.get("ADX"))
        change = cls._num(stock.get("Change %"))

        score = 40

        if 50 <= rsi <= 70:
            score += 20
        elif rsi > 70:
            score += 5
        elif rsi < 35:
            score -= 10

        if macd > 0:
            score += 15

        if adx >= 25:
            score += 15
        elif adx >= 18:
            score += 8

        if change > 0:
            score += 10
        elif change < 0:
            score -= 10

        return cls._clamp(score)

    @classmethod
    def trend_score(cls, stock):

        trend = str(stock.get("Trend", "")).lower()

        if trend == "bullish":
            return 90

        if trend == "bearish":
            return 25

        return 55

    @classmethod
    def risk_score(cls, stock):

        price = cls._num(stock.get("Price"))
        stop = cls._num(stock.get("Stop Loss"))
        atr = cls._num(stock.get("ATR"))

        if price <= 0:
            return 0

        risk_pct = ((price - stop) / price) * 100 if stop else 0
        atr_pct = (atr / price) * 100 if atr else 0

        score = 75

        if risk_pct > 12:
            score -= 25
        elif risk_pct > 8:
            score -= 10

        if atr_pct > 7:
            score -= 20
        elif atr_pct > 4:
            score -= 10

        return cls._clamp(score)

    @classmethod
    def score(cls, stock):

        technical = cls.technical_score(stock)
        fundamental = cls._clamp(stock.get("Fundamental Score", 50))
        momentum = cls.momentum_score(stock)
        trend = cls.trend_score(stock)
        risk = cls.risk_score(stock)
        news = cls._clamp(stock.get("News Score", 50))

        total = (
            technical * cls.WEIGHTS["Technical"]
            + fundamental * cls.WEIGHTS["Fundamental"]
            + momentum * cls.WEIGHTS["Momentum"]
            + trend * cls.WEIGHTS["Trend"]
            + risk * cls.WEIGHTS["Risk"]
            + news * cls.WEIGHTS["News"]
        )

        stock["Technical Score"] = technical
        stock["Fundamental Score"] = fundamental
        stock["Momentum Score"] = momentum
        stock["Trend Score"] = trend
        stock["Risk Score"] = risk
        stock["News Score"] = news
        stock["AI Score"] = cls._clamp(total)
        stock["Score"] = stock["AI Score"]

        return stock
