"""
MarketPulse v2 dashboard shell.

The app coordinates services and widgets. Analysis and business rules live in
the engines and services, while widgets only render already-prepared data.
"""

import io
import json
import os
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
from analysis.backtester import backtest_daily, backtest_reversal_playbook, backtest_rsi_divergence, backtest_rsi_wave, backtest_weekly, summarize_trades
from analysis.market_regime import MarketRegimeEngine
from analysis.reversal_playbook import ReversalPlaybook
from analysis.reversal_playbook_daily import DailyWeeklyReversalPlaybook
from analysis.rsi_divergence_strategy import RSIDivergenceStrategy
from analysis.rsi_wave_strategy import RSIWaveStrategy
from core.loader import AssetLoader
from dashboard.services.alert_log import AlertLog
from dashboard.services import conviction_ranking
from dashboard.services.dashboard_loader import DashboardLoader
from dashboard.services import economic_calendar
from dashboard.services.fundamental_scan_service import FundamentalScanService
from dashboard.services import fundamental_insights
from dashboard.services import macro_news
from dashboard.services.ticker_aliases import resolve_ticker
from dashboard.services import trusted_ips
from dashboard.services.reversal_status import ReversalStatusService
from dashboard.services.reversal_status_daily import DailyReversalStatusService
from analysis.ema_proximity import PROXIMITY_TOLERANCE_PCT
from dashboard.services.ema_proximity_status import EMAProximityStatusService
from dashboard.services.pattern_status import ChartPatternStatusService, PATTERN_STATE_LABELS
from dashboard.services.performance_ranking_status import PerformanceRankingStatusService
from dashboard.services.rsi_divergence_status import RSIDivergenceStatusService
from dashboard.services.rsi_wave_status import RSIWaveStatusService
from dashboard.services.stock_news_service import StockNewsService
from dashboard.services.telegram_notifier import TelegramNotifier
from dashboard.services.tradingview_links import tradingview_url
from dashboard.services.trade_journal import TradeJournal
from dashboard.services import time_utils
from dashboard.services import universe_cache
from dashboard.services import notify_baseline
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

EMA_PROXIMITY_REFRESH_SECONDS = 86400   # once a day, explicitly requested - Weekly/Monthly bars don't change intra-day anyway

MACRO_NEWS_REFRESH_SECONDS = 1800   # news doesn't need to be second-fresh, and this is 6 extra yfinance calls every refresh


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


# Shared with scripts/telegram_scan.py, which needs the exact same
# "Jul 16, 7 PM CET" formatting so a Telegram alert's timestamp always
# matches what Command Center's "When" column would show for the same
# event - kept in time_utils.py (no Streamlit dependency) rather than
# here so that standalone script can import it too.
_format_event_time = time_utils.format_event_time


NOTIFY_BASELINE_KEYS = ["wave_states", "wave_states_seeded", "reversal_states", "reversal_states_seeded", "divergence_states", "divergence_states_seeded", "pattern_states", "pattern_states_seeded"]

for _prefix, _country, _title in [("us", None, None), ("india", None, None), ("crypto", None, None)]:
    NOTIFY_BASELINE_KEYS += [
        f"{_prefix}_wave_states", f"{_prefix}_wave_states_seeded",
        f"{_prefix}_reversal_states", f"{_prefix}_reversal_states_seeded",
        f"{_prefix}_daily_reversal_states", f"{_prefix}_daily_reversal_states_seeded",
        f"{_prefix}_divergence_states", f"{_prefix}_divergence_states_seeded",
    ]


def _persist_notify_baseline():
    """
    Snapshots every notification baseline key into one file after each
    update - see dashboard/services/notify_baseline.py for why this
    exists (restarting the server used to always wipe these, silently
    swallowing any signal that transitioned around the same time).
    """

    notify_baseline.save({key: st.session_state.get(key) for key in NOTIFY_BASELINE_KEYS})


def init_state():

    persisted = notify_baseline.load()

    defaults = {
        "global_market": None,
        "global_selected_ticker": None,
        "global_sector": "All",
        "wave_states": persisted.get("wave_states", {}),
        "wave_states_seeded": persisted.get("wave_states_seeded", False),
        "reversal_states": persisted.get("reversal_states", {}),
        "reversal_states_seeded": persisted.get("reversal_states_seeded", False),
        "divergence_states": persisted.get("divergence_states", {}),
        "divergence_states_seeded": persisted.get("divergence_states_seeded", False),
        "pattern_states": persisted.get("pattern_states", {}),
        "pattern_states_seeded": persisted.get("pattern_states_seeded", False),
        "fundamental_scan_result": None,
    }

    for prefix, _country, _title in UNIVERSE_TABS:
        defaults[f"{prefix}_market"] = None
        defaults[f"{prefix}_selected_ticker"] = None
        defaults[f"{prefix}_wave_states"] = persisted.get(f"{prefix}_wave_states", {})
        defaults[f"{prefix}_wave_states_seeded"] = persisted.get(f"{prefix}_wave_states_seeded", False)
        defaults[f"{prefix}_reversal_states"] = persisted.get(f"{prefix}_reversal_states", {})
        defaults[f"{prefix}_reversal_states_seeded"] = persisted.get(f"{prefix}_reversal_states_seeded", False)
        defaults[f"{prefix}_daily_reversal_states"] = persisted.get(f"{prefix}_daily_reversal_states", {})
        defaults[f"{prefix}_daily_reversal_states_seeded"] = persisted.get(f"{prefix}_daily_reversal_states_seeded", False)
        defaults[f"{prefix}_divergence_states"] = persisted.get(f"{prefix}_divergence_states", {})
        defaults[f"{prefix}_divergence_states_seeded"] = persisted.get(f"{prefix}_divergence_states_seeded", False)
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
    _persist_notify_baseline()

    for entry in new_entries:

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

        # Atomic check-and-log - see AlertLog.claim_if_new(). Catches
        # the same real signal getting independently re-detected by
        # another open tab/session (shared CSV, not st.session_state),
        # closing the race a plain check-then-log had.
        if not AlertLog.claim_if_new(
            entry["ticker"], entry["direction"], entry["name"], entry["price"], entry["rsi"], stop_target,
            source="Global Indices", signal_type="RSI Wave",
        ):
            continue

        levels = (
            f"\nStop {stop_target['stop']} · Target {stop_target['target1']} · R:R 1:{stop_target['risk_reward']}"
            if stop_target
            else ""
        )

        # Name first (what an end user actually recognizes), ticker in
        # parentheses for cross-referencing elsewhere - no repeated
        # "MarketPulse" branding, no raw internal state code.
        description = full_status["description"] if full_status else ""
        message = f"{icon} {entry['name']} ({entry['ticker']}) — {entry['direction']} entry\n{event_time}\nPrice {price} · RSI {rsi}{levels}\n{description}"

        st.toast(f"{entry['direction']} entry: {entry['name']}", icon=icon)

        if TelegramNotifier.is_configured():
            TelegramNotifier.send(message)

    render_notification_trigger(new_entries)


REVERSAL_SIGNAL_DIRECTIONS = {
    "BUY_SIGNAL": "LONG",
    "BUY_SIGNAL_PATH_C": "LONG",
    "SELL_SIGNAL": "SHORT",
    "SELL_SIGNAL_CONTINUATION": "SHORT",
}

REVERSAL_SIGNAL_LABELS = {
    "BUY_SIGNAL": "BUY",
    "BUY_SIGNAL_PATH_C": "BUY (2nd touch — higher confidence)",
    "SELL_SIGNAL": "SELL",
    "SELL_SIGNAL_CONTINUATION": "SELL (continuation)",
}

# RSI Divergence (analysis/rsi_divergence_strategy.py) - Global Indices
# + US Stocks only (explicit scope request), not Indian Stocks/Crypto.
# "Div1"/"Div2" rather than "Path D" - Reversal Playbook already has an
# unrelated Path D.
DIVERGENCE_SIGNAL_DIRECTIONS = {
    "ENTRY_LONG_DIVERGENCE": "LONG",
    "ENTRY_SHORT_DIVERGENCE": "SHORT",
}

DIVERGENCE_SIGNAL_LABELS = {
    "ENTRY_LONG_DIVERGENCE": "Div1 (bullish divergence)",
    "ENTRY_SHORT_DIVERGENCE": "Div2 (bearish divergence)",
}

# Weekly confluence (analysis/reversal_playbook_daily.py) is
# bullish-breakout-only - no SELL side - so every actionable state here
# is a LONG. PATH_C_FORMING is deliberately excluded, same as the Daily/
# 1H engines only notifying once a setup is forming->confirmed, not
# while forming.
WEEKLY_SIGNAL_LABELS = {
    "MULTI_TRY_BREAKOUT": "Multi-try breakout",
    "PATH_C_CONFIRMED": "Path C confirmed",
}

PREFIX_SOURCE_LABELS = {"us": "US Stocks", "india": "Indian Stocks", "crypto": "Crypto"}

# Crypto notifications are noisy across ~250 symbols of wildly varying
# quality/liquidity - only Bitcoin is worth an alert.
CRYPTO_ALERT_TICKER = "BTC-USD"


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
    _persist_notify_baseline()

    browser_entries = []

    for signal in new_signals:

        full_status = ReversalStatusService.analyse(signal["ticker"])
        stop_target = full_status["stop_target"] if full_status else None

        # Atomic check-and-log - see AlertLog.claim_if_new() /
        # check_for_new_entries().
        if not AlertLog.claim_if_new(
            signal["ticker"], signal["direction"], signal["name"], signal["price"], signal["rsi"], stop_target,
            source="Global Indices", signal_type="Reversal 1H",
        ):
            continue

        icon = "🟢" if signal["direction"] == "LONG" else "🔴"
        price = round(signal["price"], 2) if signal["price"] is not None else "?"
        signal_label = REVERSAL_SIGNAL_LABELS.get(signal["state"], signal["state"])
        event_time = time_utils.now_cet().strftime("%Y-%m-%d %H:%M:%S CET")

        levels = (
            f"\nStop {stop_target['stop']} · Target {stop_target['target1']} · R:R 1:{stop_target['risk_reward']}"
            if stop_target and stop_target.get("stop") is not None
            else ""
        )

        description = full_status["description"] if full_status else ""
        message = (
            f"{icon} {signal['name']} ({signal['ticker']}) — {signal_label} (Reversal Playbook)\n"
            f"{event_time}\nPrice {price} · RSI {signal['rsi']}{levels}\n{description}"
        )

        st.toast(f"{signal_label}: {signal['name']}", icon=icon)

        if TelegramNotifier.is_configured():
            TelegramNotifier.send(message)

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


