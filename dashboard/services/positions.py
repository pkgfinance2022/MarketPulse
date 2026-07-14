"""
Manually-taken positions.

Separate from AlertLog (system-detected signals) and TradeJournal
(parked ideas not yet acted on) - this is "I actually took this trade,
follow it." Entry/Stop/Target are typed in by hand rather than
auto-captured, since there's often a gap between taking a trade and
logging it here.

Backed by a plain CSV under database/ (same convention as AlertLog/
TradeJournal) - durable across app restarts/reboots on purpose, so a
Streamlit Cloud reboot never wipes a position's history. evaluate()
writes its result straight back to this CSV every time it runs rather
than only updating in-memory/session state, so nothing here depends on
the process staying alive.
"""

from pathlib import Path

import pandas as pd

from dashboard.services.time_utils import now_cet
from providers.yahoo import YahooProvider

POSITIONS_PATH = Path(__file__).resolve().parent.parent.parent / "database" / "positions.csv"

COLUMNS = [
    "Ticker",
    "Direction",
    "Entry",
    "Stop",
    "Target1",
    "Target2",
    "OpenedAt",
    "Status",
    "ClosedPrice",
    "ClosedAt",
    "ReturnPct",
    "Notes",
]

# Common CFD/broker-style index nicknames, resolved to their Yahoo
# Finance symbol. Anything not listed here is passed straight through
# (uppercased) so a raw Yahoo ticker (^GDAXI, AAPL, BTC-USD, ...) still
# works untouched.
TICKER_ALIASES = {
    "FRA40": "^FCHI",
    "CAC40": "^FCHI",
    "GER40": "^GDAXI",
    "DAX40": "^GDAXI",
    "DAX": "^GDAXI",
    "UK100": "^FTSE",
    "FTSE100": "^FTSE",
    "US30": "^DJI",
    "DOW": "^DJI",
    "US100": "^NDX",
    "NAS100": "^NDX",
    "US500": "^GSPC",
    "SPX500": "^GSPC",
    "SPX": "^GSPC",
    "JPN225": "^N225",
    "JP225": "^N225",
    "NIKKEI": "^N225",
    "HK50": "^HSI",
}


def resolve_ticker(raw):
    symbol = raw.strip().upper()
    return TICKER_ALIASES.get(symbol, symbol)


class Positions:

    @staticmethod
    def _ensure():

        POSITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)

        if not POSITIONS_PATH.exists():
            pd.DataFrame(columns=COLUMNS).to_csv(POSITIONS_PATH, index=False)

    @classmethod
    def load(cls):

        cls._ensure()

        return pd.read_csv(POSITIONS_PATH)

    @classmethod
    def open_position(cls, ticker, direction, entry, stop, target1, target2=None, notes=""):

        cls._ensure()

        df = cls.load()

        row = {
            "Ticker": resolve_ticker(ticker),
            "Direction": direction,
            "Entry": entry,
            "Stop": stop,
            "Target1": target1,
            "Target2": target2,
            "OpenedAt": now_cet().strftime("%Y-%m-%d %H:%M:%S"),
            "Status": "OPEN",
            "ClosedPrice": None,
            "ClosedAt": None,
            "ReturnPct": None,
            "Notes": notes,
        }

        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(POSITIONS_PATH, index=False)

    @classmethod
    def evaluate(cls):
        """
        Re-checks every still-OPEN position against the latest price:
        marks it HIT TARGET / HIT STOP if price reached either level,
        and updates the running return % either way. Writes the result
        straight back to the CSV every call - the persisted file IS
        the record, nothing lives only in session_state. Returns the
        full (updated) log.
        """

        df = cls.load()

        if df.empty:
            return df

        # Fresh rows start with ClosedPrice/ClosedAt/ReturnPct all NaN,
        # which pandas infers as float64 - assigning a string (a
        # timestamp) into that column then raises TypeError.
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

            entry = float(row["Entry"])
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
                df.at[idx, "ClosedAt"] = now_cet().strftime("%Y-%m-%d %H:%M:%S")

        df.to_csv(POSITIONS_PATH, index=False)

        return df

    @classmethod
    def close_manually(cls, row_index):
        """Closes a still-OPEN position right now, at the latest available price."""

        df = cls.load()

        if row_index not in df.index or df.at[row_index, "Status"] != "OPEN":
            return

        ticker = df.at[row_index, "Ticker"]

        try:
            bars = YahooProvider().history(ticker, interval="15m", period="5d")
            price = float(bars["Close"].iloc[-1]) if not bars.empty else None
        except Exception:
            price = None

        if price is None:
            return

        entry = float(df.at[row_index, "Entry"])
        direction = df.at[row_index, "Direction"]

        return_pct = (
            (price / entry - 1) * 100
            if direction == "LONG"
            else (entry / price - 1) * 100
        )

        for col in ("Status", "ClosedPrice", "ClosedAt", "ReturnPct"):
            df[col] = df[col].astype(object)

        df.at[row_index, "Status"] = "CLOSED MANUALLY"
        df.at[row_index, "ClosedPrice"] = round(price, 2)
        df.at[row_index, "ClosedAt"] = now_cet().strftime("%Y-%m-%d %H:%M:%S")
        df.at[row_index, "ReturnPct"] = round(return_pct, 2)

        df.to_csv(POSITIONS_PATH, index=False)

    @classmethod
    def remove(cls, row_index):

        df = cls.load()

        if row_index in df.index:
            df = df.drop(index=row_index).reset_index(drop=True)
            df.to_csv(POSITIONS_PATH, index=False)

    @staticmethod
    def summary(df):

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
        closed_count = len(closed)

        return {
            "total": len(df),
            "open": int((df["Status"] == "OPEN").sum()),
            "hit_target": int((df["Status"] == "HIT TARGET").sum()),
            "hit_stop": int((df["Status"] == "HIT STOP").sum()),
            "win_rate": round((df["Status"] == "HIT TARGET").sum() / closed_count * 100, 1) if closed_count else 0.0,
            "avg_return": round(df["ReturnPct"].dropna().astype(float).mean(), 2) if df["ReturnPct"].notna().any() else 0.0,
        }
