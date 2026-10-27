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
    calculate_month_target,
    BEGINNER_BASELINE_BY_AREA,
    BEGINNER_BASELINE_CARDIO_REPS,
    BEGINNER_BASELINE_CORE_SECONDS,
    BEGINNER_BASELINE_CORE_REPS,
    _is_timed_unit,
    WeekThemeEnum,
    baseline_value_from_markers,
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
from tools.user_profile import get_user_profile, get_progression_multipliers
from tools.baseline_assessment import get_latest_baseline_assessment

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
    description: str = Field(description="e.g. 'Lose 2kg'")
    metric_name: Optional[str] = Field(default=None, description="e.g. 'body_weight'")
    target_value: Optional[float] = Field(default=None, description="Target change/value")
    unit: Optional[str] = Field(default=None)
    baseline_value: Optional[float] = Field(default=None, description="Current value")
    volume_targets: Optional[List[VolumeTarget]] = Field(
        default=None, description="List of {exercise, unit, month_target, balance_area}"
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
        has_vol = bool(self.volume_targets)
        if not has_metric and not has_vol:
            raise ValueError("Need metric target or volume_targets or both.")
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
    FIRST-TIME GOAL SETUP ONLY. Call this once per exercise BEFORE calling
    stage_month_goal — never invent a month_target yourself. Pass the
    user's stated baseline for this movement if they gave one; omit
    baseline_value if they didn't (a beginner default will be used).
    Returns the computed month_target to use in stage_month_goal's
    volume_targets.
    """
    target_month = month_id or _current_month_id()

    # Single source of truth for the unit: the exercise's canonical unit in the
    # workout library. Whatever unit the LLM passed in is only used as a
    # fallback when the exercise is absent from the library (it still feeds
    # the rest of the calculation AND the final label in that rare case).
    resolved_unit = _exercise_unit(exercise, unit)

    # Authoritative source: read experience_level directly from stored user_profile
    profile = get_user_profile()
    stored_exp = profile.get("experience_level") if profile else None
    if not stored_exp:
        raise RuntimeError(
            "No stored experience_level found in user profile. User must complete onboarding baseline assessment first."
        )

    # Baseline resolution order:
    #   1. baseline_value explicitly passed by the LLM (user stated it this turn)
    #   2. the user's stored onboarding baseline marker for this area
    #   3. the conservative beginner default (handled inside calculate_month_target
    #      when baseline_value is None)
    resolved_baseline = baseline_value
    # Cardio is the only area whose onboarding marker (run_distance_km) is a
    # running *distance*, so the marker path only applies to distance-based
    # cardio exercises (unit km/mi/m). All other areas, incl. rep/time-based
    # cardio, skip this marker path and use a beginner default instead.
    # Similarly, core onboarding marker (plank_seconds) is a timed hold in
    # seconds, so it only applies to timed core exercises (unit seconds).
    # Rep-based core moves must never use seconds as reps.
    use_stored_marker = True
    if balance_area == "cardio":
        use_stored_marker = _is_distance_unit(resolved_unit)
    elif balance_area == "core":
        use_stored_marker = _is_timed_unit(resolved_unit)

    if resolved_baseline is None and use_stored_marker:
        assessment = get_latest_baseline_assessment()
        raw_markers = (assessment or {}).get("answers", {}).get("raw_markers")
        resolved_baseline = baseline_value_from_markers(
            raw_markers, balance_area, unit=resolved_unit
        )

    # Dedicated beginner defaults when no baseline is available:
    default_baseline = None
    if balance_area == "cardio" and not use_stored_marker:
        default_baseline = BEGINNER_BASELINE_CARDIO_REPS
    elif balance_area == "core":
        default_baseline = (
            BEGINNER_BASELINE_CORE_SECONDS
            if _is_timed_unit(resolved_unit)
            else BEGINNER_BASELINE_CORE_REPS
        )

    # Deterministic progression multiplier: read from the profile we already
    # fetched, scoped to this balance area. Defaults to 1.0 for new users /
    # missing field (lazy migration — no backfill required). Applied inside
    # calculate_month_target as the final multiplicative factor on the computed
    # month_target. _calculate_week_targets works from the already-stored
    # month_target value and never calls calculate_month_target, so there is no
    # double-apply risk.
    stored_multipliers = (profile or {}).get("progression_multipliers") or {}
    try:
        progression_multiplier = float(stored_multipliers.get(balance_area, 1.0))
    except (TypeError, ValueError):
        progression_multiplier = 1.0

    result = calculate_month_target(
        unit=resolved_unit,
        balance_area=balance_area,
        experience_level=stored_exp,
        baseline_value=resolved_baseline,
        sessions_per_week=sessions_per_week,
        sets_per_session=sets_per_session,
        weeks_in_month=weeks_in_month(target_month),
        default_baseline=default_baseline,
        progression_multiplier=progression_multiplier,
    )

    # Distinguish the stored-onboarding source from an LLM-provided one so the
    # output is honest about where the baseline came from.
    baseline_source = result["baseline_source"]
    if baseline_source == "provided" and baseline_value is None:
        baseline_source = "stored_baseline"

    multiplier_note = (
        f", progression_multiplier: {progression_multiplier:.3g}"
        if progression_multiplier != 1.0
        else ""
    )
    return (
        f"month_target for {exercise}: {result['month_target']} {resolved_unit}. "
        f"(baseline used: {result['baseline_used']} [{baseline_source}], "
        f"experience: {stored_exp}, "
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
) -> str:
    """Stage a monthly goal. Call once you have enough to propose. Ask user to confirm."""
    collection = get_month_plans_collection()

    # --- Last-week cutoff ---
    # If the user is in the last calendar week of the current month and has not
    # explicitly chosen a different month_id, redirect to the next month so
    # they begin with a full periodized cycle.
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

    # --- Mid-month scaling ---
    # Compute how many days remain in target_month from today (or from the
    # first of the month if we're staging for a future month).
    target_month_start_str = f"{target_month}-01"
    today_str = today.strftime("%Y-%m-%d")
    if today_str[:7] == target_month:
        goal_start = today  # mid-month or full-month (day 1 -> scale = 1.0)
    else:
        # Staging for a future month: always full month
        from datetime import date as _date
        year_t, month_t = map(int, target_month.split("-"))
        import calendar as _cal
        goal_start = _date(year_t, month_t, 1)

    scaling = compute_goal_scaling(goal_start, target_month)
    scale = scaling["scale"]
    goal_start_date = scaling["goal_start_date"]
    is_mid_month = scale < 1.0

    # Apply scaling to volume targets; preserve full_month_target for rollover.
    scaled_volume_targets = None
    if volume_targets:
        scaled_volume_targets = []
        for vt in volume_targets:
            d = vt.model_dump()
            full_target = float(d["month_target"])
            if is_mid_month:
                # Scale proportionally by active days.
                if vt.unit in ("km", "mi", "m"):
                    scaled = round(full_target * scale, 1)
                else:
                    scaled = int(round(full_target * scale))
                d["month_target"] = scaled
            d["full_month_target"] = full_target  # always stored
            d["goal_start_date"] = goal_start_date  # always stored
            scaled_volume_targets.append(d)

    now = datetime.now(ZoneInfo("UTC"))
    goal = {
        "description": description,
        "metric_name": metric_name,
        "target_value": target_value,
        "unit": unit,
        "baseline_value": baseline_value,
        "volume_targets": scaled_volume_targets,
        # Snapshot the multiplier used to compute these month_targets. The
        # scheduled rollover applies only the delta between this snapshot and
        # the latest persisted multipliers, never the cumulative factor twice.
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

    parts = [f"Staged: {description}. Ask user to confirm."]
    if redirect_note:
        parts.append(redirect_note)
    if is_mid_month:
        parts.append(
            f"(Mid-month start detected: {scaling['days_remaining']} of "
            f"{scaling['days_in_month']} days remain in {target_month}. "
            f"Monthly targets have been scaled to {scale:.0%} of the full-month amount. "
            f"Full targets are preserved for future month rollover.)"
        )
    return " ".join(parts)


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

    lines = [f"Goal ({goal.get('status', 'unknown')}): {goal.get('description', 'unspecified')}"]

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