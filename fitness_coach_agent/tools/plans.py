"""Persist forward plans (tomorrow, day+2, day+3). Never today. Validates dates + exercises."""

import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Optional, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

from db.mongo_client import get_plans_collection, get_month_plans_collection, get_week_plans_collection
from db.guards import mongo_guarded
from tools.week_plans import ensure_week_plan_exists
from auth.context import get_current_user_id
from tools.backlog import get_backlog



from tools.week_plans import (
    ensure_week_plan_exists, save_week_plan, _get_week_id_for_date,
    _get_week_number_from_date, _weeks_in_month, _find_week_doc_for_date,
    get_week_focus_for_date, BlockInput, VolumeTargetInput,
)
from tools.month_plans import has_theme_path_for_current_month
from utils.theme_defaults import default_theme_path
from agent.monthly_review import build_week_blocks
from agent.plan_generation import generate_backfill_days
from agent.error_handling import StructuredOutputFailed
from tools.plan_history import get_past_plans
from tools.knowledge_base import search_fitness_knowledge_base
from tools.workout_library import search_workout_library
from db.mongo_client import get_backlog_collection

LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")

# Load valid exercise names from workouts.json for validation
_WORKOUTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "workouts.json"
)

with open(_WORKOUTS_PATH, "r") as f:
    _workouts_data = json.load(f)

# New build format wraps the list: {"version", "generated_at", "total_exercises", "exercises": [...]}
_WORKOUTS = (
    _workouts_data["exercises"]
    if isinstance(_workouts_data, dict) and "exercises" in _workouts_data
    else _workouts_data
)

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
    doc = month_plans.find_one({"user_id": get_current_user_id(), "month_id": _current_month_id()})
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
    equipment: Optional[List[str]] = Field(
        default=None, description="Required equipment, e.g. ['dumbbells', 'bench']."
    )
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

        if self.status == "rest" and self.exercises:
            raise ValueError("A 'rest' day must not include exercises.")

        if self.date not in _valid_plan_dates():
            raise ValueError(
                f"'{self.date}' is not a valid plan date. Plans may only target tomorrow, "
                "day+2, or day+3 - never today. Use record_checkin for today's coaching."
            )

        return self



class BackfillDayPlanInput(BaseModel):
    """
    Schema for plans created by generate_today_plan.

    Validates exercise count, phase distribution, status, and duration
    without restricting the date value. Date correctness is validated
    by the caller.
    """

    date: str = Field(description="Plan date in YYYY-MM-DD format.")
    focus_area: str = Field(description="Training focus.")
    status: str = Field(description="'planned' or 'rest'.")
    duration_minutes: Optional[int] = Field(
        default=None, description="Session duration."
    )
    exercises: List[ExercisePlanItem] = Field(
        ...,
        description="Complete session exercises. Minimum 7, maximum 20. "
        "Must include warmup (2-4), main (4-12), and cooldown (1-4) phases.",
    )
    notes: Optional[str] = Field(default=None, description="Plan notes.")
    avoid_body_parts: Optional[List[str]] = Field(default=None, description="Excluded body parts.")
    source_checkin_date: Optional[str] = Field(default=None, description="Related check-in date.")

    @model_validator(mode="after")
    def _validate_status_and_phases(self):
        if self.status not in ("planned", "rest"):
            raise ValueError("status must be 'planned' or 'rest'.")

        if self.status == "planned":
            if not self.exercises:
                raise ValueError(
                    "A 'planned' day cannot be saved with no exercises. "
                    "Assemble real exercises from the provided knowledge/library "
                    "search results before returning."
                )

            if not (7 <= len(self.exercises) <= 20):
                raise ValueError(
                    f"Need 7-20 exercises total, got {len(self.exercises)}."
                )

            cats = [e.category for e in self.exercises]
            warmup_count = cats.count("warmup")
            main_count = cats.count("main")
            cooldown_count = cats.count("cooldown")

            if warmup_count < 2:
                raise ValueError(f"Need at least 2 warmup exercises, found {warmup_count}.")
            if main_count < 4:
                raise ValueError(f"Need at least 4 main exercises, found {main_count}.")
            if cooldown_count < 1:
                raise ValueError(f"Need at least 1 cooldown exercise, found {cooldown_count}.")

        if self.status == "rest" and self.exercises:
            raise ValueError("A 'rest' day must not include exercises.")

        # NOTE: deliberately no date-membership check here — see class
        # docstring. generate_today_plan validates exact date values
        # post-generation.

        return self


