"""
Summary Service.

Calculates summary information
for every Asset.
"""

from core.asset import Asset


class SummaryService:

    @staticmethod
    def build(asset: Asset):

        if asset.data_15m.empty:
            return

        df = asset.data_15m

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        asset.summary.price = float(close.iloc[-1])
        asset.summary.high = float(high.iloc[-1])
        asset.summary.low = float(low.iloc[-1])
        asset.summary.volume = float(volume.iloc[-1])

        # --------------------------
        # Percentage Changes
        # --------------------------

        def pct_change(periods: int):

            if len(close) <= periods:
                return None

            old = float(close.iloc[-periods - 1])
            new = float(close.iloc[-1])

            return round(((new - old) / old) * 100, 2)

        asset.summary.change_15m = pct_change(1)
        asset.summary.change_1h = pct_change(4)
        asset.summary.change_4h = pct_change(16)
        asset.summary.change_1d = pct_change(96)