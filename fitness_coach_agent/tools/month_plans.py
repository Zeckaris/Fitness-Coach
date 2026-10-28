"""Month plan tools — V8."""

import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Optional, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

from db.mongo_client import get_month_plans_collection
from db.guards import mongo_guarded
from auth.context import get_current_user_id
from typing import List, Optional, Literal, Tuple
from utils.baseline_targets import (
    calculate_exercise_month_target,
    FOCUS_VOLUME_MULTIPLIER,
    MAINTENANCE_VOLUME_MULTIPLIER,
    _is_timed_unit,
    WeekThemeEnum,
)
from utils.calendar_weeks import (
    weeks_in_month,
    current_month_id,
    compute_goal_scaling,
    is_last_week_of_month,
    today_local,
    days_in_month,
    compute_month_weeks,
)
from tools.user_profile import get_user_profile, get_progression_multipliers, resolve_user_equipment
from tools.baseline_assessment import get_latest_baseline_assessment
from utils.goal_composition import (
    compose_goal_exercise_list,
    is_cardio_exercise,
    VALID_FOCUSES,
    GROUP_TO_BALANCE_AREA,
    _matches_equipment,
)

LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")


BALANCE_AREAS = {"upper_body", "lower_body", "core", "cardio"}

_WORKOUTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "workouts.json"
)

with open(_WORKOUTS_PATH, "r") as f:
    _workouts_data = json.load(f)

_WORKOUTS = (
    _workouts_data["exercises"]
    if isinstance(_workouts_data, dict) and "exercises" in _workouts_data
    else _workouts_data
)

_VALID_EXERCISE_NAMES = {w["name"] for w in _WORKOUTS}
_WORKOUTS_BY_NAME = {w["name"]: w for w in _WORKOUTS}

# Units that represent continuous distance cardio (e.g. running), where the
# onboarding run_distance_km marker is a valid baseline. Any other unit
# (reps, seconds, reps_per_side, ...) means the exercise is rep/time based and
# must NOT be seeded from a running-distance marker.
_DISTANCE_UNITS = {"km", "mi", "m"}


def _exercise_unit(exercise: str, fallback_unit: str) -> str:
    """Return the exercise's canonical unit from the workout library, falling
    back to the caller-supplied unit when the exercise isn't in the library
    (e.g. distance-based 'Continuous Run' is not stored in workouts.json)."""
    workout = _WORKOUTS_BY_NAME.get(exercise)
    unit = (workout or {}).get("baseline", {}).get("unit")
    return unit if unit else fallback_unit


def _is_distance_unit(unit: str) -> bool:
    """True if the unit represents continuous distance (km/mi/m)."""
    return unit in _DISTANCE_UNITS


def _previous_month_id() -> str:
    today = datetime.now(LOCAL_TZ).date()
    first_day = today.replace(day=1)
    prev_month = first_day - timedelta(days=1)
    return prev_month.strftime("%Y-%m")


def _next_month_id(from_month_id: Optional[str] = None) -> str:
    """Return the YYYY-MM month following from_month_id (default: current month)."""
    mid = from_month_id or _current_month_id()
    year, month = map(int, mid.split("-"))
    month += 1
    if month > 12:
        month = 1
        year += 1
    return f"{year}-{month:02d}"


def get_previous_month_review_context() -> Optional[str]:
    """Coaching context from last month's close-out, for goal_context_node. None if none exists."""
    collection = get_month_plans_collection()
    doc = collection.find_one({"user_id": get_current_user_id(), "month_id": _previous_month_id()})
    if not doc:
        return None
    close_out = doc.get("close_out_summary") or {}
    return close_out.get("coaching_context") or None


def _current_month_id() -> str:
    return current_month_id()


class VolumeTarget(BaseModel):
    exercise: str = Field(
        description="Exercise name. Must exist in the workout library. "
        "Call search_workout_library first to find valid exercise names."
    )
    unit: str
    month_target: float
    balance_area: Literal["upper_body", "lower_body", "core", "cardio"] = Field(
        description="Which of the 4 body balance areas this exercise covers."
    )

    @model_validator(mode="after")
    def _validate_exercise_in_library(self):
        if self.exercise not in _VALID_EXERCISE_NAMES:
            valid_list = ", ".join(sorted(_VALID_EXERCISE_NAMES)[:10]) + "..."
            raise ValueError(
                f"Exercise '{self.exercise}' not found in workout library. "
                f"Call search_workout_library to find valid exercises. "
                f"Examples: {valid_list}"
            )
        # The workout library is the single source of truth for the unit of a
        # stored volume target. Whatever unit the LLM passed in is overwritten
        # by the exercise's canonical unit so the saved/displayed target is
        # never mislabelled (e.g. Jump Rope must be 'reps', never 'seconds').
        self.unit = _exercise_unit(self.exercise, self.unit)
        if self.unit in ("km", "mi", "m") and self.balance_area != "cardio":
            raise ValueError(
                f"Continuous unit '{self.unit}' can only be used with balance_area='cardio'. "
                f"For {self.balance_area}, use 'reps' or 'seconds'."
            )
        return self


