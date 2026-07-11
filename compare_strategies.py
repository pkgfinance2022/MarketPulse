"""
Compares the current AI-Score signal engine against three simple,
well-known strategies (trend-following, mean-reversion, breakout) on
the same historical data, so we can honestly see whether any of them
has a repeatable edge before trusting one for real buy/sell decisions.

The current engine's five-way Signal (STRONG BUY/BUY/HOLD/SELL/STRONG
SELL) is collapsed to LONG/FLAT/SHORT (BUY-family -> LONG, SELL-family
-> SHORT) so it's directly comparable to the other three strategies,
which are LONG/FLAT/SHORT by construction.

Usage:
    python compare_strategies.py                        # default index universe, 10y
    python compare_strategies.py --sector "US Indices"   # narrower universe
    python compare_strategies.py --period 15y            # longer history
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.backtest_engine import BacktestEngine
from analysis.strategy_lab import StrategyLab
from core.loader import AssetLoader

INDEX_SECTORS = {"Indian Indices", "US Indices", "European Indices", "Asian Indices"}

SIGNAL_TO_DIRECTION = {
    "STRONG BUY": "LONG",
    "BUY": "LONG",
    "HOLD": "FLAT",
    "SELL": "SHORT",
    "STRONG SELL": "SHORT",
}


def parse_args():

    parser = argparse.ArgumentParser(description="Compare the current signal engine vs simple strategies.")
    parser.add_argument("--country", default=None)
    parser.add_argument("--sector", default=None)
    parser.add_argument("--period", default="10y")
    return parser.parse_args()


def resolve_symbols(args):

    assets = AssetLoader().all_assets()

    if args.country:
        assets = [a for a in assets if a.country.lower() == args.country.lower()]
    elif args.sector:
        assets = [a for a in assets if a.category == args.sector]
    else:
        assets = [a for a in assets if a.category in INDEX_SECTORS]

    return [a.symbol for a in assets]


def print_summary(label, df):

    print("\n" + "=" * 100)
    print(f"{label}  (n={len(df)} signal-days)")
    print("=" * 100)

    if df.empty:
        print("No signals generated.")
        return

    print(BacktestEngine.summarize(df).to_string(index=False))


def main():

    args = parse_args()
    symbols = resolve_symbols(args)

    if not symbols:
        print("No assets matched those filters.")
        return

    print(f"Universe: {len(symbols)} symbols, period={args.period}\n")

    print("--- Current Engine (AI Score / Signal) ---")
    engine_df = BacktestEngine.run(symbols, period=args.period)

    if not engine_df.empty:
        engine_df["Signal"] = engine_df["Signal"].map(SIGNAL_TO_DIRECTION)

    print_summary("CURRENT ENGINE (collapsed to LONG/FLAT/SHORT)", engine_df)

    for strategy_name in StrategyLab.STRATEGIES:

        print(f"\n--- {strategy_name} ---")
        strat_df = StrategyLab.run(symbols, strategy_name, period=args.period)
        print_summary(strategy_name.upper(), strat_df)

    print("\n" + "=" * 100)
    print("Full per-signal-day rows are NOT saved by this script (comparison-only).")
    print("Use backtest.py for the current engine's raw CSV export.")


if __name__ == "__main__":
    main()
