"""
Background scan cache for the per-universe tabs (US/India/Crypto).

Streamlit reruns the whole script synchronously per interaction, so a
multi-minute full-universe scan run inline blocks the page - nothing
useful renders until it's done. This runs the scan in a background
thread instead: the UI always renders whatever's already cached (even
if it's from hours ago), with a "last refreshed" timestamp, while a
fresh scan quietly completes in the background and swaps in the next
time the page checks - no more staring at spinners for 10 minutes.

Deliberately NOT built on st.session_state - a background thread has
no access to a session's Streamlit script context, so toasts/session
writes can't happen there. This is a plain module-level cache instead,
shared across all sessions in this process (fine for a personal
dashboard, not built for multi-tenant isolation).

Each "pool" gets its own semaphore serializing the heavy work within
that pool (only one scan per pool runs at a time), even though a
thread can be "started" for every prefix at once. Without this, all
of US/India/Crypto going stale simultaneously (e.g. right after a
fresh deploy) piles up several ThreadPoolExecutors concurrently -
harmless on a local machine with a generous thread limit, but this hit
"RuntimeError: can't start new thread" on Streamlit Community Cloud's
much more constrained free-tier container.

Global Indices gets its OWN pool, separate from US/India/Crypto - it's
a much smaller universe (~26 symbols vs. 70-160+) and is meant to be
the fast, "live" tab. Sharing one semaphore across all four meant
Global Indices could get stuck queued behind a much bigger scan on a
fresh start (all four requesting a scan at once, whichever grabs the
single slot first blocks everyone else) - exactly the "Global Indices
not ready for 10 minutes" symptom this was built to prevent.
"""

import hashlib
import pickle
import threading
import time
from collections import defaultdict
from pathlib import Path

_lock = threading.Lock()
_cache = {}
_scan_semaphores = defaultdict(lambda: threading.Semaphore(1))

# Persists each prefix's last completed scan to disk, so a fresh
# server process (a redeploy, a Streamlit Cloud sleep/wake cycle, or
# just restarting `streamlit run` locally to pick up a code change)
# doesn't have to sit through a multi-minute rescan before it can show
# anything - it loads straight from the last real result instead.
#
# Tagged with a fingerprint of the code that actually determines a
# scan's output (see _scan_logic_fingerprint below) - a persisted
# result computed under different analysis/provider logic isn't "the
# same past finding", it's just wrong, so it's discarded on load
# rather than trusted. Deliberately scoped to just those modules
# rather than "any commit" (the previous approach, keyed to the git
# HEAD commit): on a day with dozens of commits touching UI-only files
# (app.py copy tweaks, a new tab, README edits), that discarded every
# tab's perfectly good cached scan on every single restart even though
# nothing that affects a scan's actual output had changed - exactly
# the "reloading everything, every time" pain this cache exists to
# avoid. The routine hourly/10-min staleness cycle will naturally
# recompute it either way; this only controls what's shown in the
# meantime.
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "database" / "universe_cache"

# Every directory/module whose code path a _scan_*_data() function in
# app.py transitively runs through to produce row data - i.e. what
# would make a previously-cached result actually wrong if changed.
# Deliberately excludes dashboard/app.py and dashboard/widgets/ (pure
# rendering - changing a label or adding a tab doesn't change what a
# scan computes).
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


def _load_persisted_cache():
    """
    Called once at import time. Populates _cache from disk for every
    prefix whose persisted commit hash matches the code actually
    running right now - anything else is left absent, so the normal
    "cache is missing -> scan" path takes over exactly as if this were
    a brand new process with nothing cached yet.
    """

    if not CACHE_DIR.exists():
        return

    current_fingerprint = _scan_logic_fingerprint()

    for path in CACHE_DIR.glob("*.pkl"):

        try:
            with open(path, "rb") as f:
                saved = pickle.load(f)
        except (pickle.PickleError, EOFError, OSError, AttributeError):
            continue

        if saved.get("logic_fingerprint") != current_fingerprint:
            continue

        _cache[path.stem] = {
            "data": saved["data"],
            "ts": saved["ts"],
            "loading": False,
            "loading_since": None,
        }


def _persist(prefix, entry):

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        with open(CACHE_DIR / f"{prefix}.pkl", "wb") as f:
            pickle.dump({"data": entry["data"], "ts": entry["ts"], "logic_fingerprint": _scan_logic_fingerprint()}, f)

    except (pickle.PickleError, OSError):
        pass  # best-effort - a failed write just means the next process starts cold, same as today


_load_persisted_cache()

