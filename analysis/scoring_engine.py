"""
Canonical scoring engine for MarketPulse.

Every factor is normalized to 0-100, then combined into one AI Score
using the weights below. Design notes:

- Technical and Momentum used to both lean on RSI/MACD, so "diversified"
  factors were secretly correlated. Technical is now purely structural
  (EMA stack, price position, breakout proximity); Momentum owns the
  oscillators (RSI/MACD/ADX/rate of change).
- Fundamental and Valuation are real now (see fundamental_engine.py /
  valuation_engine.py) instead of a constant 50, and Valuation is
  actually part of the weighted formula - but only for equities, since
  P/E and ROE don't exist for gold or EUR/USD.
- Risk Score is computed once, properly, by RiskEngine (using real
  support/resistance/stop-loss distance) before this engine runs. This
  engine reads that value rather than recomputing a different, cruder
  one and silently overwriting it.
- News has no real data source yet, so it is not counted in the score
  (a constant "50" contributing weight is noise, not signal). It's
  still computed/displayed for future use once a real source exists.
- Non-equity assets (indices, forex, commodities, bonds, ETFs) get a
  different weight profile. Fundamental/Valuation are neutral 50 for
  them by definition - if they kept 40% weight, every macro asset's
  score would be artificially compressed toward the middle regardless
  of how strong or weak its actual setup is. That weight is instead
  redistributed across the factors that ARE real for those assets.
"""


class ScoringEngine:

    # Used when Fundamental/Valuation data actually exists (equities).
    EQUITY_WEIGHTS = {
        "Technical": 0.20,
        "Fundamental": 0.25,
        "Valuation": 0.15,
        "Momentum": 0.15,
        "Trend": 0.15,
        "Risk": 0.10,
    }

    # Used for indices, forex, commodities, bonds, ETFs - anything
    # FundamentalEngine/ValuationEngine can't score for real. The 40%
    # that would have gone to Fundamental+Valuation is redistributed
    # across Technical/Momentum/Trend/Risk in roughly the same
    # proportions they held before, plus a bit extra on Risk since
    # macro instruments (rates, FX) can move on gap/event risk that
    # equities usually don't.
    MACRO_WEIGHTS = {
        "Technical": 0.30,
        "Momentum": 0.25,
        "Trend": 0.25,
        "Risk": 0.20,
    }

    # Asset classes that get real Fundamental/Valuation data. Keep in
    # sync with FUNDAMENTAL_ASSET_CLASSES / VALUATION_ASSET_CLASSES.
    EQUITY_ASSET_CLASSES = {"Equity"}

    # Backward-compatible alias - existing callers of configure()
    # still work and update the equity profile.
    WEIGHTS = EQUITY_WEIGHTS

    @classmethod
    def configure(cls, weights, profile="equity"):

        total = sum(weights.values())

        if not total:
            raise ValueError("Scoring weights cannot be empty")

        normalized = {
            key: value / total
            for key, value in weights.items()
        }

        if profile == "macro":
            cls.MACRO_WEIGHTS = normalized
        else:
            cls.EQUITY_WEIGHTS = normalized
            cls.WEIGHTS = normalized

    @classmethod
    def _weights_for(cls, stock):

        asset_class = stock.get("Market", "Equity")

        if asset_class in cls.EQUITY_ASSET_CLASSES:
            return cls.EQUITY_WEIGHTS

        return cls.MACRO_WEIGHTS

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
        """
        Purely structural: where is price relative to its moving
        averages, and is it breaking out. No oscillators here - those
        belong to momentum_score.
        """

        price = cls._num(stock.get("Price"))
        ema20 = cls._num(stock.get("EMA20"))
        ema50 = cls._num(stock.get("EMA50"))
        ema200 = cls._num(stock.get("EMA200"))
        resistance = cls._num(stock.get("Resistance"))
        support = cls._num(stock.get("Support"))

        score = 0

        if price and ema20 and price > ema20:
            score += 25

        if ema20 and ema50 and ema20 > ema50:
            score += 25

        if ema50 and ema200 and ema50 > ema200:
            score += 25

        if price and resistance and price > resistance:
            # Genuine breakout above recent resistance.
            score += 25
        elif price and support and resistance and resistance > support:
            # Reward sitting in the upper half of the recent range,
            # scaled smoothly instead of a flat bonus.
            position = (price - support) / (resistance - support)
            score += max(0, min(25, position * 25))

        return cls._clamp(score)

    @classmethod
    def momentum_score(cls, stock):
        """
        Rate-of-change and oscillator strength: RSI, MACD, ADX,
        short-term price change.
        """

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
        """
        Multi-timeframe alignment. A daily "bullish" label alone is a
        weak signal; the same direction holding across 15m/1H/1D is a
        much stronger one, and a 1D trend contradicted by the shorter
        timeframes is worth flagging as weaker, not equally "bullish".
        """

        timeframes = [
            (str(stock.get("15m Trend", "")).lower(), 1),
            (str(stock.get("1H Trend", "")).lower(), 2),
            (str(stock.get("1D Trend", "")).lower(), 3),
        ]

        total_weight = sum(w for _, w in timeframes)
        weighted = 0

        for trend, weight in timeframes:

            if trend == "bullish":
                weighted += weight * 90
            elif trend == "bearish":
                weighted += weight * 20
            else:
                weighted += weight * 55

        if total_weight == 0:
            return 55

        return cls._clamp(weighted / total_weight)

    @classmethod
    def score(cls, stock):

        weights = cls._weights_for(stock)

        technical = cls.technical_score(stock)
        fundamental = cls._clamp(stock.get("Fundamental Score", 50))
        valuation = cls._clamp(stock.get("Valuation Score", 50))
        momentum = cls.momentum_score(stock)
        trend = cls.trend_score(stock)

        # Risk Score is computed by RiskEngine from real stop-loss /
        # support / resistance distance before this runs - use it
        # as-is rather than deriving a second, conflicting number.
        risk = cls._clamp(stock.get("Risk Score", 50))

        news = cls._clamp(stock.get("News Score", 50))

        total = (
            technical * weights.get("Technical", 0)
            + fundamental * weights.get("Fundamental", 0)
            + valuation * weights.get("Valuation", 0)
            + momentum * weights.get("Momentum", 0)
            + trend * weights.get("Trend", 0)
            + risk * weights.get("Risk", 0)
            + news * weights.get("News", 0)
        )

        stock["Technical Score"] = technical
        stock["Fundamental Score"] = fundamental
        stock["Valuation Score"] = valuation
        stock["Momentum Score"] = momentum
        stock["Trend Score"] = trend
        stock["Risk Score"] = risk
        stock["News Score"] = news
        stock["AI Score"] = cls._clamp(total)
        stock["Score"] = stock["AI Score"]

        return stock