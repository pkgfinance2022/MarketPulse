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
import ta

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
    "Size",
    "Leverage",
    "OpenedAt",
    "Status",
    "ClosedPrice",
    "ClosedAt",
    "ReturnPct",
    "LeveragedReturnPct",
    "PnL",
    "Notes",
    "Comment",
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


def _pnl(entry, price, direction, size, leverage):
    """
    return_pct is the raw price move (what the instrument itself did).
    leverage multiplies that into your actual equity swing, and PnL
    scales it by how much capital you actually committed - the number
    that answers "did I make or lose money," not just "did price move."
    """

    return_pct = (
        (price / entry - 1) * 100
        if direction == "LONG"
        else (entry / price - 1) * 100
    )

    leverage = float(leverage) if pd.notna(leverage) and leverage else 1.0
    size = float(size) if pd.notna(size) and size else 0.0

    leveraged_return_pct = return_pct * leverage
    pnl = size * leveraged_return_pct / 100

    return round(return_pct, 2), round(leveraged_return_pct, 2), round(pnl, 2)


def _rsi_from_bars(bars):
    """
    RSI on OHLC4 (typical price), window=14 - same convention as
    ReversalPlaybook/RSIWaveStrategy elsewhere in the app. Computed on
    whatever bars evaluate() already fetched for price (15m here,
    intentionally - this tab is meant to be watched closely) rather
    than a separate 1H fetch, so following a position doesn't double
    the yfinance calls on every 15s tick.
    """

    if bars is None or bars.empty or len(bars) < 20:
        return None

    typical_price = (bars["Open"] + bars["High"] + bars["Low"] + bars["Close"]) / 4
    rsi_series = ta.momentum.rsi(typical_price, window=14)

    if rsi_series.empty or pd.isna(rsi_series.iloc[-1]):
        return None

    return round(float(rsi_series.iloc[-1]), 1)


def risk_reward_at_entry(direction, entry, stop, target1):
    """Pure math, no network - available the instant a position is logged."""

    if not all(pd.notna(v) and v for v in (entry, stop, target1)):
        return None

    risk = abs(entry - stop)
    reward = abs(target1 - entry)

    if risk == 0:
        return None

    return round(reward / risk, 2)


