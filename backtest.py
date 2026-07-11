"""
Backtest runner.

Answers "is the Signal engine actually any good" by replaying it over
real historical data instead of trusting its hand-picked weights on
faith. See analysis/backtest_engine.py for the exact methodology and
its one real limitation (no historical fundamentals).

Usage:
    python backtest.py                        # every asset in the database
    python backtest.py --sector "US Indices"   # just one sector
    python backtest.py --country usa           # just one country
    python backtest.py --period 5y             # override history length (default 10y)
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.backtest_engine import BacktestEngine
from core.loader import AssetLoader


def parse_args():

    parser = argparse.ArgumentParser(description="Backtest MarketPulse's signal engine.")

    parser.add_argument("--country", default=None, help="Filter by country (usa, india, crypto, global)")
    parser.add_argument("--sector", default=None, help="Filter by sector/category (e.g. 'US Indices')")
    parser.add_argument("--period", default="10y", help="History length to backtest over (default 10y)")

    return parser.parse_args()


def main():

    args = parse_args()

    assets = AssetLoader().all_assets()

    if args.country:
        assets = [a for a in assets if a.country.lower() == args.country.lower()]

    if args.sector:
        assets = [a for a in assets if a.category == args.sector]

    symbols = [a.symbol for a in assets]

    if not symbols:
        print("No assets matched those filters.")
        return

    print(f"Backtesting {len(symbols)} symbol(s) over {args.period} of daily history...\n")

    df = BacktestEngine.run(symbols, period=args.period)

    if df.empty:
        print("\nNo usable signals were generated (not enough history for any symbol).")
        return

    summary = BacktestEngine.summarize(df)

    print("\n" + "=" * 100)
    print("BACKTEST SUMMARY (forward returns after each signal, vs. an all-days baseline)")
    print("=" * 100)
    print(summary.to_string(index=False))

    out_path = PROJECT_ROOT / "backtest_results.csv"
    df.to_csv(out_path, index=False)
    print(f"\nRaw per-signal rows saved to {out_path}")


if __name__ == "__main__":
    main()
