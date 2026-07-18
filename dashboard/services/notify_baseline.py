"""
Persists the notification "have I already seen this ticker in this
state" baselines (wave_states/reversal_states/daily_reversal_states and
their _seeded flags, for Global Indices and each of US/India/Crypto)
across process restarts.

Without this, restarting the Streamlit server (to pick up a code
change, or Streamlit Cloud recycling the process) wipes st.session_state
entirely, and the very next notification check treats whatever's
currently active as "already known" instead of comparing it against
real history - silently swallowing a signal that genuinely transitioned
around the same time as the restart. Same problem universe_cache.py
solves for scan *data*, but for the notification *baseline* instead.

Tagged with a fingerprint of the analysis/provider code that actually
determines these states (same approach as
universe_cache._scan_logic_fingerprint, duplicated here rather than
imported so this module has no dependency on that one) - a persisted
baseline is discarded if that code has changed since it was saved,
since the analysis engines producing these states may have changed
too. Deliberately scoped to just those modules rather than "any commit"
- dashboard/app.py's own UI-only edits (copy tweaks, a new tab) don't
change what these states mean, so they shouldn't wipe a perfectly
good baseline on every restart either.
"""

import hashlib
import pickle
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent.parent.parent / "database" / "notify_baseline.pkl"

_SCAN_LOGIC_ROOTS = ["analysis", "providers", "core", "dashboard/services"]


def _scan_logic_fingerprint():

    repo_root = Path(__file__).resolve().parent.parent.parent
    hasher = hashlib.sha256()

    paths = []
    for root_name in _SCAN_LOGIC_ROOTS:
        root = repo_root / root_name
        if root.exists():
            paths.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)

    for path in sorted(paths, key=lambda p: p.relative_to(repo_root).as_posix()):

        try:
            hasher.update(path.relative_to(repo_root).as_posix().encode())
            hasher.update(path.read_bytes())
        except OSError:
            continue

    return hasher.hexdigest()


def load():
    """Returns the persisted baseline dict, or {} if missing, unreadable,
    or saved under different scan logic than what's currently running."""

    if not STATE_PATH.exists():
        return {}

    try:
        with open(STATE_PATH, "rb") as f:
            saved = pickle.load(f)
    except (pickle.PickleError, EOFError, OSError, AttributeError):
        return {}

    if saved.get("logic_fingerprint") != _scan_logic_fingerprint():
        return {}

    return saved.get("state", {})


def save(state):
    """Best-effort - a failed write just means the next process starts
    cold for this, same as before this module existed."""

    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

        with open(STATE_PATH, "wb") as f:
            pickle.dump({"state": state, "logic_fingerprint": _scan_logic_fingerprint()}, f)

    except (pickle.PickleError, OSError):
        pass
