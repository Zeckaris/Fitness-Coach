"""
get_recent_checkins — Reads a past check-in from MongoDB.

Read-only. Used for dates beyond the automatically provided yesterday
context.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from langchain_core.tools import tool

from db.mongo_client import get_checkins_collection
from db.guards import mongo_guarded
from auth.context import get_current_user_id



LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")


def yesterday_str() -> str:
    """Returns yesterday's date, in the user's local timezone, as 'YYYY-MM-DD'."""
    return (datetime.now(LOCAL_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")


def format_checkin(doc: Optional[dict], date: str) -> str:
    """
    Formats a single check-in document into a short human-readable summary
    for the LLM to reason over. Mirrors the plain-text style used by
    search_workout_library / search_fitness_knowledge_base.
    """
    if not doc:
        return f"No check-in recorded for {date}."

    parts = []

    if doc.get("sickness"):
        parts.append(f"sickness: {doc['sickness']}")
    if doc.get("injury"):
        parts.append(f"injury: {doc['injury']}")
    if doc.get("fatigue"):
        parts.append(f"fatigue: {doc['fatigue']}")
    if doc.get("equipment_note"):
        parts.append(f"equipment note: {doc['equipment_note']}")
    if doc.get("time_constraint_minutes") is not None:
        parts.append(f"time available: {doc['time_constraint_minutes']} min")

    beverages = doc.get("beverages_consumed")
    if beverages:
        bev_str = ", ".join(f"{b['name']} ({b['amount_ml']}ml)" for b in beverages)
        parts.append(f"beverages: {bev_str}")

    if doc.get("protein_adequate") is not None:
        parts.append(f"protein adequate: {'yes' if doc['protein_adequate'] else 'no'}")
    if doc.get("water_glasses") is not None:
        parts.append(f"water: {doc['water_glasses']} glasses")

    if not parts:
        return f"Check-in for {date}: nothing notable logged."

    return f"Check-in for {date}: " + ", ".join(parts)


def fetch_checkin(date: str) -> str:
    """
    Plain Python fetch - no LLM/tool machinery. Used directly by
    history_check_node for the deterministic once-per-session pull, so it
    never depends on the LLM deciding to call anything.

    NOT @mongo_guarded: history_check_node (agent/graph.py) is the
    "non-critical, continue" node per the V9.2 design — it needs its own
    internal try/except that swallows failure and returns a neutral
    {"yesterday_context": None} shape. Guarding here instead would leak
    a "couldn't reach your data" fallback STRING into yesterday_context,
    which then gets silently injected into the coach's system prompt as
    if it were real check-in content — the node-level catch is what's
    supposed to produce the clean None instead.
    """
    collection = get_checkins_collection()
    doc = collection.find_one({"user_id": get_current_user_id(), "date": date})
    return format_checkin(doc, date)


@tool
@mongo_guarded
def get_recent_checkins(date: str) -> str:
    """
    Look up a past check-in by date.

    Use only for dates beyond the provided yesterday context.
    Date format: YYYY-MM-DD.
    """
    return fetch_checkin(date)


# Quick manual test: python -m tools.checkin_history
if __name__ == "__main__":
    print(fetch_checkin(yesterday_str()))
    print(get_recent_checkins.invoke({"date": "2026-07-14"}))