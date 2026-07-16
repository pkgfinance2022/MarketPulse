"""
Standalone signal scanner + Telegram notifier.

Runs independently of the Streamlit app, via a scheduled GitHub
Actions workflow (.github/workflows/telegram_scan.yml) - so alerts
still fire even when nobody has the dashboard open in a browser.
Streamlit Cloud apps (and Streamlit fragments' run_every timers) only
execute while a session is actively connected; this script has no
such dependency, since it's just a plain Python process GitHub starts
on a cron schedule.

Deliberately separate from the Streamlit app's own AlertLog/session-
state dedup - a scheduled run starts from a fresh git checkout each
time (a different process, a different filesystem, no shared memory
with any Streamlit Cloud session), so "have I already alerted on
this" is tracked in its own small JSON file (database/scan_state.json)
that the workflow commits back to the repo after every run. A signal
only re-alerts once its state actually changes from what was last
seen - not on every run - so this can never send the same alert twice
in a row.

Scope, matching what the app itself computes for each source:
- Global Indices ("Global", sector "All") + Crypto (BTC-USD only):
  Hourly (RSI Wave entries + Reversal Playbook 1H BUY/SELL) - these
  are the only two sources that get an Hourly view anywhere in the
  app.
- US Stocks + Indian Stocks + Crypto (BTC-USD): Daily+Weekly
  (Reversal Playbook Daily/Weekly engine) - matches the US/India
  tabs' own timeframe exactly (they don't get Hourly - see
  render_universe_live's show_hourly gate in dashboard/app.py).

Runs hourly, not more often - US/India alone are 100+ symbols each,
and each Daily+Weekly check is 2 yfinance fetches per symbol. Anything
faster risks the same YFRateLimitError already hit once this session
(see providers/yahoo.py's fetch cache, added for the same reason).
"""

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.loader import AssetLoader
from analysis.reversal_playbook import ReversalPlaybook
from analysis.reversal_playbook_daily import DailyWeeklyReversalPlaybook
from analysis.rsi_wave_strategy import RSIWaveStrategy
from dashboard.services.rsi_wave_status import RSIWaveStatusService
from dashboard.services.telegram_notifier import TelegramNotifier

STATE_PATH = REPO_ROOT / "database" / "scan_state.json"

CRYPTO_ALERT_TICKER = "BTC-USD"
GLOBAL_MACRO_INDIA_INCLUDE = {"^NSEI", "^NSEBANK"}   # mirrors DashboardLoader.GLOBAL_MACRO_INDIA_INCLUDE

REVERSAL_SIGNAL_DIRECTIONS = {
    "BUY_SIGNAL": "LONG",
    "BUY_SIGNAL_PATH_C": "LONG",
    "BUY_SIGNAL_PATH_D": "LONG",
    "SELL_SIGNAL": "SHORT",
    "SELL_SIGNAL_CONTINUATION": "SHORT",
}

REVERSAL_SIGNAL_LABELS = {
    "BUY_SIGNAL": "BUY",
    "BUY_SIGNAL_PATH_C": "BUY (2nd touch — higher confidence)",
    "BUY_SIGNAL_PATH_D": "BUY (counter-trend)",
    "SELL_SIGNAL": "SELL",
    "SELL_SIGNAL_CONTINUATION": "SELL (continuation)",
}


def load_state():

    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())

    return {}


def save_state(state):

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))


def tickers_for(country, sector="All"):
    """Mirrors DashboardLoader.load()'s country/sector filtering, without
    that method's much heavier per-asset scoring/network pipeline - this
    script only needs the ticker list."""

    assets = AssetLoader().all_assets()

    if country != "All":
        assets = [a for a in assets if a.country.lower() == country.lower()]

    if country == "Global" and sector == "All":
        assets = [
            a for a in assets
            if a.category != "Indian Indices" or a.symbol in GLOBAL_MACRO_INDIA_INCLUDE
        ]

    if sector != "All":
        assets = [a for a in assets if a.category == sector]

    return [(a.symbol, a.name) for a in assets]


def send_alert(name, ticker, label, direction, price, rsi, stop_target, timeframe):

    icon = "🟢" if direction == "LONG" else "🔴"
    price_str = round(price, 4) if price is not None else "?"
    rsi_str = round(rsi, 2) if rsi is not None else "?"

    levels = ""
    if stop_target and stop_target.get("stop") is not None:
        levels = f"\nStop {stop_target['stop']} · Target {stop_target['target1']} · R:R 1:{stop_target['risk_reward']}"

    message = (
        f"{icon} {name} ({ticker}) — {label} ({timeframe})\n"
        f"Price {price_str} · RSI {rsi_str}{levels}"
    )

    print(f"ALERT: {message}")

    if TelegramNotifier.is_configured():
        TelegramNotifier.send(message)
    else:
        print("Telegram not configured (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing) - alert logged here only.")