class StageMonthGoalInput(BaseModel):
    description: str = Field(description="e.g. 'Build upper body strength', 'Lose 2kg'")
    focus: Optional[Literal["upper_body", "lower_body", "core", "cardio", "balanced"]] = Field(
        default=None,
        description=(
            "The user's stated focus area: 'upper_body', 'lower_body', 'core', 'cardio', or 'balanced'. "
            "Pass 'balanced' if the user stated none. When volume_targets is omitted, the code "
            "automatically composes the exercise list and calculates monthly volume targets."
        ),
    )
    metric_name: Optional[str] = Field(default=None, description="e.g. 'body_weight'")
    target_value: Optional[float] = Field(default=None, description="Target change/value")
    unit: Optional[str] = Field(default=None)
    baseline_value: Optional[float] = Field(default=None, description="Current value")
    volume_targets: Optional[List[VolumeTarget]] = Field(
        default=None, description="List of {exercise, unit, month_target, balance_area}. Omit to compose automatically."
    )
    month_id: Optional[str] = Field(
        default=None,
        description=(
            "Target month in YYYY-MM format. Defaults to current month. "
            "When the system detects the user is in the last calendar week of the "
            "current month it automatically redirects the goal to the following month "
            "so they begin with a full periodized cycle — do not set this manually "
            "in that case."
        ),
    )

    @model_validator(mode="after")
    def _validate(self):
        has_metric = self.metric_name is not None
        if has_metric and (self.target_value is None or self.unit is None or self.baseline_value is None):
            raise ValueError("metric_name needs target_value, unit, baseline_value.")
        return self


class CalculateVolumeTargetInput(BaseModel):
    exercise: str = Field(description="Exercise name, must exist in the workout library.")
    unit: str = Field(description="'reps', 'seconds', or 'km'.")
    balance_area: Literal["upper_body", "lower_body", "core", "cardio"]
    baseline_value: Optional[float] = Field(
        default=None,
        description="The user's stated single-set/single-effort max for this movement "
        "(e.g. 30 for '30 pushups no rest', 5 for '5km run'). Omit this field entirely "
        "if the user never stated a baseline for this movement — do NOT guess a number; "
        "a conservative beginner default will be used automatically.",
    )
    experience_level: Optional[Literal["beginner", "intermediate", "advanced"]] = Field(
        default=None,
        description="Deprecated/ignored: experience_level is now read authoritatively from the stored user profile.",
    )
    sessions_per_week: int = Field(default=4, description="Training sessions per week for this exercise.")
    sets_per_session: Optional[int] = Field(
        default=None, description="Working sets per session. Omit to use a sensible default."
    )
    month_id: Optional[str] = Field(
        default=None, description="Target month in YYYY-MM format. Defaults to current month."
    )


def _compute_volume_target_for_exercise(
    exercise: str,
    balance_area: Literal["upper_body", "lower_body", "core", "cardio"],
    unit: Optional[str] = None,
    baseline_value: Optional[float] = None,
    month_id: Optional[str] = None,
    user_id: Optional[str] = None,
    sessions_per_week: int = 4,
    sets_per_session: Optional[int] = None,
    volume_multiplier: float = 1.0,
) -> dict:
    target_month = month_id or _current_month_id()

    workout = _WORKOUTS_BY_NAME.get(exercise)
    canonical_unit = (workout or {}).get("baseline", {}).get("unit", unit or "reps")
    resolved_unit = _exercise_unit(exercise, unit or canonical_unit)

    try:
        resolved_uid = user_id or get_current_user_id()
    except Exception:
        resolved_uid = user_id

    profile = get_user_profile(resolved_uid) if resolved_uid else None
    stored_exp = profile.get("experience_level") if profile else None
    if not stored_exp:
        stored_exp = "beginner"

    raw_markers = None
    if resolved_uid:
        try:
            assessment = get_latest_baseline_assessment(resolved_uid)
            raw_markers = (assessment or {}).get("answers", {}).get("raw_markers")
        except Exception:
            raw_markers = None

    stored_multipliers = (profile or {}).get("progression_multipliers") or {}
    try:
        progression_multiplier = float(stored_multipliers.get(balance_area, 1.0))
    except (TypeError, ValueError):
        progression_multiplier = 1.0

    result = calculate_exercise_month_target(
        exercise=exercise,
        unit=resolved_unit,
        balance_area=balance_area,
        experience_level=stored_exp,
        workout=workout,
        baseline_value=baseline_value,
        raw_markers=raw_markers,
        sessions_per_week=sessions_per_week,
        sets_per_session=sets_per_session,
        weeks_in_month=weeks_in_month(target_month),
        progression_multiplier=progression_multiplier,
        volume_multiplier=volume_multiplier,
    )

    return {
        "exercise": exercise,
        "unit": resolved_unit,
        "month_target": result["month_target"],
        "balance_area": balance_area,
        "stored_exp": stored_exp,
        "baseline_source": result["baseline_source"],
        "progression_multiplier": progression_multiplier,
        "difficulty_factor": result["difficulty_factor"],
        "category": result["category"],
        "calculation_result": result,
    }


