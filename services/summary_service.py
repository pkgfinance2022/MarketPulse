"""
Summary Service.

Calculates summary information
for every Asset.
"""

from core.asset import Asset
from core.timeseries import time_based_pct_change


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

        # 15m/1H use real timestamps, not a fixed bar count - a fixed
        # count silently spans into the PRIOR session right after a
        # fresh market open (e.g. "4 bars back" for 1H becomes
        # yesterday afternoon vs today's open when today only has 1
        # bar so far), understating or misrepresenting the real
        # trailing move. 4H/1D intentionally still span sessions (a
        # daily change is supposed to cross into the prior session).
        asset.summary.change_15m = time_based_pct_change(close, 15)
        asset.summary.change_1h = time_based_pct_change(close, 60)

        def pct_change(periods: int):

            if len(close) <= periods:
                return None

            old = float(close.iloc[-periods - 1])
            new = float(close.iloc[-1])

            return round(((new - old) / old) * 100, 2)

        asset.summary.change_4h = pct_change(16)
        asset.summary.change_1d = pct_change(96)