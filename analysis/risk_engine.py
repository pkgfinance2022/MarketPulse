"""
Risk engine.

Calculates downside, upside, risk label, and risk/reward from canonical fields.
"""


class RiskEngine:

    @staticmethod
    def analyse(stock):

        price = float(stock.get("Price", 0.0) or 0.0)
        support = float(stock.get("Support", 0.0) or 0.0)
        resistance = float(stock.get("Resistance", 0.0) or 0.0)
        stop_loss = float(stock.get("Stop Loss", support) or 0.0)

        if price <= 0:
            return {
                "risk": "UNKNOWN",
                "stop_loss": 0.0,
                "target1": 0.0,
                "target2": 0.0,
                "downside": 0.0,
                "upside": 0.0,
                "risk_reward": 0.0,
                "risk_score": 0,
            }

        downside = max(price - stop_loss, 0.0)
        target1 = resistance if resistance > price else price + downside * 2
        target2 = price + downside * 3 if downside else target1
        upside = max(target1 - price, 0.0)
        risk_reward = round(upside / downside, 2) if downside else 0.0

        risk_pct = (downside / price) * 100 if downside else 0.0

        if risk_pct <= 4:
            risk = "LOW"
            risk_score = 85
        elif risk_pct <= 8:
            risk = "MEDIUM"
            risk_score = 65
        else:
            risk = "HIGH"
            risk_score = 40

        return {
            "risk": risk,
            "stop_loss": round(stop_loss, 2),
            "target1": round(target1, 2),
            "target2": round(target2, 2),
            "downside": round(downside, 2),
            "upside": round(upside, 2),
            "risk_reward": risk_reward,
            "risk_score": risk_score,
        }
