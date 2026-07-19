"""
Cross-asset driver status service.

Computes rolling correlations (see analysis/cross_asset_drivers.py)
for a curated set of major indices - not the whole universe. This is
a real research finding, not a blanket assumption: the driver
relationships only showed up as meaningfully strong for major US
equity benchmarks in the validation check (NASDAQ/S&P), so this
doesn't pretend to have a story for every single Global Indices row.
"""

from analysis.cross_asset_drivers import CrossAssetDriverEngine
from providers.yahoo import YahooProvider

# (ticker, display name) - the major indices the validation check
# (analysis/cross_asset_drivers.py's own docstring) actually found a
# real, current relationship for. Adding more tickers here is cheap,
# but only add ones you've actually checked show a real correlation -
# otherwise this becomes the "textbook chain asserted as fact for
# every ticker" problem this was built to avoid.
CROSS_ASSET_TARGETS = {
    "^GSPC": "S&P 500",
    "^NDX": "NASDAQ 100",
    "^DJI": "Dow Jones",
    "^RUT": "Russell 2000",
}


class CrossAssetStatusService:

    @classmethod
    def screen_correlations(cls):
        """
        Returns {ticker: {"US10Y": corr, "DXY": corr, ...}} for every
        target in CROSS_ASSET_TARGETS - one shared YahooProvider so the
        4 driver histories are fetched once and reused across all
        targets, not re-fetched per target.
        """

        provider = YahooProvider()

        return {
            ticker: CrossAssetDriverEngine.correlations(ticker, provider=provider)
            for ticker in CROSS_ASSET_TARGETS
        }
