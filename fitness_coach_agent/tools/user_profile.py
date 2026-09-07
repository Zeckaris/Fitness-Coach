"""
User Profile tools.

Profile creation happens from a blocking onboarding screen.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Literal, List
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from db.mongo_client import (
    get_user_profile_collection,
    get_plans_collection,
    get_baseline_assessment_collection,
)
from auth.context import get_current_user_id

_NO_EQUIPMENT_TOKENS = {"body weight", "none", "bodyweight"}


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
    equipment: Optional[list[str]] = None,
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
        "equipment": [e.strip().lower() for e in (equipment or []) if e.strip().lower() not in _NO_EQUIPMENT_TOKENS],
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
    equipment: Optional[list[str]] = None,
    user_id: Optional[str] = None,
) -> None:
    """
    Update partial user profile fields (DOB, gender, height, unit_preferences, equipment).
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
    if equipment is not None:
        update_fields["equipment"] = [e.strip().lower() for e in equipment if e.strip().lower() not in _NO_EQUIPMENT_TOKENS]

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


def resolve_user_equipment(user_id: Optional[str] = None) -> tuple[Optional[list[str]], dict]:
    """
    Resolves the effective equipment for a user with precedence:
    1. Active, unexpired equipment_override:
       - mode='all' -> None (unrestricted, skip filtering).
       - mode='custom' -> list[str] of equipment tokens.
    2. Fallback to permanent profile equipment (list[str], default [] for bodyweight only).

    Returns:
      (resolved_equipment, info_dict)
      where resolved_equipment is None (unrestricted) or a list of lower-case equipment strings.
    """
    if user_id is None:
        user_id = get_current_user_id()

    profile = get_user_profile(user_id)
    if not profile:
        return ([], {"mode": "permanent", "active_override": False})

    override = profile.get("equipment_override")
    if isinstance(override, dict):
        expires_at = override.get("expires_at")
        now = datetime.now(ZoneInfo("UTC"))
        is_active = True

        if isinstance(expires_at, datetime):
            exp_tz = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=ZoneInfo("UTC"))
            if exp_tz <= now:
                is_active = False
        elif isinstance(expires_at, str):
            try:
                exp_tz = datetime.fromisoformat(expires_at)
                if exp_tz.tzinfo is None:
                    exp_tz = exp_tz.replace(tzinfo=ZoneInfo("UTC"))
                if exp_tz <= now:
                    is_active = False
            except Exception:
                pass

        if is_active:
            mode = override.get("mode", "custom")
            if mode == "all":
                return (None, {"mode": "override_all", "active_override": True, "expires_at": expires_at})
            else:
                eq_list = override.get("equipment")
                if eq_list is None:
                    eq_list = []
                else:
                    eq_list = [e.strip().lower() for e in eq_list if e.strip().lower() not in _NO_EQUIPMENT_TOKENS]
                return (eq_list, {"mode": "override_custom", "active_override": True, "expires_at": expires_at})

    perm_eq = profile.get("equipment")
    if perm_eq is None:
        perm_eq = []
    else:
        perm_eq = [e.strip().lower() for e in perm_eq if e.strip().lower() not in _NO_EQUIPMENT_TOKENS]
    return (perm_eq, {"mode": "permanent", "active_override": False})


class SetEquipmentOverrideInput(BaseModel):
    mode: Literal["custom", "all"] = Field(
        default="custom",
        description=(
            "Override mode: 'custom' to set a temporary specific list of equipment "
            "(e.g. [] for bodyweight/traveling), or 'all' to temporarily grant unrestricted equipment access."
        ),
    )
    equipment: Optional[List[str]] = Field(
        default=None,
        description=(
            "List of available equipment tokens when mode is 'custom', e.g. ['dumbbells']. "
            "Defaults to [] (bodyweight only) if omitted under mode='custom'. Ignored if mode='all'. "
            "Allowed vocabulary: ab_wheel, bench, dumbbells, kettlebell, pull_up_bar, resistance_band, rope, stability_ball."
        ),
    )
    duration_days: Optional[float] = Field(
        default=7.0,
        description="Duration of the override in days (e.g. 1 for a day, 7 for a week, 30 for a month). Defaults to 7 days.",
    )
    clear: Optional[bool] = Field(
        default=False,
        description="Set to True to immediately clear/cancel any active temporary equipment override.",
    )
    reason: Optional[str] = Field(
        default=None,
        description="Optional description of why the override is set (e.g. 'traveling for a week').",
    )


@tool(args_schema=SetEquipmentOverrideInput)
def set_equipment_override(
    mode: Literal["custom", "all"] = "custom",
    equipment: Optional[List[str]] = None,
    duration_days: Optional[float] = 7.0,
    clear: Optional[bool] = False,
    reason: Optional[str] = None,
) -> str:
    """Set or clear a temporary equipment access override for the user."""
    user_id = get_current_user_id()
    collection = get_user_profile_collection()
    now = datetime.now(ZoneInfo("UTC"))

    if clear:
        collection.update_one(
            {"user_id": user_id},
            {"$unset": {"equipment_override": ""}, "$set": {"updated_at": now}}
        )
        return "Temporary equipment override cleared. Reverted to permanent equipment profile."

    dur = duration_days if (duration_days is not None and duration_days > 0) else 7.0
    expires_at = now + timedelta(days=dur)

    if mode == "custom":
        clean_eq = [e.strip().lower() for e in (equipment or []) if e.strip().lower() not in _NO_EQUIPMENT_TOKENS]
        override_doc = {
            "mode": "custom",
            "equipment": clean_eq,
            "duration_days": dur,
            "set_at": now,
            "expires_at": expires_at,
            "reason": reason,
        }
        eq_desc = ", ".join(clean_eq) if clean_eq else "none (bodyweight only)"
        msg = (
            f"Temporary equipment override set to custom list [{eq_desc}] for {dur:g} day(s) "
            f"(expires {expires_at.strftime('%Y-%m-%d %H:%M UTC')})."
        )
    else:
        override_doc = {
            "mode": "all",
            "equipment": None,
            "duration_days": dur,
            "set_at": now,
            "expires_at": expires_at,
            "reason": reason,
        }
        msg = (
            f"Temporary equipment override set to 'all' (unrestricted access) for {dur:g} day(s) "
            f"(expires {expires_at.strftime('%Y-%m-%d %H:%M UTC')})."
        )

    collection.update_one(
        {"user_id": user_id},
        {"$set": {"equipment_override": override_doc, "updated_at": now}},
        upsert=True
    )
    return msg


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