def live_read(direction, price, entry, stop, target1, rsi):
    """
    The "what's happening right now" line for an OPEN position: how far
    price has travelled toward target vs. stop, plus a 15m-RSI read
    flagged against the direction taken - a bounce/pullback risk if
    you're already leaning on an extreme in your own direction. This is
    a live, non-persisted read (recomputed every tick); only the
    snapshot at the moment a position actually closes gets written to
    the CSV, so the durable history isn't cluttered with a live ticker.
    """

    notes = []

    if pd.notna(target1) and pd.notna(stop) and entry != stop:

        if direction == "LONG":
            progress = (price - entry) / (target1 - entry) * 100 if target1 != entry else None
            stop_used = (entry - price) / (entry - stop) * 100
        else:
            progress = (entry - price) / (entry - target1) * 100 if target1 != entry else None
            stop_used = (price - entry) / (stop - entry) * 100

        if progress is not None:
            if progress >= 100:
                notes.append("🎯 target reached")
            elif progress <= 0:
                notes.append(f"⏳ {abs(progress):.0f}% away from entry, no progress to target yet")
            else:
                notes.append(f"📈 {progress:.0f}% of the way to target")

        if stop_used >= 75:
            notes.append("🔴 getting close to your stop")

    if rsi is not None:

        if direction == "LONG" and rsi >= 75:
            notes.append(f"⚠️ 15m RSI {rsi} already overbought — pullback risk")
        elif direction == "SHORT" and rsi <= 25:
            notes.append(f"⚠️ 15m RSI {rsi} already oversold — bounce risk")
        else:
            notes.append(f"15m RSI {rsi}")

    return " · ".join(notes) if notes else None


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
    def open_position(cls, ticker, direction, entry, stop, target1, size, leverage=1.0, target2=None, notes=""):

        cls._ensure()

        df = cls.load()

        rr = risk_reward_at_entry(direction, entry, stop, target1)
        comment = f"Risk:Reward {rr}:1 at entry" if rr else ""

        if rr is not None and rr < 1:
            comment += " ⚠️ risking more than the target - poor R:R"

        row = {
            "Ticker": resolve_ticker(ticker),
            "Direction": direction,
            "Entry": entry,
            "Stop": stop,
            "Target1": target1,
            "Target2": target2,
            "Size": size,
            "Leverage": leverage,
            "OpenedAt": now_cet().strftime("%Y-%m-%d %H:%M:%S"),
            "Status": "OPEN",
            "ClosedPrice": None,
            "ClosedAt": None,
            "ReturnPct": None,
            "LeveragedReturnPct": None,
            "PnL": None,
            "Notes": notes,
            "Comment": comment,
        }

        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(POSITIONS_PATH, index=False)

        return rr

    @classmethod
    def evaluate(cls):
        """
        Re-checks every still-OPEN position against the latest price:
        marks it HIT TARGET / HIT STOP if price reached either level,
        and updates the running return % either way. Writes the result
        straight back to the CSV every call - the persisted file IS
        the record, nothing lives only in session_state.

        Also attaches "LiveRSI"/"LiveNote" columns to the RETURNED df
        for still-OPEN rows - a live progress-to-target/stop and 15m
        RSI read, computed from the same bars fetched for price (no
        extra network call). These two columns are deliberately added
        AFTER the CSV write, so they're transient - only what happens
        at the moment a position actually closes gets persisted
        (Comment, below), keeping the durable history free of
        every-15-seconds ticker noise.
        """

        df = cls.load()

        if df.empty:
            return df

        # Fresh rows start with ClosedPrice/ClosedAt/ReturnPct/etc all
        # NaN, which pandas infers as float64 - assigning a string (a
        # timestamp) into that column then raises TypeError.
        for col in ("Status", "ClosedPrice", "ClosedAt", "ReturnPct", "LeveragedReturnPct", "PnL", "Comment"):
            df[col] = df[col].astype(object)

        provider = YahooProvider()
        bars_cache = {}
        live_by_idx = {}

        for idx, row in df.iterrows():

            if row["Status"] != "OPEN":
                continue

            ticker = row["Ticker"]

            if ticker not in bars_cache:

                try:
                    bars_cache[ticker] = provider.history(ticker, interval="15m", period="5d")
                except Exception:
                    bars_cache[ticker] = None

            bars = bars_cache[ticker]
            price = float(bars["Close"].iloc[-1]) if bars is not None and not bars.empty else None

            if price is None:
                continue

            entry = float(row["Entry"])
            direction = row["Direction"]
            stop = row["Stop"]
            target1 = row["Target1"]

            return_pct, leveraged_return_pct, pnl = _pnl(entry, price, direction, row["Size"], row["Leverage"])
            rsi = _rsi_from_bars(bars)

            status = "OPEN"

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

            df.at[idx, "ReturnPct"] = return_pct
            df.at[idx, "LeveragedReturnPct"] = leveraged_return_pct
            df.at[idx, "PnL"] = pnl

            if status != "OPEN":
                df.at[idx, "Status"] = status
                df.at[idx, "ClosedPrice"] = round(price, 2)
                df.at[idx, "ClosedAt"] = now_cet().strftime("%Y-%m-%d %H:%M:%S")
                closing_note = f"Closed {status} at {round(price, 2)}"
                if rsi is not None:
                    closing_note += f", 15m RSI {rsi}"
                existing = df.at[idx, "Comment"]
                df.at[idx, "Comment"] = (
                    f"{existing} · {closing_note}" if pd.notna(existing) and existing else closing_note
                )
            else:
                live_by_idx[idx] = (rsi, live_read(direction, price, entry, stop, target1, rsi))

        df.to_csv(POSITIONS_PATH, index=False)

        df["LiveRSI"] = None
        df["LiveNote"] = None

        for idx, (rsi, note) in live_by_idx.items():
            df.at[idx, "LiveRSI"] = rsi
            df.at[idx, "LiveNote"] = note

        return df

    @classmethod
    def close_manually(cls, row_index):
        """
        Closes a still-OPEN position right now, at the latest available
        price. Returns True on success, False if it couldn't (already
        closed/removed, or the price fetch failed) - the caller should
        surface that rather than treating a no-op as if it worked.
        """

        df = cls.load()

        if row_index not in df.index or df.at[row_index, "Status"] != "OPEN":
            return False

        ticker = df.at[row_index, "Ticker"]

        try:
            bars = YahooProvider().history(ticker, interval="15m", period="5d")
            price = float(bars["Close"].iloc[-1]) if not bars.empty else None
        except Exception:
            price = None
            bars = None

        if price is None:
            return False

        entry = float(df.at[row_index, "Entry"])
        direction = df.at[row_index, "Direction"]

        return_pct, leveraged_return_pct, pnl = _pnl(
            entry, price, direction, df.at[row_index, "Size"], df.at[row_index, "Leverage"]
        )
        rsi = _rsi_from_bars(bars)

        for col in ("Status", "ClosedPrice", "ClosedAt", "ReturnPct", "LeveragedReturnPct", "PnL", "Comment"):
            df[col] = df[col].astype(object)

        closing_note = f"Closed CLOSED MANUALLY at {round(price, 2)}"
        if rsi is not None:
            closing_note += f", 15m RSI {rsi}"
        existing = df.at[row_index, "Comment"]

        df.at[row_index, "Status"] = "CLOSED MANUALLY"
        df.at[row_index, "ClosedPrice"] = round(price, 2)
        df.at[row_index, "ClosedAt"] = now_cet().strftime("%Y-%m-%d %H:%M:%S")
        df.at[row_index, "ReturnPct"] = return_pct
        df.at[row_index, "LeveragedReturnPct"] = leveraged_return_pct
        df.at[row_index, "PnL"] = pnl
        df.at[row_index, "Comment"] = f"{existing} · {closing_note}" if pd.notna(existing) and existing else closing_note

        df.to_csv(POSITIONS_PATH, index=False)

        return True

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
                "total_pnl": 0.0,
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
            "total_pnl": round(df["PnL"].dropna().astype(float).sum(), 2) if df["PnL"].notna().any() else 0.0,
        }
