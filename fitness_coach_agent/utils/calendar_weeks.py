"""
Calendar-aligned week boundary utilities — v1.7.

Single source of truth for all week/month date arithmetic in the system.
Every other module that previously computed week boundaries independently
(tools/week_plans.py, tools/plans.py, tools/month_plans.py, etc.) must
import from here instead.

Design:
  - Week 1: month_start through first Saturday on-or-after month_start.
    Special case: if month_start IS a Sunday, week 1 = that single day
    (immediately subject to the merge rule).
  - Middle weeks: full Sunday-Saturday spans, entirely within the month.
  - Last week: Sunday-after-last-full-week through month_end.
  - Merge rule: if week 1 or last week has only 1-2 days, it is merged
    into its adjacent week. Result: all weeks are 3-9 days long.

No I/O. No Mongo. No decorators. All functions are pure and deterministic.
"""

import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")


# ---------------------------------------------------------------------------
# Core boundary computation
# ---------------------------------------------------------------------------

def compute_month_weeks(month_id: str) -> list[dict]:
    """
    Return an ordered list of week descriptors for the given month.

    Each descriptor:
        {
            "week_number": int,   # 1-indexed within the month
            "start_date":  str,   # YYYY-MM-DD (first day of this week in the month)
            "end_date":    str,   # YYYY-MM-DD (last day of this week in the month)
            "day_count":   int,   # number of calendar days in this week (always 3-9)
        }

    Invariants guaranteed:
        - Every calendar day in the month appears in exactly one descriptor.
        - No descriptor spans two calendar months.
        - Every descriptor has day_count >= 3.
        - sum(day_count) == days in month.
    """
    year, month = map(int, month_id.split("-"))
    month_start = date(year, month, 1)
    days_in_month = calendar.monthrange(year, month)[1]
    month_end = date(year, month, days_in_month)

    # Build raw segments iteratively.
    # Each segment is [start, end] (mutable lists for in-place merge edits).
    segments: list[list[date]] = []
    current = month_start

    while current <= month_end:
        # Special case: if the first day of the month is a Sunday, that
        # Sunday alone forms the first (tiny) segment.  The generic formula
        # would extend it to the following Saturday (7 days), which is wrong.
        if current == month_start and current.weekday() == 6:  # Sunday
            seg_end = current  # 1-day segment; merge rule will absorb it
        else:
            # Extend to the next Saturday (or month_end, whichever is earlier).
            days_to_saturday = (5 - current.weekday()) % 7
            seg_end = min(current + timedelta(days=days_to_saturday), month_end)

        segments.append([current, seg_end])
        current = seg_end + timedelta(days=1)

    # --- Merge rule (first segment) ---
    if len(segments) >= 2:
        first_len = (segments[0][1] - segments[0][0]).days + 1
        if first_len <= 2:
            # Pull the first segment's start into the second segment.
            segments[1][0] = segments[0][0]
            segments.pop(0)

    # --- Merge rule (last segment) ---
    if len(segments) >= 2:
        last_len = (segments[-1][1] - segments[-1][0]).days + 1
        if last_len <= 2:
            # Extend the second-to-last segment's end to cover the last one.
            segments[-2][1] = segments[-1][1]
            segments.pop()

    # Convert to typed dicts.
    return [
        {
            "week_number": i + 1,
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
            "day_count": (end - start).days + 1,
        }
        for i, (start, end) in enumerate(segments)
    ]


# ---------------------------------------------------------------------------
# Derived helpers
# ---------------------------------------------------------------------------

def get_week_for_date(date_str: str) -> dict:
    """
    Return the week descriptor that contains date_str.

    Raises ValueError if the date is somehow not found (should never happen
    for a valid YYYY-MM-DD date).
    """
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    month_id = month_id_for_date(date_str)
    for week in compute_month_weeks(month_id):
        start = datetime.strptime(week["start_date"], "%Y-%m-%d").date()
        end = datetime.strptime(week["end_date"], "%Y-%m-%d").date()
        if start <= d <= end:
            return week
    raise ValueError(
        f"Date {date_str} not found in any computed week of {month_id}. "
        "This should never happen — please file a bug."
    )


def weeks_in_month(month_id: str) -> int:
    """Return the total number of calendar-aligned weeks in the given month."""
    return len(compute_month_weeks(month_id))


def month_id_for_date(date_str: str) -> str:
    """
    Return the YYYY-MM month_id for a specific date string.

    Unlike _current_month_id() (which snapshots today at call time), this
    function is deterministic for any given date — use it wherever the
    month must be derived from a specific date, not from today's clock.
    """
    return date_str[:7]  # "YYYY-MM-DD"[:7] == "YYYY-MM"


def current_month_id() -> str:
    """Return today's YYYY-MM in the local timezone."""
    return datetime.now(LOCAL_TZ).date().strftime("%Y-%m")


def date_range(start_date_str: str, end_date_str: str) -> list[str]:
    """
    Return an ordered list of YYYY-MM-DD strings from start_date_str through
    end_date_str, inclusive.  Useful for iterating over a week's actual days.
    """
    start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    out = []
    cur = start
    while cur <= end:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Quick smoke-test (run as __main__)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_months = [
        "2026-09",  # Sep 2026: starts Tuesday -> expect 5 weeks
        "2026-10",  # Oct 2026: starts Thursday -> expect 5 weeks
        "2026-11",  # Nov 2026: starts Sunday  -> expect 4 weeks (merge first+last)
        "2026-02",  # Feb 2026: starts Sunday  -> expect 4 weeks
        "2026-03",  # Mar 2026: starts Sunday  -> expect 5 weeks
        "2026-01",  # Jan 2026: starts Thursday -> expect 5 weeks
    ]

    for mid in test_months:
        weeks = compute_month_weeks(mid)
        total_days = sum(w["day_count"] for w in weeks)
        import calendar as _cal
        y, m = map(int, mid.split("-"))
        expected_days = _cal.monthrange(y, m)[1]
        status = "OK" if total_days == expected_days else f"BAD (got {total_days}, want {expected_days})"
        print(f"\n{mid} ({weeks[-1]['week_number']} weeks) [{status}]:")
        for w in weeks:
            print(f"  W{w['week_number']}: {w['start_date']} - {w['end_date']} ({w['day_count']}d)")
