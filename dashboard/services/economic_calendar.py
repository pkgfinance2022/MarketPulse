"""
Static known-events economic calendar - FOMC decision dates, US CPI,
US Nonfarm Payrolls, US PPI, and US Retail Sales release dates for
2026, sourced from the Federal Reserve's, BLS's, and Census Bureau's
own published schedules (all publish these many months ahead, so
unlike a live news feed this doesn't need a paid calendar API to be
accurate) - plus two rule-generated recurring releases that don't need
a per-date CSV row: US Initial Jobless Claims (every Thursday) and
ISM Manufacturing/Services PMI (1st/3rd business day of each month).

Deliberately narrow in scope rather than a general economic calendar -
these are the events that reliably move markets across every asset
class MarketPulse covers, and a wider calendar (earnings, minor
regional data, speeches) would add noise without a paid data source to
keep it current and complete.

A rare official reschedule (it happens - see the Feb 2026 CPI date
being pushed back two days in BLS's own schedule notes, the Mar 2026
Retail Sales report shifting from Apr 16 to Apr 21, or a recurring
rule shifting around a holiday week) won't be reflected here until
this file/rule is manually updated.
"""

from pathlib import Path

import pandas as pd

CALENDAR_PATH = Path(__file__).resolve().parent.parent.parent / "database" / "economic_calendar.csv"

# What each event actually measures, why it moves markets, and which
# of MarketPulse's own tracked instruments tend to react most - so
# "FOMC Decision, Jul 29" reads as more than a name and a date. Not
# instrument-specific consensus/forecast numbers (that needs a paid
# data feed to stay current) - just the durable "what to watch for"
# context that doesn't change release to release.
EVENT_DETAILS = {
    "US Nonfarm Payrolls": {
        "what": "Net change in US jobs last month, plus the unemployment rate and average hourly earnings - the single most-watched US labor report.",
        "watch": "A big beat (strong jobs) tends to lift the Dollar (DX-Y.NYB) and US10Y yield (^TNX) on reduced rate-cut odds, often pressuring Gold (GC=F) and growth-heavy NASDAQ (^NDX); a big miss tends to do the opposite.",
    },
    "US CPI": {
        "what": "Headline and core month-over-month/year-over-year US inflation - the main input into the Fed's rate-decision math.",
        "watch": "A hot CPI print pushes US10Y yields and the Dollar up and typically hits NASDAQ/Gold; a cooler-than-expected print does the reverse and can fuel a broad equity rally.",
    },
    "FOMC Decision": {
        "what": "The Fed's rate decision plus its statement (and, at SEP meetings, updated dot-plot rate projections) - sets the near-term path for US interest rates.",
        "watch": "A hawkish surprise (fewer/later cuts than priced in) lifts yields and the Dollar, pressures Gold, and often hits high-multiple NASDAQ names hardest; a dovish surprise does the opposite.",
    },
    "US Initial Jobless Claims": {
        "what": "New unemployment filings for the latest week - the most frequent, most real-time read on US labor conditions between the monthly jobs reports.",
        "watch": "Usually a smaller, faster market reaction than Payrolls/CPI/FOMC - a sharp, unexpected jump can move US10Y/Dollar/equities the same directional way a weak jobs report would, but a single week rarely moves markets on its own unless it breaks a trend.",
    },
    "US PPI": {
        "what": "Wholesale/producer-level price changes - an earlier stage of the inflation pipeline than CPI, and one input into the Fed's preferred PCE measure.",
        "watch": "Same direction as CPI but usually a smaller market reaction unless it surprises sharply or diverges from CPI's message - a hot print pressures yields/Dollar up and NASDAQ/Gold down.",
    },
    "US Retail Sales": {
        "what": "Month-over-month change in US consumer spending at retail/food-service businesses - the broadest, most direct read on whether the US consumer (~2/3 of GDP) is still spending.",
        "watch": "A strong beat is typically read as good for growth-sensitive equities but can also lift yields/Dollar on reduced rate-cut odds; a sharp miss raises recession concern and can pressure equities broadly, not just one sector.",
    },
    "ISM Manufacturing PMI": {
        "what": "Survey-based gauge of US factory activity - above 50 signals expansion, below 50 signals contraction. Released on the 1st business day of the month, ahead of almost every other data point covering that same month.",
        "watch": "Often the market's FIRST read on how the new month is shaping up - a surprise swing across the 50 line can move yields/Dollar/equities before other confirming data arrives later in the month.",
    },
    "ISM Services PMI": {
        "what": "Same survey concept as Manufacturing PMI but for the much larger US services sector - released on the 3rd business day of the month.",
        "watch": "Arguably more representative of the overall US economy than the Manufacturing read (services is a far bigger share of GDP) - a surprise miss/beat can move broad equity indices, yields, and the Dollar.",
    },
}