def _role_for_area(focus: Optional[str], balance_area: Optional[str]) -> str:
    clean_focus = (focus or "balanced").strip().lower()
    if clean_focus == "balanced":
        return "balanced"
    return "focus" if balance_area == clean_focus else "maintenance"


def _volume_multiplier_for_role(role: str) -> float:
    if role == "maintenance":
        return MAINTENANCE_VOLUME_MULTIPLIER
    if role == "focus":
        return FOCUS_VOLUME_MULTIPLIER
    return 1.0


def _unit_family(unit: Optional[str]) -> str:
    if unit in ("km", "mi", "m"):
        return "distance"
    if _is_timed_unit(unit or ""):
        return "time"
    return "reps"


def _round_target_for_unit(value: float, unit: Optional[str]) -> float:
    return round(value, 1) if _unit_family(unit) == "distance" else int(round(value))


def _cap_maintenance_target(comp: dict, focus_targets: List[dict]) -> dict:
    """Keep a maintenance exercise below the focus exercises of the same unit
    family. Without this, a naturally high baseline (e.g. squats) could make a
    half-volume maintenance movement exceed a focus movement of similar
    difficulty."""
    family = _unit_family(comp.get("unit"))
    refs = [
        float(f["month_target"]) for f in focus_targets
        if _unit_family(f.get("unit")) == family
    ]
    if not refs:
        return comp
    cap = max(refs) * MAINTENANCE_VOLUME_MULTIPLIER
    if float(comp["month_target"]) > cap:
        comp["month_target"] = _round_target_for_unit(cap, comp.get("unit"))
        comp["calculation_result"]["month_target"] = comp["month_target"]
    return comp


@tool(args_schema=CalculateVolumeTargetInput)
def calculate_volume_target(
    exercise: str,
    unit: str,
    balance_area: Literal["upper_body", "lower_body", "core", "cardio"],
    baseline_value: Optional[float] = None,
    experience_level: Optional[Literal["beginner", "intermediate", "advanced"]] = None,
    sessions_per_week: int = 4,
    sets_per_session: Optional[int] = None,
    month_id: Optional[str] = None,
) -> str:
    """
    Optional helper for an ad-hoc single-exercise estimate. Do NOT use this
    during monthly goal setup: call stage_month_goal with the user's focus and
    omit volume_targets, and the code composes every exercise and computes its
    own target. Pass the user's stated baseline for this movement if they gave
    one; omit baseline_value if they didn't (a beginner default will be used).
    """
    profile = get_user_profile()
    stored_exp = profile.get("experience_level") if profile else None
    if not stored_exp:
        raise RuntimeError(
            "No stored experience_level found in user profile. User must complete onboarding baseline assessment first."
        )

    comp = _compute_volume_target_for_exercise(
        exercise=exercise,
        balance_area=balance_area,
        unit=unit,
        baseline_value=baseline_value,
        month_id=month_id,
        sessions_per_week=sessions_per_week,
        sets_per_session=sets_per_session,
    )
    result = comp["calculation_result"]
    multiplier_note = (
        f", progression_multiplier: {comp['progression_multiplier']:.3g}"
        if comp["progression_multiplier"] != 1.0
        else ""
    )
    return (
        f"month_target for {exercise}: {result['month_target']} {comp['unit']}. "
        f"(baseline used: {result['baseline_used']} [{comp['baseline_source']}], "
        f"experience: {comp['stored_exp']}, "
        f"intensity: {result['intensity_factor']}, "
        f"{result['sets_per_session']} sets x {result['sessions_per_week']} sessions/week "
        f"x {result['weeks_in_month']:.2f} weeks{multiplier_note})"
    )


