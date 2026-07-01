"""
Asset Loader

Loads all assets from the database folder.
"""

from pathlib import Path

import pandas as pd

from core.asset import Asset


class AssetLoader:

    DATABASE = Path("database")

    FILES = [
        "india_master.csv",
        "us_master.csv",
        "crypto_master.csv",
        "macro_master.csv",
    ]

    @staticmethod
    def _float(row, key, default=0.0):

        value = row.get(key, default)

        if pd.isna(value):
            return default

        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def all_assets(self):

        assets = []

        for filename in self.FILES:

            path = self.DATABASE / filename

            if not path.exists():
                print(f"Missing: {path}")
                continue

            df = pd.read_csv(path)

            for _, row in df.iterrows():

                active = str(
                    row.get("Active", "TRUE")
                ).upper()

                if active != "TRUE":
                    continue

                asset = Asset(
                    name=row["Name"],
                    symbol=row["Symbol"],
                    category=row["Sector"],
                    exchange=row.get("Exchange", ""),
                    currency="",
                    country=row["Country"],

                    watchlist=str(row.get("Watchlist", "FALSE")).upper() == "TRUE",
                    portfolio=str(row.get("Portfolio", "FALSE")).upper() == "TRUE",
                    priority=int(row.get("Priority", 3)),
                    quantity=self._float(row, "Quantity"),
                    average_buy=self._float(row, "Average Buy"),
                    target_allocation=self._float(row, "Target Allocation"),
                    theme=row.get("Theme", ""),
                    investment_theme=row.get("Investment Theme", ""),
                    thesis=row.get("Thesis", row.get("Why I Own It", "")),
                    expected_cagr=self._float(row, "Expected CAGR"),
                    time_horizon=row.get("Time Horizon", ""),
                    next_review_date=row.get("Next Review Date", ""),
                    exit_conditions=row.get("Exit Conditions", ""),

                    asset_class=row.get("AssetClass", ""),
                    industry=row.get("Industry", ""),
                    tags=row.get("Tags", ""),
                )

                assets.append(asset)

        return assets
