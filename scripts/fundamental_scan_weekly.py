"""
Standalone weekly fundamentals scanner.

Runs independently of the Streamlit app (same reasoning as
scripts/telegram_scan.py - a scheduled GitHub Actions workflow, so
this stays pre-loaded regardless of whether anyone has the dashboard
open). Scans every US + Indian equity's fundamentals once a week and
writes a dated snapshot plus a "latest" file the app reads directly -
the app never fetches this live, only reads what's already on disk.

Fundamentals change at most quarterly, so a weekly cadence is already
far more often than needed - this is really about having a fresh,
comparable snapshot on a predictable schedule, not about catching
same-day changes.

Deliberately captures EVERY equity, not just ones with "improving"
fundamentals right now (unlike FundamentalScanService.scan(), which
filters by min_score for the on-demand CLI/tab use case) - a stock
that looks weak this week is exactly the one worth watching to see if
it turns around next week, and filtering it out here would make that
comparison impossible.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

from analysis.fundamental_engine import FundamentalEngine
from analysis.fundamental_trend import FundamentalTrendEngine
from core.loader import AssetLoader
from providers.fundamentals import FundamentalsProvider

HISTORY_DIR = REPO_ROOT / "database" / "fundamentals_history"
LATEST_PATH = REPO_ROOT / "database" / "fundamentals_latest.csv"

_fundamentals_provider = FundamentalsProvider()


def _latest_history_file(exclude_today):
    """Returns the most recent dated snapshot before today, or None."""

    if not HISTORY_DIR.exists():
        return None

    files = sorted(HISTORY_DIR.glob("fundamentals_*.csv"))
    files = [f for f in files if f.stem != f"fundamentals_{exclude_today}"]

    return files[-1] if files else None


def scan_one(asset):

    try:
        fundamental = FundamentalEngine.analyse(asset)
        trend = FundamentalTrendEngine.analyse(asset.symbol)
        raw = _fundamentals_provider.get(asset.symbol)
    except Exception as e:
        print(f"  {asset.symbol}: FAILED ({e})")
        return None

    return {
        "Ticker": asset.symbol,
        "Name": asset.name,
        "Country": asset.country,
        "Fundamental Score": fundamental["fundamental_score"],
        "ROE %": fundamental["roe"],
        "Sales Growth %": fundamental["sales_growth"],
        "Profit Growth %": fundamental["profit_growth"],
        "Profit Margin %": fundamental["profit_margin"],
        "Debt/Equity": fundamental["debt_equity"],
        "Trailing PE": raw.get("trailingPE"),
        "Forward PE": raw.get("forwardPE"),
        "Trailing EPS": raw.get("trailingEps"),
        "Forward EPS": raw.get("forwardEps"),
        "Improving Score": trend["improving_score"],
        "Revenue Trend": FundamentalTrendEngine.label(trend["revenue_trend"]),
        "Earnings Trend": FundamentalTrendEngine.label(trend["earnings_trend"]),
        "Margin Trend": FundamentalTrendEngine.label(trend["margin_trend"]),
        "ROE Trend": FundamentalTrendEngine.label(trend["roe_trend"]),
        "EPS Trend": FundamentalTrendEngine.label(trend["eps_trend"]),
        "Analyst Trend": FundamentalTrendEngine.label(trend["analyst_trend"]),
        "Was Loss Now Profit": trend["was_loss_now_profit"],
        "Latest Net Income": trend["latest_net_income"],
        "Latest Revenue": trend["latest_revenue"],
        "Latest EPS (Quarterly)": trend["latest_eps"],
        "Previous EPS (Quarterly)": trend["previous_eps"],
    }


def main():

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    assets = [a for a in AssetLoader().all_assets() if a.asset_class == "Equity" and a.country in ("USA", "India")]

    print(f"Scanning {len(assets)} US + Indian equities for fundamentals ({today})...")

    rows = []

    for i, asset in enumerate(assets, start=1):
        print(f"[{i}/{len(assets)}] {asset.symbol}")
        row = scan_one(asset)
        if row:
            rows.append(row)

    df = pd.DataFrame(rows)
    print(f"\n{len(df)}/{len(assets)} scanned successfully.")

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    dated_path = HISTORY_DIR / f"fundamentals_{today}.csv"
    df.to_csv(dated_path, index=False)
    df.to_csv(LATEST_PATH, index=False)

    print(f"Saved to {dated_path} and {LATEST_PATH}")

    previous_file = _latest_history_file(exclude_today=today)
    print(f"Previous snapshot for comparison: {previous_file if previous_file else 'none yet - this is the first run'}")


if __name__ == "__main__":
    main()