@tool(args_schema=StageMonthGoalInput)
@mongo_guarded
def stage_month_goal(
    description: str,
    metric_name: Optional[str] = None,
    target_value: Optional[float] = None,
    unit: Optional[str] = None,
    baseline_value: Optional[float] = None,
    volume_targets: Optional[List[VolumeTarget]] = None,
    month_id: Optional[str] = None,
    focus: Optional[Literal["upper_body", "lower_body", "core", "cardio", "balanced"]] = None,
) -> str:
    """Stage a monthly goal. Call once you have enough to propose. Ask user to confirm."""
    collection = get_month_plans_collection()

    # --- Last-week cutoff ---
    today = today_local()
    current_mid = _current_month_id()
    if month_id is None and is_last_week_of_month(today, current_mid):
        target_month = _next_month_id(current_mid)
        redirect_note = (
            f"NOTE: Because you're in the last week of {current_mid}, your goal has been "
            f"set for {target_month} so you begin with a full training cycle."
        )
    else:
        target_month = month_id or current_mid
        redirect_note = None

    existing = collection.find_one({"user_id": get_current_user_id(), "month_id": target_month})
    if existing and existing.get("goal", {}).get("status") == "confirmed":
        return f"Goal already confirmed for {target_month}. Cannot change."

    short_groups_note = None

    if volume_targets:
        covered = {vt.balance_area for vt in volume_targets}
        missing = BALANCE_AREAS - covered
        if missing:
            missing_str = ", ".join(sorted(missing))
            return (
                f"ERROR: 4-part balance rule violated. Missing areas: {missing_str}. "
                f"Every month goal must include at least one exercise for each of: "
                f"upper_body, lower_body, core, cardio. "
                f"Please add exercises covering these missing areas and try again."
            )
        resolved_focus = (focus or "balanced").strip().lower()
        if resolved_focus not in VALID_FOCUSES:
            resolved_focus = "balanced"
        raw_volume_targets = [vt.model_dump() for vt in volume_targets]
    else:
        # Automatic composition path
        prev = get_most_recent_confirmed_goal()
        prev_mid, prev_goal = prev if prev else (None, None)

        if focus is not None and focus.strip().lower() in VALID_FOCUSES:
            resolved_focus = focus.strip().lower()
        else:
            resolved_focus = (prev_goal.get("focus") if prev_goal else None) or "balanced"

        # Check if previous focus and exercises carry over:
        # User specified no focus change and previous confirmed goal exists from another month
        carry_over_exercises = (
            focus is None
            and prev_goal is not None
            and bool(prev_goal.get("volume_targets"))
            and prev_mid != target_month
        )

        raw_items = []
        if carry_over_exercises:
            for p_vt in prev_goal["volume_targets"]:
                raw_items.append({
                    "exercise": p_vt["exercise"],
                    "balance_area": p_vt.get("balance_area"),
                })
        else:
            resolved_eq, _ = resolve_user_equipment()
            profile = get_user_profile()
            stored_exp = (profile.get("experience_level") if profile else None) or "beginner"
            composed = compose_goal_exercise_list(
                focus=resolved_focus,
                available_equipment=resolved_eq,
                experience_level=stored_exp,
            )
            raw_items = [
                {"exercise": item["name"], "balance_area": item["balance_area"]}
                for item in composed["exercises"]
            ]

            if composed.get("short_groups"):
                short_groups_note = (
                    f"Short groups omitted due to equipment profile: {', '.join(composed['short_groups'])}."
                )

        computed_items = []
        for item in raw_items:
            role = _role_for_area(resolved_focus, item["balance_area"])
            comp = _compute_volume_target_for_exercise(
                exercise=item["exercise"],
                balance_area=item["balance_area"],
                month_id=target_month,
                volume_multiplier=_volume_multiplier_for_role(role),
            )
            computed_items.append({
                "comp": comp,
                "role": role,
                "balance_area": item["balance_area"],
            })

        focus_targets = [
            c["comp"] for c in computed_items if c["role"] == "focus"
        ]
        if focus_targets:
            for c in computed_items:
                if c["role"] == "maintenance":
                    _cap_maintenance_target(c["comp"], focus_targets)

        raw_volume_targets = [
            {
                "exercise": c["comp"]["exercise"],
                "unit": c["comp"]["unit"],
                "month_target": c["comp"]["month_target"],
                "balance_area": c["balance_area"],
            }
            for c in computed_items
        ]

    # --- Mid-month scaling ---
    today_str = today.strftime("%Y-%m-%d")
    if today_str[:7] == target_month:
        goal_start = today
    else:
        from datetime import date as _date
        year_t, month_t = map(int, target_month.split("-"))
        goal_start = _date(year_t, month_t, 1)

    scaling = compute_goal_scaling(goal_start, target_month)
    scale = scaling["scale"]
    goal_start_date = scaling["goal_start_date"]
    is_mid_month = scale < 1.0

    scaled_volume_targets = []
    for vt in raw_volume_targets:
        d = dict(vt)
        full_target = float(d["month_target"])
        if is_mid_month:
            if d.get("unit") in ("km", "mi", "m"):
                scaled = round(full_target * scale, 1)
            else:
                scaled = int(round(full_target * scale))
            d["month_target"] = scaled
        d["full_month_target"] = full_target
        d["goal_start_date"] = goal_start_date
        scaled_volume_targets.append(d)

    now = datetime.now(ZoneInfo("UTC"))
    goal = {
        "description": description,
        "focus": resolved_focus,
        "metric_name": metric_name,
        "target_value": target_value,
        "unit": unit,
        "baseline_value": baseline_value,
        "volume_targets": scaled_volume_targets,
        "progression_multipliers": get_progression_multipliers(),
        "goal_start_date": goal_start_date,
        "status": "pending",
        "set_at": now,
        "confirmed_at": None,
    }

    collection.update_one(
        {"user_id": get_current_user_id(), "month_id": target_month},
        {
            "$set": {"goal": goal, "updated_at": now},
            "$setOnInsert": {"created_at": now, "week_plan_path": []},
        },
        upsert=True,
    )

    parts = [f"Staged goal for {target_month}: {description} (Focus: {resolved_focus})."]
    if redirect_note:
        parts.append(redirect_note)
    if is_mid_month:
        parts.append(
            f"(Mid-month start detected: {scaling['days_remaining']} of "
            f"{scaling['days_in_month']} days remain in {target_month}. "
            f"Monthly targets have been scaled to {scale:.0%} of the full-month amount. "
            f"Full targets are preserved for future month rollover.)"
        )
    if scaled_volume_targets:
        parts.append("Proposed volume targets:")
        for vt in scaled_volume_targets:
            full_note = f" (full month: {vt['full_month_target']})" if is_mid_month else ""
            parts.append(f"- {vt['exercise']}: {vt['month_target']} {vt['unit']} ({vt['balance_area']}){full_note}")
    if short_groups_note:
        parts.append(short_groups_note)
    parts.append("Ask user to confirm or if they want to swap any exercises.")
    return "\n".join(parts)


