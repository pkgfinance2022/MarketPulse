class DashboardStats:

    @staticmethod
    def summary(df):

        return {

            "assets": len(df),

            "bullish":
                len(df[df["Trend"]=="Bullish"]),

            "neutral":
                len(df[df["Trend"]=="Neutral"]),

            "bearish":
                len(df[df["Trend"]=="Bearish"]),

            "avg_rsi":
                df["RSI"].mean(),

            "avg_score":
                df["AI Score"].mean(),

            "avg_volume":
                df["Volume"].mean(),

            "avg_change":
                df["1D %"].mean()

        }