@st.fragment(run_every=300)
def check_for_new_divergence_signals():
    """
    Same pattern as check_for_new_reversal_signals(), for RSI
    Divergence instead - Global Indices only here (US Stocks' own
    divergence notify lives inside _notify_universe_changes since that
    tab isn't on this same auto-refreshing fragment cadence).
    """

    market = st.session_state.global_market

    if market is None:
        return

    tickers = market["df"]["Ticker"].tolist()
    name_map = dict(zip(market["df"]["Ticker"], market["df"]["Name"]))

    current_states = RSIDivergenceStatusService.screen_states(tickers)
    previous_states = st.session_state.divergence_states
    is_first_check = not st.session_state.divergence_states_seeded

    new_signals = (
        []
        if is_first_check
        else [
            {
                "ticker": ticker,
                "name": name_map.get(ticker, ticker),
                "direction": DIVERGENCE_SIGNAL_DIRECTIONS[info["state"]],
                "state": info["state"],
                "price": info["price"],
                "rsi": info["rsi"],
            }
            for ticker, info in current_states.items()
            if info["state"] in DIVERGENCE_SIGNAL_DIRECTIONS
            and (previous_states.get(ticker) or {}).get("state") != info["state"]
        ]
    )

    st.session_state.divergence_states = current_states
    st.session_state.divergence_states_seeded = True
    _persist_notify_baseline()

    browser_entries = []

    for signal in new_signals:

        full_status = RSIDivergenceStatusService.analyse(signal["ticker"])
        stop_target = full_status["stop_target"] if full_status else None

        # Atomic check-and-log - see AlertLog.claim_if_new() /
        # check_for_new_entries().
        if not AlertLog.claim_if_new(
            signal["ticker"], signal["direction"], signal["name"], signal["price"], signal["rsi"], stop_target,
            source="Global Indices", signal_type="RSI Divergence",
        ):
            continue

        icon = "🟢" if signal["direction"] == "LONG" else "🔴"
        price = round(signal["price"], 2) if signal["price"] is not None else "?"
        signal_label = DIVERGENCE_SIGNAL_LABELS.get(signal["state"], signal["state"])
        event_time = time_utils.now_cet().strftime("%Y-%m-%d %H:%M:%S CET")

        levels = (
            f"\nStop {stop_target['stop']} · Target {stop_target['target1']} · R:R 1:{stop_target['risk_reward']}"
            if stop_target and stop_target.get("stop") is not None
            else ""
        )

        description = full_status["description"] if full_status else ""
        message = (
            f"{icon} {signal['name']} ({signal['ticker']}) — {signal_label} (RSI Divergence)\n"
            f"{event_time}\nPrice {price} · RSI {signal['rsi']}{levels}\n{description}"
        )

        st.toast(f"{signal_label}: {signal['name']}", icon=icon)

        if TelegramNotifier.is_configured():
            TelegramNotifier.send(message)

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