# Kept separate from _cache (and NOT wiped by force_clear_all/
# force_clear) - a scan's own past duration is the best available
# estimate for "how long will this one take", and that's most useful
# right after a forced full reset, when there's otherwise no
# in-progress timing data at all yet.
_last_durations = {}

# A real scan (US/India/Crypto's full universe, or Global Indices)
# has always finished in well under 3 minutes in practice.
# STUCK_WARNING_SECONDS is when the UI starts offering a manual
# "force restart" (see app.py's _render_scan_now_button) - well past
# normal, but not yet auto-healed. MAX_SCAN_SECONDS is the automatic
# ceiling: past this, start_scan() itself treats a "loading" entry as
# abandoned (a hung network call yfinance's own timeout didn't catch,
# or a retry storm) and lets a fresh scan take over even without the
# user manually forcing it - either way, no more needing to restart
# the whole app just to get a tab unstuck.
STUCK_WARNING_SECONDS = 180
MAX_SCAN_SECONDS = 600


def get(prefix):
    """
    Returns {"data":, "ts":, "loading":, "loading_since":,
    "last_duration":} for this prefix, or None if no scan has ever
    completed or started. "last_duration" (seconds) is how long the
    most recent successful scan actually took - callers use it to
    show a practical "~X remaining" estimate for the current one,
    since a given universe's size doesn't change run to run. Survives
    force_clear_all()/force_clear(), so it's still available as an
    estimate right after a forced reset.
    """

    with _lock:
        entry = _cache.get(prefix)

        if not entry:
            return None

        result = dict(entry)
        result["last_duration"] = _last_durations.get(prefix)

        return result


def force_clear_all():
    """
    Marks every prefix that ISN'T currently mid-scan as stale, so each
    tab's next refresh check kicks off a fresh scan on its own - the
    "refresh everything, like a reboot" button.

    Deliberately leaves any prefix that's already loading untouched,
    rather than wiping it too: clicking this while a scan is still
    in-flight (a large universe genuinely takes a few minutes) used to
    wipe its "loading" flag along with everything else, so a second
    click - or the fragment's own poll tick - would see a missing
    entry and start ANOTHER full scan on top of the one still
    running, with no limit on how many could pile up. That's exactly
    what caused a segfault in production: repeated clicks stacked up
    dozens of concurrent full-universe scans until the container ran
    out of threads/sockets. Leaving in-flight entries alone makes this
    safe to click any number of times - it can only ever add work for
    prefixes that are actually idle.
    """

    with _lock:
        for entry in _cache.values():
            if not entry["loading"]:
                entry["ts"] = 0


def force_clear(prefix):
    """
    Immediately clears a prefix's "loading" flag, regardless of how
    long it's been running - lets a user manually recover a stuck scan
    right now instead of waiting out MAX_SCAN_SECONDS (or restarting
    the whole app, which was previously the only way out). Safe to
    call even if the scan isn't actually stuck: the abandoned
    background thread (if any) is still running and will just
    overwrite the cache with its own result whenever it eventually
    finishes or errors, same as any other stale/duplicate scan.
    """

    with _lock:
        entry = _cache.get(prefix)

        if entry:
            entry["loading"] = False
            entry["loading_since"] = None


def start_scan(prefix, scan_fn, pool="universe"):
    """
    Starts scan_fn() (a zero-arg callable doing the actual fetch/scan
    work, returning whatever the caller wants cached) in a background
    thread, unless one is already running for this prefix. Never
    blocks. Returns True if a new scan was actually started.

    `pool` picks which semaphore this scan competes for - prefixes in
    the same pool are serialized against each other, but different
    pools run fully independently. Every tab should pass a pool of its
    own (e.g. "global", or the tab's own prefix) so one tab's scan is
    never queued behind - or stuck waiting on - a different tab's.
    """

    with _lock:
        entry = _cache.setdefault(prefix, {"data": None, "ts": 0, "loading": False, "loading_since": None})

        stuck = entry["loading"] and entry.get("loading_since") and (time.time() - entry["loading_since"]) > MAX_SCAN_SECONDS

        if entry["loading"] and not stuck:
            return False

        started_at = time.time()
        entry["loading"] = True
        entry["loading_since"] = started_at

    def _worker():

        with _scan_semaphores[pool]:

            try:
                result = scan_fn()
                completed_at = time.time()

                with _lock:
                    _cache[prefix] = {"data": result, "ts": completed_at, "loading": False, "loading_since": None}
                    _last_durations[prefix] = completed_at - started_at

                _persist(prefix, _cache[prefix])

            except Exception:
                with _lock:
                    _cache[prefix]["loading"] = False
                    _cache[prefix]["loading_since"] = None

    threading.Thread(target=_worker, daemon=True).start()

    return True
