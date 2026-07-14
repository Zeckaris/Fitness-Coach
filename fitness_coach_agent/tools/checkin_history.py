"""
get_recent_checkins — V5's history-lookup tool.

V5 scope: reads a single day's check-in back from MongoDB, formatted for
either (a) direct injection into the system prompt by history_check_node
(deterministic, once per session, always "yesterday"), or (b) an LLM tool
call when the user's message needs a different/further-back date. Both
paths share the same underlying fetch function so there's one source of
truth for how a check-in is read and formatted.

Read-only - does not modify anything in Mongo. Pairs with tools/checkins.py,
which is the write side.

V5 fix: yesterday_str() now computes "yesterday" in the user's local
timezone (hardcoded to Africa/Addis_Ababa, matching tools/checkins.py's
_today_str()) instead of UTC. Both functions must stay in agreement on
what "today"/"yesterday" mean, or check-ins get written under one date
and looked up under another (exactly what caused the original bug).

KNOWN DEBT: same as tools/checkins.py - this hardcoded timezone needs to
become per-user once multi-user support exists.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from langchain_core.tools import tool

from db.mongo_client import get_checkins_collection

DEFAULT_USER_ID = "default_user"

# Hardcoded until per-user timezone support exists.
# Must match tools/checkins.py's LOCAL_TZ.
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
    """
    collection = get_checkins_collection()
    doc = collection.find_one({"user_id": DEFAULT_USER_ID, "date": date})
    return format_checkin(doc, date)


@tool
def get_recent_checkins(date: str) -> str:
    """
    Look up the check-in recorded for a specific past date.

    Use this when the user asks about a date beyond what you already have
    in context - e.g. "how was I doing earlier this week?", "what did I
    log on Monday?". You do NOT need to call this for yesterday's data at
    the start of a conversation - that is already provided to you
    automatically.

    Args:
        date: the date to look up, formatted "YYYY-MM-DD".

    Returns:
        A short summary of that day's check-in, or a message if none was
        recorded.
    """
    return fetch_checkin(date)


# Quick manual test: python -m tools.checkin_history
if __name__ == "__main__":
    print(fetch_checkin(yesterday_str()))
    print(get_recent_checkins.invoke({"date": "2026-07-14"}))