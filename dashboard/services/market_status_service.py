from datetime import datetime
from zoneinfo import ZoneInfo


class MarketStatusService:

    # Symbol -> (tz, open_hour, close_hour). Asian indices genuinely sit in
    # different timezones/hours, so this needs per-symbol dispatch rather
    # than one lumped "Asia" bucket like European Indices can get away with.
    ASIAN_INDEX_HOURS = {
        "^N225": ("Asia/Tokyo", 9, 15),
        "^HSI": ("Asia/Hong_Kong", 9, 16),
        "^KS11": ("Asia/Seoul", 9, 15),
        "^STI": ("Asia/Singapore", 9, 17),
    }

    @staticmethod
    def _simple_session(tz, open_hour, close_hour, close_minute=0):

        now = datetime.now(ZoneInfo(tz))

        if now.weekday() >= 5:
            return "🔴 Closed"

        if open_hour <= now.hour < close_hour:
            return "🟢 Live"

        if now.hour == close_hour and now.minute <= close_minute:
            return "🟢 Live"

        return "🔴 Closed"

    @classmethod
    def _asian_index_status(cls, asset):

        tz, open_hour, close_hour = cls.ASIAN_INDEX_HOURS.get(
            asset.symbol,
            cls.ASIAN_INDEX_HOURS["^N225"],
        )

        return cls._simple_session(tz, open_hour, close_hour)

    @staticmethod
    def _commodity_status():

        # CME/COMEX convention: open Sunday ~18:00 ET through Friday ~17:00 ET,
        # closed the rest of Saturday/Sunday.
        now = datetime.now(ZoneInfo("America/New_York"))

        if now.weekday() == 5:
            return "🔴 Closed"

        if now.weekday() == 6 and now.hour < 18:
            return "🔴 Closed"

        return "🟢 Live"

    @staticmethod
    def _forex_status():

        # Forex trades ~24x5, closed only on Saturday (ET convention).
        now = datetime.now(ZoneInfo("America/New_York"))

        if now.weekday() == 5:
            return "🔴 Closed"

        return "🟢 Live"

    @classmethod
    def status(cls, asset):

        country = asset.country.lower()

        # ---------- Crypto ----------
        if country == "crypto":
            return "🟢 24x7"

        # ---------- India ----------
        if country == "india":

            now = datetime.now(ZoneInfo("Asia/Kolkata"))

            if now.weekday() >= 5:
                return "🔴 Closed"

            if 9 <= now.hour < 15:
                return "🟢 Live"

            if now.hour == 15 and now.minute <= 30:
                return "🟢 Live"

            return "🔴 Closed"

        # ---------- USA ----------
        if country == "usa":

            now = datetime.now(ZoneInfo("America/New_York"))

            if now.weekday() >= 5:
                return "🔴 Closed"

            # 09:30–16:00 ET
            if (
                (now.hour == 9 and now.minute >= 30)
                or (10 <= now.hour < 16)
            ):
                return "🟢 Live"

            # 04:00–09:30
            if 4 <= now.hour < 9:
                return "🟡 Pre"

            # 16:00–20:00
            if 16 <= now.hour < 20:
                return "🟠 After"

            return "🔴 Closed"

        # ---------- Global assets: dispatch by category (Sector), since
        # Country is just "Global" for all of these and doesn't
        # distinguish region ----------

        category = asset.category

        if category == "European Indices":
            return cls._simple_session("Europe/Amsterdam", 9, 17, close_minute=30)

        if category == "Asian Indices":
            return cls._asian_index_status(asset)

        if category == "Commodities":
            return cls._commodity_status()

        if category == "Currencies":
            return cls._forex_status()

        # ---------- Fallback for anything uncategorized ----------
        return cls._simple_session("Europe/Amsterdam", 9, 17, close_minute=30)
