"""
MarketPulse v2 dashboard shell.

The app coordinates services and widgets. Analysis and business rules live in
the engines and services, while widgets only render already-prepared data.
"""

import json
import sys
import time
from datetime import timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from analysis import fifteen_min_readiness
from analysis.reversal_playbook import ReversalPlaybook
from analysis.reversal_playbook_daily import DailyWeeklyReversalPlaybook
from analysis.rsi_wave_strategy import RSIWaveStrategy
from core.loader import AssetLoader
from dashboard.services.alert_log import AlertLog
from dashboard.services.dashboard_loader import DashboardLoader
from dashboard.services.fundamental_scan_service import FundamentalScanService
from dashboard.services.reversal_status import ReversalStatusService
from dashboard.services.reversal_status_daily import DailyReversalStatusService
from dashboard.services.rsi_wave_status import RSIWaveStatusService
from dashboard.services.stock_news_service import StockNewsService
from dashboard.services.telegram_notifier import TelegramNotifier
from dashboard.services.tradingview_links import tradingview_url
from dashboard.services.trade_journal import TradeJournal
from dashboard.services import time_utils
from dashboard.services import universe_cache
from dashboard.widgets.header import Header
from dashboard.widgets.market_status import MarketStatus
from dashboard.widgets.scanner import Scanner
from dashboard.widgets.sidebar import Sidebar


st.set_page_config(
    page_title="MarketPulse",
    page_icon="MP",
    layout="wide",
)


UNIVERSE_TABS = [
    ("us", "USA", "🇺🇸 US Stocks"),
    ("india", "India", "🇮🇳 Indian Stocks"),
    ("crypto", "Crypto", "🪙 Crypto"),
]

UNIVERSE_REFRESH_SECONDS = 3600   # full stock/crypto universes are much bigger than Global Indices - a slow, once-an-hour cadence keeps yfinance usage sane (explicitly requested)


def _format_duration(seconds):
    """"2m 30s" / "45s" - human, not "0:02:30"."""

    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)

    if minutes:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"

    return f"{secs}s"


def _scan_eta_text(cache_entry):
    """
    A practical, best-guess ETA for a scan currently in progress -
    based on how long the LAST successful scan for this exact prefix
    actually took (a universe's size doesn't change run to run, so its
    own history is a far better estimate than a generic one-size-fits-
    all guess). Falls back to a rough range on the very first-ever
    scan for a prefix, when there's no history yet to go on.
    """

    last_duration = cache_entry.get("last_duration") if cache_entry else None
    loading_since = cache_entry.get("loading_since") if cache_entry else None

    if not last_duration or not loading_since:
        return "no estimate yet - typically 1-4 minutes depending on the universe size"

    remaining = last_duration - (time.time() - loading_since)

    if remaining <= 0:
        return "should finish any moment now"

    return f"~{_format_duration(remaining)} remaining (based on the last scan)"


def _format_event_time(ts):
    """
    Formats an engine's event_time into a display string for the
    scanner's Timestamp columns. Rounded to the hour deliberately (no
    minutes) - "3 PM" is plenty precise for "when did you see this",
    and it's what was actually asked for. Hourly/15m bars carry a real
    time of day, always converted to CET/CEST so events from different
    exchanges (each with their own tz from yfinance) read on one
    shared clock instead of a different one per row. Daily/Weekly bars
    carry a midnight exchange-local timestamp that only identifies
    WHICH trading day - converting that to CET would risk shifting it
    onto the wrong calendar date (e.g. a Tokyo midnight bar sliding
    back a day), so those are shown as their original date, untouched.
    """

    if ts is None or pd.isna(ts):
        return "—"

    ts = pd.Timestamp(ts)

    if ts.hour == 0 and ts.minute == 0:
        return ts.strftime("%b %d")

    ts = time_utils.to_cet(ts)
    hour12 = ts.hour % 12 or 12
    ampm = "AM" if ts.hour < 12 else "PM"

    return f"{ts.strftime('%b %d')}, {hour12} {ampm} CET"


def init_state():

    defaults = {
        "global_market": None,
        "global_selected_ticker": None,
        "global_sector": "All",
        "wave_states": {},
        "wave_states_seeded": False,
        "reversal_states": {},
        "reversal_states_seeded": False,
        "fundamental_scan_result": None,
    }

    for prefix, _country, _title in UNIVERSE_TABS:
        defaults[f"{prefix}_market"] = None
        defaults[f"{prefix}_selected_ticker"] = None
        defaults[f"{prefix}_wave_states"] = {}
        defaults[f"{prefix}_wave_states_seeded"] = False
        defaults[f"{prefix}_reversal_states"] = {}
        defaults[f"{prefix}_reversal_states_seeded"] = False
        defaults[f"{prefix}_last_loaded_ts"] = 0
        defaults[f"{prefix}_seen_cache_ts"] = 0

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_notification_enable_button():
    """
    Browser desktop notifications require a user gesture to grant
    permission - rendered once, outside any auto-refreshing fragment,
    so the permission prompt only ever appears from an actual click.
    """

    components.html(
        """
        <div style="padding:4px 0;">
        <button id="enableBtn" style="padding:6px 14px;font-size:13px;cursor:pointer;
            border-radius:6px;border:1px solid #ccc;background:#f5f5f5;">
            🔔 Enable Desktop Notifications
        </button>
        <span id="notifStatus" style="margin-left:10px;font-size:13px;"></span>
        </div>
        <script>
        const btn = document.getElementById('enableBtn');
        const status = document.getElementById('notifStatus');

        function updateStatus() {
            if (Notification.permission === 'granted') {
                status.innerText = '✅ Enabled — you will get a popup when a new entry fires.';
                btn.style.display = 'none';
            } else if (Notification.permission === 'denied') {
                status.innerText = '❌ Blocked — enable notifications for this site in your browser settings.';
            }
        }

        btn.onclick = function() {
            Notification.requestPermission().then(function(permission) {
                updateStatus();
                if (permission === 'granted') {
                    new Notification('MarketPulse', { body: 'Notifications enabled - you will be alerted here when a new entry fires.' });
                }
            });
        };

        updateStatus();
        </script>
        """,
        height=45,
    )


def render_notification_trigger(new_entries):
    """
    Fires one browser notification per newly-detected entry. Only
    called when there's actually something new - an empty component
    reload every 45s would be wasted work and risks browsers
    throttling/ignoring rapid-fire Notification calls.
    """

    if not new_entries:
        return

    def _format(e):

        price = round(e["price"], 2) if e["price"] is not None else "?"
        rsi = e["rsi"] if e["rsi"] is not None else "?"
        stop_target = e.get("stop_target")

        levels = (
            f", Stop {stop_target['stop']}, Target {stop_target['target1']}"
            if stop_target
            else ""
        )

        return f"{e['name']} ({e['ticker']}) — {e['direction']} entry, Price {price}, RSI {rsi}{levels}"

    messages = [_format(e) for e in new_entries]

    components.html(
        f"""
        <script>
        if (Notification.permission === 'granted') {{
            const messages = {json.dumps(messages)};
            messages.forEach(function(msg, i) {{
                setTimeout(function() {{
                    new Notification('MarketPulse — New Entry', {{ body: msg }});
                }}, i * 400);
            }});
        }}
        </script>
        """,
        height=0,
    )


@st.fragment(run_every=180)
def check_for_new_entries():
    """
    Separate, slower-cadence fragment from the 45s price/scanner
    refresh - re-screening every symbol in the loaded region is ~1
    yfinance fetch each, too expensive to repeat every 45s. Compares
    each symbol's current RSI-wave state against what it was last
    check, and fires a browser notification + Telegram message only
    for ones that just became an actual entry (not re-notifying every
    cycle while it stays in that state).
    """

    market = st.session_state.global_market

    if market is None:
        return

    tickers = market["df"]["Ticker"].tolist()
    name_map = dict(zip(market["df"]["Ticker"], market["df"]["Name"]))

    current_states = RSIWaveStatusService.screen_states(tickers)
    previous_states = st.session_state.wave_states
    is_first_check = not st.session_state.wave_states_seeded

    # On the very first check (fresh session, or just switched region),
    # there's no real "previous" to compare against - whatever is
    # already sitting in an entry state isn't NEW, it just happens to
    # be the current state. Record the baseline silently instead of
    # notifying about everything that was already true.
    new_entries = (
        []
        if is_first_check
        else [
            {
                "ticker": ticker,
                "name": name_map.get(ticker, ticker),
                "direction": "LONG" if info["state"] == "ENTRY_LONG" else "SHORT",
                "price": info["price"],
                "rsi": info["rsi"],
            }
            for ticker, info in current_states.items()
            if info["state"] in ("ENTRY_LONG", "ENTRY_SHORT")
            and (previous_states.get(ticker) or {}).get("state") != info["state"]
        ]
    )

    st.session_state.wave_states = current_states
    st.session_state.wave_states_seeded = True

    for entry in new_entries:

        # Cross-session, cross-restart dedup - AlertLog's CSV is
        # shared (unlike st.session_state), so this catches the same
        # real signal getting independently re-detected by another
        # open tab/session, or a restart resetting in-memory state,
        # not just repeats within this one session's own memory.
        if AlertLog.recently_logged(entry["ticker"], entry["direction"]):
            continue

        icon = "🟢" if entry["direction"] == "LONG" else "🔴"
        price = round(entry["price"], 2) if entry["price"] is not None else "?"
        rsi = entry["rsi"] if entry["rsi"] is not None else "?"
        event_time = time_utils.now_cet().strftime("%Y-%m-%d %H:%M:%S CET")

        # Computed once, reused for the message AND the log - avoids
        # a second fetch and guarantees the alert you see matches
        # exactly what gets tracked in Alert Tracking.
        full_status = RSIWaveStatusService.analyse(entry["ticker"], period="730d")
        stop_target = full_status["stop_target"] if full_status else None
        entry["stop_target"] = stop_target

        levels = (
            f"\nStop {stop_target['stop']} · Target {stop_target['target1']} · R:R 1:{stop_target['risk_reward']}"
            if stop_target
            else ""
        )

        # Name first (what an end user actually recognizes), ticker in
        # parentheses for cross-referencing elsewhere - no repeated
        # "MarketPulse" branding, no raw internal state code.
        message = f"{icon} {entry['name']} ({entry['ticker']}) — {entry['direction']} entry\n{event_time}\nPrice {price} · RSI {rsi}{levels}"

        st.toast(f"{entry['direction']} entry: {entry['name']}", icon=icon)

        if TelegramNotifier.is_configured():
            TelegramNotifier.send(message)

        # Log every alert automatically so it can be checked later
        # against what price actually did.
        AlertLog.log_alert(
            entry["ticker"],
            entry["name"],
            entry["direction"],
            entry["price"],
            entry["rsi"],
            stop_target,
        )

    render_notification_trigger(new_entries)