def _jobless_claims_rows(start, end):
    """
    Every Thursday in [start, end] - the standing weekly US Initial
    Jobless Claims release day (BLS/DOL convention; an occasional
    holiday-week shift isn't modeled here, same tolerance already
    accepted for the CSV-based events above).
    """

    first_thursday = start + pd.Timedelta(days=(3 - start.weekday()) % 7)
    thursdays = pd.date_range(first_thursday, end, freq="W-THU")

    return pd.DataFrame({
        "Date": thursdays,
        "Event": "US Initial Jobless Claims",
        "Importance": "Medium",
        "Notes": "Weekly initial claims - released every Thursday",
    })


def _nth_business_day(year, month, n):
    """The nth Monday-Friday day of the given month (no US federal holiday adjustment - same tolerance already accepted elsewhere in this module)."""

    day = pd.Timestamp(year=year, month=month, day=1)
    count = 0

    while True:

        if day.weekday() < 5:
            count += 1
            if count == n:
                return day

        day += pd.Timedelta(days=1)


def _ism_rows(start, end):
    """
    ISM Manufacturing PMI (1st business day of the month) and ISM
    Services PMI (3rd business day) for every month touching
    [start, end] - both a fixed, well-documented ISM convention rather
    than per-date lookups.
    """

    rows = []

    period = pd.Period(start, freq="M")
    end_period = pd.Period(end, freq="M")

    while period <= end_period:

        manufacturing_date = _nth_business_day(period.year, period.month, 1)
        services_date = _nth_business_day(period.year, period.month, 3)

        if start <= manufacturing_date <= end:
            rows.append({"Date": manufacturing_date, "Event": "ISM Manufacturing PMI", "Importance": "High", "Notes": "1st business day of the month"})

        if start <= services_date <= end:
            rows.append({"Date": services_date, "Event": "ISM Services PMI", "Importance": "High", "Notes": "3rd business day of the month"})

        period += 1

    # Real bug: pd.DataFrame(rows, columns=[...]) with an empty `rows`
    # list (no ISM date falls in this particular window - happens for
    # a real stretch of days most months, right after that month's own
    # 3rd business day has already passed) defaults every column,
    # including "Date", to object dtype instead of datetime64. That
    # empty object-dtype "Date" column then poisons the whole combined
    # calendar's dtype once concatenated with the real datetime rows
    # in upcoming() below, breaking the later .dt.strftime() call.
    # Explicit cast makes this correct even when rows is empty.
    df = pd.DataFrame(rows, columns=["Date", "Event", "Importance", "Notes"])
    df["Date"] = pd.to_datetime(df["Date"])

    return df


def upcoming(days=14, reference_date=None):
    """
    Returns events from reference_date (default: today) through
    reference_date + days, ascending by date - the CSV's fixed-date
    events plus the generated weekly Jobless Claims and monthly ISM
    PMI rows. Empty DataFrame if the CSV is missing and no generated
    rows fall in range either.
    """

    today = pd.Timestamp(reference_date) if reference_date is not None else pd.Timestamp.now().normalize()
    cutoff = today + pd.Timedelta(days=days)

    frames = [_jobless_claims_rows(today, cutoff), _ism_rows(today, cutoff)]

    if CALENDAR_PATH.exists():

        df = pd.read_csv(CALENDAR_PATH, parse_dates=["Date"])
        frames.append(df[(df["Date"] >= today) & (df["Date"] <= cutoff)])

    combined = pd.concat(frames, ignore_index=True)

    # Belt-and-suspenders, same reasoning as _ism_rows' own fix above -
    # concatenating an empty object-dtype "Date" column (from any
    # future frame source, not just ISM) with real datetime64 rows
    # silently produces an object-dtype result, breaking every caller
    # that expects to call .dt on this column.
    combined["Date"] = pd.to_datetime(combined["Date"])

    return combined.sort_values("Date")


def detail_for(event_name):
    """{"what", "watch"} for a known event name, or None if it isn't in EVENT_DETAILS (an unrecognized/future event type)."""

    return EVENT_DETAILS.get(event_name)