@st.fragment(run_every=300)
def check_for_new_pattern_signals():
    """
    Same pattern as check_for_new_divergence_signals(), for chart
    patterns instead - Global Indices only, Piercing Pattern + Double
    Bottom only (see analysis/candlestick_patterns.py for why the
    other four didn't make the cut). Both are LONG-only - nothing here
    fires short.
    """

    market = st.session_state.global_market

    if market is None:
        return

    tickers = market["df"]["Ticker"].tolist()
    name_map = dict(zip(market["df"]["Ticker"], market["df"]["Name"]))

    current_states = ChartPatternStatusService.screen_states(tickers)
    previous_states = st.session_state.pattern_states
    is_first_check = not st.session_state.pattern_states_seeded

    new_signals = (
        []
        if is_first_check
        else [
            {
                "ticker": ticker,
                "name": name_map.get(ticker, ticker),
                "state": info["state"],
                "price": info["price"],
            }
            for ticker, info in current_states.items()
            if info["state"] in PATTERN_STATE_LABELS and info["state"] != "WATCHING"
            and (previous_states.get(ticker) or {}).get("state") != info["state"]
        ]
    )

    st.session_state.pattern_states = current_states
    st.session_state.pattern_states_seeded = True
    _persist_notify_baseline()

    browser_entries = []

    for signal in new_signals:

        signal_label = PATTERN_STATE_LABELS.get(signal["state"], signal["state"])

        full_status = ChartPatternStatusService.analyse(signal["ticker"])
        stop_target = full_status["stop_target"] if full_status else None

        # Atomic check-and-log - see AlertLog.claim_if_new() /
        # check_for_new_entries(). This is the exact path that produced
        # duplicate Telegram alerts in production (same signal, two
        # near-simultaneous sends) before the race was closed here.
        if not AlertLog.claim_if_new(
            signal["ticker"], "LONG", signal["name"], signal["price"], None, stop_target,
            source="Global Indices", signal_type=signal_label.split(" — ")[0].replace("🟢 ", ""),
        ):
            continue

        price = round(signal["price"], 4) if signal["price"] is not None else "?"
        event_time = time_utils.now_cet().strftime("%Y-%m-%d %H:%M:%S CET")

        levels = (
            f"\nStop {stop_target['stop']} · Target {stop_target['target1']} · R:R 1:{stop_target['risk_reward']}"
            if stop_target and stop_target.get("stop") is not None
            else ""
        )

        description = full_status["description"] if full_status else ""
        message = (
            f"🟢 {signal['name']} ({signal['ticker']}) — {signal_label}\n"
            f"{event_time}\nPrice {price}{levels}\n{description}"
        )

        st.toast(f"{signal_label}: {signal['name']}", icon="🟢")

        if TelegramNotifier.is_configured():
            TelegramNotifier.send(message)

        browser_entries.append(
            {
                "ticker": signal["ticker"],
                "name": signal["name"],
                "direction": "LONG",
                "price": signal["price"],
                "rsi": None,
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

    # See the identical guard in _scan_universe_data - a real region
    # filter should never legitimately come back empty; an empty
    # result means every fetch failed (transient), not a genuinely
    # empty universe. Raising preserves the last good cached result
    # instead of blanking it.
    if df.empty:
        raise RuntimeError(f"Global Indices scan for sector={sector} returned zero rows (failed={failed}) - treating as a transient failure, not a real empty universe.")

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

        # US Rates (^TNX/^FVX/^IRX - added for the Daily Must Open
        # regime read, see analysis/market_regime.py) are yield levels,
        # not tradeable price series - running Setup/Reversal/Chart
        # Patterns/etc. on them produces nonsense ("Piercing Pattern"
        # on a bond yield has no meaning) and, worse, fires real
        # Telegram alerts on it. Screened out of every signal engine
        # below; Price/Change %/Status still populate normally via the
        # unfiltered `df` above, which is all the regime read needs.
        tickers = df[df["Sector"] != "US Rates"]["Ticker"].tolist()

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
            if info["state"] in ("BUY_ALERT_CONFIRM", "BUY_ALERT_CONFIRM_PATH_C_FORMING", "BUY_SIGNAL", "BUY_SIGNAL_PATH_C")
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

        # RSI Divergence (1H) - a separate, deliberately narrower engine
        # from Setup/Reversal above (see analysis/rsi_divergence_strategy.py).
        # Global Indices only by design - explicitly not extended to
        # Indian Stocks/Crypto (see _scan_universe_data for the US-only
        # equivalent on that side).
        divergence_states = RSIDivergenceStatusService.screen_states(tickers)
        divergence_labels = {t: RSIDivergenceStrategy.STATE_LABELS.get(info["state"], "⚪ Watching") for t, info in divergence_states.items()}
        df["RSI Divergence"] = df["Ticker"].map(divergence_labels).fillna("⚪ Watching")
        df["RSI Divergence Full"] = df["Ticker"].map({t: info["description"] for t, info in divergence_states.items()}).fillna("")
        df["RSI Divergence Timestamp"] = df["Ticker"].map({t: _format_event_time(info["event_time"]) for t, info in divergence_states.items()}).fillna("—")

        # Chart Patterns (Daily) - Piercing Pattern + Double Bottom only,
        # the two candlestick/chart patterns that actually backtested
        # positive on real Global Indices data (see
        # analysis/candlestick_patterns.py) - Global Indices only,
        # same explicit scope as RSI Divergence.
        pattern_states = ChartPatternStatusService.screen_states(tickers)
        pattern_labels = {t: PATTERN_STATE_LABELS.get(info["state"], "⚪ Watching") for t, info in pattern_states.items()}
        df["Chart Patterns"] = df["Ticker"].map(pattern_labels).fillna("⚪ Watching")
        df["Chart Patterns Full"] = df["Ticker"].map({t: info["description"] for t, info in pattern_states.items()}).fillna("")
        df["Chart Patterns Timestamp"] = df["Ticker"].map({t: _format_event_time(info["event_time"]) for t, info in pattern_states.items()}).fillna("—")

    else:
        divergence_states = {}
        pattern_states = {}

    return {
        "df": df,
        "success": success,
        "failed": failed,
        "sector": sector,
        "wave_states": wave_states,
        "reversal_states": reversal_states,
        "divergence_states": divergence_states,
        "pattern_states": pattern_states,
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
        previous_market = st.session_state.global_market
        sector_changed = previous_market is not None and previous_market["sector"] != result["sector"]

        st.session_state.global_market = {
            "df": result["df"],
            "success": result["success"],
            "failed": result["failed"],
            "sector": result["sector"],
        }

        if st.session_state.global_selected_ticker not in result["df"]["Ticker"].tolist():
            st.session_state.global_selected_ticker = result["df"].iloc[0]["Ticker"] if not result["df"].empty else None

        # Only reset the notification baseline when the ticker universe
        # actually changed (switched region) - this used to reset
        # unconditionally on every routine rescan of the SAME region
        # (every GLOBAL_INDICES_REFRESH_SECONDS, ~10 min), which
        # silently re-seeded "whatever's currently active" as the new
        # baseline without alerting, roughly every 10 minutes - a real
        # new signal landing right after one of those resets got
        # swallowed instead of notified. check_for_new_entries() /
        # check_for_new_reversal_signals() already track their own
        # state changes independently on their own faster cadence, so
        # this display-data refresh has no reason to touch them at all
        # unless the underlying ticker set is genuinely different now.
        if sector_changed:
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

    st.caption("🔴 Live — refreshes every 45s (scanner: 15m bars · pullback setup: 1H). For the VIX/regime risk read, see 🌅 Daily Must Open.")

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

    df = _load_combined_alert_log()

    if df.empty:
        st.info("No notifications yet - alerts will show up here the moment something fires.")
        return

    df = df.copy()
    df["_ts"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df = df.sort_values("_ts", ascending=False)

    now = time_utils.now_cet().replace(tzinfo=None)

    # Older entries stay in Alert Tracking below (durable history for
    # later analysis) - this feed is just "what's actually new," so a
    # 2-day-old push isn't worth scrolling past anymore.
    df = df[df["_ts"] >= now - pd.Timedelta(days=2)]

    if df.empty:
        st.info("Nothing in the last 2 days - older alerts are still in Alert Tracking below.")
        return

    new_count = int((df["_ts"] >= now - pd.Timedelta(hours=1)).sum())

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
                if pd.notna(row["_ts"]):
                    st.caption(row["_ts"].strftime("%b %d, %H:%M CET"))


GH_ALERT_LOG_PATH = PROJECT_ROOT / "database" / "gh_alert_log.csv"


def _load_combined_alert_log():
    """
    Merges the Streamlit app's own local log with the standalone
    GitHub Actions scanner's separate, git-tracked log (see
    scripts/telegram_scan.py's ALERT_LOG_PATH) - two independent
    processes, two files, combined here so Weekly Report / Alert
    Tracking show a complete picture regardless of which one actually
    fired a given alert.
    """

    local_df = AlertLog.load()

    if not GH_ALERT_LOG_PATH.exists():
        return local_df

    gh_df = AlertLog.load(path=GH_ALERT_LOG_PATH)

    return pd.concat([local_df, gh_df], ignore_index=True)


@st.fragment(run_every=300)
def _auto_evaluate_alerts():
    """
    Automatically re-checks every still-OPEN alert against the latest
    price every 5 minutes, instead of only when someone happens to
    click "Check alert outcomes" - an alert that already hit its
    target/stop was otherwise sitting marked OPEN indefinitely (and
    excluded from the win-rate stats) until a manual click, which could
    be hours or days after the fact. Silent (no toast) - this is
    housekeeping on the log, not a new signal to announce.
    """

    AlertLog.evaluate()

    if GH_ALERT_LOG_PATH.exists():
        AlertLog.evaluate(path=GH_ALERT_LOG_PATH)


def render_notifications_tab():

    _auto_evaluate_alerts()
    render_notifications_feed()
    st.divider()
    render_alert_tracking()


def _render_alert_stats_and_table(df, key_prefix):
    """
    Shared by render_alert_tracking() (all-time) and
    render_weekly_report_tab() (last 7 days) - takes an already-filtered
    log and renders the same metrics + table for it, so both views stay
    visually consistent and a future tweak only has to happen once.
    """

    if df.empty:
        st.caption("No alerts in this range.")
        return

    stats = AlertLog.summary(df)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total alerts", stats["total"])
    c2.metric("Still open", stats["open"])
    c3.metric("Hit target", stats["hit_target"])
    c4.metric("Hit stop", stats["hit_stop"])
    c5.metric("Win rate (closed)", f"{stats['win_rate']}%")

    display_cols = [
        "Timestamp", "Source", "SignalType", "Ticker", "Name", "Direction", "EntryPrice",
        "Stop", "Target1", "Status", "ReturnPct", "ClosedAt",
    ]

    st.dataframe(
        df[display_cols].sort_values("Timestamp", ascending=False),
        use_container_width=True,
        hide_index=True,
        key=f"{key_prefix}_alert_table",
    )


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
            if GH_ALERT_LOG_PATH.exists():
                AlertLog.evaluate(path=GH_ALERT_LOG_PATH)

    df = _load_combined_alert_log()

    _render_alert_stats_and_table(df, key_prefix="alltime")


def _alert_breakdown(df, group_col):
    """
    Per-group (Source or SignalType) alert counts + win rate, so it's
    obvious at a glance which source/signal type actually did the work
    this week, not just the totals.
    """

    rows = []

    for group_value, group_df in df.groupby(group_col):

        stats = AlertLog.summary(group_df)

        rows.append({
            group_col: group_value,
            "Alerts": stats["total"],
            "Hit Target": stats["hit_target"],
            "Hit Stop": stats["hit_stop"],
            "Still Open": stats["open"],
            "Win Rate %": stats["win_rate"],
        })

    return pd.DataFrame(rows).sort_values("Alerts", ascending=False)


def render_weekly_report_tab():
    """
    A week-scoped digest of AlertLog, separate from the all-time
    "Alert Tracking" table in Notifications - answers "what happened
    this week" directly instead of making you scroll/filter the
    all-time log yourself: what fired, grouped by source/signal type,
    what actually worked out vs didn't, the single best/worst mover,
    which day was busiest, and how this week's win rate stacks up
    against the all-time average for context.
    """

    st.subheader("🗓 Weekly Report")

    full_df = _load_combined_alert_log()

    if full_df.empty:
        st.caption("No alerts logged yet - check back once some have fired.")
        return

    full_df["Timestamp"] = pd.to_datetime(full_df["Timestamp"])

    week_start = time_utils.now_cet().replace(tzinfo=None) - timedelta(days=7)
    st.caption(
        f"{week_start.strftime('%b %d')} – {time_utils.now_cet().strftime('%b %d, %Y')} "
        "· every alert this app actually sent in the last 7 days (including the always-on "
        "GitHub Actions scanner), and how it played out."
    )

    if st.button("🔄 Check alert outcomes", key="refresh_alert_log_weekly"):
        with st.spinner("Checking current prices against each alert's stop/target..."):
            AlertLog.evaluate()
            if GH_ALERT_LOG_PATH.exists():
                AlertLog.evaluate(path=GH_ALERT_LOG_PATH)
        full_df = _load_combined_alert_log()
        full_df["Timestamp"] = pd.to_datetime(full_df["Timestamp"])

    week_df = full_df[full_df["Timestamp"] >= week_start]

    if week_df.empty:
        st.info("Nothing fired in the last 7 days.")
        return

    _render_alert_stats_and_table(week_df, key_prefix="weekly")

    # This week's win rate vs all-time - context for whether this was a
    # typical week or an outlier, not just a number in isolation.
    week_stats = AlertLog.summary(week_df)
    all_time_stats = AlertLog.summary(full_df)

    if week_stats["hit_target"] + week_stats["hit_stop"] > 0:
        delta = round(week_stats["win_rate"] - all_time_stats["win_rate"], 1)
        st.caption(
            f"This week's win rate ({week_stats['win_rate']}%) vs all-time ({all_time_stats['win_rate']}%): "
            f"{'+' if delta >= 0 else ''}{delta} points."
        )

    st.divider()

    source_breakdown = _alert_breakdown(week_df, "Source")
    signal_breakdown = _alert_breakdown(week_df, "SignalType")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**By source**")
        st.dataframe(source_breakdown, use_container_width=True, hide_index=True)

    with col2:
        st.markdown("**By signal type**")
        st.dataframe(signal_breakdown, use_container_width=True, hide_index=True)

    st.markdown("**By day**")
    by_day = week_df.copy()
    by_day["Day"] = by_day["Timestamp"].dt.strftime("%a %b %d")
    day_counts = by_day.groupby("Day").size().reindex(
        [d for d in by_day.sort_values("Timestamp")["Day"].unique()]
    )
    st.bar_chart(day_counts)

    closed = week_df[week_df["Status"] != "OPEN"].copy()
    closed["ReturnPct"] = pd.to_numeric(closed["ReturnPct"], errors="coerce")
    closed = closed.dropna(subset=["ReturnPct"])

    if not closed.empty:

        best = closed.loc[closed["ReturnPct"].idxmax()]
        worst = closed.loc[closed["ReturnPct"].idxmin()]

        col1, col2 = st.columns(2)

        with col1:
            st.success(
                f"🏆 Best this week: {best['Name']} ({best['Ticker']}) — {best['Direction']}, "
                f"{best['ReturnPct']:+.2f}% ({best['Status']})"
            )

        with col2:
            st.error(
                f"🔻 Worst this week: {worst['Name']} ({worst['Ticker']}) — {worst['Direction']}, "
                f"{worst['ReturnPct']:+.2f}% ({worst['Status']})"
            )
    else:
        st.caption("Nothing closed yet this week to rank a best/worst mover.")

    _export_weekly_report_excel(week_df, source_breakdown, signal_breakdown)


def _export_weekly_report_excel(week_df, source_breakdown, signal_breakdown):
    """
    Same download-button + local-file pattern as Command Center's own
    export (see _export_command_center_excel) - a multi-sheet workbook
    here since the weekly report has three distinct views (the raw
    alert list, and two breakdowns) worth keeping separate rather than
    flattened into one sheet.
    """

    display_cols = [
        "Timestamp", "Source", "SignalType", "Ticker", "Name", "Direction",
        "EntryPrice", "Stop", "Target1", "Status", "ReturnPct", "ClosedAt",
    ]
    alerts_sheet = week_df[display_cols].sort_values("Timestamp", ascending=False)

    def _write(target):
        with pd.ExcelWriter(target, engine="openpyxl") as writer:
            alerts_sheet.to_excel(writer, index=False, sheet_name="Alerts")
            source_breakdown.to_excel(writer, index=False, sheet_name="By Source")
            signal_breakdown.to_excel(writer, index=False, sheet_name="By Signal Type")

    buffer = io.BytesIO()
    _write(buffer)
    buffer.seek(0)

    st.download_button(
        "⬇️ Export Weekly Report to Excel",
        data=buffer,
        file_name=f"weekly_report_{time_utils.now_cet().strftime('%Y-%m-%d')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="weekly_report_export_btn",
    )

    try:
        _write(PROJECT_ROOT / "database" / "weekly_report_latest.xlsx")
    except Exception:
        pass


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
    ("Reversal", "BUY_SIGNAL"): ("Entry condition met — Path A (EMA20/200 far apart) or Path B (200-EMA reclaim + retest).", "Yes — BUY"),
    ("Reversal", "BUY_SIGNAL_PATH_C"): ("Path C specifically — RSI crossed 65, pulled back to hold 60-65 as support, then crossed 65 a SECOND time with price already reclaiming the EMA20/200. This second touch typically carries more confidence than a first-touch confirm.", "Yes — BUY (higher confidence)"),
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
    check_for_new_divergence_signals()
    check_for_new_pattern_signals()

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
        f"{prefix}_daily_reversal_states": {},
        f"{prefix}_daily_reversal_states_seeded": False,
        f"{prefix}_divergence_states": {},
        f"{prefix}_divergence_states_seeded": False,
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

    # A real universe (country="All" filter) should never legitimately
    # come back empty - if it does, every fetch failed (a transient
    # yfinance rate-limit/network blip, the actual cause seen in
    # production), not a genuinely empty result. Raising here (instead
    # of caching the empty df) lets universe_cache.start_scan's own
    # exception handling keep whatever good result was cached before,
    # rather than blanking "No assets found" over it until the next
    # scan happens to succeed.
    if df.empty:
        raise RuntimeError(f"Universe scan for {country} returned zero rows (failed={failed}) - treating as a transient failure, not a real empty universe.")

    wave_states = {}
    reversal_states = {}
    daily_reversal_states = {}
    divergence_states = {}

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

        # RSI Divergence (1H) - US Stocks only, deliberately not
        # extended to Indian Stocks or Crypto (explicit scope request -
        # see analysis/rsi_divergence_strategy.py). Hourly on a
        # normally-Daily-only universe is a deliberate exception - see
        # the Command Center column filter for the matching carve-out.
        if country == "USA":
            divergence_states = RSIDivergenceStatusService.screen_states(tickers)
            divergence_labels = {t: RSIDivergenceStrategy.STATE_LABELS.get(info["state"], "⚪ Watching") for t, info in divergence_states.items()}
            df["RSI Divergence"] = df["Ticker"].map(divergence_labels).fillna("⚪ Watching")
            df["RSI Divergence Full"] = df["Ticker"].map({t: info["description"] for t, info in divergence_states.items()}).fillna("")
            df["RSI Divergence Timestamp"] = df["Ticker"].map({t: _format_event_time(info["event_time"]) for t, info in divergence_states.items()}).fillna("—")

    return {
        "df": df,
        "success": success,
        "failed": failed,
        "wave_states": wave_states,
        "reversal_states": reversal_states,
        "daily_reversal_states": daily_reversal_states,
        "divergence_states": divergence_states,
    }


def _notify_universe_changes(prefix, name_map, wave_states, reversal_states, daily_reversal_states, divergence_states):
    """
    Same new-entry / new-signal diffing as check_for_new_entries() /
    check_for_new_reversal_signals(), generalized across the three
    stock/crypto universes. On the very first load (nothing seeded
    yet), whatever is already active isn't NEW - just record the
    baseline silently, same reasoning as the Global Indices tab.

    Hourly (RSI Wave entry / 1H Reversal Playbook) alerts only fire for
    Crypto - US/India aren't traded intraday and don't get an Hourly
    view anywhere else in the app (see render_universe_live's
    show_hourly gate), so notifying on hourly noise for those two would
    just be alert fatigue for a timeframe the user doesn't even look
    at. Daily Reversal and Weekly confluence notifications fire for all
    three universes below, since that's the timeframe the US/India tabs
    actually show.
    """

    if prefix == "crypto":

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
                if ticker == CRYPTO_ALERT_TICKER
                and info["state"] in ("ENTRY_LONG", "ENTRY_SHORT")
                and (previous_wave.get(ticker) or {}).get("state") != info["state"]
            ]
        )

        for entry in new_entries:

            full_status = RSIWaveStatusService.analyse(entry["ticker"], period="730d")
            stop_target = full_status["stop_target"] if full_status else None

            # Atomic check-and-log - see AlertLog.claim_if_new() /
            # check_for_new_entries().
            if not AlertLog.claim_if_new(
                entry["ticker"], entry["direction"], entry["name"], entry["price"], entry["rsi"], stop_target,
                source=PREFIX_SOURCE_LABELS[prefix], signal_type="RSI Wave",
            ):
                continue

            icon = "🟢" if entry["direction"] == "LONG" else "🔴"
            price = round(entry["price"], 2) if entry["price"] is not None else "?"
            rsi = entry["rsi"] if entry["rsi"] is not None else "?"
            event_time = time_utils.now_cet().strftime("%Y-%m-%d %H:%M:%S CET")

            levels = (
                f"\nStop {stop_target['stop']} · Target {stop_target['target1']} · R:R 1:{stop_target['risk_reward']}"
                if stop_target
                else ""
            )

            st.toast(f"{entry['direction']} entry: {entry['name']}", icon=icon)

            if TelegramNotifier.is_configured():
                description = full_status["description"] if full_status else ""
                TelegramNotifier.send(
                    f"{icon} {entry['name']} ({entry['ticker']}) — {entry['direction']} entry (RSI Wave)\n"
                    f"{event_time}\nPrice {price} · RSI {rsi}{levels}\n{description}"
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
                if ticker == CRYPTO_ALERT_TICKER
                and info["state"] in REVERSAL_SIGNAL_DIRECTIONS
                and (previous_reversal.get(ticker) or {}).get("state") != info["state"]
            ]
        )

        for signal in new_signals:

            full_status = ReversalStatusService.analyse(signal["ticker"])
            stop_target = full_status["stop_target"] if full_status else None

            # Atomic check-and-log - see AlertLog.claim_if_new() /
            # check_for_new_entries().
            if not AlertLog.claim_if_new(
                signal["ticker"], signal["direction"], signal["name"], signal["price"], signal["rsi"], stop_target,
                source=PREFIX_SOURCE_LABELS[prefix], signal_type="Reversal 1H",
            ):
                continue

            icon = "🟢" if signal["direction"] == "LONG" else "🔴"
            signal_label = REVERSAL_SIGNAL_LABELS.get(signal["state"], signal["state"])
            price = round(signal["price"], 2) if signal["price"] is not None else "?"
            rsi = signal["rsi"] if signal["rsi"] is not None else "?"
            event_time = time_utils.now_cet().strftime("%Y-%m-%d %H:%M:%S CET")

            levels = (
                f"\nStop {stop_target['stop']} · Target {stop_target['target1']} · R:R 1:{stop_target['risk_reward']}"
                if stop_target
                else ""
            )

            st.toast(f"{signal_label}: {signal['name']}", icon=icon)

            if TelegramNotifier.is_configured():
                description = full_status["description"] if full_status else ""
                TelegramNotifier.send(
                    f"{icon} {signal['name']} ({signal['ticker']}) — {signal_label} (Reversal Playbook)\n"
                    f"{event_time}\nPrice {price} · RSI {rsi}{levels}\n{description}"
                )

    previous_daily = st.session_state[f"{prefix}_daily_reversal_states"]
    is_first_daily_check = not st.session_state[f"{prefix}_daily_reversal_states_seeded"]

    new_daily_signals = (
        []
        if is_first_daily_check
        else [
            {
                "ticker": ticker,
                "name": name_map.get(ticker, ticker),
                "direction": REVERSAL_SIGNAL_DIRECTIONS[info["state"]],
                "state": info["state"],
                "price": info["price"],
                "rsi": info["rsi"],
            }
            for ticker, info in daily_reversal_states.items()
            if (prefix != "crypto" or ticker == CRYPTO_ALERT_TICKER)
            and info["state"] in REVERSAL_SIGNAL_DIRECTIONS
            and (previous_daily.get(ticker) or {}).get("state") != info["state"]
        ]
    )

    for signal in new_daily_signals:

        full_status = DailyReversalStatusService.analyse(signal["ticker"])
        stop_target = full_status["stop_target"] if full_status else None

        # Atomic check-and-log - see AlertLog.claim_if_new() /
        # check_for_new_entries().
        if not AlertLog.claim_if_new(
            signal["ticker"], signal["direction"], signal["name"], signal["price"], signal["rsi"], stop_target,
            source=PREFIX_SOURCE_LABELS[prefix], signal_type="Daily Reversal",
        ):
            continue

        icon = "🟢" if signal["direction"] == "LONG" else "🔴"
        signal_label = REVERSAL_SIGNAL_LABELS.get(signal["state"], signal["state"])
        price = round(signal["price"], 2) if signal["price"] is not None else "?"
        rsi = signal["rsi"] if signal["rsi"] is not None else "?"
        event_time = time_utils.now_cet().strftime("%Y-%m-%d %H:%M:%S CET")

        levels = (
            f"\nStop {stop_target['stop']} · Target {stop_target['target1']} · R:R 1:{stop_target['risk_reward']}"
            if stop_target
            else ""
        )

        st.toast(f"{signal_label} (Daily): {signal['name']}", icon=icon)

        if TelegramNotifier.is_configured():
            description = full_status["description"] if full_status else ""
            TelegramNotifier.send(
                f"{icon} {signal['name']} ({signal['ticker']}) — {signal_label} (Daily)\n"
                f"{event_time}\nPrice {price} · RSI {rsi}{levels}\n{description}"
            )

    new_weekly_signals = (
        []
        if is_first_daily_check
        else [
            {
                "ticker": ticker,
                "name": name_map.get(ticker, ticker),
                "state": info["weekly_state"],
                "price": info["price"],
                "rsi": info["rsi"],
                "description": info.get("weekly_description", ""),
            }
            for ticker, info in daily_reversal_states.items()
            if (prefix != "crypto" or ticker == CRYPTO_ALERT_TICKER)
            and info["weekly_state"] in WEEKLY_SIGNAL_LABELS
            and (previous_daily.get(ticker) or {}).get("weekly_state") != info["weekly_state"]
        ]
    )

    for signal in new_weekly_signals:

        # Weekly confluence is LONG-only - see WEEKLY_SIGNAL_LABELS.
        # Atomic check-and-log - see AlertLog.claim_if_new() /
        # check_for_new_entries().
        if not AlertLog.claim_if_new(
            signal["ticker"], "LONG", signal["name"], signal["price"], signal["rsi"], None,
            source=PREFIX_SOURCE_LABELS[prefix], signal_type="Weekly Confluence",
        ):
            continue

        signal_label = WEEKLY_SIGNAL_LABELS[signal["state"]]
        price = round(signal["price"], 2) if signal["price"] is not None else "?"
        rsi = signal["rsi"] if signal["rsi"] is not None else "?"
        event_time = time_utils.now_cet().strftime("%Y-%m-%d %H:%M:%S CET")

        st.toast(f"{signal_label} (Weekly): {signal['name']}", icon="🟢")

        if TelegramNotifier.is_configured():
            TelegramNotifier.send(
                f"🟢 {signal['name']} ({signal['ticker']}) — {signal_label} (Weekly)\n"
                f"{event_time}\nPrice {price} · RSI {rsi}\n{signal['description']}"
            )

    if prefix == "us":

        previous_divergence = st.session_state[f"{prefix}_divergence_states"]
        is_first_divergence_check = not st.session_state[f"{prefix}_divergence_states_seeded"]

        new_divergence_signals = (
            []
            if is_first_divergence_check
            else [
                {
                    "ticker": ticker,
                    "name": name_map.get(ticker, ticker),
                    "direction": DIVERGENCE_SIGNAL_DIRECTIONS[info["state"]],
                    "state": info["state"],
                    "price": info["price"],
                    "rsi": info["rsi"],
                }
                for ticker, info in divergence_states.items()
                if info["state"] in DIVERGENCE_SIGNAL_DIRECTIONS
                and (previous_divergence.get(ticker) or {}).get("state") != info["state"]
            ]
        )

        for signal in new_divergence_signals:

            full_status = RSIDivergenceStatusService.analyse(signal["ticker"])
            stop_target = full_status["stop_target"] if full_status else None

            # Atomic check-and-log - see AlertLog.claim_if_new() /
            # check_for_new_entries().
            if not AlertLog.claim_if_new(
                signal["ticker"], signal["direction"], signal["name"], signal["price"], signal["rsi"], stop_target,
                source="US Stocks", signal_type="RSI Divergence",
            ):
                continue

            icon = "🟢" if signal["direction"] == "LONG" else "🔴"
            signal_label = DIVERGENCE_SIGNAL_LABELS.get(signal["state"], signal["state"])
            price = round(signal["price"], 2) if signal["price"] is not None else "?"
            rsi = signal["rsi"] if signal["rsi"] is not None else "?"
            event_time = time_utils.now_cet().strftime("%Y-%m-%d %H:%M:%S CET")

            levels = (
                f"\nStop {stop_target['stop']} · Target {stop_target['target1']} · R:R 1:{stop_target['risk_reward']}"
                if stop_target and stop_target.get("stop") is not None
                else ""
            )

            st.toast(f"{signal_label}: {signal['name']}", icon=icon)

            if TelegramNotifier.is_configured():
                description = full_status["description"] if full_status else ""
                TelegramNotifier.send(
                    f"{icon} {signal['name']} ({signal['ticker']}) — {signal_label} (RSI Divergence)\n"
                    f"{event_time}\nPrice {price} · RSI {rsi}{levels}\n{description}"
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

        _notify_universe_changes(prefix, name_map, result["wave_states"], result["reversal_states"], result["daily_reversal_states"], result["divergence_states"])

        st.session_state[f"{prefix}_market"] = {"df": result_df, "success": result["success"], "failed": result["failed"]}

        if st.session_state[f"{prefix}_selected_ticker"] not in result_df["Ticker"].tolist():
            st.session_state[f"{prefix}_selected_ticker"] = result_df.iloc[0]["Ticker"] if not result_df.empty else None

        st.session_state[f"{prefix}_wave_states"] = result["wave_states"]
        st.session_state[f"{prefix}_wave_states_seeded"] = True
        st.session_state[f"{prefix}_reversal_states"] = result["reversal_states"]
        st.session_state[f"{prefix}_reversal_states_seeded"] = True
        st.session_state[f"{prefix}_daily_reversal_states"] = result["daily_reversal_states"]
        st.session_state[f"{prefix}_daily_reversal_states_seeded"] = True
        st.session_state[f"{prefix}_divergence_states"] = result["divergence_states"]
        st.session_state[f"{prefix}_divergence_states_seeded"] = True
        st.session_state[f"{prefix}_last_loaded_ts"] = cache_entry["ts"]
        st.session_state[seen_ts_key] = cache_entry["ts"]
        _persist_notify_baseline()

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

    _render_ticker_detail(selected, show_hourly, key_prefix=prefix)


def _render_ticker_detail(selected, show_hourly, key_prefix):
    """
    RSI Wave Setup (1H) + Reversal Playbook (1H+Daily) + Reversal
    Playbook (Daily+Weekly) for one ticker - shared by every universe
    tab and the ad-hoc Algo Test tab below. Each *StatusService.analyse()
    call fetches and computes fresh from the ticker string alone - no
    dependency on a pre-scanned universe dataframe, so this works for
    literally any symbol, not just ones already sitting in session
    state. key_prefix only needs to keep widget keys unique across
    callers (e.g. "us"/"india"/"crypto"/"algotest").
    """

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

            notes = st.text_input("Notes (optional)", key=f"{key_prefix}_park_notes_{selected}")

            if st.button("📌 Park this trade", key=f"{key_prefix}_park_btn_{selected}"):

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

            reversal_notes = st.text_input("Notes (optional)", key=f"{key_prefix}_reversal_notes_{selected}")

            if st.button("📌 Park this trade", key=f"{key_prefix}_reversal_park_btn_{selected}"):

                TradeJournal.park(
                    selected, reversal["direction"], reversal["price"], r_target, reversal["state"], reversal["rsi"], notes=reversal_notes,
                )

                st.success(f"Parked {reversal['direction']} {selected} @ {reversal['price']}")

        st.divider()

    # A separate Daily+Weekly read (analysis/reversal_playbook_daily.py) -
    # for US/India/stocks generally, this is the primary (only)
    # technical read here since the 1H boxes above are for intraday-
    # traded instruments only.
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

        daily_reversal_notes = st.text_input("Notes (optional)", key=f"{key_prefix}_daily_reversal_notes_{selected}")

        if st.button("📌 Park this trade", key=f"{key_prefix}_daily_reversal_park_btn_{selected}"):

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

# Indian Stocks' buy/sell calls are deliberately excluded from Command
# Center's aggregated tables (not from the Indian Stocks tab itself,
# which is untouched) - per explicit instruction, not a call the user
# ever intends to act on from here. 200 EMA proximity is a better fit
# for that tickers-worth-a-look need (already covers India/US/Crypto/
# Global in one place) - see the 📍 200 EMA Watch tab, not duplicated
# here in Command Center.
COMMAND_CENTER_BUYSELL_SOURCES = [(label, key) for label, key in COMMAND_CENTER_SOURCES if key != "india_market"]

COMMAND_CENTER_COLUMNS = [
    # column, full-text column, timestamp column, base timeframe, keywords that identify an "act now" label (vs. watching/alert/forming), style
    ("Setup", "Setup Full", "Setup Timestamp", "Hourly", ("entry",), "📈 Momentum"),
    ("Reversal", "Reversal Full", "Reversal Timestamp", "Hourly", ("signal", "trigger", "continuation"), "🔄 Reversal"),
    ("Daily Reversal", "Daily Reversal Full", "Daily Reversal Timestamp", "Daily", ("signal", "trigger", "continuation"), "🔄 Reversal"),
    ("RSI Divergence", "RSI Divergence Full", "RSI Divergence Timestamp", "Hourly", ("entry",), "🔄 Reversal"),
    ("Chart Patterns", "Chart Patterns Full", "Chart Patterns Timestamp", "Daily", ("entry",), "🔄 Reversal"),
]

# Setup/RSI Wave is the one engine that rides an already-established
# move (trend-following) - every other engine looks for a trend
# stalling/rejecting and bets on it turning (counter-trend). Surfaced
# as its own column in Command Center because two engines can and do
# legitimately disagree on the SAME ticker at the SAME time without
# either being wrong (e.g. BTC's Hourly Wave riding a strong short-term
# up-move while its Daily Reversal engine flags the slower daily trend
# rejecting) - they're answering different questions on different
# clocks, not making one unified "the algorithm's opinion" call. Once
# rows were flattened into a single list, that distinction was
# invisible and just looked like the app contradicting itself.
COMMAND_CENTER_STYLE_NOTES = {
    "📈 Momentum": "Rides an already-established move - expects it to continue.",
    "🔄 Reversal": "Looks for a trend stalling/rejecting - expects it to turn.",
}

# RSI Divergence is Hourly on purpose for US Stocks too (unlike Setup/
# Reversal, which US/India skip entirely, see COMMAND_CENTER_HOURLY_SOURCES
# below) - it's a deliberate scope exception (Global Indices + US Stocks
# only, explicitly not Indian Stocks or Crypto). Indian Stocks/Crypto
# never get the column computed at all (see _scan_universe_data), so
# they're excluded naturally without needing a special case here.
COMMAND_CENTER_HOURLY_EXCEPTIONS = {"RSI Divergence"}

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
    ], COMMAND_CENTER_BUYSELL_SOURCES),
    ("🗓 Weekly", "cc_weekly", [
        ("Weekly", "Weekly Full", "Weekly Timestamp"),
    ], COMMAND_CENTER_BUYSELL_SOURCES),
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

            # Crypto is noisy across ~250 symbols of wildly varying
            # quality/liquidity - same reasoning as the Telegram
            # alerts, which already only fire for BTC.
            if session_key == "crypto_market" and row["Ticker"] != CRYPTO_ALERT_TICKER:
                continue

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