REVERSAL_SIGNAL_DIRECTIONS = {
    "BUY_SIGNAL": "LONG",
    "SELL_SIGNAL": "SHORT",
    "SELL_SIGNAL_CONTINUATION": "SHORT",
}

REVERSAL_SIGNAL_LABELS = {
    "BUY_SIGNAL": "BUY",
    "SELL_SIGNAL": "SELL",
    "SELL_SIGNAL_CONTINUATION": "SELL (continuation)",
}


@st.fragment(run_every=300)
def check_for_new_reversal_signals():
    """
    Same pattern as check_for_new_entries(), for the Reversal Playbook
    (1H + Daily EMA200) instead of the RSI wave strategy. Runs on a
    slower cadence (5 min, not 3) since this screener costs 2 fetches
    per symbol (1H + Daily) versus the wave screener's 1.
    """

    market = st.session_state.global_market

    if market is None:
        return

    tickers = market["df"]["Ticker"].tolist()
    name_map = dict(zip(market["df"]["Ticker"], market["df"]["Name"]))

    current_states = ReversalStatusService.screen_states(tickers)
    previous_states = st.session_state.reversal_states
    is_first_check = not st.session_state.reversal_states_seeded

    new_signals = (
        []
        if is_first_check
        else [
            {
                "ticker": ticker,
                "name": name_map.get(ticker, ticker),
                "direction": REVERSAL_SIGNAL_DIRECTIONS[info["state"]],
                "state": info["state"],
                "price": info["price"],
                "rsi": info["rsi"],
            }
            for ticker, info in current_states.items()
            if info["state"] in REVERSAL_SIGNAL_DIRECTIONS
            and (previous_states.get(ticker) or {}).get("state") != info["state"]
        ]
    )

    st.session_state.reversal_states = current_states
    st.session_state.reversal_states_seeded = True

    browser_entries = []

    for signal in new_signals:

        # Cross-session, cross-restart dedup - see check_for_new_entries().
        if AlertLog.recently_logged(signal["ticker"], signal["direction"]):
            continue

        icon = "🟢" if signal["direction"] == "LONG" else "🔴"
        price = round(signal["price"], 2) if signal["price"] is not None else "?"
        signal_label = REVERSAL_SIGNAL_LABELS.get(signal["state"], signal["state"])
        event_time = time_utils.now_cet().strftime("%Y-%m-%d %H:%M:%S CET")

        full_status = ReversalStatusService.analyse(signal["ticker"])
        stop_target = full_status["stop_target"] if full_status else None

        levels = (
            f"\nStop {stop_target['stop']} · Target {stop_target['target1']} · R:R 1:{stop_target['risk_reward']}"
            if stop_target and stop_target.get("stop") is not None
            else ""
        )

        message = (
            f"{icon} {signal['name']} ({signal['ticker']}) — {signal_label} (Reversal Playbook)\n"
            f"{event_time}\nPrice {price} · RSI {signal['rsi']}{levels}"
        )

        st.toast(f"{signal_label}: {signal['name']}", icon=icon)

        if TelegramNotifier.is_configured():
            TelegramNotifier.send(message)

        AlertLog.log_alert(
            signal["ticker"],
            signal["name"],
            signal["direction"],
            signal["price"],
            signal["rsi"],
            stop_target,
        )

        browser_entries.append(
            {
                "ticker": signal["ticker"],
                "name": signal["name"],
                "direction": signal["direction"],
                "price": signal["price"],
                "rsi": signal["rsi"],
                "stop_target": stop_target,
            }
        )

    render_notification_trigger(browser_entries)


GLOBAL_INDICES_REFRESH_SECONDS = 600   # faster than the universe tabs' hourly cadence (this is the "live, intraday" tab), but not so fast it re-fires the 4-engine scan pointlessly often

# US market open (9:30 ET) usually lands at 15:30 CET, but shifts to 14:30
# CET during the ~1-2 week windows each spring/autumn where the US and EU
# flip their DST clocks on different Sundays - so both are covered. Global
# macro assets (European indices, currencies, commodities) tend to move
# right as the US session opens, so this forces an early refresh instead
# of waiting for the routine 10-minute cadence to catch up.
US_MARKET_OPEN_CET_TIMES = ((14, 30), (15, 30))
US_MARKET_OPEN_REFRESH_WINDOW_MINUTES = 10


def _in_us_market_open_refresh_window(now_cet, cache_ts):
    for hour, minute in US_MARKET_OPEN_CET_TIMES:
        window_start = now_cet.replace(hour=hour, minute=minute, second=0, microsecond=0)
        window_end = window_start + timedelta(minutes=US_MARKET_OPEN_REFRESH_WINDOW_MINUTES)
        if window_start <= now_cet <= window_end and cache_ts < window_start.timestamp():
            return True
    return False


def _scan_global_indices_data(sector):
    """
    Pure, background-thread-safe region scan - no st.* calls (mirrors
    _scan_universe_data). Runs in a background thread via
    universe_cache so switching regions / loading no longer blocks the
    page for 1-2 minutes; the tab shows whatever's cached immediately
    and swaps in the fresh scan once it completes.
    """

    df, success, failed = DashboardLoader.load(
        {
            "country": "Global",
            "sector": sector,
            "search": "",
            "portfolio_only": False,
            "watchlist_only": False,
            "priority": 1,
        }
    )

    wave_states = {}
    reversal_states = {}

    if not df.empty:
        # ATR as a % of price - already computed, no extra fetch.
        # Normalizes wildly different instrument types (index points
        # vs FX pips vs commodity prices) onto one comparable scale,
        # so "Nikkei is more volatile than currencies right now" or
        # "NASDAQ more than DAX/CAC" becomes a sortable column instead
        # of an eyeballed guess.
        df["Volatility %"] = (df["ATR"] / df["Price"] * 100).replace([float("inf"), -float("inf")], None)

        tickers = df["Ticker"].tolist()

        wave_states = RSIWaveStatusService.screen_states(tickers)
        wave_labels = {t: RSIWaveStrategy.STATE_LABELS.get(info["state"], "⚪ Watching") for t, info in wave_states.items()}
        df["Setup"] = df["Ticker"].map(wave_labels).fillna(df["Setup"])
        df["Setup Full"] = df["Ticker"].map({t: info["description"] for t, info in wave_states.items()}).fillna("")
        df["Setup Timestamp"] = df["Ticker"].map({t: _format_event_time(info["event_time"]) for t, info in wave_states.items()}).fillna("—")

        reversal_states = ReversalStatusService.screen_states(tickers)
        reversal_labels = {t: ReversalPlaybook.STATE_LABELS.get(info["state"], "⚪ Watching") for t, info in reversal_states.items()}
        df["Reversal"] = df["Ticker"].map(reversal_labels).fillna(df["Reversal"])
        df["Reversal Full"] = df["Ticker"].map({t: info["description"] for t, info in reversal_states.items()}).fillna("")
        df["Reversal Timestamp"] = df["Ticker"].map({t: _format_event_time(info["event_time"]) for t, info in reversal_states.items()}).fillna("—")

        # 15m readiness - NOT an independent scan across the whole
        # universe (that dual-timeframe complexity was explicitly
        # removed earlier). Only checked for symbols whose 1H has
        # ALREADY confirmed - a confirmation lens on a small subset,
        # one extra 15m/5d fetch each, not ~25 more fetches every load.
        # "Confirmed" covers the RSI-crossed-65 states AND the Uptrend
        # RSI-40 support note (touched oversold, struggled back above
        # ~40, holding it as support - the same "15m should show its
        # own signal at that moment" pattern, described twice now).
        confirmed_tickers = [
            t for t, info in reversal_states.items()
            if info["state"] in ("BUY_ALERT_CONFIRM", "BUY_ALERT_CONFIRM_PATH_C_FORMING", "BUY_SIGNAL")
            or "Uptrend RSI-40 support" in info["description"]
        ]

        fifteen_min_labels = {}
        fifteen_min_full = {}

        for t in confirmed_tickers:

            readiness = fifteen_min_readiness.check_readiness(t)

            if readiness:
                fifteen_min_labels[t] = readiness["label"]
                fifteen_min_full[t] = f"{readiness['label']} (15m RSI {readiness['rsi']})"

        df["15m Setup"] = df["Ticker"].map(fifteen_min_labels).fillna("— (needs 1H confirm first)")
        df["15m Setup Full"] = df["Ticker"].map(fifteen_min_full).fillna("")

        # Third screener, same pattern - the Daily+Weekly Reversal
        # Playbook, additive alongside the 1H one above.
        daily_reversal_states = DailyReversalStatusService.screen_states(tickers)

        daily_reversal_labels = {t: DailyWeeklyReversalPlaybook.STATE_LABELS.get(info["state"], "⚪ Watching") for t, info in daily_reversal_states.items()}
        df["Daily Reversal"] = df["Ticker"].map(daily_reversal_labels).fillna("⚪ Watching")
        df["Daily Reversal Full"] = df["Ticker"].map({t: info["description"] for t, info in daily_reversal_states.items()}).fillna("")
        df["Daily Reversal Timestamp"] = df["Ticker"].map({t: _format_event_time(info["event_time"]) for t, info in daily_reversal_states.items()}).fillna("—")

        # Weekly confluence, derived from the same Daily+Weekly scan
        # above - no extra fetch.
        weekly_labels = {t: DailyWeeklyReversalPlaybook.WEEKLY_STATE_LABELS.get(info["weekly_state"], "⚪ Watching") for t, info in daily_reversal_states.items()}
        df["Weekly"] = df["Ticker"].map(weekly_labels).fillna("⚪ Watching")
        df["Weekly Full"] = df["Ticker"].map({t: info["weekly_description"] for t, info in daily_reversal_states.items()}).fillna("")
        df["Weekly Timestamp"] = df["Ticker"].map({t: _format_event_time(info["weekly_event_time"]) for t, info in daily_reversal_states.items()}).fillna("—")

    return {
        "df": df,
        "success": success,
        "failed": failed,
        "sector": sector,
        "wave_states": wave_states,
        "reversal_states": reversal_states,
    }


