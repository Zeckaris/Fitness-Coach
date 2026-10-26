"""
Calendar-aligned week boundary utilities — v1.8.

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


def today_local() -> date:
    """Return today's date in the local timezone."""
    return datetime.now(LOCAL_TZ).date()


def _coerce_date(reference=None) -> date:
    """Convert None/date/datetime/YYYY-MM-DD into a local date."""
    if reference is None:
        return today_local()
    if isinstance(reference, datetime):
        return reference.date()
    if isinstance(reference, date):
        return reference
    return datetime.strptime(reference, "%Y-%m-%d").date()


def get_plannable_window(reference=None) -> dict:
    """
    Return the plannable window for a reference date (default: today).

    The window is always the reference date through the end of its
    calendar-aligned month week. This is the single source of truth for
    every read/write path that decides which forward dates are visible.
    """
    ref = _coerce_date(reference)
    ref_str = ref.strftime("%Y-%m-%d")
    week = get_week_for_date(ref_str)
    dates = [d for d in date_range(week["start_date"], week["end_date"]) if d >= ref_str]
    return {
        "today": ref_str,
        "weekday": ref.strftime("%A"),
        "start_date": dates[0],
        "end_date": dates[-1],
        "dates": dates,
    }


def get_plannable_dates(reference=None) -> list[str]:
    """Return every plannable YYYY-MM-DD date for a reference date (default: today)."""
    return get_plannable_window(reference)["dates"]


def get_last_week_info(reference=None) -> Optional[dict]:
    """
    If the reference date (default: today) falls in the last calendar-aligned
    week of its month, return a dict with:
        {
            "month_id": str,          # YYYY-MM of the current month
            "next_month_id": str,     # YYYY-MM of the following month
            "remaining_dates": list,  # YYYY-MM-DD strings from today through month end
            "last_week_start": str,   # start date of the last week
            "last_week_end": str,     # end date of the last week (= month end)
        }
    Returns None if the date is NOT in the last week.
    """
    ref = _coerce_date(reference)
    month_id = ref.strftime("%Y-%m")
    weeks = compute_month_weeks(month_id)
    if not weeks:
        return None
    last_week = weeks[-1]
    last_start = datetime.strptime(last_week["start_date"], "%Y-%m-%d").date()
    last_end = datetime.strptime(last_week["end_date"], "%Y-%m-%d").date()
    if not (last_start <= ref <= last_end):
        return None
    # Next month
    year, month = map(int, month_id.split("-"))
    month += 1
    if month > 12:
        month = 1
        year += 1
    next_month_id = f"{year}-{month:02d}"
    remaining_dates = date_range(
        ref.strftime("%Y-%m-%d"),
        last_week["end_date"],
    )
    return {
        "month_id": month_id,
        "next_month_id": next_month_id,
        "remaining_dates": remaining_dates,
        "last_week_start": last_week["start_date"],
        "last_week_end": last_week["end_date"],
    }


def format_coach_date_context(window: Optional[dict] = None) -> str:
    """Render the date/window context injected into every coach turn."""
    w = window or get_plannable_window()
    base = (
        f"CURRENT DATE: Today is {w['weekday']}, {w['today']} "
        f"(Africa/Addis_Ababa). Plannable window: {w['start_date']} "
        f"through {w['end_date']}."
    )
    lw = get_last_week_info()
    if lw:
        remaining_str = ", ".join(lw["remaining_dates"])
        base += (
            f"\nLAST_WEEK_OF_MONTH=TRUE: Today is in the last calendar week of "
            f"{lw['month_id']} (week runs {lw['last_week_start']} to "
            f"{lw['last_week_end']}). Remaining dates this month: {remaining_str}. "
            f"Next month: {lw['next_month_id']}."
        )
    return base


def current_month_id() -> str:
    """Return today's YYYY-MM in the local timezone."""
    return today_local().strftime("%Y-%m")


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
# Mid-month goal scaling helpers
# ---------------------------------------------------------------------------

def days_in_month(month_id: str) -> int:
    """Return the total number of calendar days in the given YYYY-MM month."""
    year, month = map(int, month_id.split("-"))
    return calendar.monthrange(year, month)[1]


def compute_goal_scaling(start_date: date, month_id: str) -> dict:
    """
    Compute the scaling ratio for a mid-month goal start.

    Returns:
        {
            "scale": float,           # days_remaining / days_in_month  (1.0 for day 1)
            "days_remaining": int,    # start_date through last day of month, inclusive
            "days_in_month": int,     # total days in the month
            "goal_start_date": str,   # YYYY-MM-DD of the start_date
        }

    ``start_date`` must be a date object.  ``month_id`` must match the
    month containing ``start_date`` — the caller is responsible for this;
    no guard is applied here to keep the function pure.
    """
    total_days = days_in_month(month_id)
    year, month = map(int, month_id.split("-"))
    last_day = date(year, month, total_days)
    remaining = (last_day - start_date).days + 1  # inclusive
    remaining = max(1, min(remaining, total_days))  # clamp
    scale = remaining / total_days
    return {
        "scale": scale,
        "days_remaining": remaining,
        "days_in_month": total_days,
        "goal_start_date": start_date.strftime("%Y-%m-%d"),
    }


def is_last_week_of_month(ref_date: date, month_id: str) -> bool:
    """
    Return True if ref_date falls within the LAST calendar-aligned week of
    month_id (as computed by compute_month_weeks).

    This is the cutoff condition: goals set in the last week are redirected
    to the following month so the user begins with a full periodized cycle.
    """
    weeks = compute_month_weeks(month_id)
    if not weeks:
        return False
    last_week = weeks[-1]
    last_start = datetime.strptime(last_week["start_date"], "%Y-%m-%d").date()
    last_end = datetime.strptime(last_week["end_date"], "%Y-%m-%d").date()
    return last_start <= ref_date <= last_end


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
