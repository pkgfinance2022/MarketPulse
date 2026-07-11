"""
MarketPulse v2 dashboard shell.

The app coordinates services and widgets. Analysis and business rules live in
the engines and services, while widgets only render already-prepared data.
"""

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
import streamlit.components.v1 as components

from analysis.reversal_playbook import ReversalPlaybook
from analysis.rsi_wave_strategy import RSIWaveStrategy
from core.loader import AssetLoader
from dashboard.services.alert_log import AlertLog
from dashboard.services.chart_service import ChartService
from dashboard.services.dashboard_loader import DashboardLoader
from dashboard.services.dashboard_stats import DashboardStats
from dashboard.services.fundamental_scan_service import FundamentalScanService
from dashboard.services.reversal_status import ReversalStatusService
from dashboard.services.rsi_wave_status import RSIWaveStatusService
from dashboard.services.stock_news_service import StockNewsService
from dashboard.services.telegram_notifier import TelegramNotifier
from dashboard.services.tradingview_links import tradingview_url
from dashboard.services.trade_journal import TradeJournal
from dashboard.widgets.charts import Charts
from dashboard.widgets.header import Header
from dashboard.widgets.market_status import MarketStatus
from dashboard.widgets.metrics import Metrics
from dashboard.widgets.scanner import Scanner
from dashboard.widgets.sidebar import Sidebar
from dashboard.widgets.stock_details import StockDetails
from dashboard.widgets.top_opportunities import TopOpportunities


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


