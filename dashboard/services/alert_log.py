"""
Alert log.

Every time the RSI-wave checker fires a real alert (LONG/SHORT entry),
it gets logged here automatically - separate from TradeJournal, which
only records trades the user deliberately "parks". This is an
unattended record of what the system actually told the user, so it
can be checked later ("did the alerts work?") against what price
actually did afterward.

Backed by a plain CSV under database/, matching the rest of the app's
persistence convention. Gitignored (user-generated runtime data, not
source).
"""

from datetime import datetime
from pathlib import Path

import pandas as pd

from providers.yahoo import YahooProvider

LOG_PATH = Path(__file__).resolve().parent.parent.parent / "database" / "alert_log.csv"

COLUMNS = [
    "Timestamp",
    "Ticker",
    "Name",
    "Direction",
    "EntryPrice",
    "RSI",
    "Stop",
    "Target1",
    "Target2",
    "RiskReward",
    "Status",
    "ClosedPrice",
    "ClosedAt",
    "ReturnPct",
]


class AlertLog:

    @staticmethod
    def _ensure():

        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        if not LOG_PATH.exists():
            pd.DataFrame(columns=COLUMNS).to_csv(LOG_PATH, index=False)

    @classmethod
    def load(cls):

        cls._ensure()

        return pd.read_csv(LOG_PATH)

    @classmethod
    def log_alert(cls, ticker, name, direction, entry_price, rsi, stop_target):

        cls._ensure()

        df = cls.load()

        row = {
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Ticker": ticker,
            "Name": name,
            "Direction": direction,
            "EntryPrice": entry_price,
            "RSI": rsi,
            "Stop": stop_target["stop"] if stop_target else None,
            "Target1": stop_target["target1"] if stop_target else None,
            "Target2": stop_target["target2"] if stop_target else None,
            "RiskReward": stop_target["risk_reward"] if stop_target else None,
            "Status": "OPEN",
            "ClosedPrice": None,
            "ClosedAt": None,
            "ReturnPct": None,
        }

        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(LOG_PATH, index=False)

    @classmethod
    def evaluate(cls):
        """
        Re-checks every still-OPEN alert against the latest price:
        marks it HIT TARGET / HIT STOP if price has reached either
        level, and updates the running return % either way. Returns
        the full (updated) log.
        """

        df = cls.load()

        if df.empty:
            return df

        # A fresh log starts with ClosedPrice/ClosedAt/ReturnPct all
        # NaN, which pandas infers as float64 - assigning a string
        # (ClosedAt's timestamp) into that column then raises
        # TypeError. Force these to plain object dtype before writing.
        for col in ("Status", "ClosedPrice", "ClosedAt", "ReturnPct"):
            df[col] = df[col].astype(object)

        provider = YahooProvider()
        price_cache = {}

        for idx, row in df.iterrows():

            if row["Status"] != "OPEN":
                continue

            ticker = row["Ticker"]

            if ticker not in price_cache:

                try:
                    bars = provider.history(ticker, interval="15m", period="5d")
                    price_cache[ticker] = float(bars["Close"].iloc[-1]) if not bars.empty else None
                except Exception:
                    price_cache[ticker] = None

            price = price_cache[ticker]

            if price is None:
                continue

            entry = float(row["EntryPrice"])
            direction = row["Direction"]

            return_pct = (
                (price / entry - 1) * 100
                if direction == "LONG"
                else (entry / price - 1) * 100
            )

            status = "OPEN"

            stop = row["Stop"]
            target1 = row["Target1"]

            if direction == "LONG":

                if pd.notna(target1) and price >= float(target1):
                    status = "HIT TARGET"
                elif pd.notna(stop) and price <= float(stop):
                    status = "HIT STOP"

            else:  # SHORT

                if pd.notna(target1) and price <= float(target1):
                    status = "HIT TARGET"
                elif pd.notna(stop) and price >= float(stop):
                    status = "HIT STOP"

            df.at[idx, "ReturnPct"] = round(return_pct, 2)

            if status != "OPEN":
                df.at[idx, "Status"] = status
                df.at[idx, "ClosedPrice"] = round(price, 2)
                df.at[idx, "ClosedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        df.to_csv(LOG_PATH, index=False)

        return df

    @classmethod
    def remove(cls, row_index):

        df = cls.load()

        if row_index in df.index:
            df = df.drop(index=row_index).reset_index(drop=True)
            df.to_csv(LOG_PATH, index=False)

    @staticmethod
    def summary(df):
        """
        Aggregate stats: how many alerts are open vs hit target vs hit
        stop, win rate among closed ones, and average return.
        """

        if df.empty:
            return {
                "total": 0,
                "open": 0,
                "hit_target": 0,
                "hit_stop": 0,
                "win_rate": 0.0,
                "avg_return": 0.0,
            }

        closed = df[df["Status"] != "OPEN"]

        hit_target = int((df["Status"] == "HIT TARGET").sum())
        hit_stop = int((df["Status"] == "HIT STOP").sum())
        closed_count = len(closed)

        return {
            "total": len(df),
            "open": int((df["Status"] == "OPEN").sum()),
            "hit_target": hit_target,
            "hit_stop": hit_stop,
            "win_rate": round(hit_target / closed_count * 100, 1) if closed_count else 0.0,
            "avg_return": round(df["ReturnPct"].dropna().astype(float).mean(), 2) if df["ReturnPct"].notna().any() else 0.0,
        }
