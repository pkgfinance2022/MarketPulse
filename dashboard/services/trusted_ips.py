"""
Trusted IP allowlist for the app's password gate.

Not real security (same disclaimer as the gate itself) - just avoids
re-prompting for the password more than about once a day from the
same network. Backed by a plain CSV under database/, same persistence
convention as the rest of the app, so it survives across sessions and
app reboots.

IP-based trust is inherently approximate: a dynamic IP (common on
mobile connections and some ISPs) can change and force a fresh login
even within the trust window - there's no way around that without
real accounts, which is more than this casual gate is meant to be.
"""

from pathlib import Path

import pandas as pd

from dashboard.services.time_utils import now_cet

TRUSTED_IPS_PATH = Path(__file__).resolve().parent.parent.parent / "database" / "trusted_ips.csv"

COLUMNS = ["IP", "LastAuthenticatedAt"]

TRUST_WINDOW_HOURS = 24


def _ensure():

    TRUSTED_IPS_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not TRUSTED_IPS_PATH.exists():
        pd.DataFrame(columns=COLUMNS).to_csv(TRUSTED_IPS_PATH, index=False)


def is_trusted(ip):

    if not ip:
        return False

    _ensure()
    df = pd.read_csv(TRUSTED_IPS_PATH)

    match = df[df["IP"] == ip]

    if match.empty:
        return False

    last = pd.to_datetime(match.iloc[-1]["LastAuthenticatedAt"])
    hours_since = (now_cet().replace(tzinfo=None) - last).total_seconds() / 3600

    return hours_since < TRUST_WINDOW_HOURS


def mark_trusted(ip):

    if not ip:
        return

    _ensure()
    df = pd.read_csv(TRUSTED_IPS_PATH)

    now_str = now_cet().strftime("%Y-%m-%d %H:%M:%S")

    if ip in df["IP"].values:
        df.loc[df["IP"] == ip, "LastAuthenticatedAt"] = now_str
    else:
        df = pd.concat([df, pd.DataFrame([{"IP": ip, "LastAuthenticatedAt": now_str}])], ignore_index=True)

    df.to_csv(TRUSTED_IPS_PATH, index=False)