class SwapGoalExerciseInput(BaseModel):
    old_exercise: str = Field(description="Name of the exercise currently in the staged goal to be replaced.")
    new_exercise: str = Field(description="Name of the exercise from the workout library to replace it with.")
    month_id: Optional[str] = Field(
        default=None,
        description="Target month in YYYY-MM format. Defaults to current/staged month.",
    )


@tool(args_schema=SwapGoalExerciseInput)
@mongo_guarded
def swap_goal_exercise(
    old_exercise: str,
    new_exercise: str,
    month_id: Optional[str] = None,
) -> str:
    """
    Swap one exercise in a staged (pending) goal for another exercise in the same muscle group.
    The new exercise must exist in the workout library, respect available equipment, and stay
    in the same muscle group. Confirmed goals cannot be changed.
    """
    collection = get_month_plans_collection()

    today = today_local()
    current_mid = _current_month_id()
    if month_id is None and is_last_week_of_month(today, current_mid):
        target_month = _next_month_id(current_mid)
    else:
        target_month = month_id or current_mid

    existing = collection.find_one({"user_id": get_current_user_id(), "month_id": target_month})
    if not existing or not existing.get("goal"):
        return f"No staged goal found for {target_month}."

    goal = existing["goal"]
    if goal.get("status") == "confirmed":
        return f"Goal already confirmed for {target_month}. Cannot change."

    if goal.get("status") != "pending":
        return f"No staged goal waiting for confirmation for {target_month}."

    volume_targets = goal.get("volume_targets") or []
    if not volume_targets:
        return f"Staged goal for {target_month} has no volume targets to swap."

    # Locate old_exercise (case-insensitive exact or substring match)
    old_idx = -1
    matched_old_vt = None
    old_norm = old_exercise.strip().lower()
    for idx, vt in enumerate(volume_targets):
        if vt.get("exercise", "").strip().lower() == old_norm:
            old_idx = idx
            matched_old_vt = vt
            break

    if old_idx == -1:
        for idx, vt in enumerate(volume_targets):
            if old_norm in vt.get("exercise", "").strip().lower():
                old_idx = idx
                matched_old_vt = vt
                break

    if matched_old_vt is None:
        avail_names = ", ".join(vt.get("exercise", "") for vt in volume_targets)
        return f"Exercise '{old_exercise}' is not in the staged goal. Current exercises: {avail_names}."

    # Match new_exercise against workout library
    new_norm = new_exercise.strip().lower()
    matched_new_workout = None
    for name, w in _WORKOUTS_BY_NAME.items():
        if name.strip().lower() == new_norm:
            matched_new_workout = w
            break

    if matched_new_workout is None:
        for name, w in _WORKOUTS_BY_NAME.items():
            if new_norm in name.strip().lower():
                matched_new_workout = w
                break

    if matched_new_workout is None:
        return f"Exercise '{new_exercise}' not found in workout library."

    matched_new_name = matched_new_workout["name"]

    if any(vt.get("exercise") == matched_new_name for vt in volume_targets):
        return f"Exercise '{matched_new_name}' is already included in the staged goal."

    # Check equipment
    resolved_eq, _ = resolve_user_equipment()
    if not _matches_equipment(matched_new_workout, resolved_eq):
        req_eq = [e for e in matched_new_workout.get("equipment", []) if e.strip().lower() not in _NO_EQUIPMENT_TOKENS]
        req_str = ", ".join(req_eq) if req_eq else "specified equipment"
        return f"Cannot swap to '{matched_new_name}': requires {req_str}, which is not in your available equipment."

    # Validate muscle group compatibility
    old_ex_name = matched_old_vt["exercise"]
    old_workout = _WORKOUTS_BY_NAME.get(old_ex_name)
    if old_workout is None:
        for w in _WORKOUTS:
            if w["name"].strip().lower() == old_ex_name.strip().lower():
                old_workout = w
                break
    balance_area = matched_old_vt.get("balance_area")

    if balance_area == "cardio":
        if not is_cardio_exercise(matched_new_workout):
            return f"Cannot swap: '{old_ex_name}' is a cardio exercise, but '{matched_new_name}' is not a cardio exercise."
    else:
        old_primary = (old_workout or {}).get("primary_target_area")
        new_primary = matched_new_workout.get("primary_target_area")
        new_secondary = matched_new_workout.get("secondary_target_areas", [])
        if old_primary and (new_primary != old_primary and old_primary not in new_secondary):
            return (
                f"Cannot swap: '{matched_new_name}' (primary: {new_primary}) "
                f"is not in the same muscle group as '{old_ex_name}' ({old_primary})."
            )

    # Compute this replacement exercise's own target by the same rules.
    role = _role_for_area(goal.get("focus"), balance_area)
    computed = _compute_volume_target_for_exercise(
        exercise=matched_new_name,
        balance_area=balance_area,
        month_id=target_month,
        volume_multiplier=_volume_multiplier_for_role(role),
    )
    full_target = float(computed["month_target"])

    if role == "maintenance":
        focus_refs = [
            {
                "unit": vt.get("unit"),
                "month_target": float(vt.get("full_month_target") or vt["month_target"]),
            }
            for vt in volume_targets
            if _role_for_area(goal.get("focus"), vt.get("balance_area")) == "focus"
        ]
        if focus_refs:
            capped = _cap_maintenance_target(
                {
                    "unit": computed["unit"],
                    "month_target": full_target,
                    "calculation_result": computed,
                },
                focus_refs,
            )
            full_target = float(capped["month_target"])

    scaled_target = full_target

    # Scale if mid-month
    old_full = float(matched_old_vt.get("full_month_target") or matched_old_vt.get("month_target", full_target))
    old_current = float(matched_old_vt.get("month_target", full_target))
    if old_full > 0 and old_current < old_full:
        ratio = old_current / old_full
        if computed["unit"] in ("km", "mi", "m"):
            scaled_target = round(full_target * ratio, 1)
        else:
            scaled_target = int(round(full_target * ratio))

    new_vt_dict = {
        "exercise": matched_new_name,
        "unit": computed["unit"],
        "month_target": scaled_target,
        "balance_area": balance_area,
        "full_month_target": full_target,
        "goal_start_date": matched_old_vt.get("goal_start_date") or goal.get("goal_start_date"),
    }

    updated_vts = list(volume_targets)
    updated_vts[old_idx] = new_vt_dict

    now = datetime.now(ZoneInfo("UTC"))
    collection.update_one(
        {"user_id": get_current_user_id(), "month_id": target_month},
        {"$set": {"goal.volume_targets": updated_vts, "updated_at": now}},
    )

    scale_note = f" (full month: {full_target} {computed['unit']})" if scaled_target != full_target else ""
    return (
        f"Swapped '{old_ex_name}' with '{matched_new_name}': "
        f"{scaled_target} {computed['unit']} ({balance_area}){scale_note}. "
        f"Staged goal updated. Ask user to confirm."
    )


