"""
update_three_day_plan — V6 plan persistence tool.

Writes and patches structured plans for the forward 3-day window:
tomorrow, day+2, and day+3. Never writes today's plan.

This tool only stores validated plan data. Exercise selection and plan
reasoning happen before this tool call through the required search tools.

Validation prevents invalid dates and prevents planned days without exercises.

KNOWN DEBT:
- DEFAULT_USER_ID / LOCAL_TZ remain placeholders for future multi-user support.
- focus_area is currently inferred by the agent.
- No scheduler or progression tracking yet.
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
    """Valid plan dates are tomorrow, day+2, and day+3."""
    today = datetime.now(LOCAL_TZ).date()
    return {
        (today + timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in (1, 2, 3)
    }


class ExercisePlanItem(BaseModel):
    name: str = Field(description="Exercise name.")
    focus: str = Field(description="Exercise target area.")
    sets: Optional[int] = Field(default=None, description="Number of sets.")
    reps: Optional[str] = Field(
        default=None, description="Rep range or duration."
    )
    equipment: Optional[str] = Field(default=None, description="Required equipment.")
    duration_minutes: Optional[int] = Field(
        default=None, description="Estimated duration."
    )


class DayPlanInput(BaseModel):
    """A single forward plan day."""

    date: str = Field(description="Plan date in YYYY-MM-DD format.")
    focus_area: str = Field(description="Training focus.")
    status: str = Field(description="'planned' or 'rest'.")
    duration_minutes: Optional[int] = Field(
        default=None, description="Session duration."
    )
    exercises: Optional[List[ExercisePlanItem]] = Field(
        default=None,
        description="Exercises; required for planned days.",
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
        description="Plan days to create or update."
    )


@tool(args_schema=UpdateThreeDayPlanInput)
def update_three_day_plan(days: List[DayPlanInput]) -> str:
    """
    Create or patch forward 3-day plan entries.
    Only supports tomorrow, day+2, and day+3.
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