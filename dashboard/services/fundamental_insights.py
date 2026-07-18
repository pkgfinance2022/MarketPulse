"""
Fundamental insights service.

Reads the local weekly snapshot (database/fundamentals_latest.csv,
written by scripts/fundamental_scan_weekly.py) - never fetches
anything live. Two things need comparing against the PREVIOUS week's
snapshot specifically (PE change, Fundamental Score change - both
depend on price/market data that moves week to week), while
loss-to-profit and EPS trend are already quarter-over-quarter
comparisons baked into each row by FundamentalTrendEngine, so those
need no historical file at all.
"""

from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HISTORY_DIR = REPO_ROOT / "database" / "fundamentals_history"
LATEST_PATH = REPO_ROOT / "database" / "fundamentals_latest.csv"

TOP_N = 10


def _history_files():
    if not HISTORY_DIR.exists():
        return []
    return sorted(HISTORY_DIR.glob("fundamentals_*.csv"))


def load_snapshot():
    """
    Returns (df, latest_date, previous_date) - df has the latest
    snapshot merged with "PE Change" / "Fundamental Score Change"
    columns if a previous snapshot exists, otherwise those columns are
    all NaN. Dates are the YYYY-MM-DD strings embedded in the
    filenames, or None if nothing's been scanned yet.
    """

    if not LATEST_PATH.exists():
        return pd.DataFrame(), None, None

    df = pd.read_csv(LATEST_PATH)

    files = _history_files()

    if not files:
        return df, None, None

    latest_date = files[-1].stem.replace("fundamentals_", "")
    previous_date = files[-2].stem.replace("fundamentals_", "") if len(files) >= 2 else None

    df["PE Change"] = None
    df["Fundamental Score Change"] = None
    df["Improving Score Change"] = None

    if previous_date:

        prev_df = pd.read_csv(files[-2])
        prev_df = prev_df.set_index("Ticker")

        for idx, row in df.iterrows():

            ticker = row["Ticker"]

            if ticker not in prev_df.index:
                continue

            prev_row = prev_df.loc[ticker]

            if pd.notna(row.get("Trailing PE")) and pd.notna(prev_row.get("Trailing PE")):
                df.at[idx, "PE Change"] = round(row["Trailing PE"] - prev_row["Trailing PE"], 2)

            if pd.notna(row.get("Fundamental Score")) and pd.notna(prev_row.get("Fundamental Score")):
                df.at[idx, "Fundamental Score Change"] = row["Fundamental Score"] - prev_row["Fundamental Score"]

            if pd.notna(row.get("Improving Score")) and pd.notna(prev_row.get("Improving Score")):
                df.at[idx, "Improving Score Change"] = row["Improving Score"] - prev_row["Improving Score"]

    return df, latest_date, previous_date


def derive_highlights(df):
    """
    Returns a dict of small, ranked DataFrames - "where to focus"
    instead of just the raw table. Every list is capped at TOP_N so
    this stays a highlight reel, not another wall of rows.
    """

    highlights = {}

    if df.empty:
        return highlights

    loss_to_profit = df[df["Was Loss Now Profit"] == True]  # noqa: E712 - pandas bool column, `is True` doesn't broadcast
    if not loss_to_profit.empty:
        highlights["loss_to_profit"] = loss_to_profit[
            ["Ticker", "Name", "Country", "Latest Net Income", "Fundamental Score"]
        ].sort_values("Latest Net Income", ascending=False).head(TOP_N)

    eps_up = df[df["EPS Trend"] == "up"].copy()
    if not eps_up.empty and "Previous EPS (Quarterly)" in eps_up.columns:
        eps_up["EPS Change"] = eps_up["Latest EPS (Quarterly)"] - eps_up["Previous EPS (Quarterly)"]
        highlights["eps_up"] = eps_up[
            ["Ticker", "Name", "Country", "Previous EPS (Quarterly)", "Latest EPS (Quarterly)", "EPS Change"]
        ].sort_values("EPS Change", ascending=False).head(TOP_N)

    if "PE Change" in df.columns and df["PE Change"].notna().any():

        cheaper = df[df["PE Change"] < 0].sort_values("PE Change").head(TOP_N)
        if not cheaper.empty:
            highlights["pe_compressed"] = cheaper[["Ticker", "Name", "Country", "Trailing PE", "PE Change"]]

        pricier = df[df["PE Change"] > 0].sort_values("PE Change", ascending=False).head(TOP_N)
        if not pricier.empty:
            highlights["pe_expanded"] = pricier[["Ticker", "Name", "Country", "Trailing PE", "PE Change"]]

    top_improving = df[df["Improving Score"] >= 2].sort_values("Improving Score", ascending=False).head(TOP_N)
    if not top_improving.empty:
        highlights["top_improving"] = top_improving[
            ["Ticker", "Name", "Country", "Improving Score", "Fundamental Score", "Revenue Trend", "Earnings Trend"]
        ]

    top_declining = df[df["Improving Score"] <= -2].sort_values("Improving Score").head(TOP_N)
    if not top_declining.empty:
        highlights["top_declining"] = top_declining[
            ["Ticker", "Name", "Country", "Improving Score", "Fundamental Score", "Revenue Trend", "Earnings Trend"]
        ]

    return highlights