@tool
@mongo_guarded
def confirm_month_goal(month_id: Optional[str] = None) -> str:
    """Lock staged goal. Call ONLY after explicit user yes."""
    collection = get_month_plans_collection()
    target_month = month_id or _current_month_id()

    existing = collection.find_one({"user_id": get_current_user_id(), "month_id": target_month})
    if not existing or existing.get("goal", {}).get("status") != "pending":
        return f"No staged goal waiting for confirmation for {target_month}."

    now = datetime.now(ZoneInfo("UTC"))

    # Build a Foundation-first theme path sized to actual weeks remaining in the
    # target month from the goal start date.  For full-month goals (start on day 1
    # of the month, or goals set for a future month) this equals total_weeks and
    # produces the same path as before.
    from utils.theme_defaults import default_theme_path
    goal = existing.get("goal") or {}
    goal_start_date_str = goal.get("goal_start_date")
    total_weeks = weeks_in_month(target_month)

    if goal_start_date_str:
        from datetime import date as _date
        goal_start = _date.fromisoformat(goal_start_date_str)
        # Count weeks that overlap with [goal_start, month_end].
        all_weeks = compute_month_weeks(target_month)
        active_weeks = [
            w for w in all_weeks
            if _date.fromisoformat(w["end_date"]) >= goal_start
        ]
        effective_weeks = len(active_weeks)
    else:
        effective_weeks = total_weeks

    # Always Foundation-first, truncated to effective_weeks.
    theme_path = default_theme_path(effective_weeks)
    # If mid-month (effective_weeks < total_weeks), renumber so week numbers
    # match the actual calendar weeks the user will train in.
    if effective_weeks < total_weeks:
        all_weeks = compute_month_weeks(target_month)
        active_weeks = [
            w for w in all_weeks
            if goal_start_date_str and
            _date.fromisoformat(w["end_date"]) >= _date.fromisoformat(goal_start_date_str)
        ]
        theme_path = [
            {"week_number": active_weeks[i]["week_number"], "theme": tp["theme"]}
            for i, tp in enumerate(theme_path)
            if i < len(active_weeks)
        ]

    collection.update_one(
        {"user_id": get_current_user_id(), "month_id": target_month},
        {
            "$set": {
                "goal.status": "confirmed",
                "goal.confirmed_at": now,
                "week_plan_path": theme_path,
                "theme_path_source": "foundation_first_on_confirm",
                "updated_at": now,
            }
        },
    )
    theme_str = " → ".join(t["theme"] for t in theme_path)
    return (
        f"Goal confirmed for {target_month}. Locked for the month. "
        f"Week themes: {theme_str}."
    )

