from analysis.recommendation_engine import RecommendationEngine


class SignalEngine:

    @staticmethod
    def analyse(stock):

        recommendation = RecommendationEngine.analyse(stock)

        return {
            "signal": recommendation["signal"],
            "confidence": recommendation["confidence"],
            "reasons": recommendation["summary"],
            "risks": recommendation["risks"],
        }