class BackfillPlanOutput(BaseModel):
    """
    Output schema for generate_today_plan.

    Validates that the output contains exactly 4 unique day plans.
    Date correctness is validated by the caller.
    """

    days: List[BackfillDayPlanInput] = Field(
        description="Exactly 4 day plans: today, tomorrow, day+2, day+3, in that order."
    )

    @model_validator(mode="after")
    def _validate_shape(self):
        if len(self.days) != 4:
            raise ValueError(f"Expected exactly 4 days, got {len(self.days)}.")
        dates = [d.date for d in self.days]
        if len(set(dates)) != 4:
            raise ValueError(f"Day dates must be unique, got {dates}.")
        return self





class UpdateForwardPlanInput(BaseModel):
    days: List[DayPlanInput] = Field(
        description="Plan days to create or update."
    )


@tool(args_schema=UpdateForwardPlanInput)
@mongo_guarded
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
            "user_id": get_current_user_id(),
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
            {"user_id": get_current_user_id(), "date": day.date},
            {
                "$set": set_fields,
                "$setOnInsert": {"created_at": datetime.now(ZoneInfo("UTC"))},
            },
            upsert=True,
        )
        updated_dates.append(day.date)

    return f"Plan updated for: {', '.join(updated_dates)}."

def _get_month_doc() -> Optional[dict]:
    collection = get_month_plans_collection()
    return collection.find_one({"user_id": get_current_user_id(), "month_id": _current_month_id()})




