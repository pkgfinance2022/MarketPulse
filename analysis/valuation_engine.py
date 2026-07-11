"""
Valuation engine.

Judges whether the price being paid is cheap or expensive - P/E, P/B,
EV/EBITDA, PEG - independent of business quality (that's
FundamentalEngine's job). Shares the same cached Yahoo data so this
doesn't cost a second network round-trip per ticker.
"""

from analysis.band_scoring import average, linear_score
from providers.fundamentals import FundamentalsProvider

VALUATION_ASSET_CLASSES = {"Equity"}

_provider = FundamentalsProvider()


class ValuationEngine:

    @staticmethod
    def _label(score):

        if score is None:
            return "Unknown"

        if score >= 70:
            return "Undervalued"

        if score >= 45:
            return "Fair"

        return "Overvalued"

    @classmethod
    def analyse(cls, asset):

        asset_class = getattr(asset, "asset_class", "")
        symbol = getattr(asset, "symbol", "")

        if asset_class not in VALUATION_ASSET_CLASSES:

            return {
                "pe": None,
                "pb": None,
                "ev_ebitda": None,
                "peg": None,
                "valuation": "Unknown",
                "valuation_score": 50,
            }

        data = _provider.get(symbol)

        pe = data.get("trailingPE") or data.get("forwardPE")
        pb = data.get("priceToBook")
        ev_ebitda = data.get("enterpriseToEbitda")
        peg = data.get("pegRatio")

        scores = []

        # P/E: a negative or absent P/E (no earnings) is genuinely
        # ambiguous for a screener - don't score it, rather than
        # silently treating "no earnings" as "cheap".
        if pe is not None and pe > 0:
            scores.append(
                linear_score(pe, [(8, 100), (15, 80), (25, 55), (40, 30), (70, 10)])
            )

        if pb is not None and pb > 0:
            scores.append(
                linear_score(pb, [(1, 100), (3, 75), (6, 50), (10, 25), (20, 10)])
            )

        if ev_ebitda is not None and ev_ebitda > 0:
            scores.append(
                linear_score(ev_ebitda, [(5, 100), (10, 75), (15, 50), (25, 25), (40, 10)])
            )

        if peg is not None and peg > 0:
            # PEG below 1 is the classic "growth at a reasonable price"
            # threshold.
            scores.append(
                linear_score(peg, [(0.5, 100), (1, 80), (1.5, 55), (2.5, 30), (4, 10)])
            )

        valuation_score = average(scores, default=50)

        return {
            "pe": pe,
            "pb": pb,
            "ev_ebitda": ev_ebitda,
            "peg": peg,
            "valuation": cls._label(valuation_score if scores else None),
            "valuation_score": valuation_score,
        }
