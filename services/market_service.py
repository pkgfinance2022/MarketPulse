"""
Market Service

Downloads market data for all configured assets.
"""

from core.asset import Asset
from core.repository import AssetRepository
from providers.yahoo import YahooProvider


class MarketService:

    def __init__(self):

        self.provider = YahooProvider()

    def load_market(self, assets: list[dict]) -> AssetRepository:

        repository = AssetRepository()

        total = len(assets)

        print()
        print("Downloading market data...")
        print()

        for index, info in enumerate(assets, start=1):

            print(f"[{index}/{total}] {info['symbol']}")

            asset = Asset(
                name=info["name"],
                symbol=info["symbol"],
                category=info["category"],
                exchange=info.get("exchange", ""),
                currency=info.get("currency", ""),
            )

            # 15 Minute
            asset.data_15m = self.provider.download(
                symbol=asset.symbol,
                period="60d",
                interval="15m",
            )

            # 1 Hour
            asset.data_1h = self.provider.download(
                symbol=asset.symbol,
                period="730d",
                interval="1h",
            )

            # Daily
            asset.data_1d = self.provider.download(
                symbol=asset.symbol,
                period="5y",
                interval="1d",
            )

            repository.add(asset)

        return repository