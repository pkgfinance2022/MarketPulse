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

Tagged with the git commit the state was saved under (same read-.git/-
directly approach as universe_cache._current_commit_hash, duplicated
here rather than imported so this module has no dependency on that
one) - a persisted baseline from a different commit is discarded,
since the analysis engines producing these states may have changed
since it was saved.
"""

import pickle
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent.parent.parent / "database" / "notify_baseline.pkl"


def _current_commit_hash():

    git_dir = Path(__file__).resolve().parent.parent.parent / ".git"
    head_file = git_dir / "HEAD"

    if not head_file.exists():
        return None

    try:
        content = head_file.read_text().strip()
    except OSError:
        return None

    if not content.startswith("ref:"):
        return content  # detached HEAD - already a commit hash

    ref_path = git_dir / content.split(" ", 1)[1]

    try:
        return ref_path.read_text().strip()
    except OSError:
        return None


def load():
    """Returns the persisted baseline dict, or {} if missing, unreadable,
    or saved under a different commit than the one currently running."""

    if not STATE_PATH.exists():
        return {}

    try:
        with open(STATE_PATH, "rb") as f:
            saved = pickle.load(f)
    except (pickle.PickleError, EOFError, OSError, AttributeError):
        return {}

    if saved.get("git_commit") != _current_commit_hash():
        return {}

    return saved.get("state", {})


def save(state):
    """Best-effort - a failed write just means the next process starts
    cold for this, same as before this module existed."""

    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

        with open(STATE_PATH, "wb") as f:
            pickle.dump({"state": state, "git_commit": _current_commit_hash()}, f)

    except (pickle.PickleError, OSError):
        pass
