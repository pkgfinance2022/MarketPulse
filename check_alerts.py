"""
End-of-day alert check.

Re-evaluates every still-open alert the dashboard has sent (see
dashboard/services/alert_log.py) against current prices, marks any
that hit their stop or target, and prints a summary - "did the alerts
actually work" without needing the browser open.

Usage:
    python check_alerts.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.services.alert_log import AlertLog


def main():

    df = AlertLog.load()

    if df.empty:
        print("No alerts have been logged yet.")
        return

    print(f"Checking {int((df['Status'] == 'OPEN').sum())} open alert(s) against current prices...\n")

    df = AlertLog.evaluate()

    stats = AlertLog.summary(df)

    print("=" * 70)
    print("ALERT TRACKING SUMMARY")
    print("=" * 70)
    print(f"Total alerts sent : {stats['total']}")
    print(f"Still open        : {stats['open']}")
    print(f"Hit target        : {stats['hit_target']}")
    print(f"Hit stop          : {stats['hit_stop']}")
    print(f"Win rate (closed) : {stats['win_rate']}%")
    print(f"Avg return so far : {stats['avg_return']}%")
    print()

    cols = ["Timestamp", "Ticker", "Name", "Direction", "EntryPrice", "Stop", "Target1", "Status", "ReturnPct", "ClosedAt"]

    print(df[cols].sort_values("Timestamp", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
