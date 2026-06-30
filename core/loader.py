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

    def all_assets(self):

        assets = []

        for filename in self.FILES:

            path = self.DATABASE / filename

            if not path.exists():
                print(f"Missing: {path}")
                continue

            df = pd.read_csv(path)

            for _, row in df.iterrows():

                active = str(row.get("Active", "TRUE")).upper()

                if active != "TRUE":
                    continue

                assets.append(
                    Asset(
                        name=row["Name"],
                        symbol=row["Symbol"],
                        category=row["Sector"],
                        exchange=row.get("Exchange", ""),
                        currency="",
                        country=row["Country"],
                    )
                )

        return assets