def _export_command_center_excel(df):
    """
    Offers the Command Center table as a downloadable .xlsx - works
    identically on localhost and Streamlit Cloud since a download
    button never touches disk, just streams bytes to the browser.

    Also overwrites a fixed local path on every render when one is
    reachable (a real, persistent file when running locally). No
    explicit "am I running on Streamlit Cloud" check is needed for
    this to behave correctly there too - Cloud's container filesystem
    is writable but ephemeral and never exposed to the user, so the
    write either lands somewhere nobody can ever reach (harmless) or
    fails outright on a read-only mount (caught below) - either way
    it's a no-op from the user's perspective, exactly like being
    skipped.
    """

    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, sheet_name="Command Center")
    buffer.seek(0)

    st.download_button(
        "⬇️ Export to Excel",
        data=buffer,
        file_name=f"command_center_{time_utils.now_cet().strftime('%Y-%m-%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="command_center_export_btn",
    )

    try:
        df.to_excel(PROJECT_ROOT / "database" / "command_center_latest.xlsx", index=False, sheet_name="Command Center")
    except Exception:
        pass


def _parse_command_center_when(when_text):
    """
    Turns the "When" column's display text ("Jul 16, 7 PM CET" or the
    daily/weekly "Jul 16" form) back into a sortable timestamp, so
    Command Center's table can default to latest-first instead of
    whatever order rows happened to be scanned in. No year in the
    original text (see time_utils.format_event_time) - assumes the
    current year, which only misorders rows right at a December/
    January boundary, harmless for a table that only ever shows
    recent signals anyway. "—" (never fired) sorts to the very bottom.
    """

    if not when_text or when_text == "—":
        return pd.Timestamp.min

    year = time_utils.now_cet().year
    cleaned = when_text.replace(" CET", "")

    try:
        return pd.to_datetime(f"{cleaned} {year}", format="%b %d, %I %p %Y")
    except ValueError:
        pass

    try:
        return pd.to_datetime(f"{cleaned} {year}", format="%b %d %Y")
    except ValueError:
        return pd.Timestamp.min


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