def _refresh_global_indices(sector):
    """
    Non-blocking: kicks off a background scan for this sector if the
    cached result is stale, missing, or for a different sector, but
    never waits for it - same pattern as _refresh_universe_body(),
    keyed by sector since the region can change (US/India/Crypto are
    fixed universes, this one isn't).
    """

    cache_key = f"global_{sector}"
    cache_entry = universe_cache.get(cache_key)
    stale = (
        cache_entry is None
        or (not cache_entry["loading"] and (
            (time.time() - cache_entry["ts"]) >= GLOBAL_INDICES_REFRESH_SECONDS
            or _in_us_market_open_refresh_window(time_utils.now_cet(), cache_entry["ts"])
        ))
    )

    if stale:
        universe_cache.start_scan(cache_key, lambda: _scan_global_indices_data(sector), pool="global")
        cache_entry = universe_cache.get(cache_key)

    if cache_entry is None or cache_entry["data"] is None:
        eta = _scan_eta_text(cache_entry) if cache_entry else "no estimate yet - typically 1-4 minutes depending on the universe size"
        st.info(f"Scanning {sector} for the first time — {eta}. Feel free to check other tabs meanwhile.")
        return

    seen_ts_key = "global_seen_cache_ts"

    if cache_entry["ts"] > st.session_state.get(seen_ts_key, 0):

        result = cache_entry["data"]

        st.session_state.global_market = {
            "df": result["df"],
            "success": result["success"],
            "failed": result["failed"],
            "sector": result["sector"],
        }

        if st.session_state.global_selected_ticker not in result["df"]["Ticker"].tolist():
            st.session_state.global_selected_ticker = result["df"].iloc[0]["Ticker"] if not result["df"].empty else None

        # New data available = treat the next entry-check as a fresh
        # baseline instead of comparing against stale states (or
        # notifying about everything already true on arrival).
        st.session_state.wave_states = {}
        st.session_state.wave_states_seeded = False
        st.session_state.reversal_states = {}
        st.session_state.reversal_states_seeded = False

        st.session_state[seen_ts_key] = cache_entry["ts"]

    last_loaded = cache_entry["ts"]
    age_minutes = round((time.time() - last_loaded) / 60)
    refreshed_at = time_utils.unix_to_cet(last_loaded).strftime("%H:%M:%S CET")

    if cache_entry["loading"]:
        st.caption(f"🕐 Showing data from {refreshed_at} ({age_minutes} min ago) — 🔄 a fresh scan is running in the background, {_scan_eta_text(cache_entry)}.")
    else:
        st.caption(f"🕐 Last refreshed at {refreshed_at} ({age_minutes} min ago) — refreshes automatically every {GLOBAL_INDICES_REFRESH_SECONDS // 60} min, or click Scan Now above.")


@st.fragment(run_every=20)
def refresh_global_indices():
    """
    Polls the staleness check on its own timer - same idea as
    refresh_us_universe()/refresh_india_universe()/refresh_crypto_universe()
    below - so the US-market-open force-refresh window in
    _refresh_global_indices() actually fires on the wall clock instead of
    only being checked when a user happens to click something on this tab.
    """

    sector = st.session_state.get("global_sector")
    if sector:
        _refresh_global_indices(sector)


def _resolve_clicked_ticker(prefix, selections):
    """
    Picks whichever ticker was just ACTUALLY clicked across several
    independent Scanner tables on the same tab.

    Each st.dataframe row-selection widget keeps its own selected row
    checked across reruns until the user changes it - so on any given
    rerun, more than one table can simultaneously report "row X is
    selected" (whatever was last clicked in each of them, possibly
    days ago). Naively taking "the first truthy one" in a fixed table
    order means an old, stale selection in an earlier table always
    wins over a fresh click in a later one - which is exactly the "the
    TradingView link opens the wrong/random ticker" bug: clicking a
    row in the Volatility table did nothing because the Hourly table's
    older selection kept taking priority.

    Compares each table's current selection against what it returned
    last render; whichever one actually changed is this render's real
    click. Returns None if nothing changed (nobody clicked anything
    new this run).
    """

    state_key = f"{prefix}_last_table_selection"
    previous = st.session_state.get(state_key, {})

    changed = None

    for table_key, ticker in selections.items():
        if ticker != previous.get(table_key):
            changed = ticker

    st.session_state[state_key] = dict(selections)

    return changed


def _only_active_rows(df, columns):
    """
    Hides rows where every one of `columns` is a neutral read - "⚪
    Watching"/"⚪ 15m not aligned yet" or the "— (needs 1H confirm
    first)" placeholder - keeping only rows where at least one of
    them actually found something worth looking at. A row survives if
    ANY of the given columns is non-neutral.
    """

    available = [c for c in columns if c in df.columns]

    if not available:
        return df

    mask = pd.Series(False, index=df.index)

    for column in available:
        mask = mask | ~df[column].astype(str).str.strip().str.startswith(("⚪", "—"))

    return df[mask]


def _vix_risk_note(df):
    """
    VIX's own 1H Reversal Playbook state, read as a market-wide risk
    sentiment gauge - reuses the exact same engine already running on
    every other symbol, no extra fetch (^VIX is just another row in
    the Global universe). Rising VIX RSI (crossing/holding above 65)
    typically coincides with equity weakness, so a fresh BUY signal on
    an equity index while VIX is doing this deserves extra caution.
    Purely informational, not a gate - explicitly for you to factor
    into your own planning, not an automatic suppression.
    """

    vix_row = df[df["Ticker"] == "^VIX"]

    if vix_row.empty:
        return None

    row = vix_row.iloc[0]
    rsi = row.get("1H RSI")
    reversal_label = str(row.get("Reversal", ""))

    if rsi is None or pd.isna(rsi):
        return None

    rsi = round(float(rsi), 2)

    if "crossed 65" in reversal_label or "Path C forming" in reversal_label or "BUY signal" in reversal_label or rsi >= 65:
        return f"🔴 VIX Risk-OFF — VIX 1H RSI {rsi} (crossed/holding 65+) — fear rising, be extra cautious with fresh equity BUY signals right now."

    if "SELL" in reversal_label or rsi <= 35:
        return f"🟢 VIX Risk-ON — VIX 1H RSI {rsi} (falling / below 35) — fear easing, generally supportive of bullish continuation."

    return None


@st.fragment(run_every=45)
def render_global_indices_live():
    """
    Reruns on its own every 45s without touching the sidebar, the main
    Scanner tab, or triggering a full DashboardLoader.load() - it only
    re-fetches 15m bars for the tickers already loaded into
    `global_market`. (refresh_global_indices() above is the other
    auto-refreshing piece on this tab - it's on its own 20s timer so
    the staleness/US-market-open checks in _refresh_global_indices()
    fire on the wall clock too.)
    """

    market = st.session_state.global_market

    if market is None:
        # refresh_global_indices() (called just before this) already
        # shows its own "scanning for the first time" message - nothing
        # more to add here until that first background scan lands.
        return

    df = DashboardLoader.refresh_intraday_prices(market["df"])
    st.session_state.global_market["df"] = df

    st.caption("🔴 Live — refreshes every 45s (scanner: 15m bars · pullback setup: 1H)")

    vix_note = _vix_risk_note(df)

    if vix_note:
        if vix_note.startswith("🔴"):
            st.warning(vix_note)
        else:
            st.info(vix_note)

    # "Where did 65 just get crossed" - a quick at-a-glance highlight,
    # since that's often where the real move starts. Reuses the
    # already-computed Reversal label (no extra fetch) rather than a
    # separate table - BUY_ALERT_CONFIRM and its Path C variant both
    # mean "RSI just crossed 65, watching for the actual entry."
    just_crossed_65 = df[df["Reversal"].astype(str).str.contains("crossed 65|Path C forming", regex=True)]

    if not just_crossed_65.empty:
        names = ", ".join(f"{row['Ticker']} ({row['Name']})" for _, row in just_crossed_65.iterrows())
        st.info(f"🎯 Just crossed 65 (1H) — game may happen here: {names}")

    # Three separate tables instead of one wide mixed-timeframe grid -
    # each timeframe's signal(s) get their own focused view. All three
    # drive the same selected-ticker detail boxes below, whichever one
    # you click a row in. Each is also pre-filtered to hide rows where
    # nothing's been captured yet (plain Watching / not-aligned-yet /
    # needs-1H-confirm-first) - only symbols with something active in
    # THAT timeframe show up, instead of the full universe every time.
    df_15m = _only_active_rows(df, ["15m Setup"])
    df_1h = _only_active_rows(df, ["Setup", "Reversal"])
    df_1d = _only_active_rows(df, ["Daily Reversal", "Weekly"])

    if df_15m.empty:
        st.caption("⏱ 15-Minute: nothing captured yet.")
        ticker_15m = None
    else:
        ticker_15m = Scanner.render(
            df_15m, default_sort="15m %", key_prefix="global_15m", compact=False,
            columns=["Status", "Ticker", "Name", "Price", "15m %", "15m Setup"],
            title="⏱ 15-Minute", height=350,
        )

    if df_1h.empty:
        st.caption("🕐 Hourly: nothing captured yet.")
        ticker_1h = None
    else:
        ticker_1h = Scanner.render(
            df_1h, default_sort="Reversal", key_prefix="global_1h", compact=False,
            columns=["Status", "Ticker", "Name", "Price", "1H %", "Setup", "Setup Timestamp", "Reversal", "Reversal Timestamp"],
            title="🕐 Hourly", height=350,
        )

    if df_1d.empty:
        st.caption("📆 Daily: nothing captured yet.")
        ticker_1d = None
    else:
        ticker_1d = Scanner.render(
            df_1d, default_sort="Daily Reversal", key_prefix="global_1d", compact=False,
            columns=["Status", "Ticker", "Name", "Price", "1D %", "Daily Reversal", "Daily Reversal Timestamp", "Weekly", "Weekly Timestamp"],
            title="📆 Daily", height=350,
        )

    ticker_vol = Scanner.render(
        df, default_sort="Volatility %", key_prefix="global_vol", compact=False,
        columns=["Status", "Ticker", "Name", "Price", "Volatility %", "1H %"],
        title="🌡 Volatility Ranking — where to focus right now", height=350,
    )

    ticker = _resolve_clicked_ticker(
        "global",
        {"15m": ticker_15m, "1h": ticker_1h, "1d": ticker_1d, "vol": ticker_vol},
    )

    if ticker:
        st.session_state.global_selected_ticker = ticker
    elif st.session_state.global_selected_ticker not in df["Ticker"].tolist():
        st.session_state.global_selected_ticker = (
            df.iloc[0]["Ticker"] if not df.empty else None
        )

    selected = st.session_state.global_selected_ticker

    if selected:

        header_col, link_col = st.columns([4, 1])

        with header_col:
            st.subheader(f"📈 {selected} — RSI Wave Setup (1H)")

        with link_col:
            st.link_button(
                "📊 Open in TradingView",
                tradingview_url("https://www.tradingview.com/chart/gV4Z67QB/", selected),
                use_container_width=True,
            )

        status = RSIWaveStatusService.analyse(selected, period="730d")

        if status is None:
            st.info("Not enough 1H history to evaluate this instrument yet.")
        else:
            st.info(status["description"])

        if status and status["direction"] and status["stop_target"]:

            st_target = status["stop_target"]

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Direction", status["direction"])
            c2.metric("Entry", round(status["price"], 2))
            c3.metric("Stop", st_target["stop"])
            c4.metric("Target 1", st_target["target1"])
            c5.metric("Risk:Reward", f"1:{st_target['risk_reward']}")

            notes = st.text_input("Notes (optional)", key=f"park_notes_{selected}")

            if st.button("📌 Park this trade", key=f"park_btn_{selected}"):

                TradeJournal.park(
                    selected,
                    status["direction"],
                    round(status["price"], 2),
                    st_target,
                    status["state"],
                    status["rsi"],
                    notes=notes,
                )

                st.success(f"Parked {status['direction']} {selected} @ {round(status['price'], 2)}")

        st.divider()
        st.subheader(f"🔀 {selected} — Reversal Playbook (1H + Daily)")

        reversal = ReversalStatusService.analyse(selected)

        if reversal is None:
            st.info("Not enough 1H+Daily history to evaluate this instrument yet.")
        else:
            st.info(reversal["description"])

        if reversal and reversal["direction"] and reversal["stop_target"] and reversal["stop_target"]["stop"] is not None:

            r_target = reversal["stop_target"]

            cols = st.columns(5)
            cols[0].metric("Direction", reversal["direction"])
            cols[1].metric("Entry", reversal["price"])
            cols[2].metric("Stop", r_target["stop"])
            cols[3].metric("Target", r_target["target1"])
            cols[4].metric("Risk:Reward", f"1:{r_target['risk_reward']}")

            reversal_notes = st.text_input("Notes (optional)", key=f"reversal_notes_{selected}")

            if st.button("📌 Park this trade", key=f"reversal_park_btn_{selected}"):

                TradeJournal.park(
                    selected,
                    reversal["direction"],
                    reversal["price"],
                    r_target,
                    reversal["state"],
                    reversal["rsi"],
                    notes=reversal_notes,
                )

                st.success(f"Parked {reversal['direction']} {selected} @ {reversal['price']}")

        st.divider()
        st.subheader(f"📆 {selected} — Reversal Playbook (Daily + Weekly)")

        daily_reversal = DailyReversalStatusService.analyse(selected)

        if daily_reversal is None:
            st.info("Not enough Daily+Weekly history to evaluate this instrument yet.")
        else:
            st.info(daily_reversal["description"])

        if daily_reversal and daily_reversal["direction"] and daily_reversal["stop_target"] and daily_reversal["stop_target"]["stop"] is not None:

            dr_target = daily_reversal["stop_target"]

            cols = st.columns(5)
            cols[0].metric("Direction", daily_reversal["direction"])
            cols[1].metric("Entry", daily_reversal["price"])
            cols[2].metric("Stop", dr_target["stop"])
            cols[3].metric("Target", dr_target["target1"])
            cols[4].metric("Risk:Reward", f"1:{dr_target['risk_reward']}")

            daily_reversal_notes = st.text_input("Notes (optional)", key=f"global_daily_reversal_notes_{selected}")

            if st.button("📌 Park this trade", key=f"global_daily_reversal_park_btn_{selected}"):

                TradeJournal.park(
                    selected, daily_reversal["direction"], daily_reversal["price"], dr_target,
                    daily_reversal["state"], daily_reversal["rsi"], notes=daily_reversal_notes,
                )

                st.success(f"Parked {daily_reversal['direction']} {selected} @ {daily_reversal['price']}")


