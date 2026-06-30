"""
Database Validator

Checks MarketPulse database integrity.
"""

from pathlib import Path
import pandas as pd

DATABASE = Path("database")

FILES = [
    "india_master.csv",
    "us_master.csv",
    "crypto_master.csv",
    "macro_master.csv",
]

REQUIRED_COLUMNS = [
    "Country",
    "AssetClass",
    "Sector",
    "Industry",
    "Name",
    "Symbol",
    "Exchange",
    "Priority",
    "Watchlist",
    "Portfolio",
    "Active",
    "Tags",
    "Notes",
]


def main():

    print("=" * 70)
    print("MARKETPULSE DATABASE VALIDATOR")
    print("=" * 70)

    total = 0
    duplicates = set()

    all_symbols = []

    for file in FILES:

        path = DATABASE / file

        print(f"\nChecking {file}")

        if not path.exists():
            print("❌ Missing file")
            continue

        df = pd.read_csv(path)

        print(f"Rows : {len(df)}")

        total += len(df)

        missing = [
            col
            for col in REQUIRED_COLUMNS
            if col not in df.columns
        ]

        if missing:
            print("Missing columns:", missing)

        for symbol in df["Symbol"]:

            if symbol in all_symbols:
                duplicates.add(symbol)

            all_symbols.append(symbol)

    print("\n" + "=" * 70)

    print(f"Total Assets : {total}")

    print(f"Unique Symbols : {len(set(all_symbols))}")

    print(f"Duplicate Symbols : {len(duplicates)}")

    if duplicates:

        print()

        print("Duplicates:")

        for symbol in sorted(duplicates):
            print(symbol)

    else:

        print("✓ No duplicates")


if __name__ == "__main__":
    main()