def _scan_macro_news_data():
    """
    Pure, background-thread-safe scan (no st.* calls) - see
    dashboard/services/macro_news.py.
    """

    return {"headlines": macro_news.top_headlines(limit=10)}


def _macro_value(df, ticker, column):

    row = df[df["Ticker"] == ticker]

    return row[column].iloc[0] if not row.empty else None


def _render_market_regime(df):

    vix_level = _macro_value(df, "^VIX", "Price")
    vix_change = _macro_value(df, "^VIX", "Change %")
    dxy_change = _macro_value(df, "DX-Y.NYB", "Change %")
    y10_level = _macro_value(df, "^TNX", "Price")
    y10_change = _macro_value(df, "^TNX", "Change %")
    gold_change = _macro_value(df, "GC=F", "Change %")

    index_rows = df[df["Sector"].astype(str).str.contains("Indices", na=False)]
    breadth = (index_rows["Change %"] > 0).mean() * 100 if not index_rows.empty else None

    regime = MarketRegimeEngine.classify(
        vix_level=vix_level, vix_change_pct=vix_change,
        dxy_change_pct=dxy_change,
        yield10_level=y10_level, yield10_change_pct=y10_change,
        gold_change_pct=gold_change,
        breadth_pct=breadth,
    )

    st.markdown(f"### {regime['label']}")
    st.caption("A real-time read of today's cross-asset moves using classic macro relationships - not a prediction, not backtested. See the 🎯 tab below for the actual validated, backtested signals.")

    for factor in regime["factors"]:
        st.write(f"- {factor}")


def _render_key_levels(df):

    st.markdown("**💵 Key Levels**")

    levels = [
        ("VIX", "^VIX", "Price"),
        ("DXY", "DX-Y.NYB", "Price"),
        ("Gold", "GC=F", "Price"),
        ("Oil (WTI)", "CL=F", "Price"),
        ("US 10Y", "^TNX", "Price"),
        ("US 5Y", "^FVX", "Price"),
    ]

    cols = st.columns(len(levels))

    for col, (label, ticker, price_col) in zip(cols, levels):

        price = _macro_value(df, ticker, price_col)
        change = _macro_value(df, ticker, "Change %")

        with col:
            if price is None:
                st.metric(label, "—")
            else:
                unit = "%" if ticker in ("^TNX", "^FVX", "^IRX") else ""
                st.metric(label, f"{price:g}{unit}", delta=f"{change:+.2f}%" if change is not None else None)


def _render_macro_dashboard(df):

    st.markdown("**📊 Macro Dashboard — Global Indices**")

    index_rows = df[df["Sector"].astype(str).str.contains("Indices", na=False)]

    if index_rows.empty:
        st.caption("No index data yet.")
        return

    display = index_rows[["Sector", "Ticker", "Name", "Price", "Change %"]].sort_values(["Sector", "Change %"], ascending=[True, False])

    st.dataframe(
        display.style.format({"Price": "{:g}", "Change %": "{:+.2f}"}).map(Scanner.color_price, subset=["Change %"]),
        use_container_width=True, hide_index=True, key="dmo_macro_dashboard",
    )


TOP_WORST_PERFORMERS_N = 10


def _render_top_worst_performers():

    st.markdown("**🏆 Top / Worst Performers — Week & Month**")
    st.caption("Across all four sources (Global Indices, US Stocks, Indian Stocks, Crypto). Refreshes once a day - week/month returns don't meaningfully change intra-day.")

    cache_entry = universe_cache.get("performance_ranking")

    if cache_entry is None or cache_entry["data"] is None:
        st.info("Scanning for the first time — check back shortly.")
        return

    rows = cache_entry["data"]["rows"]

    if not rows:
        st.caption("No performance data yet.")
        return

    df = pd.DataFrame(rows)

    for label, column in [("Week", "Week %"), ("Month", "Month %")]:

        col_df = df[df[column].notna()]

        if col_df.empty:
            continue

        top = col_df.sort_values(column, ascending=False).head(TOP_WORST_PERFORMERS_N)
        worst = col_df.sort_values(column, ascending=True).head(TOP_WORST_PERFORMERS_N)

        left, right = st.columns(2)

        with left:
            st.markdown(f"**Top {label} Gainers**")
            st.dataframe(
                top[["Source", "Ticker", "Name", column]].style.format({column: "{:+.2f}"}).map(Scanner.color_price, subset=[column]),
                use_container_width=True, hide_index=True, key=f"dmo_top_{label.lower()}",
            )

        with right:
            st.markdown(f"**Worst {label} Losers**")
            st.dataframe(
                worst[["Source", "Ticker", "Name", column]].style.format({column: "{:+.2f}"}).map(Scanner.color_price, subset=[column]),
                use_container_width=True, hide_index=True, key=f"dmo_worst_{label.lower()}",
            )


def _render_top_news():

    st.markdown("**📰 Top News**")

    cache_entry = universe_cache.get("macro_news")

    if cache_entry is None or cache_entry["data"] is None:
        st.info("Fetching headlines for the first time — check back shortly.")
        return

    headlines = cache_entry["data"]["headlines"]

    if not headlines:
        st.caption("No headlines available right now.")
        return

    for item in headlines:
        title = item.get("title") or "(untitled)"
        url = item.get("url")
        publisher = item.get("publisher") or ""
        if url:
            st.markdown(f"- [{title}]({url}) — *{publisher}*")
        else:
            st.markdown(f"- {title} — *{publisher}*")


def _render_economic_calendar():

    st.markdown("**📅 Economic Calendar — Next 21 Days**")
    st.caption("FOMC decisions, US CPI, and US Nonfarm Payrolls - the recurring events that reliably move every asset class here. From published Fed/BLS schedules, not a live feed.")

    events = economic_calendar.upcoming(days=21)

    if events.empty:
        st.caption("Nothing in the next 21 days.")
        return

    st.dataframe(
        events.assign(Date=events["Date"].dt.strftime("%b %d (%a)"))[["Date", "Event", "Importance", "Notes"]],
        use_container_width=True, hide_index=True, key="dmo_econ_calendar",
    )


