"""
Trade journal.

Lets the user "park" a trade idea (ticker, direction, entry, stop,
targets) they're considering but not acting on immediately. Backed by
a plain CSV under database/, matching how the rest of the app already
treats CSV as its persistence layer (database/*_master.csv) - durable
across restarts, human-readable, no new dependency.
"""

from pathlib import Path

import pandas as pd

from dashboard.services.time_utils import now_cet

JOURNAL_PATH = Path(__file__).resolve().parent.parent.parent / "database" / "parked_trades.csv"

COLUMNS = [
    "Ticker",
    "Direction",
    "Entry",
    "Stop",
    "Target1",
    "Target2",
    "RiskReward",
    "Trend",
    "RSI",
    "ParkedAt",
    "Notes",
]


class TradeJournal:

    @staticmethod
    def _ensure():

        JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)

        if not JOURNAL_PATH.exists():
            pd.DataFrame(columns=COLUMNS).to_csv(JOURNAL_PATH, index=False)

    @classmethod
    def load(cls):

        cls._ensure()

        return pd.read_csv(JOURNAL_PATH)

    @classmethod
    def park(cls, ticker, direction, entry, stop_target, trend, rsi, notes=""):

        cls._ensure()

        df = cls.load()

        row = {
            "Ticker": ticker,
            "Direction": direction,
            "Entry": entry,
            "Stop": stop_target["stop"],
            "Target1": stop_target["target1"],
            "Target2": stop_target["target2"],
            "RiskReward": stop_target["risk_reward"],
            "Trend": trend,
            "RSI": rsi,
            "ParkedAt": now_cet().strftime("%Y-%m-%d %H:%M:%S"),
            "Notes": notes,
        }

        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(JOURNAL_PATH, index=False)

    @classmethod
    def remove(cls, row_index):

        df = cls.load()

        if row_index in df.index:
            df = df.drop(index=row_index).reset_index(drop=True)
            df.to_csv(JOURNAL_PATH, index=False)