def format_month_plan(doc: Optional[dict]) -> str:
    if not doc:
        return "No goal set."

    goal = doc.get("goal") or {}
    if not goal:
        return "No goal set."

    focus = goal.get("focus")
    focus_str = f" [Focus: {focus}]" if focus else ""
    lines = [f"Goal ({goal.get('status', 'unknown')}){focus_str}: {goal.get('description', 'unspecified')}"]

    if goal.get("metric_name"):
        lines.append(
            f"Metric: {goal['metric_name']} target {goal.get('target_value')} {goal.get('unit', '')} "
            f"(baseline {goal.get('baseline_value')})"
        )

    for vt in goal.get("volume_targets") or []:
        area = vt.get("balance_area", "unspecified")
        lines.append(f"Volume: {vt['exercise']} - {vt['month_target']} {vt['unit']} this month ({area})")

    themes = doc.get("week_plan_path") or []
    if themes:
        theme_str = ", ".join(f"week {t['week_number']}: {t['theme']}" for t in themes)
        lines.append(f"Week themes: {theme_str}")

    return "\n".join(lines)


def get_current_goal_summary() -> Optional[str]:
    """One-line confirmed goal summary for goal_context_node. Returns None if none."""
    collection = get_month_plans_collection()
    doc = collection.find_one({"user_id": get_current_user_id(), "month_id": _current_month_id()})
    goal = doc.get("goal") if doc else None

    if not goal or goal.get("status") != "confirmed":
        return None
    return goal.get("description")


def has_theme_path_for_current_month() -> bool:
    """Read-only check: does the current month already have a week theme
    path set? False is a legitimate result (not yet generated), so this
    is NOT @mongo_guarded"""
    collection = get_month_plans_collection()
    doc = collection.find_one({"user_id": get_current_user_id(), "month_id": _current_month_id()})
    return bool(doc and doc.get("week_plan_path"))


@tool
@mongo_guarded
def get_current_month_plan() -> str:
    """Fetch month goal + week themes. Use when user asks for detail beyond context."""
    collection = get_month_plans_collection()
    doc = collection.find_one({"user_id": get_current_user_id(), "month_id": _current_month_id()})
    return format_month_plan(doc)


