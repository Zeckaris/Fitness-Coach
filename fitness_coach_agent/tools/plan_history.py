"""
get_current_plan / get_past_plans — V6's plan read tools.

V6 scope: two read-only tools over the same `plans` collection that
tools/plans.py writes to (read/write split, same as
checkin_history.py / checkins.py).

- get_current_plan(): looks FORWARD - fetches whatever currently exists
  for tomorrow, day+2, day+3. Used before PATCHING, so the agent knows
  what's already there (and whether it's patching an existing entry or
  generating a fresh one for a day that has nothing yet).

- get_past_plans(): looks BACKWARD - fetches the 3 days before today.
  Used before GENERATING a fresh day's plan, purely for variety (avoid
  repeating the same exercises that were just done). Missing/nonexistent
  past days are reported as such, not treated as an error - this is
  expected and common early in the app's life.

Both are explicit LLM-callable tools, not auto-injected like
history_check_node's yesterday-checkin pull - plan work only happens on
some turns, and the agent needs to judge which (if either) is relevant.

Read-only - does not modify anything in Mongo.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from db.mongo_client import get_plans_collection

DEFAULT_USER_ID = "default_user"


LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")


def _forward_dates() -> list:
    """Tomorrow, day+2, day+3 - the plan's forward window."""
    today = datetime.now(LOCAL_TZ).date()
    return [(today + timedelta(days=offset)).strftime("%Y-%m-%d") for offset in (1, 2, 3)]


def _backward_dates() -> list:
    """The 3 days before today."""
    today = datetime.now(LOCAL_TZ).date()
    return [(today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in (1, 2, 3)]


def format_plan_day(doc: dict, date: str) -> str:
    """
    Formats a single plan-day document into a short human-readable summary.
    Mirrors checkin_history.py's format_checkin style.
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
    Fetch the existing plan for the forward window (tomorrow, day+2,
    day+3), showing whatever is currently saved for each day.

    Use this BEFORE patching the plan, so you know what's already there -
    e.g. to avoid duplicating a focus area already planned for another day,
    or to confirm whether a day needs a fresh plan (no entry yet) versus
    a patch (entry already exists).

    Returns:
        One line per day, or "no plan entry exists yet" for days with
        nothing saved.
    """
    return _fetch_window(_forward_dates())


@tool
def get_past_plans() -> str:
    """
    Fetch what was planned for the 3 days before today.

    Use this BEFORE generating a FRESH plan for a day that has no entry
    yet, purely to keep exercise selection varied - avoid assigning the
    same exercises that were just done. If those days don't exist (e.g.
    early in the app's use), this just reports that - not an error.

    Returns:
        One line per day, or "no plan entry exists yet" for days with
        nothing saved.
    """
    return _fetch_window(_backward_dates())


# Quick manual test: python -m tools.plan_history
if __name__ == "__main__":
    print("Current plan (forward):")
    print(get_current_plan.invoke({}))
    print("\nPast plans (backward):")
    print(get_past_plans.invoke({}))