def _render_highest_conviction():

    st.markdown("**🎯 Highest Conviction Trades**")
    st.caption("Currently-actionable signals ranked by each engine's own backtested avg return per trade (not win rate alone - a high win rate with ~0% avg return isn't conviction). Only engines with a genuinely positive backtested edge are shown.")

    rows, not_scanned = _build_command_center_rows()

    if not_scanned:
        st.info("Not yet scanned this session: " + ", ".join(not_scanned) + " — visit those tabs at least once to include them here.")

    ranked = conviction_ranking.rank(rows)

    if not ranked:
        st.success("Nothing with a positive backtested edge is actionable right now.")
        return

    display = pd.DataFrame(ranked)[["Source", "Ticker", "Name", "Price", "Signal Type", "Signal", "Win Rate %", "Avg Return %", "Backtest N", "When"]]

    st.table(display.style.hide(axis="index").format({"Price": "{:g}"}))


def render_daily_must_open_tab():
    """
    "Why is the market moving today" in under 30 seconds - the one
    tab meant to be opened first, every morning, before anything else.
    Everything here reads from already-cached scans (Global Indices'
    background scan, Command Center's row-building, a background news
    fetch) - no fresh live fetch blocks this tab's own load.
    """

    st.subheader("🌅 Daily Must Open")
    st.caption("Market regime, macro readout, news, key levels, the economic calendar, and the highest-conviction trades - the morning briefing, all in one place.")

    now = time.time()

    news_entry = universe_cache.get("macro_news")
    news_stale = news_entry is None or (not news_entry["loading"] and (now - news_entry["ts"]) >= MACRO_NEWS_REFRESH_SECONDS)
    if news_stale:
        universe_cache.start_scan("macro_news", _scan_macro_news_data, pool="macro_news")

    perf_entry = universe_cache.get("performance_ranking")
    perf_stale = perf_entry is None or (not perf_entry["loading"] and (now - perf_entry["ts"]) >= PERFORMANCE_RANKING_REFRESH_SECONDS)
    if perf_stale:
        universe_cache.start_scan("performance_ranking", _scan_performance_ranking_data, pool="performance_ranking")

    global_market = st.session_state.get("global_market")

    if global_market is None:
        st.info("Global Indices hasn't been scanned yet this session - visit that tab once, then come back here.")
    else:
        df = global_market["df"]
        _render_market_regime(df)
        st.divider()
        _render_key_levels(df)
        st.divider()
        _render_macro_dashboard(df)

    st.divider()
    _render_top_worst_performers()

    st.divider()
    _render_top_news()

    st.divider()
    _render_economic_calendar()

    st.divider()
    _render_highest_conviction()


def _build_command_center_rows():
    """
    Pure data-building step, no st.* calls - shared by Command Center's
    own Best Found table and the Daily Must Open tab's conviction
    ranking (both need "every currently-actionable row across the
    scanned tabs", just presented differently). Returns (rows,
    not_scanned) - rows is a list of dicts, not_scanned is the list of
    source labels that haven't been scanned yet this session.
    """

    rows = []
    not_scanned = []

    for label, session_key in COMMAND_CENTER_BUYSELL_SOURCES:

        market = st.session_state.get(session_key)

        if market is None:
            not_scanned.append(label)
            continue

        df = market["df"]

        if df.empty:
            continue

        for column, full_col, ts_col, base_timeframe, keywords, style in COMMAND_CENTER_COLUMNS:

            if column not in df.columns:
                continue

            # US/India don't get an Hourly view anywhere else in the
            # app (not traded intraday) - skip their Hourly-sourced
            # rows here too, even though the underlying columns still
            # exist in their scanned dataframe. RSI Divergence is a
            # deliberate exception for US Stocks (see
            # COMMAND_CENTER_HOURLY_EXCEPTIONS) - Indian Stocks never
            # has this column computed at all, so it's excluded
            # naturally regardless of this check.
            if base_timeframe == "Hourly" and session_key in ("us_market", "india_market") and column not in COMMAND_CENTER_HOURLY_EXCEPTIONS:
                continue

            mask = df[column].astype(str).str.lower().str.contains("|".join(keywords))

            for _, row in df[mask].iterrows():

                # Crypto is noisy across ~250 symbols of wildly varying
                # quality/liquidity - same reasoning as the Telegram
                # alerts, which already only fire for BTC.
                if session_key == "crypto_market" and row["Ticker"] != CRYPTO_ALERT_TICKER:
                    continue

                why = row.get(full_col, "")

                rows.append(
                    {
                        "Source": label,
                        "Ticker": row["Ticker"],
                        "Name": row["Name"],
                        "Price": row.get("Price"),
                        "Timeframe": _command_center_timeframe(base_timeframe, why),
                        "Signal Type": column,
                        "Style": style,
                        "Signal": row[column],
                        "When": row.get(ts_col, "—"),
                        "Why": why,
                    }
                )

    return rows, not_scanned


def render_command_center_tab():
    """
    Plain (non-fragment) shell that calls two independent, sibling
    top-level fragments below - _render_command_center_signals() (20s)
    and render_global_indices_movers() (60s). NOT nested (a fragment
    called from inside another fragment) - that combination raised a
    FragmentHandledException in production specifically on the inner
    fragment's own scheduled auto-rerun tick (a code path this app's
    test suite never exercised, since AppTest only drives explicit
    .run() calls, not a fragment's real run_every timer). Two sibling
    top-level fragments is the same proven pattern already used
    everywhere else in this app.
    """

    _render_command_center_signals()
    st.divider()
    render_global_indices_movers()


@st.fragment(run_every=UNIVERSE_POLL_SECONDS)
def _render_command_center_signals():
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
        "as each tab's background scan completes - no need to visit them first. For the VIX/regime risk read, "
        "see 🌅 Daily Must Open."
    )

    rows, not_scanned = _build_command_center_rows()

    if not_scanned:
        st.info("Not yet scanned this session: " + ", ".join(not_scanned) + " — visit those tabs at least once to include them here.")

    if rows:

        combined = pd.DataFrame(rows)
        combined["_when_sort"] = combined["When"].apply(_parse_command_center_when)
        combined = combined.sort_values("_when_sort", ascending=False).drop(columns="_when_sort")

        # A ticker showing up with BOTH styles at once (e.g. an Hourly
        # Momentum wave riding an up-move while the Daily Reversal
        # engine flags the slower trend rejecting) isn't the app
        # contradicting itself - they're different engines answering
        # different questions on different clocks. Flagged explicitly
        # here rather than left for whoever's reading the table to
        # notice the "conflict" and wonder which one is wrong.
        for ticker, group in combined.groupby("Ticker"):

            styles = set(group["Style"])

            if len(styles) > 1:
                st.caption(
                    f"ℹ️ {ticker} has signals from more than one style below - "
                    + " vs. ".join(f"**{s}** ({COMMAND_CENTER_STYLE_NOTES.get(s, '')})" for s in sorted(styles))
                )

        # st.dataframe's grid can't wrap cell text - long "Why" text
        # just gets clipped at the column's fixed width. st.table
        # renders a plain HTML table instead, which wraps naturally and
        # grows each row's height to fit - a taller table is an
        # accepted tradeoff for actually being able to read the text.
        #
        # Explicit Price format needed here even though the underlying
        # value is already rounded (see DashboardLoader._price_round) -
        # pandas Styler's own default float precision (6 decimals) pads
        # a clean 58576.0 into "58576.000000" regardless of the real
        # value unless a column format is given.
        st.table(combined.style.hide(axis="index").format({"Price": "{:g}"}))

        _export_command_center_excel(combined)

    else:
        st.success("Nothing actionable right now across the tabs scanned so far.")

    # Deliberately NOT gated behind "rows" above (an early return here
    # used to skip everything below whenever there was nothing in the
    # narrow "act now" set, including the broader Everything-Found
    # section and the Movers section below, even though both read from
    # completely independent data and can easily be non-empty when
    # "rows" isn't).
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


@st.fragment(run_every=60)
def render_global_indices_movers():
    """
    Its own fragment on a 1-minute timer, deliberately separate from
    Command Center's own 20s fragment above - re-rendering just this
    table off already-cached session state (no new fetch either way)
    is cheap, but nesting it means a tick here doesn't force the
    heavier "act now"/"Everything Found" aggregation above to redraw
    too, and vice versa.

    Deliberately NOT using Scanner.render() here - its sort widget
    freezes row order across reruns on purpose (protects click-to-
    select from resolving a click against the wrong ticker after a
    reorder), which is exactly wrong for this table: nothing here is
    click-selected, and the whole point is seeing the CURRENT biggest
    movers reshuffle to the top on every refresh, never a frozen order.
    """

    st.subheader("📈 Global Indices — Movers")
    st.caption(
        "Every Global Indices instrument, not just ones with an active Setup/Reversal signal - so you "
        "can see where the actual move is right now, not just where the algo has already flagged "
        "something. Always sorted by move size, biggest first; closed markets are pushed to the "
        "bottom regardless of their last move, since they're not actually moving right now."
    )

    global_market = st.session_state.get("global_market")

    if global_market is None or global_market["df"].empty:
        st.info("Global Indices hasn't been scanned yet this session - visit that tab to load it.")
        return

    timeframe_choice = st.radio(
        "Sort by", ["15m", "1H"], horizontal=True, key="command_center_movers_timeframe",
    )
    move_col = "15m %" if timeframe_choice == "15m" else "1H %"

    df = global_market["df"][["Status", "Ticker", "Name", "Price", "15m %", "1H %"]].copy()

    # Two-level sort, freshly re-applied on every single render (no
    # frozen order): closed markets ranked last regardless of move
    # size, then the chosen move column, biggest first. "Closed" is
    # matched by substring since the real Status values carry an emoji
    # prefix ("🔴 Closed", vs "🟢 Live"/"🟢 24x7"/"🟡 Pre"/"🟠 After") -
    # Pre-market/After-hours are left in the normal sort since they're
    # still genuinely trading, just thinner.
    df["_closed"] = df["Status"].str.contains("Closed", na=False)
    df = df.sort_values(["_closed", move_col], ascending=[True, False]).drop(columns="_closed")

    st.dataframe(
        df.style
        .map(Scanner.color_price, subset=["15m %", "1H %"])
        .format({"Price": "{:.2f}", "15m %": "{:+.2f}", "1H %": "{:+.2f}"}),
        use_container_width=True,
        hide_index=True,
        height=500,
    )

    st.caption(f"🕐 Last updated: {time_utils.now_cet().strftime('%H:%M:%S CET')} — refreshes every minute")


def _classify_ticker(ticker):
    """
    Best-effort asset-class read from Yahoo's own ticker conventions
    (same ones already documented in tradingview_links.py's docstring:
    ^ prefix = index, =X suffix = forex, =F suffix = commodity/futures,
    -USD-style suffix = crypto). Anything else - a plain symbol,
    optionally with an exchange suffix like .NS/.BO - is a stock.
    """

    t = ticker.strip().upper()

    if t.startswith("^"):
        return "index"

    if t.endswith("=X"):
        return "currency"

    if t.endswith("=F"):
        return "commodity"

    if any(t.endswith(f"-{quote}") for quote in ("USD", "USDT", "EUR", "BTC", "ETH")):
        return "crypto"

    return "stock"


MARKET_360_SOURCES = [
    ("🌍 Global Indices", "global_market"),
    ("🇺🇸 US Stocks", "us_market"),
    ("🇮🇳 Indian Stocks", "india_market"),
    ("🪙 Crypto", "crypto_market"),
]


