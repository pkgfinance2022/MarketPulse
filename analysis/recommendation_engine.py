"""
Recommendation engine.

Turns the sub-scores that ScoringEngine already computed into a signal,
a confidence level, and a human-readable explanation. Deliberately does
NOT re-derive things like "is RSI healthy" from raw indicators - that
logic already lives in ScoringEngine and re-deriving it here risked the
two disagreeing. This engine only reads results and reasons about them.
"""

class RecommendationEngine:

    @staticmethod
    def _get(stock, key, default=0):

        value = stock.get(key, default)

        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _signal(cls, score, trend_score, risk_score, risk_reward):

        if score >= 80 and trend_score >= 50 and risk_score >= 55 and risk_reward >= 1.5:
            return "STRONG BUY"

        if score >= 65 and trend_score >= 40 and risk_score >= 40:
            return "BUY"

        if score <= 25 or (trend_score <= 25 and score <= 35):
            return "STRONG SELL"

        if score <= 40:
            return "SELL"

        return "HOLD"

    @classmethod
    def _confidence(cls, sub_scores, score):
        """
        Confidence reflects how much the individual factors agree with
        each other and with the overall call, not just the raw score.
        Five factors all mildly bullish should read as more confident
        than one wildly bullish factor dragging three neutral ones.
        """

        direction = 1 if score >= 50 else -1

        agreements = 0
        magnitudes = []

        for value in sub_scores:

            distance = value - 50
            magnitudes.append(abs(distance))

            if distance == 0:
                agreements += 0.5
            elif (distance > 0 and direction > 0) or (distance < 0 and direction < 0):
                agreements += 1

        agreement_fraction = agreements / len(sub_scores) if sub_scores else 0
        avg_magnitude = sum(magnitudes) / len(magnitudes) if magnitudes else 0

        # avg_magnitude maxes out around 50 (fully at 0 or 100), scale to 0-100
        conviction = min(100, avg_magnitude * 2)

        confidence = conviction * (0.5 + 0.5 * agreement_fraction)

        return max(0, min(100, round(confidence)))

    @classmethod
    def analyse(cls, stock):

        score = cls._get(stock, "AI Score")
        technical = cls._get(stock, "Technical Score", 50)
        fundamental = cls._get(stock, "Fundamental Score", 50)
        valuation = cls._get(stock, "Valuation Score", 50)
        momentum = cls._get(stock, "Momentum Score", 50)
        trend = cls._get(stock, "Trend Score", 50)
        risk_score = cls._get(stock, "Risk Score", 50)
        risk_reward = cls._get(stock, "Risk Reward", 0)

        summary = []
        risks = []

        if technical >= 65:
            summary.append("Price structure is strong (above key EMAs / near breakout)")
        elif technical <= 35:
            risks.append("Price structure is weak (below key EMAs)")

        if fundamental >= 65:
            summary.append("Fundamentals are healthy (profitability & growth)")
        elif fundamental <= 35:
            risks.append("Fundamentals are weak")

        if valuation >= 65:
            summary.append("Valuation looks reasonable to cheap")
        elif valuation <= 35:
            risks.append("Valuation looks stretched")

        if momentum >= 65:
            summary.append("Momentum is strong")
        elif momentum <= 35:
            risks.append("Momentum is fading")

        if trend >= 65:
            summary.append("Trend is aligned bullish across timeframes")
        elif trend <= 35:
            risks.append("Trend is aligned bearish across timeframes")
        elif 35 < trend < 65:
            risks.append("Timeframes disagree on trend direction")

        if risk_reward and risk_reward < 1:
            risks.append(f"Risk/reward is unfavorable ({risk_reward:.2f})")
        elif risk_reward and risk_reward >= 2:
            summary.append(f"Risk/reward is attractive ({risk_reward:.2f})")

        if risk_score <= 35:
            risks.append("Downside to stop-loss is wide relative to price")

        price = cls._get(stock, "Price")
        stop_loss = cls._get(stock, "Stop Loss")

        if price and stop_loss and price <= stop_loss:
            risks.append("Price is at or below stop loss")

        signal = cls._signal(score, trend, risk_score, risk_reward)

        confidence = cls._confidence(
            [technical, fundamental, valuation, momentum, trend, risk_score],
            score,
        )

        return {
            "signal": signal,
            "confidence": confidence,
            "summary": summary,
            "risks": risks,
        }
