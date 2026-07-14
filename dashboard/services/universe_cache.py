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

import threading
import time
from collections import defaultdict

_lock = threading.Lock()
_cache = {}
_scan_semaphores = defaultdict(lambda: threading.Semaphore(1))

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

            except Exception:
                with _lock:
                    _cache[prefix]["loading"] = False
                    _cache[prefix]["loading_since"] = None

    threading.Thread(target=_worker, daemon=True).start()

    return True