def _build_market_360_data():
    """
    Pure data-building step, no fresh fetch - combines whatever's
    already cached in session state (same reasoning as Command
    Center's _build_command_center_rows()) into one Source/Sector/
    Ticker/Name/Change % frame for the heatmap below. A tab you haven't
    visited yet this session simply isn't included (flagged, not
    silently missing).
    """

    rows = []
    not_scanned = []

    for label, session_key in MARKET_360_SOURCES:

        market = st.session_state.get(session_key)

        if market is None:
            not_scanned.append(label)
            continue

        df = market["df"]

        if df.empty:
            continue

        for _, row in df.iterrows():

            change = row.get("Change %")

            if change is None or pd.isna(change):
                continue

            rows.append({
                "Source": label,
                "Sector": row.get("Sector") or "Other",
                "Ticker": row["Ticker"],
                "Name": row["Name"],
                "Change %": float(change),
            })

    return pd.DataFrame(rows), not_scanned


def render_market_360_tab():
    """
    Visual companion to 🌅 Daily Must Open - that tab is text/table
    based for a fast scan; this one is chart-only, for a slower "what's
    actually hot or cold across the whole universe" look. Reads the
    same already-cached session state every other tab does - no fetch
    of its own.
    """

    st.subheader("📊 Market 360 — Heatmap")
    st.caption(
        "Every scanned asset, grouped by source and sector, colored by today's % change - the fastest "
        "visual read of what's hot and what's not. Box size is uniform (not weighted by market cap). "
        "For the text/table morning briefing, see 🌅 Daily Must Open."
    )

    df, not_scanned = _build_market_360_data()

    if not_scanned:
        st.info("Not yet scanned this session: " + ", ".join(not_scanned) + " — visit those tabs at least once to include them here.")

    if df.empty:
        st.warning("Nothing scanned yet this session.")
        return

    import plotly.express as px

    fig = px.treemap(
        df,
        path=[px.Constant("All"), "Source", "Sector", "Ticker"],
        values=[1] * len(df),
        color="Change %",
        color_continuous_scale="RdYlGn",
        color_continuous_midpoint=0,
        range_color=[-3, 3],
        hover_data={"Name": True, "Change %": ":+.2f"},
    )
    fig.update_traces(texttemplate="%{label}<br>%{customdata[1]}", textposition="middle center")
    fig.update_layout(margin=dict(t=10, l=10, r=10, b=10), height=700)

    st.plotly_chart(fig, use_container_width=True)


def render_algo_test_tab():

    st.subheader("🧪 Algo Test — check any symbol on the fly")
    st.caption(
        "Type any ticker - stock, index, currency, crypto, whatever - and get the same Setup / "
        "Reversal Playbook / Daily+Weekly readout as the scanner tabs, without it needing to already "
        "be in a pre-scanned universe. Stocks skip Hourly (not traded intraday, same rule as the "
        "US/India tabs) - everything else gets Hourly + Daily + Weekly."
    )

    col1, col2 = st.columns([3, 1])

    with col1:
        ticker_input = st.text_input(
            "Ticker", key="algo_test_ticker_input",
            placeholder="e.g. AAPL, RELIANCE.NS, ^GDAXI, EURUSD=X, BTC-USD, FRA40",
        )

    with col2:
        st.write("")
        run = st.button("🔍 Test", key="algo_test_run_btn", use_container_width=True)

    if run:
        if ticker_input.strip():
            st.session_state["algo_test_ticker"] = resolve_ticker(ticker_input)
        else:
            st.error("Enter a ticker first.")

    ticker = st.session_state.get("algo_test_ticker")

    if not ticker:
        st.info("Enter a ticker above and click Test.")
        return

    asset_class = _classify_ticker(ticker)
    show_hourly = asset_class != "stock"

    st.caption(
        f"Resolved to `{ticker}` — detected as **{asset_class}**, "
        + ("showing Hourly + Daily + Weekly." if show_hourly else "showing Daily + Weekly only (stocks skip Hourly).")
    )

    with st.spinner(f"Analysing {ticker}..."):
        _render_ticker_detail(ticker, show_hourly, key_prefix="algotest")

    st.divider()
    st.subheader("📊 Backtest Report")
    st.caption(
        "Walks the FULL history (not just the current bar) and replays every signal that fired in "
        "the window below, checking whether price actually hit target or stop first. Same stop/"
        "target math each engine already uses live - nothing invented here. Same show_hourly rule "
        "as above: stocks skip the Hourly backtest too."
    )

    if show_hourly:
        with st.spinner(f"Backtesting {ticker} Hourly signals (last 5 days)..."):
            _render_backtest_section(
                "🕐 Hourly — RSI Wave + Reversal Playbook + RSI Divergence (last 5 days)",
                [
                    backtest_rsi_wave(ticker, window_days=5),
                    backtest_reversal_playbook(ticker, window_days=5),
                    backtest_rsi_divergence(ticker, window_days=5),
                ],
            )

    with st.spinner(f"Backtesting {ticker} Daily signals (last 3 months)..."):
        _render_backtest_section(
            "📆 Daily — Reversal Playbook (last 3 months)",
            [backtest_daily(ticker, window_days=90)],
        )

    with st.spinner(f"Backtesting {ticker} Weekly confluence (last 6 months)..."):
        _render_weekly_backtest_section(ticker, window_days=180)


def _render_backtest_section(title, results):
    """
    results: one or more {"trades": [...], "summary": {...}} dicts
    (e.g. the Hourly bucket combines RSI Wave + Reversal Playbook)
    merged into a single report, newest signal first. A None entry
    means that engine didn't have enough history for this ticker.
    """

    st.markdown(f"**{title}**")

    if all(r is None for r in results):
        st.info("Not enough history to evaluate this instrument yet.")
        return

    all_trades = []

    for r in results:
        if r:
            all_trades.extend(r["trades"])

    if not all_trades:
        st.caption("No signals fired in this window.")
        return

    all_trades.sort(key=lambda t: t["time"], reverse=True)

    summary = summarize_trades(all_trades)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Signals", summary["total"])
    c2.metric("Hit target", summary["hit_target"])
    c3.metric("Hit stop", summary["hit_stop"])
    c4.metric("Win rate", f"{summary['win_rate']}%")
    c5.metric("Avg return", f"{summary['avg_return']:+.2f}%")

    st.dataframe(
        pd.DataFrame([
            {
                "Time": t["time"].strftime("%Y-%m-%d %H:%M"),
                "Engine": t["engine"],
                "Type": t["type"],
                "Direction": t["direction"],
                "Entry": round(t["entry"], 4),
                "Stop": t["stop"],
                "Target": t["target"],
                "Outcome": t["outcome"],
                "Exit Time": t["exit_time"].strftime("%Y-%m-%d %H:%M") if t["exit_time"] is not None else "—",
                "Return %": t["return_pct"],
            }
            for t in all_trades
        ]),
        use_container_width=True,
        hide_index=True,
    )


def _render_weekly_backtest_section(ticker, window_days):

    st.markdown("**🗓 Weekly — Confluence signals (last 6 months)**")
    st.caption(
        "Weekly confluence (Multi-try breakout / Path C) has no stop/target anywhere in the app - "
        "it's a confluence note, not an independently tradeable setup - so this shows the actual "
        "price return ~4 weeks after each signal instead."
    )

    result = backtest_weekly(ticker, window_days)

    if result is None:
        st.info("Not enough Daily+Weekly history to evaluate this instrument yet.")
        return

    if not result["signals"]:
        st.caption("No Weekly confluence signals fired in this window.")
        return

    c1, c2 = st.columns(2)
    c1.metric("Signals", result["summary"]["total"])
    c2.metric("Avg return (~4wk later)", f"{result['summary']['avg_return']:+.2f}%")

    st.dataframe(
        pd.DataFrame([
            {
                "Time": s["time"].strftime("%Y-%m-%d"),
                "Type": s["type"],
                "Price at signal": round(s["entry"], 4),
                "As of": s["as_of"].strftime("%Y-%m-%d"),
                "Return %": s["return_pct"],
                "Status": s["status"],
            }
            for s in result["signals"]
        ]),
        use_container_width=True,
        hide_index=True,
    )


FUNDAMENTAL_HIGHLIGHT_SECTIONS = [
    ("loss_to_profit", "📈 Loss → Profit turnarounds"),
    ("eps_up", "💹 Biggest EPS increases"),
    ("pe_compressed", "🟢 Getting cheaper (PE dropping week over week)"),
    ("pe_expanded", "🔴 Getting pricier (PE rising week over week)"),
    ("top_improving", "⭐ Most improving fundamentals"),
    ("top_declining", "⚠️ Most declining fundamentals"),
]


def render_fundamental_insights_tab():
    """
    Reads database/fundamentals_latest.csv - written once a week by
    scripts/fundamental_scan_weekly.py via a GitHub Actions workflow,
    same "pre-loaded regardless of whether the app is open" reasoning
    as the Telegram scanner. This tab never fetches anything live -
    the "where to focus" highlights come first, the full table (every
    scanned company) is below in an expander for reference.
    """

    st.subheader("🧠 Fundamentals Insights — US & India, updated weekly")
    st.caption(
        "Loaded from a snapshot scanned once a week (GitHub Actions, independent of this app "
        "being open) - never fetched live here. \"Where to focus\" highlights first, the full "
        "table is below. This is the passive weekly briefing, no filters - for an on-demand "
        "custom scan (by country, minimum score, etc.), see 💰 Fundamentals instead."
    )

    df, latest_date, previous_date = fundamental_insights.load_snapshot()

    if df.empty:
        st.info(
            "No snapshot yet - the weekly scan hasn't run. If you don't want to wait for the "
            "schedule, trigger \"Weekly fundamentals scan\" manually from the GitHub Actions tab."
        )
        return

    if latest_date:
        compare_note = f" — compared against {previous_date}" if previous_date else " — first snapshot, no week-over-week comparison yet"
        st.caption(f"📅 Data loaded on: **{latest_date}**{compare_note} · {len(df)} companies scanned")

    highlights = fundamental_insights.derive_highlights(df)

    if not highlights:
        st.success("Nothing notable stands out this week.")
    else:
        for key, title in FUNDAMENTAL_HIGHLIGHT_SECTIONS:

            if key not in highlights:
                continue

            st.markdown(f"**{title}**")
            st.dataframe(highlights[key], use_container_width=True, hide_index=True)
            st.divider()

    with st.expander("📋 Full table — every scanned company"):
        st.dataframe(df, use_container_width=True, hide_index=True)


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
        "you want a custom filter (country, minimum score, etc.) right now. For the passive "
        "weekly briefing (loss-to-profit turnarounds, EPS/PE movers) with no setup needed, "
        "see 🧠 Fundamentals Insights instead."
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


def _app_password():
    """
    Reads the gate password from secrets/env, same fallback pattern as
    TelegramNotifier - never hardcoded in source so a fresh password
    isn't sitting in git history. Falls back to the old hardcoded value
    only if no secret/env var is configured yet, so this can't lock
    anyone out before TELEGRAM_BOT_TOKEN-style secrets are actually set
    up for APP_PASSWORD.
    """

    try:
        value = st.secrets.get("APP_PASSWORD")
    except Exception:
        value = None

    return value or os.environ.get("APP_PASSWORD") or "2402"


def _client_ip():
    """
    Best-effort client IP from the proxy headers Streamlit Cloud sets
    (X-Forwarded-For can carry a comma-separated hop chain - the first
    entry is the original client). Returns None if unavailable (e.g.
    running outside a real browser request), so callers can gracefully
    fall back to session-only auth instead of crashing.
    """

    try:
        headers = st.context.headers
    except Exception:
        return None

    if not headers:
        return None

    forwarded = headers.get("X-Forwarded-For")

    if forwarded:
        return forwarded.split(",")[0].strip()

    return headers.get("X-Real-IP")