def render_parked_trades():

    st.subheader("📌 Parked Trades")

    df = TradeJournal.load()

    if df.empty:
        st.caption("No trades parked yet.")
        return

    header = st.columns([1, 1, 1, 1, 1, 1, 1, 1])

    for col, label in zip(
        header,
        ["Ticker", "Direction", "Entry", "Stop", "Target 1", "R:R", "Parked At", ""],
    ):
        col.markdown(f"**{label}**")

    for idx, row in df.iterrows():

        cols = st.columns([1, 1, 1, 1, 1, 1, 1, 1])

        cols[0].write(row["Ticker"])
        cols[1].write(row["Direction"])
        cols[2].write(row["Entry"])
        cols[3].write(row["Stop"])
        cols[4].write(row["Target1"])
        cols[5].write(f"1:{row['RiskReward']}")
        cols[6].write(row["ParkedAt"])

        if cols[7].button("Remove", key=f"remove_parked_{idx}"):
            TradeJournal.remove(idx)
            st.rerun()


def _humanize_alert_age(ts):
    """
    "5m ago" / "3h ago" / "Yesterday" / "Jul 09, 2:30 PM CET" - reads
    like a social-media notification feed close up (relative) and
    falls back to an absolute CET timestamp once it's more than a
    day old, when "X hours ago" stops being a useful measure.
    """

    if pd.isna(ts):
        return "—"

    delta_seconds = (time_utils.now_cet().replace(tzinfo=None) - ts).total_seconds()

    if delta_seconds < 60:
        return "just now"

    if delta_seconds < 3600:
        return f"{int(delta_seconds // 60)}m ago"

    if delta_seconds < 86400:
        return f"{int(delta_seconds // 3600)}h ago"

    if delta_seconds < 172800:
        return "Yesterday"

    hour12 = ts.hour % 12 or 12
    ampm = "AM" if ts.hour < 12 else "PM"

    return f"{ts.strftime('%b %d')}, {hour12}:{ts.minute:02d} {ampm} CET"


