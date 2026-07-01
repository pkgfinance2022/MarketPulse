class SignalService:

    @staticmethod
    def summary(stock):

        # combine RSI
        # EMA alignment
        # MACD
        # ADX
        # Volume
        # ATR

        return {
            "signal": "BUY",
            "confidence": 88,
            "reason": [
                "Above EMA200",
                "RSI healthy",
                "MACD bullish",
                "Strong volume"
            ]
        }