@tool
@mongo_guarded
def generate_today_plan() -> str:
    """
    Create-only backfill: builds today's plan plus the forward
    tomorrow/day+2/day+3 window when NO plan document exists for today
    at all. Refuses immediately if any document already exists for
    today, for any reason. Reconstructs missing month theme path
    (non-LLM default) and week block structure (reused generation
    logic) as needed first.
    """
    today = datetime.now(LOCAL_TZ).date()
    today_str = today.strftime("%Y-%m-%d")
    tomorrow_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    day2_str = (today + timedelta(days=2)).strftime("%Y-%m-%d")
    day3_str = (today + timedelta(days=3)).strftime("%Y-%m-%d")

    # Guard 1: confirmed goal must exist
    if not _has_confirmed_goal():
        return (
            "ERROR: No confirmed month goal exists. Please set a goal first "
            "via stage_month_goal / confirm_month_goal before I can build today's plan."
        )

    # Guard 2 (create-only guarantee): refuse if ANY document exists for today, period.
    plans = get_plans_collection()
    if plans.find_one({"user_id": get_current_user_id(), "date": today_str}):
        return f"A plan document already exists for {today_str}. Nothing to backfill."

    month_id = _current_month_id()
    month_doc = _get_month_doc()
    goal = (month_doc or {}).get("goal") or {}
    total_weeks = _weeks_in_month(month_id)

    # Step 1: month theme path — non-LLM fallback (V9.3 addendum item 3)
    if not has_theme_path_for_current_month():
        theme_path = default_theme_path(total_weeks)
        get_month_plans_collection().update_one(
            {"user_id": get_current_user_id(), "month_id": month_id},
            {"$set": {"week_plan_path": theme_path, "updated_at": datetime.now(ZoneInfo("UTC"))}},
        )
        month_doc = _get_month_doc()  # re-fetch: now has week_plan_path

    week_plan_path = (month_doc or {}).get("week_plan_path") or []
    week_number = _get_week_number_from_date(today_str)
    week_theme = next(
        (t["theme"] for t in week_plan_path if t["week_number"] == week_number),
        "Volume",
    )

    # Step 2: week block structure — reuse build_week_blocks, single write here
    if not ensure_week_plan_exists(today_str):
        week_id = _get_week_id_for_date(today_str)
        sunday = datetime.strptime(week_id, "%Y-%m-%d").date()
        block_1_dates = [(sunday + timedelta(days=d)).strftime("%Y-%m-%d") for d in (1, 2, 3)]
        block_2_dates = [(sunday + timedelta(days=d)).strftime("%Y-%m-%d") for d in (4, 5, 6)]

        week_result = build_week_blocks(
            week_id=week_id,
            block_1_dates=block_1_dates,
            block_2_dates=block_2_dates,
            week_theme=week_theme,
            month_goal=goal,
            week_number=week_number,
            total_weeks=total_weeks,
        )
        save_week_plan(
            week_id=week_id,
            blocks=[BlockInput(**b) for b in week_result["blocks"]],
            week_volume_targets=(
                [VolumeTargetInput(**v) for v in week_result["week_volume_targets"]]
                if week_result["week_volume_targets"] else None
            ),
            rationale=week_result["rationale"],
            require_theme_path=False,
        )

    week_focus = get_week_focus_for_date(today_str) or week_theme

    past_plans_context = get_past_plans.invoke({})
    backlog_items = get_backlog.invoke({})
    knowledge_context = search_fitness_knowledge_base.invoke(
        {"query": f"{week_focus} training guidance for {goal.get('description', 'general fitness')}"}
    )

    main_pool = search_workout_library.invoke({})
    short_pool = search_workout_library.invoke({"max_duration_minutes": 6})
    available_exercises = f"{main_pool}\n{short_pool}"

    # Step 3: today + forward window, one combined structured call
    try:
        result = generate_backfill_days(
            today_date=today_str,
            tomorrow_date=tomorrow_str,
            day_plus_2_date=day2_str,
            day_plus_3_date=day3_str,
            goal_description=goal.get("description", "unspecified"),
            week_focus=week_focus,
            backlog_items=backlog_items,
            past_plans_context=past_plans_context,
            knowledge_context=knowledge_context,
            available_exercises=available_exercises,
        )
    except StructuredOutputFailed:
        return (
            "I've set up this week's structure, but couldn't generate today's "
            "actual workout plan just now — please ask again in a moment."
        )

    # Validate the 4 returned dates are EXACTLY what we expect — the
    # schema only checks "4 unique dates", not that they match reality.
    expected_dates = {today_str, tomorrow_str, day2_str, day3_str}
    returned_dates = {d.date for d in result.days}
    if returned_dates != expected_dates:
        return (
            "I've set up this week's structure, but today's plan generation "
            "returned unexpected dates — please ask again in a moment."
        )

    # Write all 4 days directly to plans_collection (bypassing
    # update_three_day_plan entirely
    now = datetime.now(ZoneInfo("UTC"))
    for day in result.days:
        set_fields = {
            "user_id": get_current_user_id(),
            "date": day.date,
            "updated_at": now,
            "focus_area": day.focus_area,
            "status": day.status,
            "duration_minutes": day.duration_minutes,
            "exercises": [e.model_dump() for e in day.exercises] if day.exercises else [],
            "notes": day.notes,
            "avoid_body_parts": day.avoid_body_parts,
            "source_checkin_date": day.source_checkin_date,
        }
        plans.update_one(
            {"user_id": get_current_user_id(), "date": day.date},
            {"$set": set_fields, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )

    backlog = get_backlog_collection()
    open_items = list(backlog.find({"user_id": get_current_user_id(), "status": "open"}))
    open_names = {item["exercise_name"] for item in open_items}
    for day in result.days:
        for ex in day.exercises:
            if ex.name in open_names:
                backlog.update_one(
                    {"user_id": get_current_user_id(), "exercise_name": ex.name, "status": "open"},
                    {"$set": {
                        "status": "reinserted",
                        "reinserted_date": day.date,
                        "updated_at": now,
                    }},
                )

    return (
        f"Backfilled plan for {today_str} through {day3_str}. "
        f"You're caught back up to your normal weekly rhythm."
    )
    
    

    
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