def render_notifications_feed():
    """
    Every alert MarketPulse has actually pushed to you (desktop
    notification, Telegram, and in-app toast, across Global Indices,
    US, India, and Crypto) as one scrollable feed, newest first - the
    same underlying log as Alert Tracking below, just read as "what
    did you tell me and when" instead of "did it hit target/stop".
    """

    st.subheader("🔔 Notifications")

    df = AlertLog.load()

    if df.empty:
        st.info("No notifications yet - alerts will show up here the moment something fires.")
        return

    df = df.copy()
    df["_ts"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df = df.sort_values("_ts", ascending=False)

    new_count = int((df["_ts"] >= time_utils.now_cet().replace(tzinfo=None) - pd.Timedelta(hours=1)).sum())

    st.caption(
        f"🆕 {new_count} in the last hour" if new_count else "Nothing new in the last hour"
    )

    for _, row in df.head(50).iterrows():

        icon = "🟢" if row["Direction"] == "LONG" else "🔴"
        is_new = pd.notna(row["_ts"]) and (time_utils.now_cet().replace(tzinfo=None) - row["_ts"]).total_seconds() < 3600

        with st.container(border=True):

            text_col, time_col = st.columns([5, 2])

            with text_col:

                st.markdown(f"{icon} **{row['Direction']} — {row['Name']} ({row['Ticker']})**" + (" 🆕" if is_new else ""))

                levels = ""
                if pd.notna(row.get("Stop")):
                    levels = f" · Stop {row['Stop']} · Target {row['Target1']}"

                st.caption(f"Price {row['EntryPrice']} · RSI {row['RSI']}{levels}")

            with time_col:
                st.caption(_humanize_alert_age(row["_ts"]))


def render_notifications_tab():

    render_notifications_feed()
    st.divider()
    render_alert_tracking()


def render_alert_tracking():
    """
    Every alert the system has actually sent, auto-logged (see
    check_for_new_entries()), with a manual "check outcomes" button
    that re-fetches current prices for still-open alerts and marks
    each HIT TARGET / HIT STOP - this is the "did the alerts work"
    check, not a manual journal like Parked Trades.
    """

    st.subheader("📋 Alert Tracking")

    if st.button("🔄 Check alert outcomes", key="refresh_alert_log"):
        with st.spinner("Checking current prices against each alert's stop/target..."):
            AlertLog.evaluate()

    df = AlertLog.load()

    if df.empty:
        st.caption("No alerts sent yet.")
        return

    stats = AlertLog.summary(df)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total alerts", stats["total"])
    c2.metric("Still open", stats["open"])
    c3.metric("Hit target", stats["hit_target"])
    c4.metric("Hit stop", stats["hit_stop"])
    c5.metric("Win rate (closed)", f"{stats['win_rate']}%")

    display_cols = [
        "Timestamp", "Ticker", "Name", "Direction", "EntryPrice",
        "Stop", "Target1", "Status", "ReturnPct", "ClosedAt",
    ]

    st.dataframe(
        df[display_cols].sort_values("Timestamp", ascending=False),
        use_container_width=True,
        hide_index=True,
    )


ALERT_TYPE_MEANINGS = {
    # (source column, state key): (meaning, act now?)
    ("Setup", "WATCHING"): ("Nothing happening - RSI not at an extreme.", "No"),
    ("Setup", "ALERT_LONG"): ("1H RSI touched oversold (~20-25), watching for a clean cross above 65.", "No — wait"),
    ("Setup", "ALERT_SHORT"): ("1H RSI touched overbought (~75-80), watching for a clean cross below 35.", "No — wait"),
    ("Setup", "ENTRY_LONG"): ("A LONG entry just fired (direct or retest cross of 65).", "Yes"),
    ("Setup", "ENTRY_SHORT"): ("A SHORT entry just fired (direct or retest cross of 35).", "Yes"),
    ("Setup", "WAVE_LONG"): ("Already in a LONG wave from an earlier entry - watching for the next pullback-and-resume.", "No — already positioned"),
    ("Setup", "WAVE_SHORT"): ("Already in a SHORT wave from an earlier entry - watching for the next pullback-and-resume.", "No — already positioned"),
    ("Setup", "TOO_LATE"): ("RSI ran straight through without pausing - the move already happened, don't chase.", "No — skip"),
    ("Reversal", "WATCHING"): ("Nothing happening right now - no setup active.", "No"),
    ("Reversal", "BUY_ALERT"): ("1H RSI touched ≤22 (oversold), price above Daily 200 EMA. Watching for a cross above 65.", "No — wait"),
    ("Reversal", "BUY_ALERT_CONFIRM"): ("1H RSI crossed 65. Watching EMA20/200 spacing, a 200-EMA retest, or RSI holding 65 as support + a fresh EMA200 reclaim, for the actual entry.", "No — wait"),
    ("Reversal", "BUY_ALERT_CONFIRM_PATH_C_FORMING"): ("RSI is currently holding the 60-65 zone as support AND price is already holding above the 1H EMA20/200 - Path C could confirm on the very next bar.", "No — watch closely"),
    ("Reversal", "BUY_SIGNAL"): ("Entry condition met — Path A (EMA20/200 far apart), Path B (200-EMA reclaim + retest), or Path C (RSI held 65 as support + 200-EMA reclaim).", "Yes — BUY"),
    ("Reversal", "SELL_SIGNAL"): ("RSI broke below 40 with a fresh low, or got rejected near 60 at 1H EMA200 resistance.", "Yes — SELL"),
    ("Reversal", "SELL_SIGNAL_CONTINUATION"): ("After an initial breakdown, RSI took a weak 'slight support' bounce (stayed below 50) then broke to a fresh low again - a stronger bear continuation, more so if it followed a failed 60-65 rejection.", "Yes — SELL (stronger)"),
}


def render_alert_types_legend():

    with st.expander("🏷️ Alert Types — what every label means"):

        rows = []

        for (column, state), (meaning, act_now) in ALERT_TYPE_MEANINGS.items():

            labels = RSIWaveStrategy.STATE_LABELS if column == "Setup" else ReversalPlaybook.STATE_LABELS
            label = labels.get(state, state)

            rows.append(f"| {column} | {label} | {meaning} | {act_now} |")

        table = "\n".join(
            [
                "| Column | Label | Meaning | Act now? |",
                "|---|---|---|---|",
                *rows,
            ]
        )

        st.markdown(table)


def render_algo_reference():

    with st.expander("📖 Algo Reference — Reversal Playbook rules (1H + Daily)"):

        st.markdown(
            """
**v2 — replaces the old dual-timeframe (1H+15m) Algo1/Algo2 entirely.** Runs on 1H bars only, with the
**Daily 200 EMA** as a higher-timeframe context filter. No more 15m confirmation step.

**🟢 BUY (refines the original Algo 1)**

| Step | Condition | What happens |
|---|---|---|
| Filter | Price above the **Daily 200 EMA** | Only consider this setup in a bullish daily context |
| 1 | 1H RSI touches **≤22** (oversold) | 🟡 Start watching |
| 2 | From there, 1H RSI crosses **up through 65** | 🟠 Alert only — **not a buy yet** |
| 3a | *Path A:* 1H EMA20 and EMA200 are **far apart** (diverged ≥2%) | 🟢 BUY |
| 3b | *Path B:* 1H price crossed **up through EMA200** recently, and is now **holding it as support** | 🟢 BUY |
| 3c | *Path C:* RSI pulls back toward 65 after the initial cross but **holds it as support** (never falls below 60), then resumes back above 65 while price has **recently reclaimed the 1H 20 or 200 EMA** ("the hourly correction is over") | 🟢 BUY |

Stop = lowest 1H low since the Step 1 touch. Target = **+1.25%** (placeholder — you said you'll tune this yourself).

**🔵 Path C forming:** while RSI is currently sitting in the 60-65 band (not yet re-crossed) AND price is already holding above the 1H EMA20 or EMA200, that's shown live as "Path C forming" — a heads-up that a Path C BUY could confirm on the very next bar, not just reported after it already happened.

**🟡 Path D (counter-daily-trend):** the exact same touch(22) → confirm(65) → reclaim-and-retest-1H-200-EMA idea as Path B, but tracked completely separately, with **no Daily-trend filter at all**. Path B only fires when price is already above the Daily 200 EMA (a "trend-following" setup); Path D fires the *same* mechanical pattern even while price is still below the Daily 200 EMA - a genuine 1H trend change happening before the daily trend has caught up. Explicitly riskier (going against the broader daily context), so it's labeled and flagged separately rather than folded into Path B. Backtested: fires more often on instruments that spend more time below their Daily 200 EMA (e.g. oil), rarer on ones that don't.

---

**📅 Daily confluence (independent of the 1H machine above)**

Tracks the **Daily RSI**, separately from all of the above:
- **Multi-try breakout:** if Daily RSI rallies into the 55–65 zone and retreats below 55 **without** breaking 65 — that's a "failed try." Once Daily RSI finally breaks above 65 after **2 or more failed tries**, it's flagged as a stronger, longer-lasting move (per your USD/CHF chart example).
- **Daily Path C:** the same "cross 65 → hold it as support → reclaim the 200 EMA" idea as the 1H Path C above, but on the Daily timeframe — flagged both while forming and once confirmed.

Both are standalone notes, not gates — they appear alongside whatever the 1H engine is showing (WATCHING, an alert, or a BUY/SELL signal) for a few days after firing, then fade.

---

**🟢 Uptrend RSI-40 support (independent confluence)**

Once price has held above the **1H 200 EMA** for a sustained run (50+ bars — a "definite run," not a fresh cross), a pullback to the **35-45 RSI zone** that holds as support and bounces back above 45 is flagged as a continuation note — "not always, but not a thing to skip." Debounced the same way as the other triggers (only re-arms after RSI rallies back above 55), and fades a few bars after firing.

---

**⏱ 15m readiness (Global Indices only — a confirmation lens, not an independent scan)**

15m is never scanned across the whole universe (that dual-timeframe complexity was explicitly removed earlier). Instead, once a symbol's 1H Reversal state has **already confirmed** (RSI crossed 65, Path C forming, or a BUY signal fired), one extra 15m/5d fetch checks whether 15m RSI has recently touched oversold (≤30) and is now moving back toward/through 65 — the same "getting ready" pattern observed to often coincide with the 1H confirmation. Shown as its own column in the **⏱ 15-Minute** table; everything else shows "— (needs 1H confirm first)" since it's genuinely not computed for those symbols.

---

**🔴 SELL (fully replaces Algo 2)**

| Trigger | Condition |
|---|---|
| Breakdown | 1H RSI crosses **below 40**, and price is breaking a recent swing low at the same time |
| Rejection | 1H RSI rallies toward 60 but gets **rejected** there (rolls over without reaching 65), while price sits at/below the 1H EMA200 (resistance) |
| **Continuation (stronger)** | After a Breakdown, RSI takes a **"slight support" bounce** that stays below 50 (a weak, failed recovery — not a real reversal), then **breaks down again** to a fresh low. Flagged as an extra-strong bear call if it also follows a recent Rejection trigger (the earlier bullish breakout attempt failed, reinforcing the move). |

Guardrail: **suppressed** if price is within ~1% of the **Daily 200 EMA** (that level likely acts as support — don't fight it).
Stop = recent swing high. Target = **-1.25%** (same placeholder, mirrored). All three ideas are explicitly tentative — not fully settled.

**If a BUY signal fires shortly after a SELL trigger was active**, the alert explicitly flags that the sell thesis just got
invalidated (directly answering "what if the sell call turns into a buy — that's against me").

---

**🌡 VIX Risk Sentiment (Global Indices only)**

VIX (^VIX) runs through the exact same 1H Reversal Playbook engine as everything else - no separate logic, no extra fetch. Its RSI is read as a market-wide risk gauge: if VIX's own 1H RSI has crossed/is holding above 65 (or its Reversal state shows a BUY confirm/signal), a banner appears at the top of Global Indices flagging risk-off conditions - fear is rising, which typically coincides with equity weakness, so a fresh BUY signal elsewhere deserves extra scrutiny right then. The reverse (VIX RSI ≤35 or a SELL state) shows a risk-on banner. Purely informational - it's for your own judgment, not an automatic block on any signal.

---

**All specific numbers above (22/65/40/50/60, the 2% divergence, the 1.25% target, the 1% support band) are tunable —
flagged in code as best-effort readings of a still-evolving idea, not fixed truths.**
            """
        )


def render_global_indices_tab(meta):

    st.subheader("🌍 Global Indices — Intraday")

    render_notification_enable_button()
    render_alert_types_legend()
    render_algo_reference()

    # Same universe as the sidebar's "Global Macro" market (every Global
    # asset except Indian Indices, which has its own top-level market
    # option) - reused rather than re-filtered, so the two stay in sync.
    sectors = Sidebar._sectors(meta, "Global Macro")

    left, right = st.columns([3, 1])

    with left:
        sector = st.selectbox(
            "Region",
            sectors,
            key="global_sector",
        )

    with right:
        st.write("")
        scan_now = st.button(
            "🔄 Scan Now",
            key="global_load",
            use_container_width=True,
        )

    market = st.session_state.global_market
    region_changed = market is not None and market["sector"] != sector

    if scan_now or region_changed:
        # Forces an immediate background scan instead of waiting for
        # GLOBAL_INDICES_REFRESH_SECONDS to elapse - a region switch
        # or manual click should feel responsive, not queued behind
        # the routine refresh cadence.
        started = universe_cache.start_scan(f"global_{sector}", lambda: _scan_global_indices_data(sector), pool="global")
        st.toast("Scanning in the background..." if started else "Already scanning in the background...", icon="🔄")

    refresh_global_indices()

    render_global_indices_live()
    check_for_new_entries()
    check_for_new_reversal_signals()

    st.divider()
    render_parked_trades()


def _ensure_universe_state(prefix):
    """
    Defensive re-init for the per-universe session keys, called at the
    top of every entry point that reads them (load_universe(),
    _refresh_universe_body(), render_universe_live()) instead of
    relying solely on init_state() having already run. Necessary
    because a fragment's own run_every timer can fire independently of
    main() - if the running process was hot-reloaded after a new
    prefixed key was added to init_state()'s defaults, an
    already-open session's fragment timer would otherwise hit a
    KeyError on that key the next time it fires on its own, without
    ever passing back through main()/init_state() first.
    """

    defaults = {
        f"{prefix}_market": None,
        f"{prefix}_selected_ticker": None,
        f"{prefix}_wave_states": {},
        f"{prefix}_wave_states_seeded": False,
        f"{prefix}_reversal_states": {},
        f"{prefix}_reversal_states_seeded": False,
        f"{prefix}_last_loaded_ts": 0,
        f"{prefix}_seen_cache_ts": 0,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _scan_universe_data(country):
    """
    Pure, background-thread-safe universe scan - no st.* calls (those
    require a session's own Streamlit script-run context, which a
    background thread doesn't have). Returns a plain dict; the main
    thread is what turns this into session state + notifications, in
    _refresh_universe_body() below, once the scan is actually done.
    """

    df, success, failed = DashboardLoader.load(
        {
            "country": country,
            "sector": "All",
            "search": "",
            "portfolio_only": False,
            "watchlist_only": False,
            "priority": 1,
        }
    )

    wave_states = {}
    reversal_states = {}

    if not df.empty:

        tickers = df["Ticker"].tolist()

        wave_states = RSIWaveStatusService.screen_states(tickers)
        wave_labels = {t: RSIWaveStrategy.STATE_LABELS.get(info["state"], "⚪ Watching") for t, info in wave_states.items()}
        df["Setup"] = df["Ticker"].map(wave_labels).fillna(df["Setup"])
        df["Setup Full"] = df["Ticker"].map({t: info["description"] for t, info in wave_states.items()}).fillna("")
        df["Setup Timestamp"] = df["Ticker"].map({t: _format_event_time(info["event_time"]) for t, info in wave_states.items()}).fillna("—")

        reversal_states = ReversalStatusService.screen_states(tickers)
        reversal_labels = {t: ReversalPlaybook.STATE_LABELS.get(info["state"], "⚪ Watching") for t, info in reversal_states.items()}
        df["Reversal"] = df["Ticker"].map(reversal_labels).fillna(df["Reversal"])
        df["Reversal Full"] = df["Ticker"].map({t: info["description"] for t, info in reversal_states.items()}).fillna("")
        df["Reversal Timestamp"] = df["Ticker"].map({t: _format_event_time(info["event_time"]) for t, info in reversal_states.items()}).fillna("—")

        # Daily+Weekly read (separate engine,
        # analysis/reversal_playbook_daily.py) - additive alongside the
        # two 1H-based columns above, everywhere.
        daily_reversal_states = DailyReversalStatusService.screen_states(tickers)
        daily_reversal_labels = {t: DailyWeeklyReversalPlaybook.STATE_LABELS.get(info["state"], "⚪ Watching") for t, info in daily_reversal_states.items()}
        df["Daily Reversal"] = df["Ticker"].map(daily_reversal_labels).fillna("⚪ Watching")
        df["Daily Reversal Full"] = df["Ticker"].map({t: info["description"] for t, info in daily_reversal_states.items()}).fillna("")
        df["Daily Reversal Timestamp"] = df["Ticker"].map({t: _format_event_time(info["event_time"]) for t, info in daily_reversal_states.items()}).fillna("—")

        # Weekly confluence, derived from the same Daily+Weekly scan
        # above - no extra fetch.
        weekly_labels = {t: DailyWeeklyReversalPlaybook.WEEKLY_STATE_LABELS.get(info["weekly_state"], "⚪ Watching") for t, info in daily_reversal_states.items()}
        df["Weekly"] = df["Ticker"].map(weekly_labels).fillna("⚪ Watching")
        df["Weekly Full"] = df["Ticker"].map({t: info["weekly_description"] for t, info in daily_reversal_states.items()}).fillna("")
        df["Weekly Timestamp"] = df["Ticker"].map({t: _format_event_time(info["weekly_event_time"]) for t, info in daily_reversal_states.items()}).fillna("—")

    return {
        "df": df,
        "success": success,
        "failed": failed,
        "wave_states": wave_states,
        "reversal_states": reversal_states,
    }


def _notify_universe_changes(prefix, name_map, wave_states, reversal_states):
    """
    Same new-entry / new-signal diffing as check_for_new_entries() /
    check_for_new_reversal_signals(), generalized across the three
    stock/crypto universes. On the very first load (nothing seeded
    yet), whatever is already active isn't NEW - just record the
    baseline silently, same reasoning as the Global Indices tab.
    """

    previous_wave = st.session_state[f"{prefix}_wave_states"]
    is_first_wave_check = not st.session_state[f"{prefix}_wave_states_seeded"]

    new_entries = (
        []
        if is_first_wave_check
        else [
            {
                "ticker": ticker,
                "name": name_map.get(ticker, ticker),
                "direction": "LONG" if info["state"] == "ENTRY_LONG" else "SHORT",
                "price": info["price"],
                "rsi": info["rsi"],
            }
            for ticker, info in wave_states.items()
            if info["state"] in ("ENTRY_LONG", "ENTRY_SHORT")
            and (previous_wave.get(ticker) or {}).get("state") != info["state"]
        ]
    )

    for entry in new_entries:

        # Cross-session, cross-restart dedup - see check_for_new_entries().
        if AlertLog.recently_logged(entry["ticker"], entry["direction"]):
            continue

        icon = "🟢" if entry["direction"] == "LONG" else "🔴"

        full_status = RSIWaveStatusService.analyse(entry["ticker"], period="730d")
        stop_target = full_status["stop_target"] if full_status else None

        # Telegram deliberately NOT sent here - only Global Indices
        # notifies by Telegram (check_for_new_entries above). US/India/
        # Crypto still toast in-browser and log to Alert Tracking, just
        # without pinging the phone for every one of ~250 symbols.
        st.toast(f"{entry['direction']} entry: {entry['name']}", icon=icon)

        AlertLog.log_alert(
            entry["ticker"], entry["name"], entry["direction"], entry["price"], entry["rsi"], stop_target,
        )

    previous_reversal = st.session_state[f"{prefix}_reversal_states"]
    is_first_reversal_check = not st.session_state[f"{prefix}_reversal_states_seeded"]

    new_signals = (
        []
        if is_first_reversal_check
        else [
            {
                "ticker": ticker,
                "name": name_map.get(ticker, ticker),
                "direction": REVERSAL_SIGNAL_DIRECTIONS[info["state"]],
                "state": info["state"],
                "price": info["price"],
                "rsi": info["rsi"],
            }
            for ticker, info in reversal_states.items()
            if info["state"] in REVERSAL_SIGNAL_DIRECTIONS
            and (previous_reversal.get(ticker) or {}).get("state") != info["state"]
        ]
    )

    for signal in new_signals:

        # Cross-session, cross-restart dedup - see check_for_new_entries().
        if AlertLog.recently_logged(signal["ticker"], signal["direction"]):
            continue

        icon = "🟢" if signal["direction"] == "LONG" else "🔴"
        signal_label = REVERSAL_SIGNAL_LABELS.get(signal["state"], signal["state"])

        full_status = ReversalStatusService.analyse(signal["ticker"])
        stop_target = full_status["stop_target"] if full_status else None

        # Telegram deliberately NOT sent here - only Global Indices
        # notifies by Telegram. US/India/Crypto still toast in-browser
        # and log to Alert Tracking, just without pinging the phone for
        # every one of ~250 symbols.
        st.toast(f"{signal_label}: {signal['name']}", icon=icon)

        AlertLog.log_alert(
            signal["ticker"], signal["name"], signal["direction"], signal["price"], signal["rsi"], stop_target,
        )


UNIVERSE_POLL_SECONDS = 20   # how often the page checks whether a background scan finished - cheap (just a dict read, no network), so this can be much shorter than the hourly rescan cadence itself


def _refresh_universe_body(prefix, country):
    """
    Non-blocking: kicks off a background scan (real OS thread, see
    dashboard/services/universe_cache.py) if the cached result is
    stale or missing, but never waits for it. Always renders whatever
    is already cached - however old - with a "last refreshed"
    timestamp, so it's clear whether what's on screen is current or a
    fresh scan is still running. This is what lets switching to this
    tab feel instant even though a full rescan can take minutes.
    """

    _ensure_universe_state(prefix)

    cache_entry = universe_cache.get(prefix)
    stale = (
        cache_entry is None
        or (not cache_entry["loading"] and (time.time() - cache_entry["ts"]) >= UNIVERSE_REFRESH_SECONDS)
    )

    if stale:
        universe_cache.start_scan(prefix, lambda: _scan_universe_data(country), pool=prefix)
        cache_entry = universe_cache.get(prefix)

    if cache_entry is None or cache_entry["data"] is None:
        eta = _scan_eta_text(cache_entry) if cache_entry else "no estimate yet - typically 1-4 minutes depending on the universe size"
        st.info(
            f"Scanning {country} for the first time — {eta}. "
            "Feel free to check other tabs meanwhile; this keeps running in the background."
        )
        return

    # A newer result than this session has seen is ready - the diff/
    # notify step (toast/AlertLog) has to happen here on the main
    # thread, since the background worker can't touch session state or
    # call st.toast().
    seen_ts_key = f"{prefix}_seen_cache_ts"

    if cache_entry["ts"] > st.session_state.get(seen_ts_key, 0):

        result = cache_entry["data"]
        result_df = result["df"]
        name_map = dict(zip(result_df["Ticker"], result_df["Name"])) if not result_df.empty else {}

        _notify_universe_changes(prefix, name_map, result["wave_states"], result["reversal_states"])

        st.session_state[f"{prefix}_market"] = {"df": result_df, "success": result["success"], "failed": result["failed"]}

        if st.session_state[f"{prefix}_selected_ticker"] not in result_df["Ticker"].tolist():
            st.session_state[f"{prefix}_selected_ticker"] = result_df.iloc[0]["Ticker"] if not result_df.empty else None

        st.session_state[f"{prefix}_wave_states"] = result["wave_states"]
        st.session_state[f"{prefix}_wave_states_seeded"] = True
        st.session_state[f"{prefix}_reversal_states"] = result["reversal_states"]
        st.session_state[f"{prefix}_reversal_states_seeded"] = True
        st.session_state[f"{prefix}_last_loaded_ts"] = cache_entry["ts"]
        st.session_state[seen_ts_key] = cache_entry["ts"]

    last_loaded = st.session_state[f"{prefix}_last_loaded_ts"]
    age_minutes = round((time.time() - last_loaded) / 60)
    refreshed_at = time_utils.unix_to_cet(last_loaded).strftime("%H:%M:%S CET")

    if cache_entry["loading"]:
        st.caption(f"🕐 Showing data from {refreshed_at} ({age_minutes} min ago) — 🔄 a fresh scan is running in the background, {_scan_eta_text(cache_entry)}, and will swap in automatically once done.")
    else:
        st.caption(f"🕐 Last refreshed at {refreshed_at} ({age_minutes} min ago) — refreshes automatically every hour, or click Scan Now above.")


@st.fragment(run_every=UNIVERSE_POLL_SECONDS)
def refresh_us_universe():
    _refresh_universe_body("us", "USA")


@st.fragment(run_every=UNIVERSE_POLL_SECONDS)
def refresh_india_universe():
    _refresh_universe_body("india", "India")


@st.fragment(run_every=UNIVERSE_POLL_SECONDS)
def refresh_crypto_universe():
    _refresh_universe_body("crypto", "Crypto")


UNIVERSE_REFRESH_FRAGMENTS = {
    "us": refresh_us_universe,
    "india": refresh_india_universe,
    "crypto": refresh_crypto_universe,
}


def render_universe_live(prefix, title):
    """
    Reactive display of whatever is already sitting in session state -
    reruns on every normal script rerun (e.g. a Scanner row click)
    without re-fetching anything, since the actual hourly reload lives
    in the fragment above.
    """

    _ensure_universe_state(prefix)

    market = st.session_state[f"{prefix}_market"]

    if market is None:
        st.info("Loading for the first time...")
        return

    df = market["df"]

    if df.empty:
        st.warning("No assets found for this universe.")
        return

    st.caption(f"{len(df)} symbols")

    # Hourly is Crypto-only here - US/India stocks aren't traded
    # intraday, so that table (and the two 1H-based detail boxes
    # further below) would just be noise for those two.
    show_hourly = prefix not in ("us", "india")

    # Two (Crypto) or three (US/India) separate tables instead of one
    # wide mixed-timeframe grid - 15m doesn't help for stocks/crypto
    # you don't trade intraday, so Hourly/Daily/Weekly instead of
    # Global Indices' 15m/Hourly/Daily. All drive the same
    # selected-ticker detail boxes below. Each is pre-filtered to hide
    # rows with nothing captured yet (plain Watching across the
    # board), same as Global Indices.
    df_1d = _only_active_rows(df, ["Daily Reversal"])
    df_1w = _only_active_rows(df, ["Weekly"])

    ticker_1h = None

    if show_hourly:

        df_1h = _only_active_rows(df, ["Setup", "Reversal"])

        if df_1h.empty:
            st.caption("🕐 Hourly: nothing captured yet.")
        else:
            ticker_1h = Scanner.render(
                df_1h, default_sort="Reversal", key_prefix=f"{prefix}_1h", compact=False,
                columns=["Status", "Ticker", "Name", "Price", "1H %", "Setup", "Setup Timestamp", "Reversal", "Reversal Timestamp"],
                title="🕐 Hourly", height=350,
            )

    if df_1d.empty:
        st.caption("📆 Daily: nothing captured yet.")
        ticker_1d = None
    else:
        ticker_1d = Scanner.render(
            df_1d, default_sort="Daily Reversal", key_prefix=f"{prefix}_1d", compact=False,
            columns=["Status", "Ticker", "Name", "Price", "1D %", "Daily Reversal", "Daily Reversal Timestamp"],
            title="📆 Daily", height=350,
        )

    if df_1w.empty:
        st.caption("🗓 Weekly: nothing captured yet.")
        ticker_1w = None
    else:
        ticker_1w = Scanner.render(
            df_1w, default_sort="Weekly", key_prefix=f"{prefix}_1w", compact=False,
            columns=["Status", "Ticker", "Name", "Price", "Weekly", "Weekly Timestamp"],
            title="🗓 Weekly", height=350,
        )

    selections = {"1d": ticker_1d, "1w": ticker_1w}

    if show_hourly:
        selections["1h"] = ticker_1h

    ticker = _resolve_clicked_ticker(prefix, selections)

    if ticker:
        st.session_state[f"{prefix}_selected_ticker"] = ticker
    elif st.session_state[f"{prefix}_selected_ticker"] not in df["Ticker"].tolist():
        st.session_state[f"{prefix}_selected_ticker"] = df.iloc[0]["Ticker"] if not df.empty else None

    selected = st.session_state[f"{prefix}_selected_ticker"]

    if not selected:
        return

    header_col, link_col = st.columns([4, 1])

    with header_col:
        st.subheader(f"📈 {selected} — RSI Wave Setup (1H)" if show_hourly else f"📈 {selected}")

    with link_col:
        st.link_button(
            "📊 Open in TradingView",
            tradingview_url("https://www.tradingview.com/chart/gV4Z67QB/", selected),
            use_container_width=True,
        )

    if show_hourly:

        status = RSIWaveStatusService.analyse(selected, period="730d")

        if status is None:
            st.info("Not enough 1H history to evaluate this instrument yet.")
        else:
            st.info(status["description"])

        if status and status["direction"] and status["stop_target"]:

            st_target = status["stop_target"]

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Direction", status["direction"])
            c2.metric("Entry", round(status["price"], 2))
            c3.metric("Stop", st_target["stop"])
            c4.metric("Target 1", st_target["target1"])
            c5.metric("Risk:Reward", f"1:{st_target['risk_reward']}")

            notes = st.text_input("Notes (optional)", key=f"{prefix}_park_notes_{selected}")

            if st.button("📌 Park this trade", key=f"{prefix}_park_btn_{selected}"):

                TradeJournal.park(
                    selected, status["direction"], round(status["price"], 2), st_target, status["state"], status["rsi"], notes=notes,
                )

                st.success(f"Parked {status['direction']} {selected} @ {round(status['price'], 2)}")

        st.divider()
        st.subheader(f"🔀 {selected} — Reversal Playbook (1H + Daily)")

        reversal = ReversalStatusService.analyse(selected)

        if reversal is None:
            st.info("Not enough 1H+Daily history to evaluate this instrument yet.")
        else:
            st.info(reversal["description"])

        if reversal and reversal["direction"] and reversal["stop_target"] and reversal["stop_target"]["stop"] is not None:

            r_target = reversal["stop_target"]

            cols = st.columns(5)
            cols[0].metric("Direction", reversal["direction"])
            cols[1].metric("Entry", reversal["price"])
            cols[2].metric("Stop", r_target["stop"])
            cols[3].metric("Target", r_target["target1"])
            cols[4].metric("Risk:Reward", f"1:{r_target['risk_reward']}")

            reversal_notes = st.text_input("Notes (optional)", key=f"{prefix}_reversal_notes_{selected}")

            if st.button("📌 Park this trade", key=f"{prefix}_reversal_park_btn_{selected}"):

                TradeJournal.park(
                    selected, reversal["direction"], reversal["price"], r_target, reversal["state"], reversal["rsi"], notes=reversal_notes,
                )

                st.success(f"Parked {reversal['direction']} {selected} @ {reversal['price']}")

        st.divider()

    # A separate Daily+Weekly read (analysis/reversal_playbook_daily.py) -
    # for US/India, this is now the primary (only) technical read here
    # since the 1H boxes above are Crypto-only.
    st.subheader(f"📆 {selected} — Reversal Playbook (Daily + Weekly)")

    daily_reversal = DailyReversalStatusService.analyse(selected)

    if daily_reversal is None:
        st.info("Not enough Daily+Weekly history to evaluate this instrument yet.")
    else:
        st.info(daily_reversal["description"])

    if daily_reversal and daily_reversal["direction"] and daily_reversal["stop_target"] and daily_reversal["stop_target"]["stop"] is not None:

        dr_target = daily_reversal["stop_target"]

        cols = st.columns(5)
        cols[0].metric("Direction", daily_reversal["direction"])
        cols[1].metric("Entry", daily_reversal["price"])
        cols[2].metric("Stop", dr_target["stop"])
        cols[3].metric("Target", dr_target["target1"])
        cols[4].metric("Risk:Reward", f"1:{dr_target['risk_reward']}")

        daily_reversal_notes = st.text_input("Notes (optional)", key=f"{prefix}_daily_reversal_notes_{selected}")

        if st.button("📌 Park this trade", key=f"{prefix}_daily_reversal_park_btn_{selected}"):

            TradeJournal.park(
                selected, daily_reversal["direction"], daily_reversal["price"], dr_target,
                daily_reversal["state"], daily_reversal["rsi"], notes=daily_reversal_notes,
            )

            st.success(f"Parked {daily_reversal['direction']} {selected} @ {daily_reversal['price']}")


@st.fragment(run_every=30)
def _render_scan_now_button(prefix, country):
    """
    Its own fragment so clicking Scan Now only reruns this small
    button widget, not the entire script - the button used to live
    directly in the top-level tab body, so clicking it triggered a
    full-page rerun, and Streamlit dims/overlays the WHOLE app (every
    tab, not just this one) while a full rerun is in flight, which
    read as "the other tabs went blurry/read-only" even though
    nothing about them actually changed. The 30s timer here just keeps
    the "stuck?" check below fresh without needing a click first.

    If the current scan has been "loading" for a while, offers a
    manual force-restart instead of the normal button - previously the
    only way to recover a genuinely stuck scan (e.g. a hung network
    call) was to reboot the whole app; now a click here clears it and
    starts fresh immediately, without waiting out
    universe_cache.MAX_SCAN_SECONDS's automatic ceiling either.
    """

    entry = universe_cache.get(prefix)
    stuck_for = (
        time.time() - entry["loading_since"]
        if entry and entry["loading"] and entry.get("loading_since")
        else 0
    )

    if stuck_for > universe_cache.STUCK_WARNING_SECONDS:

        st.caption(f"⚠️ Stuck for {int(stuck_for // 60)}+ min")

        if st.button("⚠️ Force Restart", key=f"{prefix}_force_restart", use_container_width=True):
            universe_cache.force_clear(prefix)
            universe_cache.start_scan(prefix, lambda: _scan_universe_data(country), pool=prefix)
            st.toast("Cleared the stuck scan and started a fresh one...", icon="⚠️")

    elif st.button("🔄 Scan Now", key=f"{prefix}_manual_scan", use_container_width=True):
        started = universe_cache.start_scan(prefix, lambda: _scan_universe_data(country), pool=prefix)
        st.toast("Scan started in the background..." if started else "Already scanning in the background...", icon="🔄")


def render_universe_tab(prefix, country, title):

    _ensure_universe_state(prefix)

    header_col, button_col = st.columns([4, 1])

    with header_col:
        st.subheader(f"{title} — Reversal Playbook + RSI Wave")
        st.caption(
            "Full universe, auto-refreshed once an hour (not intraday-live like Global Indices) - too many symbols "
            "to rescan every few minutes without risking yfinance rate limits. Includes both the 1H Reversal "
            "Playbook and a separate Daily+Weekly read."
        )

    with button_col:
        st.write("")
        _render_scan_now_button(prefix, country)

    UNIVERSE_REFRESH_FRAGMENTS[prefix]()
    render_universe_live(prefix, title)


# (label, session key, columns to scan for actionable rows -> keywords that mark that column's label as "act now")
COMMAND_CENTER_SOURCES = [
    ("🌍 Global Indices", "global_market"),
    ("🇺🇸 US Stocks", "us_market"),
    ("🇮🇳 Indian Stocks", "india_market"),
    ("🪙 Crypto", "crypto_market"),
]

COMMAND_CENTER_COLUMNS = [
    # column, full-text column, base timeframe, keywords that identify an "act now" label (vs. watching/alert/forming)
    ("Setup", "Setup Full", "Hourly", ("entry",)),
    ("Reversal", "Reversal Full", "Hourly", ("signal", "trigger", "continuation")),
    ("Daily Reversal", "Daily Reversal Full", "Daily", ("signal", "trigger", "continuation")),
]

# US/India aren't traded intraday (see render_universe_live's
# show_hourly gate) - their dataframes still carry Setup/Reversal
# columns internally (the underlying scan still computes them), so
# Command Center has to explicitly exclude those two sources for
# Hourly-based rows rather than just checking column presence.
COMMAND_CENTER_HOURLY_SOURCES = [
    (label, key) for label, key in COMMAND_CENTER_SOURCES if key not in ("us_market", "india_market")
]

# Mirrors the exact column sets each per-tab timeframe table already
# renders (Global Indices / US / India / Crypto) - (display column,
# full-text column, timestamp column). A row is pulled in if ANY of a
# table's signal columns isn't a plain "Watching" read, so this
# surfaces everything already-found-interesting (Path C forming,
# multi-try breakouts, alerts - not just the narrower "act now" set
# the combined table above filters to).
COMMAND_CENTER_TIMEFRAME_TABLES = [
    ("🕐 Hourly", "cc_hourly", [
        ("Setup", "Setup Full", "Setup Timestamp"),
        ("Reversal", "Reversal Full", "Reversal Timestamp"),
    ], COMMAND_CENTER_HOURLY_SOURCES),
    ("📆 Daily", "cc_daily", [
        ("Daily Reversal", "Daily Reversal Full", "Daily Reversal Timestamp"),
    ], COMMAND_CENTER_SOURCES),
    ("🗓 Weekly", "cc_weekly", [
        ("Weekly", "Weekly Full", "Weekly Timestamp"),
    ], COMMAND_CENTER_SOURCES),
]


def _build_command_center_timeframe_df(signal_columns, sources):
    """
    Combines the given signal columns across the given sources into
    one dataframe, keeping only rows where at least one of those
    columns is more than a plain "Watching" read - reads cached
    session state only, same as the combined table above.
    """

    rows = []

    for label, session_key in sources:

        market = st.session_state.get(session_key)

        if market is None:
            continue

        df = market["df"]

        if df.empty:
            continue

        available = [(c, fc, tc) for c, fc, tc in signal_columns if c in df.columns]

        if not available:
            continue

        mask = pd.Series(False, index=df.index)

        for column, _, _ in available:
            mask = mask | ~df[column].astype(str).str.contains("watching", case=False, na=False)

        for _, row in df[mask].iterrows():

            entry = {
                "Source": label,
                "Status": row.get("Status"),
                "Ticker": row["Ticker"],
                "Name": row["Name"],
                "Price": row.get("Price"),
            }

            for column, _, ts_column in available:
                entry[column] = row[column]
                entry[ts_column] = row.get(ts_column, "—")

            rows.append(entry)

    return pd.DataFrame(rows)


def _command_center_timeframe(base_timeframe, why_text):
    """
    The engine that FIRED the signal runs on `base_timeframe`, but its
    description text can also carry a higher-timeframe confluence note
    (the 1H Reversal engine appends "Daily confluence"/"Daily Path C";
    the Daily+Weekly engine appends "Weekly confluence"/"Weekly Path
    C") - surfaced here so it's not lost in a plain "Hourly"/"Daily"
    label.
    """

    text = (why_text or "").lower()

    if "daily confluence" in text or "daily path c" in text:
        return f"{base_timeframe} + Daily"

    if "weekly confluence" in text or "weekly path c" in text:
        return f"{base_timeframe} + Weekly"

    return base_timeframe


@st.fragment(run_every=UNIVERSE_POLL_SECONDS)
def render_command_center_tab():
    """
    Pulls together every currently-actionable row (a fresh RSI Wave
    entry, or a BUY/SELL Reversal Playbook signal - 1H or Daily+Weekly)
    across all four scanned tabs into one table, so you don't have to
    click through each tab to see what's live right now.

    Reads only what's already cached in each tab's session state - no
    extra fetches - so a tab you haven't opened yet this session simply
    can't be included (flagged explicitly rather than silently omitted).

    Wrapped as its own auto-refreshing fragment (same poll cadence as
    the universe tabs) so it picks up newly-completed background scans
    on its own - all tab bodies actually run in the same script pass
    regardless of which one is visually selected, so the underlying
    scans for US/India/Crypto already start the moment the app loads,
    with no click needed; without this, Command Center itself (which
    renders first, before those scans finish) would just sit showing
    stale data until something forced a full rerun.
    """

    st.subheader("🎯 Command Center — Best Found, All Tabs")
    st.caption(
        "Aggregates every actionable signal already sitting in Global Indices, US Stocks, Indian Stocks, and "
        "Crypto - reads each tab's cached scan, doesn't trigger any new fetches. Updates itself automatically "
        "as each tab's background scan completes - no need to visit them first."
    )

    rows = []
    not_scanned = []

    for label, session_key in COMMAND_CENTER_SOURCES:

        market = st.session_state.get(session_key)

        if market is None:
            not_scanned.append(label)
            continue

        df = market["df"]

        if df.empty:
            continue

        for column, full_col, base_timeframe, keywords in COMMAND_CENTER_COLUMNS:

            if column not in df.columns:
                continue

            # US/India don't get an Hourly view anywhere else in the
            # app (not traded intraday) - skip their Hourly-sourced
            # rows here too, even though the underlying columns still
            # exist in their scanned dataframe.
            if base_timeframe == "Hourly" and session_key in ("us_market", "india_market"):
                continue

            mask = df[column].astype(str).str.lower().str.contains("|".join(keywords))

            for _, row in df[mask].iterrows():

                why = row.get(full_col, "")

                rows.append(
                    {
                        "Source": label,
                        "Ticker": row["Ticker"],
                        "Name": row["Name"],
                        "Price": row.get("Price"),
                        "Timeframe": _command_center_timeframe(base_timeframe, why),
                        "Signal Type": column,
                        "Signal": row[column],
                        "Why": why,
                    }
                )

    if not_scanned:
        st.info("Not yet scanned this session: " + ", ".join(not_scanned) + " — visit those tabs at least once to include them here.")

    if not rows:
        st.success("Nothing actionable right now across the tabs scanned so far.")
        return

    combined = pd.DataFrame(rows)

    st.dataframe(
        combined,
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config={"Why": st.column_config.TextColumn("Why", width=520)},
    )

    st.divider()
    st.subheader("📋 Everything Found — by Timeframe, All Tabs")
    st.caption(
        "Every non-Watching read (forming/alert/confirmed/breakout - not just the narrower "
        "\"act now\" set above) across Global Indices, US Stocks, Indian Stocks, and Crypto, "
        "grouped by timeframe so it's all in one place instead of tab by tab."
    )

    for title, key_prefix, signal_columns, sources in COMMAND_CENTER_TIMEFRAME_TABLES:

        table_df = _build_command_center_timeframe_df(signal_columns, sources)

        if table_df.empty:
            st.caption(f"{title}: nothing beyond Watching right now.")
            continue

        columns = ["Source", "Status", "Ticker", "Name", "Price"]

        for column, _, ts_column in signal_columns:
            columns.append(column)
            columns.append(ts_column)

        Scanner.render(
            table_df, default_sort=signal_columns[0][0], key_prefix=key_prefix, compact=False,
            columns=columns, title=title, height=300,
        )


def render_fundamentals_tab():
    """
    On-demand only, by design (see fundamental_scan.py's docstring) -
    fundamentals don't change hourly, so this has no auto-refresh
    fragment, no background checks, nothing running until you click
    Run.
    """

    st.subheader("💰 Fundamental Improvement Scanner")

    st.caption(
        "Finds stocks in india_master/us_master with a genuinely IMPROVING fundamental trend "
        "(revenue, earnings, margin, ROE, analyst sentiment - not just a high snapshot score), "
        "then checks whether the DAILY technical trend agrees. On-demand only - run it whenever "
        "you want (e.g. weekly, or after earnings season)."
    )

    col1, col2, col3 = st.columns([2, 2, 1])

    with col1:
        country_choice = st.selectbox("Country", ["All", "India", "USA"], key="fund_country")

    with col2:
        min_score = st.slider("Minimum Improving Score", -5, 5, 1, key="fund_min_score")

    with col3:
        limit = st.number_input("Limit (0 = all)", min_value=0, value=0, step=10, key="fund_limit")

    run = st.button("🔍 Run Fundamental Scan", key="fund_run_btn", use_container_width=True)

    if run:

        assets = [a for a in AssetLoader().all_assets() if a.asset_class == "Equity"]

        if country_choice != "All":
            assets = [a for a in assets if a.country.lower() == country_choice.lower()]

        if limit:
            assets = assets[: int(limit)]

        if not assets:
            st.warning("No assets matched that filter.")
        else:

            progress_bar = st.progress(0, text="Starting scan...")

            def _progress(index, total, symbol):
                progress_bar.progress(index / total, text=f"[{index}/{total}] {symbol}...")

            df = FundamentalScanService.scan(assets, min_score=min_score, progress_callback=_progress)

            progress_bar.empty()

            st.session_state.fundamental_scan_result = df

    result = st.session_state.fundamental_scan_result

    if result is None:
        st.info("No scan run yet this session - pick your filters above and click Run.")
        return

    if result.empty:
        st.warning("No stocks matched the improving-fundamentals criteria.")
        return

    st.success(f"{len(result)} stock(s) matched.")

    event = st.dataframe(
        result,
        use_container_width=True,
        hide_index=True,
        height=600,
        on_select="rerun",
        selection_mode="single-row",
        key="fund_results_table",
    )

    out_path = PROJECT_ROOT / "fundamental_scan_results.csv"
    result.to_csv(out_path, index=False)
    st.caption(f"Also saved to {out_path}")

    selected_rows = event.selection.rows if event and event.selection else []

    if selected_rows:
        selected_ticker = result.iloc[selected_rows[0]]["Ticker"]
        selected_name = result.iloc[selected_rows[0]]["Name"]
        render_stock_news(selected_ticker, selected_name)
    else:
        st.caption("Click a row above to see its latest headlines - just the numbers don't tell you *why*.")


def render_stock_news(ticker, name):
    """
    Real headlines, not a summary I write - Yahoo's per-ticker news
    feed mixes genuinely company-specific stories with broader
    sector/market news loosely tagged to the symbol, so read titles
    with that in mind rather than assuming every item is about this
    company specifically.
    """

    st.subheader(f"📰 {name} ({ticker}) — Latest Headlines")

    with st.spinner(f"Fetching news for {ticker}..."):
        articles = StockNewsService.latest(ticker)

    if not articles:
        st.caption("No news found for this ticker right now.")
        return

    for article in articles:

        title = article["title"] or "(untitled)"
        publisher = article["publisher"] or "Unknown source"
        pub_date = article["pub_date"] or ""

        if article["url"]:
            st.markdown(f"**[{title}]({article['url']})**")
        else:
            st.markdown(f"**{title}**")

        st.caption(f"{publisher} · {pub_date}")

        if article["summary"]:
            st.write(article["summary"])

        st.divider()


def main():

    init_state()

    meta = DashboardLoader.metadata()

    Header.render()
    MarketStatus.render()

    refresh_col, _ = st.columns([1, 5])

    with refresh_col:
        # A deliberate full-page rerun (not fragment-scoped, unlike the
        # per-tab Scan Now buttons) - wiping the cache only helps if
        # every tab's own refresh check actually runs and notices it's
        # gone, and that needs this script pass to reach every tab's
        # body, not just this button's own corner of the page. Same
        # end result as rebooting the whole app (every tab starts
        # pulling fresh data), without needing an actual restart.
        if st.button("🔄 Refresh Everything", use_container_width=True, key="refresh_everything_btn"):
            universe_cache.force_clear_all()
            st.toast("Cleared every cached scan - all tabs are starting fresh pulls now.", icon="🔄")

    # Command Center first - a cross-tab summary of what's already been
    # scanned. Global Indices second so it's still the first *live*
    # tab a fresh session lands on. The old sidebar-driven "Scanner"
    # tab was removed - its Setup/Reversal/Daily Reversal columns were
    # never actually scanned (stale/fake), duplicating Command Center
    # without the fix; its AI Score/chart/stock-details features had
    # no unique value the four specialized tabs don't already cover.
    tab_command, tab_notifications, tab_global, tab_us, tab_india, tab_crypto, tab_fundamentals = st.tabs(
        ["🎯 Command Center", "🔔 Notifications", "🌍 Global Indices", "🇺🇸 US Stocks", "🇮🇳 Indian Stocks", "🪙 Crypto", "💰 Fundamentals"]
    )

    with tab_command:
        render_command_center_tab()

    with tab_notifications:
        render_notifications_tab()

    with tab_global:
        render_global_indices_tab(meta)

    with tab_us:
        render_universe_tab("us", "USA", "🇺🇸 US Stocks")

    with tab_india:
        render_universe_tab("india", "India", "🇮🇳 Indian Stocks")

    with tab_crypto:
        render_universe_tab("crypto", "Crypto", "🪙 Crypto")

    with tab_fundamentals:
        render_fundamentals_tab()


if __name__ == "__main__":
    main()