def _is_local_request():
    """
    True when the request's Host header is localhost/127.0.0.1 - i.e.
    accessed directly via `streamlit run`, with no reverse proxy in
    front (Streamlit Cloud's own proxy always rewrites Host to the
    public app domain, never localhost). The whole point of the
    password gate is stopping a stranger who stumbles on the public
    URL - that threat doesn't exist for someone already sitting at the
    machine running it locally, so there's nothing to gate here.
    """

    try:
        headers = st.context.headers
    except Exception:
        return False

    if not headers:
        return False

    host = (headers.get("Host") or "").split(":")[0].lower()

    return host in ("localhost", "127.0.0.1")


def _require_password():
    """
    A simple lock screen, not real security - just enough to stop
    anyone who stumbles on the public app URL from poking around.
    Skipped entirely for local access (see _is_local_request) - a
    reverse-proxied deployment always still requires it.

    On top of the existing per-session st.session_state check (which
    already skips the prompt for the rest of one browser session),
    a successful login also trusts the client's IP for
    trusted_ips.TRUST_WINDOW_HOURS, so the same network isn't asked
    again more than about once a day even in a brand new session.
    """

    if st.session_state.get("authenticated"):
        return

    if _is_local_request():
        st.session_state.authenticated = True
        return

    client_ip = _client_ip()

    if client_ip and trusted_ips.is_trusted(client_ip):
        st.session_state.authenticated = True
        return

    st.title("🔒 MarketPulse")

    with st.form("password_gate_form"):
        entered = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Unlock")

    if submitted:
        if entered == _app_password():
            st.session_state.authenticated = True
            if client_ip:
                trusted_ips.mark_trusted(client_ip)
            st.rerun()
        else:
            st.error("Wrong password.")

    st.stop()


def _warm_background_scans():
    """
    Kicks off the same background scans _refresh_universe_body() /
    render_global_indices_tab() would start on a fresh session's first
    real render - but called before the password gate, so a scan is
    already running (or already done) by the time someone actually
    types the password in, instead of only starting after they unlock.

    Mirrors those functions' own staleness check (cache missing, or
    older than the refresh cadence and not already loading) rather than
    calling start_scan() unconditionally - this runs on every single
    rerun of the unauthenticated password screen, so without that
    check it would fire a brand new full-universe scan on every
    keystroke/rerun instead of just once an hour, hammering yfinance
    exactly like the rate-limit issue hit before.

    Uses "All" for Global Indices since that's always the selectbox's
    default on a session with no global_sector set yet (see
    Sidebar._sectors - "All" is always prepended first).
    """

    now = time.time()

    for prefix, scan_fn in (
        ("us", lambda: _scan_universe_data("USA")),
        ("india", lambda: _scan_universe_data("India")),
        ("crypto", lambda: _scan_universe_data("Crypto")),
    ):
        entry = universe_cache.get(prefix)
        stale = entry is None or (not entry["loading"] and (now - entry["ts"]) >= UNIVERSE_REFRESH_SECONDS)
        if stale:
            universe_cache.start_scan(prefix, scan_fn, pool=prefix)

    global_entry = universe_cache.get("global_All")
    global_stale = global_entry is None or (not global_entry["loading"] and (now - global_entry["ts"]) >= GLOBAL_INDICES_REFRESH_SECONDS)
    if global_stale:
        universe_cache.start_scan("global_All", lambda: _scan_global_indices_data("All"), pool="global")

    ema_entry = universe_cache.get("ema_proximity")
    ema_stale = ema_entry is None or (not ema_entry["loading"] and (now - ema_entry["ts"]) >= EMA_PROXIMITY_REFRESH_SECONDS)
    if ema_stale:
        universe_cache.start_scan("ema_proximity", _scan_ema_proximity_data, pool="ema_proximity")

    news_entry = universe_cache.get("macro_news")
    news_stale = news_entry is None or (not news_entry["loading"] and (now - news_entry["ts"]) >= MACRO_NEWS_REFRESH_SECONDS)
    if news_stale:
        universe_cache.start_scan("macro_news", _scan_macro_news_data, pool="macro_news")

    perf_entry = universe_cache.get("performance_ranking")
    perf_stale = perf_entry is None or (not perf_entry["loading"] and (now - perf_entry["ts"]) >= PERFORMANCE_RANKING_REFRESH_SECONDS)
    if perf_stale:
        universe_cache.start_scan("performance_ranking", _scan_performance_ranking_data, pool="performance_ranking")


EMA_PROXIMITY_SOURCE_LABELS = {"USA": "US Stocks", "India": "Indian Stocks", "Crypto": "Crypto", "Global": "Global Indices"}


def _scan_ema_proximity_data():
    """
    Pure, background-thread-safe scan (no st.* calls) - screens every
    asset across all four sources for Weekly-200 / Monthly-50 EMA
    proximity (see analysis/ema_proximity.py). Stateless, so unlike the
    other _scan_* functions there's no wave_states/reversal_states to
    return alongside it - just the rows themselves.
    """

    assets = AssetLoader().all_assets()
    name_map = {a.symbol: a.name for a in assets}
    source_map = {a.symbol: a.country for a in assets}
    symbols = list(name_map.keys())

    states = EMAProximityStatusService.screen_states(symbols)

    rows = []

    for symbol, info in states.items():

        weekly = info.get("weekly")
        monthly = info.get("monthly")

        if weekly is None and monthly is None:
            continue

        rows.append({
            "Source": EMA_PROXIMITY_SOURCE_LABELS.get(source_map.get(symbol, ""), source_map.get(symbol, "")),
            "Ticker": symbol,
            "Name": name_map.get(symbol, symbol),
            "Weekly Dist %": weekly["distance_pct"] if weekly else None,
            "Weekly Near": weekly["near"] if weekly else False,
            "Weekly Side": weekly["side"] if weekly else None,
            "Monthly Dist %": monthly["distance_pct"] if monthly else None,
            "Monthly Near": monthly["near"] if monthly else False,
            "Monthly Side": monthly["side"] if monthly else None,
        })

    return {"rows": rows}


PERFORMANCE_RANKING_REFRESH_SECONDS = 86400   # once a day - week/month returns don't meaningfully change intra-day


def _scan_performance_ranking_data():
    """
    Pure, background-thread-safe scan (no st.* calls) - screens every
    asset across all four sources for its week/month % return (see
    analysis/performance_ranking.py). Stateless, same pattern as
    _scan_ema_proximity_data.
    """

    assets = AssetLoader().all_assets()
    name_map = {a.symbol: a.name for a in assets}
    source_map = {a.symbol: a.country for a in assets}
    symbols = list(name_map.keys())

    states = PerformanceRankingStatusService.screen_states(symbols)

    rows = []

    for symbol, info in states.items():

        if info.get("week_pct") is None and info.get("month_pct") is None:
            continue

        rows.append({
            "Source": EMA_PROXIMITY_SOURCE_LABELS.get(source_map.get(symbol, ""), source_map.get(symbol, "")),
            "Ticker": symbol,
            "Name": name_map.get(symbol, symbol),
            "Week %": info.get("week_pct"),
            "Month %": info.get("month_pct"),
        })

    return {"rows": rows}


def render_ema_proximity_tab():

    st.subheader("📍 200 EMA Watch — Weekly & Monthly")
    st.caption(
        "Not a trade signal - just flags instruments currently trading within "
        f"{PROXIMITY_TOLERANCE_PCT}% of a major long-term trend line (Weekly 200 EMA, Monthly 50 EMA - "
        "Monthly deliberately uses a shorter period than Weekly, see analysis/ema_proximity.py), "
        "worth a manual look. Scans all four sources once a day."
    )

    cache_entry = universe_cache.get("ema_proximity")

    if cache_entry is None or cache_entry["data"] is None:
        eta = _scan_eta_text(cache_entry) if cache_entry else "typically a few minutes across ~260 tickers"
        st.info(f"Scanning for the first time — {eta}. Feel free to check other tabs meanwhile.")
        return

    if st.button("🔄 Rescan now", key="ema_proximity_rescan_btn"):
        started = universe_cache.start_scan("ema_proximity", _scan_ema_proximity_data, pool="ema_proximity")
        st.toast("Rescanning in the background..." if started else "Already scanning in the background...", icon="🔄")

    last_ts = cache_entry["ts"]
    age_minutes = round((time.time() - last_ts) / 60)
    refreshed_at = time_utils.unix_to_cet(last_ts).strftime("%b %d, %H:%M CET")

    if cache_entry["loading"]:
        st.caption(f"🕐 Showing data from {refreshed_at} ({age_minutes} min ago) — a fresh scan is running in the background.")
    else:
        st.caption(f"🕐 Last scanned: {refreshed_at} ({age_minutes} min ago) — refreshes automatically once a day.")

    rows = cache_entry["data"]["rows"]
    df = pd.DataFrame(rows)

    if df.empty:
        st.warning("No data yet.")
        return

    near_weekly = df[df["Weekly Near"]].sort_values("Weekly Dist %", key=lambda s: s.abs())
    near_monthly = df[df["Monthly Near"]].sort_values("Monthly Dist %", key=lambda s: s.abs())

    st.markdown(f"**Near Weekly 200 EMA** ({len(near_weekly)})")
    if near_weekly.empty:
        st.caption("Nothing within tolerance right now.")
    else:
        st.dataframe(
            near_weekly[["Source", "Ticker", "Name", "Weekly Dist %", "Weekly Side"]],
            use_container_width=True, hide_index=True,
        )

    st.divider()

    st.markdown(f"**Near Monthly 50 EMA** ({len(near_monthly)})")
    if near_monthly.empty:
        st.caption("Nothing within tolerance right now.")
    else:
        st.dataframe(
            near_monthly[["Source", "Ticker", "Name", "Monthly Dist %", "Monthly Side"]],
            use_container_width=True, hide_index=True,
        )


def main():

    _warm_background_scans()

    _require_password()

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

    # Daily Must Open first - the one tab meant to be opened before
    # anything else each morning (regime, macro readout, news, key
    # levels, calendar, highest-conviction trades in one place).
    # Command Center second - a cross-tab summary of what's already
    # been scanned. Global Indices third so it's still the first
    # *live* tab a fresh session lands on. The old sidebar-driven
    # "Scanner" tab was removed - its Setup/Reversal/Daily Reversal
    # columns were never actually scanned (stale/fake), duplicating
    # Command Center without the fix; its AI Score/chart/stock-details
    # features had no unique value the four specialized tabs don't
    # already cover.
    tab_daily, tab_360, tab_command, tab_weekly, tab_ema_watch, tab_notifications, tab_global, tab_us, tab_india, tab_crypto, tab_fundamentals, tab_fund_insights, tab_algo_test = st.tabs(
        ["🌅 Daily Must Open", "📊 Market 360", "🎯 Command Center", "🗓 Weekly Report", "📍 200 EMA Watch", "🔔 Notifications", "🌍 Global Indices", "🇺🇸 US Stocks", "🇮🇳 Indian Stocks", "🪙 Crypto", "💰 Fundamentals", "🧠 Fundamentals Insights", "🧪 Algo Test"]
    )

    with tab_daily:
        render_daily_must_open_tab()

    with tab_360:
        render_market_360_tab()

    with tab_command:
        render_command_center_tab()

    with tab_weekly:
        render_weekly_report_tab()

    with tab_ema_watch:
        render_ema_proximity_tab()

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

    with tab_fund_insights:
        render_fundamental_insights_tab()

    with tab_algo_test:
        render_algo_test_tab()


if __name__ == "__main__":
    main()