class WeekThemeInput(BaseModel):
    week_number: int = Field(ge=1, description="1-indexed week number within the month (1 to N where N = total weeks).")
    theme: WeekThemeEnum = Field(description="Strict theme enum: 'Foundation', 'Volume', 'Intensity', 'Peak', 'Deload'")


class UpdateMonthPlanInput(BaseModel):
    week_plan_path: List[WeekThemeInput] = Field(description="Full week theme path.")


@tool(args_schema=UpdateMonthPlanInput)
@mongo_guarded
def update_month_plan(week_plan_path: List[WeekThemeInput]) -> str:
    """Pipeline-only. Replace week theme path. Refuses if no goal exists."""
    collection = get_month_plans_collection()
    month_id = _current_month_id()

    result = collection.update_one(
        {"user_id": get_current_user_id(), "month_id": month_id, "goal": {"$exists": True}},
        {
            "$set": {
                "week_plan_path": [t.model_dump() for t in week_plan_path],
                "updated_at": datetime.now(ZoneInfo("UTC")),
            }
        },
    )
    if result.matched_count == 0:
        return f"No goal found for {month_id}."
    return f"Week themes updated for {month_id}."


def has_confirmed_goal_for_current_month() -> bool:
    """
    Public equivalent of tools/plans.py's private _has_confirmed_goal.
    Read-only, not @mongo_guarded — False is a legitimate result (no
    confirmed goal yet), not a dependency failure. 
    """
    collection = get_month_plans_collection()
    doc = collection.find_one({"user_id": get_current_user_id(), "month_id": _current_month_id()})
    if not doc:
        return False
    goal = doc.get("goal")
    return bool(goal) and goal.get("status") == "confirmed"


def has_ever_had_confirmed_goal() -> bool:
    """
    True if ANY month (past or present) has a confirmed goal for this
    user. Distinguishes a genuinely disengaged/new user (never
    confirmed a goal) from one who's simply between months. 
    """
    collection = get_month_plans_collection()
    doc = collection.find_one({
        "user_id": get_current_user_id(),
        "goal.status": "confirmed",
    })
    return doc is not None


def get_most_recent_confirmed_goal() -> Optional[Tuple[str, dict]]:
    """
    Returns (month_id, goal) for the most recent month with a confirmed
    goal, sorted descending by month_id. None if none exists. 
    """
    collection = get_month_plans_collection()
    doc = collection.find_one(
        {"user_id": get_current_user_id(), "goal.status": "confirmed"},
        sort=[("month_id", -1)],
    )
    if not doc:
        return None
    return doc["month_id"], doc["goal"]


def has_any_goal_doc_for_current_month() -> bool:
    """
    True if a goal sub-document exists for the current month at ALL
    (pending OR confirmed) — used by the scheduler to avoid clobbering
    a goal the user has already started staging manually. 
    """
    collection = get_month_plans_collection()
    doc = collection.find_one({"user_id": get_current_user_id(), "month_id": _current_month_id()})
    return bool(doc and doc.get("goal"))



if __name__ == "__main__":
    # Test: balanced goal should work
    print("--- Balanced goal test ---")
    try:
        result = stage_month_goal.invoke({
            "description": "Full body strength",
            "volume_targets": [
                {"exercise": "Push-ups", "unit": "reps", "month_target": 500, "balance_area": "upper_body"},
                {"exercise": "Squats", "unit": "reps", "month_target": 300, "balance_area": "lower_body"},
                {"exercise": "Plank", "unit": "seconds", "month_target": 600, "balance_area": "core"},
                {"exercise": "Run", "unit": "km", "month_target": 15, "balance_area": "cardio"},
            ],
        })
        print(result)
    except Exception as e:
        print(f"ERROR: {e}")

    # Test: unbalanced goal should return error string (not raise)
    print("\n--- Unbalanced goal test ---")
    result = stage_month_goal.invoke({
        "description": "Upper body only",
        "volume_targets": [
            {"exercise": "Push-ups", "unit": "reps", "month_target": 500, "balance_area": "upper_body"},
        ],
    })
    print(result)

    # Test: invalid exercise name should fail validation
    print("\n--- Invalid exercise test ---")
    try:
        result = stage_month_goal.invoke({
            "description": "Test with fake exercise",
            "volume_targets": [
                {"exercise": "Brisk Walking", "unit": "km", "month_target": 10, "balance_area": "cardio"},
                {"exercise": "Push-ups", "unit": "reps", "month_target": 500, "balance_area": "upper_body"},
                {"exercise": "Squats", "unit": "reps", "month_target": 300, "balance_area": "lower_body"},
                {"exercise": "Plank", "unit": "seconds", "month_target": 600, "balance_area": "core"},
            ],
        })
        print(result)
    except Exception as e:
        print(f"ERROR: {e}")