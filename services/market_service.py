"""
Market Service

Downloads market data and builds the repository.
"""

from core.repository import AssetRepository
from providers.yahoo import YahooProvider


class MarketService:

    def __init__(self):

        self.provider = YahooProvider()

    def load_market(self, assets):

        repository = AssetRepository()

        total = len(assets)

        print()
        print("Downloading market data...")
        print()

        for index, asset in enumerate(assets, start=1):

            print(f"[{index}/{total}] {asset.symbol}")

            try:

                asset.data_15m = self.provider.history(
                    asset.symbol,
                    interval="15m",
                    period="5d",
                )

                asset.data_1h = self.provider.history(
                    asset.symbol,
                    interval="1h",
                    period="3mo",
                )

                asset.data_1d = self.provider.history(
                    asset.symbol,
                    interval="1d",
                    period="2y",
                )

                repository.add(asset)

            except Exception as ex:

                print(f"Failed : {asset.symbol} : {ex}")

        return repository