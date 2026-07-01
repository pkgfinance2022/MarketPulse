"""
Market Clock
"""

from datetime import datetime
from zoneinfo import ZoneInfo


class MarketClock:

    MARKETS = {

        "India": {
            "tz": "Asia/Kolkata",
            "open": (9, 15),
            "close": (15, 30),
        },

        "USA": {
            "tz": "America/New_York",
            "open": (9, 30),
            "close": (16, 0),
        },

        "Europe": {
            "tz": "Europe/London",
            "open": (8, 0),
            "close": (16, 30),
        },

        "Forex": {
            "always": True,
        },

        "Crypto": {
            "always": True,
        },
    }

    @classmethod
    def status(cls, market):

        info = cls.MARKETS[market]

        if info.get("always"):
            return {
                "status": "OPEN",
                "time": "24×7",
            }

        now = datetime.now(
            ZoneInfo(info["tz"])
        )

        if now.weekday() >= 5:
            return {
                "status": "CLOSED",
                "time": "Weekend",
            }

        open_time = now.replace(
            hour=info["open"][0],
            minute=info["open"][1],
            second=0,
            microsecond=0,
        )

        close_time = now.replace(
            hour=info["close"][0],
            minute=info["close"][1],
            second=0,
            microsecond=0,
        )

        if open_time <= now <= close_time:

            diff = close_time - now

            h = diff.seconds // 3600
            m = (diff.seconds % 3600) // 60

            return {
                "status": "OPEN",
                "time": f"Closes in {h}h {m}m",
            }

        if now < open_time:

            diff = open_time - now

        else:

            tomorrow = open_time.replace(
                day=open_time.day + 1
            )

            diff = tomorrow - now

        h = diff.seconds // 3600
        m = (diff.seconds % 3600) // 60

        return {
            "status": "CLOSED",
            "time": f"Opens in {h}h {m}m",
        }