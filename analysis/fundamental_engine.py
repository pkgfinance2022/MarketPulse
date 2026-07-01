"""
Fundamental analysis engine.

The default implementation is deliberately conservative until a fundamental
data source is connected.
"""


class FundamentalEngine:

    @staticmethod
    def analyse(asset):

        return {
            "roe": 0.0,
            "roce": 0.0,
            "sales_growth": 0.0,
            "profit_growth": 0.0,
            "debt_equity": 0.0,
            "fundamental_score": 50,
        }