def init_state():

    defaults = {
        "market": None,
        "selected_ticker": None,
        "chart": None,
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

        icon = "🟢" if entry["direction"] == "LONG" else "🔴"
        price = round(entry["price"], 2) if entry["price"] is not None else "?"
        rsi = entry["rsi"] if entry["rsi"] is not None else "?"

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
        message = f"{icon} {entry['name']} ({entry['ticker']}) — {entry['direction']} entry\nPrice {price} · RSI {rsi}{levels}"

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

        icon = "🟢" if signal["direction"] == "LONG" else "🔴"
        price = round(signal["price"], 2) if signal["price"] is not None else "?"
        signal_label = REVERSAL_SIGNAL_LABELS.get(signal["state"], signal["state"])

        full_status = ReversalStatusService.analyse(signal["ticker"])
        stop_target = full_status["stop_target"] if full_status else None

        levels = (
            f"\nStop {stop_target['stop']} · Target {stop_target['target1']} · R:R 1:{stop_target['risk_reward']}"
            if stop_target and stop_target.get("stop") is not None
            else ""
        )

        message = (
            f"{icon} {signal['name']} ({signal['ticker']}) — {signal_label} (Reversal Playbook)\n"
            f"Price {price} · RSI {signal['rsi']}{levels}"
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


# The main Scanner tab's sidebar defaults to "Global Macro", which is
# the exact same universe as the Global Indices tab's default "All"
# region - without this, both tabs independently re-download the same
# ~25 symbols on first load (visible as "Downloading market data..."
# printing twice in a row). Cache by filter signature for a short
# window so whichever tab asks first serves the other.
MARKET_FETCH_TTL = 60


def _filter_key(filters):

    return (
        filters.get("country"),
        filters.get("sector"),
        filters.get("search", ""),
        filters.get("portfolio_only", False),
        filters.get("watchlist_only", False),
        filters.get("priority", 1),
    )


def _load_market_cached(filters):
    """
    Returns a fresh .copy() every time, never the cached object itself
    - callers (e.g. load_global_indices) mutate their df in place
    (adding the RSI-wave Setup labels), and without a copy that
    mutation would leak into whichever other tab shares the same
    cached object.
    """

    cache = st.session_state.setdefault("_market_fetch_cache", {})
    key = _filter_key(filters)
    cached = cache.get(key)

    if cached and (time.time() - cached["ts"]) < MARKET_FETCH_TTL:
        return cached["df"].copy(), cached["success"], cached["failed"]

    df, success, failed = DashboardLoader.load(filters)

    cache[key] = {"df": df, "success": success, "failed": failed, "ts": time.time()}

    return df.copy(), success, failed


def load_market(filters):

    with st.spinner("Loading market intelligence..."):
        df, success, failed = _load_market_cached(
            {
                "country": filters["country"],
                "sector": filters["sector"],
                "search": filters["search"],
                "portfolio_only": filters["portfolio_only"],
                "watchlist_only": filters["watchlist_only"],
                "priority": filters["priority"],
            }
        )

    st.session_state.market = {
        "df": df,
        "success": success,
        "failed": failed,
    }

    if not df.empty:
        st.session_state.selected_ticker = df.iloc[0]["Ticker"]
        st.session_state.chart = None


def selected_stock(df):

    if df.empty:
        return None

    tickers = df["Ticker"].tolist()
    selected = st.session_state.selected_ticker

    if selected not in tickers:
        selected = tickers[0]

    ticker = st.selectbox(
        "Selected asset",
        tickers,
        index=tickers.index(selected),
    )

    st.session_state.selected_ticker = ticker

    return df[df["Ticker"] == ticker].iloc[0].to_dict()


def load_chart(ticker):

    if not ticker:
        return None

    cached = st.session_state.chart

    if cached and cached["ticker"] == ticker:
        return cached["df"]

    with st.spinner("Loading chart..."):
        chart_df = ChartService.history(ticker)

    st.session_state.chart = {
        "ticker": ticker,
        "df": chart_df,
    }

    return chart_df


def load_global_indices(sector):

    with st.spinner(f"Loading {sector}..."):
        df, success, failed = _load_market_cached(
            {
                "country": "Global",
                "sector": sector,
                "search": "",
                "portfolio_only": False,
                "watchlist_only": False,
                "priority": 1,
            }
        )

    if not df.empty:

        # One extra 730d/1H fetch+walk per symbol to classify where each
        # one sits in the RSI wave state machine right now - this is the
        # actual screener the trend/RSI columns alone can't answer.
        # Deliberately only done here (region load), never on the 45s
        # auto-refresh tick, since it's ~1 yfinance call per symbol.
        # screen_states() (not screen()) so the description text -
        # "why" a row got that label - can also populate the scanner's
        # detail column, not just the short label.
        with st.spinner("Scanning RSI wave setups..."):
            wave_states = RSIWaveStatusService.screen_states(df["Ticker"].tolist())

        wave_labels = {t: RSIWaveStrategy.STATE_LABELS.get(info["state"], "⚪ Watching") for t, info in wave_states.items()}
        df["Setup"] = df["Ticker"].map(wave_labels).fillna(df["Setup"])
        df["Setup Detail"] = df["Ticker"].map({t: info["description"] for t, info in wave_states.items()}).fillna("")

        # Second screener, same pattern - the Reversal Playbook (2
        # fetches/symbol: 1H + Daily). Roughly doubles region load time
        # on top of the RSI wave screener above; worth revisiting if
        # that becomes a real problem.
        with st.spinner("Scanning reversal playbook setups..."):
            reversal_states = ReversalStatusService.screen_states(df["Ticker"].tolist())

        reversal_labels = {t: ReversalPlaybook.STATE_LABELS.get(info["state"], "⚪ Watching") for t, info in reversal_states.items()}
        df["Reversal"] = df["Ticker"].map(reversal_labels).fillna(df["Reversal"])
        df["Reversal Detail"] = df["Ticker"].map({t: info["description"] for t, info in reversal_states.items()}).fillna("")

    st.session_state.global_market = {
        "df": df,
        "success": success,
        "failed": failed,
        "sector": sector,
    }

    st.session_state.global_selected_ticker = (
        df.iloc[0]["Ticker"] if not df.empty else None
    )

    # New region = new ticker set - treat the next entry-check as a
    # fresh baseline instead of comparing against the old region's
    # states (or notifying about everything already true on arrival).
    st.session_state.wave_states = {}
    st.session_state.wave_states_seeded = False
    st.session_state.reversal_states = {}
    st.session_state.reversal_states_seeded = False


@st.fragment(run_every=45)
def render_global_indices_live():
    """
    The only auto-refreshing part of the page. Reruns on its own every
    45s without touching the sidebar, the main Scanner tab, or
    triggering a full DashboardLoader.load() - it only re-fetches 15m
    bars for the tickers already loaded into `global_market`.
    """

    market = st.session_state.global_market

    if market is None:
        st.info("Pick a region and click Load to start the live view.")
        return

    df = DashboardLoader.refresh_intraday_prices(market["df"])
    st.session_state.global_market["df"] = df

    st.caption("🔴 Live — refreshes every 45s (scanner: 15m bars · pullback setup: 1H)")

    ticker = Scanner.render(df, default_sort="Setup", key_prefix="global", compact=True)

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

---

**📅 Daily confluence (independent of the 1H machine above)**

Tracks the **Daily RSI**, separately from all of the above:
- **Multi-try breakout:** if Daily RSI rallies into the 55–65 zone and retreats below 55 **without** breaking 65 — that's a "failed try." Once Daily RSI finally breaks above 65 after **2 or more failed tries**, it's flagged as a stronger, longer-lasting move (per your USD/CHF chart example).
- **Daily Path C:** the same "cross 65 → hold it as support → reclaim the 200 EMA" idea as the 1H Path C above, but on the Daily timeframe — flagged both while forming and once confirmed.

Both are standalone notes, not gates — they appear alongside whatever the 1H engine is showing (WATCHING, an alert, or a BUY/SELL signal) for a few days after firing, then fade.

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
        load = st.button(
            "🚀 Load",
            key="global_load",
            use_container_width=True,
        )

    market = st.session_state.global_market

    if load or market is None or market["sector"] != sector:
        load_global_indices(sector)

    render_global_indices_live()
    check_for_new_entries()
    check_for_new_reversal_signals()

    st.divider()
    render_alert_tracking()

    st.divider()
    render_parked_trades()


def load_universe(prefix, country):
    """
    Generic version of load_global_indices() for a whole stock/crypto
    universe (US/India/Crypto) instead of the Global Macro indices
    list. Uses screen_states() (not screen()) for both engines, so the
    raw state dict (price/RSI included) doubles as both the scanner
    label source AND the notification-diff baseline - one fetch pass
    per symbol per hour, not two, since this only runs once an hour
    anyway (per explicit instruction to keep yfinance usage low across
    much bigger universes than Global Indices' ~25 symbols).
    """

    df, success, failed = _load_market_cached(
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
        name_map = dict(zip(df["Ticker"], df["Name"]))

        wave_states = RSIWaveStatusService.screen_states(tickers)
        wave_labels = {t: RSIWaveStrategy.STATE_LABELS.get(info["state"], "⚪ Watching") for t, info in wave_states.items()}
        df["Setup"] = df["Ticker"].map(wave_labels).fillna(df["Setup"])
        df["Setup Detail"] = df["Ticker"].map({t: info["description"] for t, info in wave_states.items()}).fillna("")

        reversal_states = ReversalStatusService.screen_states(tickers)
        reversal_labels = {t: ReversalPlaybook.STATE_LABELS.get(info["state"], "⚪ Watching") for t, info in reversal_states.items()}
        df["Reversal"] = df["Ticker"].map(reversal_labels).fillna(df["Reversal"])
        df["Reversal Detail"] = df["Ticker"].map({t: info["description"] for t, info in reversal_states.items()}).fillna("")

        _notify_universe_changes(prefix, name_map, wave_states, reversal_states)

    st.session_state[f"{prefix}_market"] = {"df": df, "success": success, "failed": failed}

    if st.session_state[f"{prefix}_selected_ticker"] not in df["Ticker"].tolist():
        st.session_state[f"{prefix}_selected_ticker"] = df.iloc[0]["Ticker"] if not df.empty else None

    st.session_state[f"{prefix}_wave_states"] = wave_states
    st.session_state[f"{prefix}_wave_states_seeded"] = True
    st.session_state[f"{prefix}_reversal_states"] = reversal_states
    st.session_state[f"{prefix}_reversal_states_seeded"] = True


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

        icon = "🟢" if entry["direction"] == "LONG" else "🔴"
        price = round(entry["price"], 2) if entry["price"] is not None else "?"
        rsi = entry["rsi"] if entry["rsi"] is not None else "?"

        full_status = RSIWaveStatusService.analyse(entry["ticker"], period="730d")
        stop_target = full_status["stop_target"] if full_status else None

        levels = (
            f"\nStop {stop_target['stop']} · Target {stop_target['target1']} · R:R 1:{stop_target['risk_reward']}"
            if stop_target
            else ""
        )

        message = f"{icon} {entry['name']} ({entry['ticker']}) — {entry['direction']} entry\nPrice {price} · RSI {rsi}{levels}"

        st.toast(f"{entry['direction']} entry: {entry['name']}", icon=icon)

        if TelegramNotifier.is_configured():
            TelegramNotifier.send(message)

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

        icon = "🟢" if signal["direction"] == "LONG" else "🔴"
        price = round(signal["price"], 2) if signal["price"] is not None else "?"
        signal_label = REVERSAL_SIGNAL_LABELS.get(signal["state"], signal["state"])

        full_status = ReversalStatusService.analyse(signal["ticker"])
        stop_target = full_status["stop_target"] if full_status else None

        levels = (
            f"\nStop {stop_target['stop']} · Target {stop_target['target1']} · R:R 1:{stop_target['risk_reward']}"
            if stop_target and stop_target.get("stop") is not None
            else ""
        )

        message = (
            f"{icon} {signal['name']} ({signal['ticker']}) — {signal_label} (Reversal Playbook)\n"
            f"Price {price} · RSI {signal['rsi']}{levels}"
        )

        st.toast(f"{signal_label}: {signal['name']}", icon=icon)

        if TelegramNotifier.is_configured():
            TelegramNotifier.send(message)

        AlertLog.log_alert(
            signal["ticker"], signal["name"], signal["direction"], signal["price"], signal["rsi"], stop_target,
        )


def _refresh_universe_body(prefix, country):
    """
    The only job of this fragment: reload + rescan the whole universe
    once an hour (and once on initial mount, since fragments execute
    inline as part of the first full script run). No interactive
    widgets here on purpose - the Scanner/detail box live in
    render_universe_live() below, a plain function so that clicking a
    row there doesn't re-trigger this heavy rescan.
    """

    load_universe(prefix, country)
    st.caption(f"🕐 {country} universe last scanned just now - refreshes automatically every hour.")


@st.fragment(run_every=UNIVERSE_REFRESH_SECONDS)
def refresh_us_universe():
    _refresh_universe_body("us", "USA")


@st.fragment(run_every=UNIVERSE_REFRESH_SECONDS)
def refresh_india_universe():
    _refresh_universe_body("india", "India")


@st.fragment(run_every=UNIVERSE_REFRESH_SECONDS)
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

    market = st.session_state[f"{prefix}_market"]

    if market is None:
        st.info("Loading for the first time...")
        return

    df = market["df"]

    if df.empty:
        st.warning("No assets found for this universe.")
        return

    st.caption(f"{len(df)} symbols · sorted by Reversal state by default")

    ticker = Scanner.render(df, default_sort="Reversal", key_prefix=prefix, compact=True)

    if ticker:
        st.session_state[f"{prefix}_selected_ticker"] = ticker
    elif st.session_state[f"{prefix}_selected_ticker"] not in df["Ticker"].tolist():
        st.session_state[f"{prefix}_selected_ticker"] = df.iloc[0]["Ticker"] if not df.empty else None

    selected = st.session_state[f"{prefix}_selected_ticker"]

    if not selected:
        return

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


def render_universe_tab(prefix, country, title):

    st.subheader(f"{title} — Reversal Playbook + RSI Wave")
    st.caption("Full universe, auto-refreshed once an hour (not intraday-live like Global Indices) - too many symbols to rescan every few minutes without risking yfinance rate limits.")

    UNIVERSE_REFRESH_FRAGMENTS[prefix]()
    render_universe_live(prefix, title)


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


def render_opportunity_center(df):

    st.subheader("Best Opportunities")
    TopOpportunities.render(df)


def render_workbench(df):

    Scanner.render(df)

    st.divider()

    stock = selected_stock(df)

    StockDetails.render(stock)

    if st.button("Load chart", use_container_width=True):

        ticker = stock["Ticker"] if stock else None

        Charts.render(
            load_chart(ticker)
        )


def render_loaded_dashboard(market):

    df = market["df"]
    stats = DashboardStats.summary(df)

    loaded, failed, displayed = st.columns(3)
    loaded.metric("Loaded", market["success"])
    failed.metric("Failed", market["failed"])
    displayed.metric("Displayed", len(df))

    Metrics.render(stats)
    render_opportunity_center(df)
    render_workbench(df)


def main():

    init_state()

    meta = DashboardLoader.metadata()

    Header.render()
    MarketStatus.render()

    filters = Sidebar.render(meta)

    if filters["refresh"]:
        st.cache_data.clear()
        st.session_state.chart = None
        st.success("Cache cleared.")

    if filters["load"] or st.session_state.market is None:
        load_market(filters)

    market = st.session_state.market

    if market is None:

        load_market(filters)

        market = st.session_state.market

    # Global Indices first so it's what's on screen when the app opens.
    tab_global, tab_us, tab_india, tab_crypto, tab_main, tab_fundamentals = st.tabs(
        ["🌍 Global Indices", "🇺🇸 US Stocks", "🇮🇳 Indian Stocks", "🪙 Crypto", "📊 Scanner", "💰 Fundamentals"]
    )

    with tab_global:
        render_global_indices_tab(meta)

    with tab_us:
        render_universe_tab("us", "USA", "🇺🇸 US Stocks")

    with tab_india:
        render_universe_tab("india", "India", "🇮🇳 Indian Stocks")

    with tab_crypto:
        render_universe_tab("crypto", "Crypto", "🪙 Crypto")

    with tab_main:
        render_loaded_dashboard(market)

    with tab_fundamentals:
        render_fundamentals_tab()


if __name__ == "__main__":
    main()