def check_hourly(ticker, name, state, is_first_run):
    """RSI Wave entries + Reversal Playbook 1H BUY/SELL - Global Indices + Crypto(BTC) only."""

    try:
        trace, df = RSIWaveStrategy.run_symbol(ticker)
    except Exception as e:
        print(f"  [RSI Wave] {ticker}: fetch failed ({e})")
        trace = None

    if trace:

        _, wave_state, _ = RSIWaveStrategy.describe(trace)
        key = f"{ticker}:wave"
        previous = state.get(key)

        if wave_state in ("ENTRY_LONG", "ENTRY_SHORT") and previous != wave_state and not is_first_run:

            direction = "LONG" if wave_state == "ENTRY_LONG" else "SHORT"
            last = trace[-1]
            status = RSIWaveStatusService.analyse(ticker)
            stop_target = status["stop_target"] if status else None

            send_alert(name, ticker, "RSI Wave entry", direction, last["price"], last["rsi"], stop_target, "Hourly")

        state[key] = wave_state

    try:
        result = ReversalPlaybook.run_symbol(ticker)
    except Exception as e:
        print(f"  [Reversal Playbook] {ticker}: fetch failed ({e})")
        result = None

    if result:

        desc, rev_state, levels, event_time = ReversalPlaybook.describe(result)
        key = f"{ticker}:reversal_1h"
        previous = state.get(key)

        if rev_state in REVERSAL_SIGNAL_DIRECTIONS and previous != rev_state and not is_first_run:

            direction = REVERSAL_SIGNAL_DIRECTIONS[rev_state]
            label = REVERSAL_SIGNAL_LABELS.get(rev_state, rev_state)
            last = result["trace"][-1]

            send_alert(name, ticker, label, direction, last["price"], last["rsi"], levels, "Reversal Playbook 1H")

        state[key] = rev_state


def check_daily_weekly(ticker, name, state, is_first_run):
    """Reversal Playbook Daily+Weekly - US Stocks + Indian Stocks + Crypto(BTC)."""

    try:
        result = DailyWeeklyReversalPlaybook.run_symbol(ticker)
    except Exception as e:
        print(f"  [Daily+Weekly] {ticker}: fetch failed ({e})")
        result = None

    if not result:
        return

    desc, daily_state, levels, event_time = DailyWeeklyReversalPlaybook.describe(result)
    key = f"{ticker}:daily"
    previous = state.get(key)

    if daily_state in REVERSAL_SIGNAL_DIRECTIONS and previous != daily_state and not is_first_run:

        direction = REVERSAL_SIGNAL_DIRECTIONS[daily_state]
        label = REVERSAL_SIGNAL_LABELS.get(daily_state, daily_state)
        last = result["trace"][-1]

        send_alert(name, ticker, label, direction, last["price"], last["rsi"], levels, "Daily")

    state[key] = daily_state

    weekly_desc, weekly_state, weekly_event_time = DailyWeeklyReversalPlaybook.weekly_describe(result)
    weekly_key = f"{ticker}:weekly"
    previous_weekly = state.get(weekly_key)

    if weekly_state in ("MULTI_TRY_BREAKOUT", "PATH_C_CONFIRMED") and previous_weekly != weekly_state and not is_first_run:

        last = result["trace"][-1]
        label = "Multi-try breakout" if weekly_state == "MULTI_TRY_BREAKOUT" else "Path C confirmed"

        send_alert(name, ticker, label, "LONG", last["price"], last["weekly_rsi"], None, "Weekly")

    state[weekly_key] = weekly_state


PACE_SECONDS = 0.5   # small delay between symbols - ~200-300 symbols x 2 fetches each is enough sustained volume to risk the same YFRateLimitError already hit once this session; this just spreads the load out rather than firing everything in a tight burst


def main():

    state = load_state()
    is_first_run = not state

    if is_first_run:
        print("First run - seeding state silently, no alerts this time (matches the app's own behavior on a fresh session).")

    print("=== Hourly: Global Indices ===")
    for ticker, name in tickers_for("Global", "All"):
        check_hourly(ticker, name, state, is_first_run)
        time.sleep(PACE_SECONDS)

    print("=== Hourly: Crypto (BTC-USD only) ===")
    check_hourly(CRYPTO_ALERT_TICKER, "Bitcoin", state, is_first_run)

    print("=== Daily+Weekly: US Stocks ===")
    for ticker, name in tickers_for("USA", "All"):
        check_daily_weekly(ticker, name, state, is_first_run)
        time.sleep(PACE_SECONDS)

    print("=== Daily+Weekly: Indian Stocks ===")
    for ticker, name in tickers_for("India", "All"):
        check_daily_weekly(ticker, name, state, is_first_run)
        time.sleep(PACE_SECONDS)

    print("=== Daily+Weekly: Crypto (BTC-USD only) ===")
    check_daily_weekly(CRYPTO_ALERT_TICKER, "Bitcoin", state, is_first_run)

    save_state(state)
    print("Done.")


if __name__ == "__main__":
    main()
