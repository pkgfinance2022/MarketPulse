"""
Fundamental improvement scanner.

Finds which stocks in india_master/us_master have IMPROVING
fundamentals - not just a high snapshot score, but a real
quarter-over-quarter trend in revenue, earnings, margin, ROE, and
analyst sentiment - then checks whether the DAILY-timeframe technical
trend agrees.

On-demand only (run whenever you want - weekly, after earnings season,
etc). Fundamentals don't change hourly or even daily, so this is
deliberately not part of the auto-refreshing dashboard - re-running it
between actual earnings releases would just re-fetch the same
quarterly filings.

Concall/news-level detail is NOT part of this scan (not automatable
across 200+ names with free data) - once this narrows the list down,
ask for a manual deep-dive on a specific name and real research
(web search for recent news/concall commentary) can be done on that
one stock.

The same scan is also available from the dashboard's "💰 Fundamentals"
tab (dashboard/app.py) - both use dashboard/services/fundamental_scan_service.py
so there's only one implementation of the actual scan logic.

Usage:
    python fundamental_scan.py                    # all equities (India + US)
    python fundamental_scan.py --country india
    python fundamental_scan.py --country usa
    python fundamental_scan.py --min-score 2       # only show improving_score >= 2 (range -5..5)
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.loader import AssetLoader
from dashboard.services.fundamental_scan_service import FundamentalScanService


def parse_args():

    parser = argparse.ArgumentParser(description="Scan for stocks with improving fundamentals.")
    parser.add_argument("--country", default=None, choices=["india", "usa"], help="Filter by country")
    parser.add_argument("--min-score", type=int, default=1, help="Minimum improving-score to show (default 1, range -5..5)")
    parser.add_argument("--limit", type=int, default=None, help="Only scan the first N assets (for a quick test run)")

    return parser.parse_args()


def main():

    args = parse_args()

    assets = [a for a in AssetLoader().all_assets() if a.asset_class == "Equity"]

    if args.country:
        assets = [a for a in assets if a.country.lower() == args.country.lower()]

    if args.limit:
        assets = assets[: args.limit]

    print(f"Scanning {len(assets)} stocks for fundamental improvement...\n")

    def progress(index, total, symbol):
        print(f"[{index}/{total}] {symbol}...")

    df = FundamentalScanService.scan(assets, min_score=args.min_score, progress_callback=progress)

    if df.empty:
        print("\nNo stocks matched the improving-fundamentals criteria.")
        return

    print("\n" + "=" * 110)
    print("STOCKS WITH IMPROVING FUNDAMENTALS")
    print("=" * 110)
    print(df.to_string(index=False))

    out_path = PROJECT_ROOT / "fundamental_scan_results.csv"
    df.to_csv(out_path, index=False)
    print(f"\nFull results saved to {out_path}")


if __name__ == "__main__":
    main()
