"""Centrelink mutual-obligation periods: pure date maths, no DB.

JobSeeker asks for a number of applications (default 20) in each one-month period. The
user sets the date one period starts (``obligation_cycle_start``); periods run monthly
from it in BOTH directions, so changing the date re-buckets the whole history the same
way. A start on the 29th-31st clamps to the last day of shorter months (31 Jan -> 28 Feb
-> 31 Mar), always measured from the anchor's own day, never chained.

A period ends the day before the next one starts. An application counts on its LOCAL
calendar date (server local time), the same rule as ``app/llm/usage.py``.
"""

from __future__ import annotations

import calendar
import datetime

Period = tuple[datetime.date, datetime.date]  # (start, end), both inclusive


def add_months(anchor: datetime.date, k: int) -> datetime.date:
    """``anchor`` moved ``k`` months (negative = back), its day clamped to the month."""
    index = anchor.year * 12 + (anchor.month - 1) + k
    year, month = divmod(index, 12)
    month += 1
    return datetime.date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def period_for(d: datetime.date, anchor: datetime.date) -> Period:
    """The period holding ``d`` when periods start on ``anchor``'s day each month."""
    k = (d.year - anchor.year) * 12 + (d.month - anchor.month)
    if d < add_months(anchor, k):
        k -= 1
    return add_months(anchor, k), add_months(anchor, k + 1) - datetime.timedelta(days=1)


def calendar_period_for(d: datetime.date) -> Period:
    """The calendar month holding ``d``: the fallback before a start date is set."""
    return d.replace(day=1), d.replace(day=calendar.monthrange(d.year, d.month)[1])


def bucket_for(anchor: datetime.date | None):
    """The function mapping a local date to its period, for this anchor (or none)."""
    if anchor is None:
        return calendar_period_for
    return lambda d: period_for(d, anchor)


def to_local_date(dt: datetime.datetime) -> datetime.date:
    """The local calendar date of a stored timestamp. SQLite hands back naive values
    (UTC by convention here), Postgres aware ones."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone().date()


def local_today() -> datetime.date:
    return to_local_date(datetime.datetime.now(datetime.timezone.utc))
