"""
update_three_day_plan — V6's plan persistence tool.

V6 scope: writes/patches structured day-plans for the 3-day forward window
(tomorrow, day+2, day+3 - never today; today stays owned entirely by
record_checkin's reactive coaching flow, see tools/checkins.py). One
document per (user_id, date), matching the checkins collection's
per-date pattern rather than one doc holding all 3 days - this makes
patching a single day a simple upsert on one document, no array surgery.

This tool is intentionally "dumb" - it persists whatever plan content
the agent hands it. The actual reasoning (what movement patterns make a
sound session, which concrete exercises to use) happens earlier in the
same turn via search_fitness_knowledge_base and search_workout_library;
this tool only validates structure and writes the result. See
agent/prompts.py for the mandatory tool-call sequencing that ensures
those steps actually happen before this one is called.

Guardrail: a day marked "planned" cannot be saved with an empty exercise
list - this is enforced in the schema (not just prompted), so the agent
can't silently skip the reasoning/retrieval steps and still succeed.

KNOWN DEBT (see also tools/checkins.py):
- DEFAULT_USER_ID / LOCAL_TZ: same placeholders, same future multi-user
  fix needed.
- focus_area assignment is currently inferred by the agent (default
  rotation, or explicit user request) - this should eventually be owned
  by the Week Plan (V7).
- No scheduler - plan writes are reactive only, triggered within a normal
  conversation turn. Proactive daily/nightly generation is future work.
- No progression/difficulty tracking yet - exercises are selected for
  variety and safety only, not escalating difficulty over time.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

from db.mongo_client import get_plans_collection


DEFAULT_USER_ID = "default_user"

LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")


def _valid_plan_dates() -> set:
    """The only 3 dates a plan entry may target: tomorrow, day+2, day+3."""
    today = datetime.now(LOCAL_TZ).date()
    return {
        (today + timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in (1, 2, 3)
    }


class ExercisePlanItem(BaseModel):
    name: str = Field(description="Exercise name, exactly as returned by search_workout_library.")
    focus: str = Field(description="What this exercise targets, e.g. 'chest', 'hamstrings'.")
    sets: Optional[int] = Field(default=None, description="Number of sets, if applicable.")
    reps: Optional[str] = Field(
        default=None, description="Rep range or scheme, e.g. '8-10' or '30 seconds'."
    )
    equipment: Optional[str] = Field(default=None, description="Equipment required, if any.")
    duration_minutes: Optional[int] = Field(
        default=None, description="Estimated time for this exercise, if known."
    )


class DayPlanInput(BaseModel):
    """A single day's plan entry - one of tomorrow, day+2, or day+3."""

    date: str = Field(description="Date this entry is FOR, formatted 'YYYY-MM-DD'.")
    focus_area: str = Field(
        description="Focus for this day, e.g. 'upper_body', 'legs', 'rest'."
    )
    status: str = Field(description="Either 'planned' or 'rest'.")
    duration_minutes: Optional[int] = Field(
        default=None, description="Total session length. Omit for rest days."
    )
    exercises: Optional[List[ExercisePlanItem]] = Field(
        default=None,
        description="Exercises for this day. Required (non-empty) when status is 'planned'. "
        "Must be omitted or empty for 'rest'.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Short explanation of why this day looks the way it does, e.g. "
        "'Avoiding shoulder-loading movements due to reported shoulder pain.'",
    )
    avoid_body_parts: Optional[List[str]] = Field(
        default=None, description="Body parts excluded from this day's exercises, if any."
    )
    source_checkin_date: Optional[str] = Field(
        default=None,
        description="Date of the check-in (or 'YYYY-MM-DD' of the triggering message) that "
        "caused this plan entry to be created/patched, for traceability.",
    )

    @model_validator(mode="after")
    def _validate_status_and_date(self):
        if self.status not in ("planned", "rest"):
            raise ValueError("status must be 'planned' or 'rest'.")

        if self.status == "planned" and not self.exercises:
            raise ValueError(
                "A 'planned' day cannot be saved with no exercises. Call "
                "search_fitness_knowledge_base and search_workout_library first to "
                "assemble real exercises, then call this tool."
            )

        if self.status == "rest" and self.exercises:
            raise ValueError("A 'rest' day must not include exercises.")

        if self.date not in _valid_plan_dates():
            raise ValueError(
                f"'{self.date}' is not a valid plan date. Plans may only target tomorrow, "
                "day+2, or day+3 - never today. Use record_checkin for today's coaching."
            )

        return self


class UpdateThreeDayPlanInput(BaseModel):
    days: List[DayPlanInput] = Field(
        description="One or more day-entries to create/patch. Only include the day(s) "
        "actually being changed - untouched days are left as they are."
    )


@tool(args_schema=UpdateThreeDayPlanInput)
def update_three_day_plan(days: List[DayPlanInput]) -> str:
    """
    Create or patch one or more days in the forward-looking 3-day plan
    (tomorrow, day+2, day+3 - never today).

    Call this only after assembling real exercises via
    search_fitness_knowledge_base (for sound movement-pattern combinations)
    and search_workout_library (for concrete exercises) - a 'planned' day
    cannot be saved without exercises. Only pass the day(s) you're actually
    changing; other days in the window are left untouched.
    """
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
                        "duration_minutes": 30,
                        "exercises": [
                            {"name": "Push-up", "focus": "chest", "sets": 3, "reps": "10-12"}
                        ],
                        "notes": "Test entry.",
                    }
                ]
            }
        )
    )