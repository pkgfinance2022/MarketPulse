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

# A real scan (US/India/Crypto's full universe, or Global Indices)
# has always finished in well under 5 minutes in practice. If a
# background thread is still marked "loading" past this, something's
# genuinely stuck (a hung network call yfinance's own default timeout
# didn't catch, or a retry storm) rather than just being slow - treat
# it as abandoned so a fresh scan can take over instead of the tab
# being stuck showing "a fresh scan is running" forever with no way
# to recover short of restarting the whole app.
MAX_SCAN_SECONDS = 900


def get(prefix):
    """Returns {"data":, "ts":, "loading":} for this prefix, or None if no scan has ever completed or started."""

    with _lock:
        entry = _cache.get(prefix)
        return dict(entry) if entry else None


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

        entry["loading"] = True
        entry["loading_since"] = time.time()

    def _worker():

        with _scan_semaphores[pool]:

            try:
                result = scan_fn()

                with _lock:
                    _cache[prefix] = {"data": result, "ts": time.time(), "loading": False, "loading_since": None}

            except Exception:
                with _lock:
                    _cache[prefix]["loading"] = False
                    _cache[prefix]["loading_since"] = None

    threading.Thread(target=_worker, daemon=True).start()

    return True
