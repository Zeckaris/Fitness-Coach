"""
Baseline Assessment tools.

Append-only, one doc per assessment event.
This is the source of truth for experience_level.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

from db.mongo_client import get_baseline_assessment_collection
from auth.context import get_current_user_id
from tools.user_profile import _sync_experience_level


def record_baseline_assessment(
    experience_level: str,
    answers: Optional[dict] = None,
    user_id: Optional[str] = None,
    **kwargs,
) -> dict:
    """
    Record a new baseline assessment event and mirror the new experience_level
    to the user_profile collection.
    """
    if user_id is None:
        user_id = get_current_user_id()

    collection = get_baseline_assessment_collection()
    now = datetime.now(ZoneInfo("UTC"))

    raw_answers = answers if answers is not None else kwargs.get("assessment_data", {})

    doc = {
        "user_id": user_id,
        "assessed_at": now,
        "timestamp": now,
        "experience_level": experience_level,
        "answers": raw_answers,
    }

    # Append-only
    collection.insert_one(doc)

    # Exactly one write path to update the mirror on user_profile
    _sync_experience_level(experience_level, user_id=user_id)
    return doc


def get_latest_baseline_assessment(user_id: Optional[str] = None) -> dict | None:
    """Retrieve the latest baseline assessment for the user."""
    if user_id is None:
        user_id = get_current_user_id()
    collection = get_baseline_assessment_collection()
    return collection.find_one(
        {"user_id": user_id},
        sort=[("assessed_at", -1), ("timestamp", -1)],
    )
