"""
get_current_plan / get_past_plans — V6 plan read tools.

Read-only tools for the plans collection.

- get_current_plan(): returns tomorrow, day+2, and day+3 entries.
- get_past_plans(): returns the previous 3 days of plans.

Used for plan generation and updates. Missing entries are returned as
empty rather than treated as errors.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from db.mongo_client import get_plans_collection

DEFAULT_USER_ID = "default_user"


LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")


def _forward_dates() -> list:
    """Return tomorrow, day+2, and day+3."""
    today = datetime.now(LOCAL_TZ).date()
    return [(today + timedelta(days=offset)).strftime("%Y-%m-%d") for offset in (1, 2, 3)]


def _backward_dates() -> list:
    """Return the previous 3 days."""
    today = datetime.now(LOCAL_TZ).date()
    return [(today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in (1, 2, 3)]


def format_plan_day(doc: dict, date: str) -> str:
    """
    Format one plan entry as a readable summary.
    """
    if not doc:
        return f"{date}: no plan entry exists yet."

    if doc.get("status") == "rest":
        return f"{date}: Rest day."

    parts = [f"focus: {doc.get('focus_area', 'unspecified')}"]

    if doc.get("duration_minutes") is not None:
        parts.append(f"duration: {doc['duration_minutes']} min")

    exercises = doc.get("exercises") or []
    if exercises:
        ex_str = ", ".join(
            f"{e['name']} ({e.get('sets', '?')}x{e.get('reps', '?')})" for e in exercises
        )
        parts.append(f"exercises: {ex_str}")

    if doc.get("avoid_body_parts"):
        parts.append(f"avoiding: {', '.join(doc['avoid_body_parts'])}")

    if doc.get("notes"):
        parts.append(f"notes: {doc['notes']}")

    return f"{date}: " + ", ".join(parts)


def _fetch_window(dates: list) -> str:
    collection = get_plans_collection()
    lines = []
    for date in dates:
        doc = collection.find_one({"user_id": DEFAULT_USER_ID, "date": date})
        lines.append(format_plan_day(doc, date))
    return "\n".join(lines)


@tool
def get_current_plan() -> str:
    """
    Fetch saved plans for tomorrow, day+2, and day+3.

    Returns:
        One line per day or no-entry status.
    """
    return _fetch_window(_forward_dates())


@tool
def get_past_plans() -> str:
    """
    Fetch plans from the previous 3 days.

    Returns:
        One line per day or no-entry status.
    """
    return _fetch_window(_backward_dates())


# Quick manual test: python -m tools.plan_history
if __name__ == "__main__":
    print("Current plan (forward):")
    print(get_current_plan.invoke({}))
    print("\nPast plans (backward):")
    print(get_past_plans.invoke({}))