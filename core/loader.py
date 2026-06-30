"""
Loads application assets from assets.yaml
"""

from pathlib import Path
import yaml


class AssetLoader:
    """Loads all assets defined in assets.yaml."""

    def __init__(self, filename: str = "assets.yaml"):
        self.filename = Path(filename)

    def load(self) -> dict:

        if not self.filename.exists():
            raise FileNotFoundError(
                f"File not found: {self.filename}"
            )

        with open(
            self.filename,
            "r",
            encoding="utf-8",
        ) as file:

            return yaml.safe_load(file)

    def all_assets(self) -> list[dict]:

        data = self.load()

        assets = []

        for category, values in data.items():

            for item in values:

                assets.append(
                    {
                        "name": item["name"],
                        "symbol": item["symbol"],
                        "category": category,
                        "exchange": item.get("exchange", ""),
                        "currency": item.get("currency", ""),
                    }
                )

        return assets