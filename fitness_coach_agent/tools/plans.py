"""Persist forward plans (tomorrow, day+2, day+3). Never today. Validates dates + exercises."""

import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Optional, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

from db.mongo_client import get_plans_collection, get_month_plans_collection, get_week_plans_collection
from tools.week_plans import ensure_week_plan_exists

DEFAULT_USER_ID = "default_user"

LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")

# Load valid exercise names from workouts.json for validation
_WORKOUTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "workouts.json"
)

with open(_WORKOUTS_PATH, "r") as f:
    _WORKOUTS = json.load(f)

_VALID_EXERCISE_NAMES = {w["name"] for w in _WORKOUTS}


def _valid_plan_dates() -> set:
    """Valid plan dates are tomorrow, day+2, and day+3."""
    today = datetime.now(LOCAL_TZ).date()
    return {
        (today + timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in (1, 2, 3)
    }


def _current_month_id() -> str:
    return datetime.now(LOCAL_TZ).date().strftime("%Y-%m")


def _has_confirmed_goal() -> bool:
    """Check if current month has a confirmed goal."""
    month_plans = get_month_plans_collection()
    doc = month_plans.find_one({"user_id": DEFAULT_USER_ID, "month_id": _current_month_id()})
    if not doc:
        return False
    goal = doc.get("goal")
    return bool(goal) and goal.get("status") == "confirmed"


class ExercisePlanItem(BaseModel):
    name: str = Field(description="Exercise name. Must exist in the workout library.")
    focus: str = Field(description="Exercise target area.")
    category: Literal["warmup", "main", "cooldown"] = Field(
        description="Session phase: warmup (joint prep, activation), main (primary work), cooldown (recovery, stretch)."
    )
    sets: Optional[int] = Field(default=None, description="Number of sets.")
    reps: Optional[str] = Field(
        default=None, description="Rep range or duration."
    )
    equipment: Optional[str] = Field(default=None, description="Required equipment.")
    duration_minutes: Optional[int] = Field(
        default=None, description="Estimated duration."
    )
    target_quantity: Optional[int] = Field(
        default=None,
        description="Target volume for this exercise if it's tracked toward a "
        "goal (e.g. '50' for '50 push-ups'). Omit for exercises with no volume "
        "target.",
    )
    unit: Optional[str] = Field(
        default=None,
        description="Unit for target_quantity, e.g. 'reps' or 'seconds'. "
        "Required if target_quantity is set.",
    )
    completed_quantity: int = Field(
        default=0,
        description="Actual amount completed. Always 0 at creation - filled "
        "in later via the Streamlit UI, never set by this tool.",
    )
    completed: bool = Field(
        default=False,
        description="Whether this exercise was done, for non-quantified "
        "exercises (e.g. '20 min mobility'). Always False at creation - "
        "filled in later via the Streamlit UI, never set by this tool.",
    )

    @model_validator(mode="after")
    def _validate_unit_pairing(self):
        if self.target_quantity is not None and not self.unit:
            raise ValueError(
                "unit is required whenever target_quantity is set."
            )
        return self

    @model_validator(mode="after")
    def _validate_exercise_in_library(self):
        if self.name not in _VALID_EXERCISE_NAMES:
            valid_list = ", ".join(sorted(_VALID_EXERCISE_NAMES)[:10]) + "..."
            raise ValueError(
                f"Exercise '{self.name}' not found in workout library. "
                f"Call search_workout_library to find valid exercises. "
                f"Examples: {valid_list}"
            )
        return self


class DayPlanInput(BaseModel):
    """A single forward plan day."""

    date: str = Field(description="Plan date in YYYY-MM-DD format.")
    focus_area: str = Field(description="Training focus.")
    status: str = Field(description="'planned' or 'rest'.")
    duration_minutes: Optional[int] = Field(
        default=None, description="Session duration."
    )
    exercises: List[ExercisePlanItem] = Field(
        ...,
        min_length=7,
        max_length=20,
        description="Complete session exercises. Minimum 7, maximum 20. "
        "Must include warmup (2-4), main (4-12), and cooldown (1-4) phases.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Plan notes."
    )
    avoid_body_parts: Optional[List[str]] = Field(
        default=None,
        description="Excluded body parts."
    )
    source_checkin_date: Optional[str] = Field(
        default=None,
        description="Related check-in date."
    )

    @model_validator(mode="after")
    def _validate_status_and_date(self):
        if self.status not in ("planned", "rest"):
            raise ValueError("status must be 'planned' or 'rest'.")

        if self.status == "planned":
            if not self.exercises:
                raise ValueError(
                    "A 'planned' day cannot be saved with no exercises. Call "
                    "search_fitness_knowledge_base and search_workout_library first to "
                    "assemble real exercises, then call this tool."
                )

            cats = [e.category for e in self.exercises]
            warmup_count = cats.count("warmup")
            main_count = cats.count("main")
            cooldown_count = cats.count("cooldown")

            if warmup_count < 2:
                raise ValueError(
                    f"Need at least 2 warmup exercises, found {warmup_count}. "
                    "Call search_workout_library for mobility/activation exercises."
                )
            if main_count < 4:
                raise ValueError(
                    f"Need at least 4 main exercises, found {main_count}. "
                    "Call search_workout_library for strength/conditioning exercises."
                )
            if cooldown_count < 1:
                raise ValueError(
                    f"Need at least 1 cooldown exercise, found {cooldown_count}. "
                    "Call search_workout_library for stretch/recovery exercises."
                )

            # Duration sanity check
            if self.duration_minutes is not None:
                n = len(self.exercises)
                min_expected = n * 2
                max_expected = n * 8
                if not (min_expected <= self.duration_minutes <= max_expected):
                    raise ValueError(
                        f"duration_minutes ({self.duration_minutes}) is unrealistic for "
                        f"{n} exercises. Expected range: {min_expected}-{max_expected} min "
                        f"(2-8 min per exercise, accounting for rest periods)."
                    )

        if self.status == "rest" and self.exercises:
            raise ValueError("A 'rest' day must not include exercises.")

        if self.date not in _valid_plan_dates():
            raise ValueError(
                f"'{self.date}' is not a valid plan date. Plans may only target tomorrow, "
                "day+2, or day+3 - never today. Use record_checkin for today's coaching."
            )

        return self


class UpdateForwardPlanInput(BaseModel):
    days: List[DayPlanInput] = Field(
        description="Plan days to create or update."
    )


@tool(args_schema=UpdateForwardPlanInput)
def update_three_day_plan(days: List[DayPlanInput]) -> str:
    """
    Create or patch forward plan entries (tomorrow, day+2, day+3).
    Only supports tomorrow, day+2, and day+3.
    """
    # Guard 1: require confirmed goal
    if not _has_confirmed_goal():
        return (
            "ERROR: No confirmed month goal exists. "
            "Please set a goal first via stage_month_goal / confirm_month_goal, "
            'then click "📅 Set Week Themes" in the app to set week themes, '
            "then ask me to generate your weekly plan, "
            "and only after both goal and week plan exist can I generate daily plans."
        )

    # Guard 2: week plan must exist for every day
    for day in days:
        if not ensure_week_plan_exists(day.date):
            return (
                f"ERROR: No week plan exists for {day.date} yet. "
                "Ask me to generate your weekly plan first, then I can create daily plans."
            )

    collection = get_plans_collection()
    updated_dates = []

    for day in days:
        set_fields = {
            "user_id": DEFAULT_USER_ID,
            "date": day.date,
            "updated_at": datetime.now(ZoneInfo("UTC")),
            "focus_area": day.focus_area,
            "status": day.status,
            "duration_minutes": day.duration_minutes,
            "exercises": [e.model_dump() for e in day.exercises] if day.exercises else [],
            "notes": day.notes,
            "avoid_body_parts": day.avoid_body_parts,
            "source_checkin_date": day.source_checkin_date,
        }

        collection.update_one(
            {"user_id": DEFAULT_USER_ID, "date": day.date},
            {
                "$set": set_fields,
                "$setOnInsert": {"created_at": datetime.now(ZoneInfo("UTC"))},
            },
            upsert=True,
        )
        updated_dates.append(day.date)

    return f"Plan updated for: {', '.join(updated_dates)}."


# Quick manual test: python -m tools.plans
if __name__ == "__main__":
    tomorrow = (datetime.now(LOCAL_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
    print(
        update_three_day_plan.invoke(
            {
                "days": [
                    {
                        "date": tomorrow,
                        "focus_area": "upper_body",
                        "status": "planned",
                        "duration_minutes": 45,
                        "exercises": [
                            {"name": "Joint Mobility Warm-Up", "focus": "full_body", "category": "warmup", "duration_minutes": 5},
                            {"name": "Arm Circles", "focus": "shoulders", "category": "warmup", "duration_minutes": 3},
                            {"name": "Push-Ups", "focus": "chest", "category": "main", "sets": 3, "reps": "10-12", "duration_minutes": 5},
                            {"name": "Pull-Ups", "focus": "back", "category": "main", "sets": 3, "reps": "8-10", "duration_minutes": 5},
                            {"name": "Overhead Press", "focus": "shoulders", "category": "main", "sets": 3, "reps": "8-10", "duration_minutes": 5},
                            {"name": "Dumbbell Rows", "focus": "back", "category": "main", "sets": 3, "reps": "10-12", "duration_minutes": 5},
                            {"name": "Tricep Dips", "focus": "triceps", "category": "main", "sets": 3, "reps": "12-15", "duration_minutes": 5},
                            {"name": "Bicep Curls", "focus": "biceps", "category": "main", "sets": 3, "reps": "12-15", "duration_minutes": 5},
                            {"name": "Static Chest Stretch", "focus": "chest", "category": "cooldown", "duration_minutes": 3},
                        ],
                        "notes": "Test entry with 9 exercises (2 warmup + 6 main + 1 cooldown).",
                    }
                ]
            }
        )
    )