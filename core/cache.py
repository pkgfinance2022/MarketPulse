"""
Disk Cache

A minimal JSON-backed cache with a time-to-live. Used for data that is
slow to fetch and doesn't change intraday (e.g. company fundamentals),
so repeated scans don't re-hit the network for every ticker every time.
"""

import json
import time
from pathlib import Path


class DiskCache:

    DIRECTORY = Path("cache")

    def __init__(self, namespace, ttl_seconds=24 * 60 * 60):

        self.namespace = namespace
        self.ttl_seconds = ttl_seconds
        self.path = self.DIRECTORY / f"{namespace}.json"

        self.DIRECTORY.mkdir(exist_ok=True)

        self._data = self._load()

    def _load(self):

        if not self.path.exists():
            return {}

        try:

            with open(self.path, "r") as handle:
                return json.load(handle)

        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self):

        try:

            with open(self.path, "w") as handle:
                json.dump(self._data, handle)

        except OSError:
            pass

    def get(self, key):

        entry = self._data.get(key)

        if not entry:
            return None

        if time.time() - entry["ts"] > self.ttl_seconds:
            return None

        return entry["value"]

    def set(self, key, value):

        self._data[key] = {
            "ts": time.time(),
            "value": value,
        }

        self._save()
