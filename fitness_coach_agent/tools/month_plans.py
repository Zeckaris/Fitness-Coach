"""Month plan tools — V8."""

import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Optional, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

from db.mongo_client import get_month_plans_collection

DEFAULT_USER_ID = "default_user"
LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")


BALANCE_AREAS = {"upper_body", "lower_body", "core", "cardio"}

_WORKOUTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "workouts.json"
)

with open(_WORKOUTS_PATH, "r") as f:
    _WORKOUTS = json.load(f)

_VALID_EXERCISE_NAMES = {w["name"] for w in _WORKOUTS}


def _previous_month_id() -> str:
    today = datetime.now(LOCAL_TZ).date()
    first_day = today.replace(day=1)
    prev_month = first_day - timedelta(days=1)
    return prev_month.strftime("%Y-%m")


def get_previous_month_review_context() -> Optional[str]:
    """Coaching context from last month's close-out, for goal_context_node. None if none exists."""
    collection = get_month_plans_collection()
    doc = collection.find_one({"user_id": DEFAULT_USER_ID, "month_id": _previous_month_id()})
    if not doc:
        return None
    close_out = doc.get("close_out_summary") or {}
    return close_out.get("coaching_context") or None


def _current_month_id() -> str:
    return datetime.now(LOCAL_TZ).date().strftime("%Y-%m")


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

    @model_validator(mode="after")
    def _validate(self):
        has_metric = self.metric_name is not None
        has_vol = bool(self.volume_targets)
        if not has_metric and not has_vol:
            raise ValueError("Need metric target or volume_targets or both.")
        if has_metric and (self.target_value is None or self.unit is None or self.baseline_value is None):
            raise ValueError("metric_name needs target_value, unit, baseline_value.")
        return self


@tool(args_schema=StageMonthGoalInput)
def stage_month_goal(
    description: str,
    metric_name: Optional[str] = None,
    target_value: Optional[float] = None,
    unit: Optional[str] = None,
    baseline_value: Optional[float] = None,
    volume_targets: Optional[List[VolumeTarget]] = None,
) -> str:
    """Stage a monthly goal. Call once you have enough to propose. Ask user to confirm."""
    collection = get_month_plans_collection()
    month_id = _current_month_id()

    existing = collection.find_one({"user_id": DEFAULT_USER_ID, "month_id": month_id})
    if existing and existing.get("goal", {}).get("status") == "confirmed":
        return f"Goal already confirmed for {month_id}. Cannot change."

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

    now = datetime.now(ZoneInfo("UTC"))
    goal = {
        "description": description,
        "metric_name": metric_name,
        "target_value": target_value,
        "unit": unit,
        "baseline_value": baseline_value,
        "volume_targets": [v.model_dump() for v in volume_targets] if volume_targets else None,
        "status": "pending",
        "set_at": now,
        "confirmed_at": None,
    }

    collection.update_one(
        {"user_id": DEFAULT_USER_ID, "month_id": month_id},
        {
            "$set": {"goal": goal, "updated_at": now},
            "$setOnInsert": {"created_at": now, "week_plan_path": []},
        },
        upsert=True,
    )
    return f"Staged: {description}. Ask user to confirm."


@tool
def confirm_month_goal() -> str:
    """Lock staged goal. Call ONLY after explicit user yes."""
    collection = get_month_plans_collection()
    month_id = _current_month_id()

    existing = collection.find_one({"user_id": DEFAULT_USER_ID, "month_id": month_id})
    if not existing or existing.get("goal", {}).get("status") != "pending":
        return "No staged goal waiting for confirmation."

    now = datetime.now(ZoneInfo("UTC"))
    collection.update_one(
        {"user_id": DEFAULT_USER_ID, "month_id": month_id},
        {"$set": {"goal.status": "confirmed", "goal.confirmed_at": now, "updated_at": now}},
    )
    return f"Goal confirmed for {month_id}. Locked for the month."


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
    doc = collection.find_one({"user_id": DEFAULT_USER_ID, "month_id": _current_month_id()})
    goal = doc.get("goal") if doc else None

    if not goal or goal.get("status") != "confirmed":
        return None
    return goal.get("description")


@tool
def get_current_month_plan() -> str:
    """Fetch month goal + week themes. Use when user asks for detail beyond context."""
    collection = get_month_plans_collection()
    doc = collection.find_one({"user_id": DEFAULT_USER_ID, "month_id": _current_month_id()})
    return format_month_plan(doc)


class WeekThemeInput(BaseModel):
    week_number: int = Field(description="1-4")
    theme: str = Field(description="e.g. 'Volume'")


class UpdateMonthPlanInput(BaseModel):
    week_plan_path: List[WeekThemeInput] = Field(description="Full week theme path.")


@tool(args_schema=UpdateMonthPlanInput)
def update_month_plan(week_plan_path: List[WeekThemeInput]) -> str:
    """Pipeline-only. Replace week theme path. Refuses if no goal exists."""
    collection = get_month_plans_collection()
    month_id = _current_month_id()

    result = collection.update_one(
        {"user_id": DEFAULT_USER_ID, "month_id": month_id, "goal": {"$exists": True}},
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