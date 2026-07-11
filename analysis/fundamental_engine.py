"""
Fundamental analysis engine.

Scores the underlying business quality: profitability, growth, and
balance-sheet strength. Deliberately separate from ValuationEngine,
which judges whether the stock is cheap or expensive for what it is -
a great business can still be a bad buy at the wrong price, and mixing
the two into one number hides that.

Only equities carry meaningful fundamentals in this dataset (crypto,
indices, commodities, forex, bonds do not), so anything else returns a
neutral score rather than a misleading one.
"""

from analysis.band_scoring import average, linear_score
from providers.fundamentals import FundamentalsProvider

FUNDAMENTAL_ASSET_CLASSES = {"Equity"}

_provider = FundamentalsProvider()


class FundamentalEngine:

    @staticmethod
    def _pct(value):

        # yfinance reports growth/margin/return fields as fractions
        # (0.184 = 18.4%), not percentages.
        if value is None:
            return None

        return value * 100

    @classmethod
    def analyse(cls, asset):

        asset_class = getattr(asset, "asset_class", "")
        symbol = getattr(asset, "symbol", "")

        if asset_class not in FUNDAMENTAL_ASSET_CLASSES:

            return {
                "roe": None,
                "sales_growth": None,
                "profit_growth": None,
                "profit_margin": None,
                "debt_equity": None,
                "fundamental_score": 50,
                "available": False,
            }

        data = _provider.get(symbol)

        roe = cls._pct(data.get("returnOnEquity"))
        sales_growth = cls._pct(data.get("revenueGrowth"))

        profit_growth_raw = data.get("earningsGrowth")

        if profit_growth_raw is None:
            profit_growth_raw = data.get("earningsQuarterlyGrowth")

        profit_growth = cls._pct(profit_growth_raw)
        profit_margin = cls._pct(data.get("profitMargins"))
        debt_equity = data.get("debtToEquity")

        scores = [
            # Return on equity: how efficiently the company turns
            # shareholder capital into profit.
            linear_score(
                roe,
                [(0, 20), (8, 45), (15, 70), (25, 90), (40, 100)],
            ),

            # Revenue growth: is the top line actually expanding.
            linear_score(
                sales_growth,
                [(-10, 10), (0, 40), (8, 65), (15, 85), (30, 100)],
            ),

            # Earnings growth: is that revenue turning into more profit.
            linear_score(
                profit_growth,
                [(-20, 10), (0, 40), (10, 65), (25, 85), (50, 100)],
            ),

            # Net margin: how profitable the business is per dollar of
            # revenue.
            linear_score(
                profit_margin,
                [(-10, 10), (0, 35), (5, 55), (15, 80), (30, 100)],
            ),

            # Debt/Equity: lower leverage is safer. yfinance reports
            # this already scaled (45.3 means a D/E of 0.45).
            linear_score(
                debt_equity,
                [(0, 100), (50, 90), (100, 70), (200, 45), (400, 15)],
            ),
        ]

        fundamental_score = average(scores, default=50)

        return {
            "roe": roe,
            "sales_growth": sales_growth,
            "profit_growth": profit_growth,
            "profit_margin": profit_margin,
            "debt_equity": debt_equity,
            "fundamental_score": fundamental_score,
            "available": any(s is not None for s in scores),
        }
