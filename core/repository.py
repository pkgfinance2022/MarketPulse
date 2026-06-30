"""
Asset Repository.

Stores every Asset object in memory.

Every other module talks to the Repository.
"""

from core.asset import Asset


class AssetRepository:

    def __init__(self):

        self._assets: dict[str, Asset] = {}

    # --------------------------------------------------

    def add(self, asset: Asset):

        self._assets[asset.symbol] = asset

    # --------------------------------------------------

    def get(self, symbol: str) -> Asset | None:

        return self._assets.get(symbol)

    # --------------------------------------------------

    def all(self) -> list[Asset]:

        return list(self._assets.values())

    # --------------------------------------------------

    def count(self) -> int:

        return len(self._assets)

    # --------------------------------------------------

    def exists(self, symbol: str) -> bool:

        return symbol in self._assets

    # --------------------------------------------------

    def clear(self):

        self._assets.clear()