"""
User Profile tools.

Profile creation happens from a blocking onboarding screen.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Literal

from db.mongo_client import (
    get_user_profile_collection,
    get_plans_collection,
    get_baseline_assessment_collection,
)
from auth.context import get_current_user_id


def has_user_profile(user_id: Optional[str] = None) -> bool:
    """Returns True if the user has a profile document in user_profile collection."""
    if user_id is None:
        user_id = get_current_user_id()
    collection = get_user_profile_collection()
    return collection.count_documents({"user_id": user_id}, limit=1) > 0


def get_user_profile(user_id: Optional[str] = None) -> dict | None:
    """Retrieve the current user's profile."""
    if user_id is None:
        user_id = get_current_user_id()
    collection = get_user_profile_collection()
    return collection.find_one({"user_id": user_id})


def create_user_profile(
    date_of_birth: str,
    gender: Literal["male", "female"],
    height_value: float,
    height_unit: Literal["cm", "in"] = "cm",
    weight_unit_preference: Literal["kg", "lb"] = "kg",
    user_id: Optional[str] = None,
    **kwargs,
) -> dict:
    """
    Create or overwrite the user's base profile document.
    Does NOT write experience_level directly — that is strictly mirrored
    from record_baseline_assessment().
    """
    if user_id is None:
        user_id = get_current_user_id()

    collection = get_user_profile_collection()
    now = datetime.now(ZoneInfo("UTC"))

    doc = {
        "user_id": user_id,
        "date_of_birth": date_of_birth,
        "gender": gender,
        "height": {
            "value": float(height_value),
            "unit": height_unit,
        },
        "weight_unit_preference": weight_unit_preference,
        "updated_at": now,
    }

    collection.update_one(
        {"user_id": user_id},
        {
            "$set": doc,
            "$setOnInsert": {"created_at": now, "experience_level": None},
        },
        upsert=True,
    )
    return doc


def update_user_profile(
    dob: Optional[str] = None,
    gender: Optional[str] = None,
    height: Optional[float | dict] = None,
    unit_preferences: Optional[dict] = None,
    weight_unit_preference: Optional[str] = None,
    user_id: Optional[str] = None,
) -> None:
    """
    Update partial user profile fields (DOB, gender, height, unit_preferences).
    Note: experience_level is NOT updated here. It is strictly maintained
    by the baseline_assessment tool when a new assessment is recorded.
    """
    if user_id is None:
        user_id = get_current_user_id()

    collection = get_user_profile_collection()

    update_fields = {}
    if dob is not None:
        update_fields["date_of_birth"] = dob
        update_fields["dob"] = dob
    if gender is not None:
        update_fields["gender"] = gender
    if height is not None:
        if isinstance(height, dict):
            update_fields["height"] = height
        else:
            update_fields["height"] = {"value": float(height), "unit": "cm"}
    if unit_preferences is not None:
        update_fields["unit_preferences"] = unit_preferences
    if weight_unit_preference is not None:
        update_fields["weight_unit_preference"] = weight_unit_preference

    if not update_fields:
        return

    now = datetime.now(ZoneInfo("UTC"))
    update_fields["updated_at"] = now

    collection.update_one(
        {"user_id": user_id},
        {
            "$set": update_fields,
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


def is_user_inactive(user_id: Optional[str] = None, days: int = 90) -> bool:
    """
    Check if a user has been inactive for 3+ months (default 90 days).

    Definition:
      1. Check most recent completed workout in plans collection (plans.completed_at
         or plan date if completed_at is missing).
      2. Check most recent baseline assessment (baseline_assessment.assessed_at
         or timestamp).
      3. The user's last activity timestamp is the most recent of (latest completed workout,
         latest baseline assessment).
      4. If the user has never completed a workout and never had an assessment, check
         profile creation date.
      5. If last activity was >= 90 days ago, returns True (inactive).
    """
    if user_id is None:
        user_id = get_current_user_id()

    # If the user doesn't have a profile yet, they are a first-time user
    # (handled by not has_user_profile), not 'inactive'.
    profile = get_user_profile(user_id)
    if not profile:
        return False

    now = datetime.now(ZoneInfo("UTC"))
    threshold_delta = timedelta(days=days)

    plans_col = get_plans_collection()
    last_plan = plans_col.find_one(
        {"user_id": user_id, "status": "completed"},
        sort=[("completed_at", -1), ("date", -1)],
    )

    last_active_time: Optional[datetime] = None

    if last_plan:
        c_at = last_plan.get("completed_at")
        if isinstance(c_at, datetime):
            last_active_time = c_at if c_at.tzinfo else c_at.replace(tzinfo=ZoneInfo("UTC"))
        elif isinstance(c_at, str):
            try:
                dt = datetime.fromisoformat(c_at)
                last_active_time = dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("UTC"))
            except Exception:
                pass

        if last_active_time is None and last_plan.get("date"):
            try:
                d = datetime.strptime(last_plan["date"], "%Y-%m-%d").replace(tzinfo=ZoneInfo("UTC"))
                last_active_time = d
            except Exception:
                pass

    # Check latest baseline assessment event
    assessments_col = get_baseline_assessment_collection()
    last_assessment = assessments_col.find_one(
        {"user_id": user_id},
        sort=[("assessed_at", -1), ("timestamp", -1)],
    )
    if last_assessment:
        a_time = last_assessment.get("assessed_at") or last_assessment.get("timestamp")
        if isinstance(a_time, datetime):
            a_time_tz = a_time if a_time.tzinfo else a_time.replace(tzinfo=ZoneInfo("UTC"))
            if last_active_time is None or a_time_tz > last_active_time:
                last_active_time = a_time_tz

    # If no workout and no assessment found, fallback to profile created_at
    if last_active_time is None and profile.get("created_at"):
        p_time = profile["created_at"]
        if isinstance(p_time, datetime):
            last_active_time = p_time if p_time.tzinfo else p_time.replace(tzinfo=ZoneInfo("UTC"))

    if last_active_time is None:
        return False

    return (now - last_active_time) >= threshold_delta


def is_user_inactive_3_months(user_id: Optional[str] = None) -> bool:
    """Convenience alias for is_user_inactive(user_id, days=90)."""
    return is_user_inactive(user_id=user_id, days=90)


def _sync_experience_level(experience_level: str, user_id: Optional[str] = None) -> None:
    """
    Internal function exclusively for tools.baseline_assessment to mirror
    the experience level into the user's profile.
    """
    if user_id is None:
        user_id = get_current_user_id()

    collection = get_user_profile_collection()
    now = datetime.now(ZoneInfo("UTC"))

    collection.update_one(
        {"user_id": user_id},
        {
            "$set": {"experience_level": experience_level, "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
