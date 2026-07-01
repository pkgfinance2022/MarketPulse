"""
Recommendation engine.

Turns the combined intelligence payload into a signal, confidence, and
human-readable explanation.
"""


class RecommendationEngine:

    @staticmethod
    def analyse(stock):

        score = int(stock.get("AI Score", stock.get("Score", 0)) or 0)
        trend = str(stock.get("Trend", "Neutral"))
        risk_score = int(stock.get("Risk Score", 0) or 0)

        summary = []
        risks = []

        if trend == "Bullish":
            summary.append("Trend is bullish")
        elif trend == "Bearish":
            risks.append("Trend is bearish")

        if stock.get("EMA20", 0) > stock.get("EMA50", 0) > stock.get("EMA200", 0):
            summary.append("EMA alignment is bullish")

        if 45 <= stock.get("RSI", 0) <= 70:
            summary.append("RSI is healthy")
        elif stock.get("RSI", 0) > 70:
            risks.append("RSI is overbought")

        if stock.get("MACD", 0) > 0:
            summary.append("MACD is bullish")

        if risk_score < 50:
            risks.append("Risk score is weak")

        if stock.get("Price", 0) <= stock.get("Stop Loss", 0):
            risks.append("Price is near or below stop loss")

        if score >= 75 and trend != "Bearish" and risk_score >= 45:
            signal = "BUY"
        elif score <= 40 or trend == "Bearish":
            signal = "SELL"
        else:
            signal = "HOLD"

        return {
            "signal": signal,
            "confidence": min(score, 100),
            "summary": summary,
            "risks": risks,
        }
