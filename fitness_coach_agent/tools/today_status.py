"""
Use when the user asks about today's workout status or whether they completed it.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from db.mongo_client import get_plans_collection

DEFAULT_USER_ID = "default_user"
LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")


def _today_str() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


@tool
def get_today_workout_status() -> str:
    """
    Check whether today's workout was completed, is still planned, is a rest
    day, or doesn't exist.
    """
    collection = get_plans_collection()
    today = _today_str()
    doc = collection.find_one({"user_id": DEFAULT_USER_ID, "date": today})

    if not doc:
        return "no_plan: No workout is planned for today."

    status = doc.get("status", "planned")
    focus = doc.get("focus_area", "unspecified").replace("_", " ").title()
    exercises = doc.get("exercises") or []
    ex_count = len(exercises)

    if status == "completed":
        completed_at = doc.get("completed_at")
        time_str = ""
        if completed_at and hasattr(completed_at, "strftime"):
            time_str = f" at {completed_at.strftime('%I:%M %p')}"
        return (
            f"completed: You completed your workout today{time_str}. "
            f"Focus: {focus}. {ex_count} exercises."
        )

    if status == "rest":
        return "rest: Today is a rest day."

    # status == "planned" — count how many exercises are done
    done = 0
    for ex in exercises:
        target = ex.get("target_quantity")
        if target is not None:
            if ex.get("completed_quantity", 0) >= target:
                done += 1
        elif ex.get("completed", False):
            done += 1

    exercise_summary = ", ".join(
        e.get("name", "?") for e in exercises[:5]
    )
    if ex_count > 5:
        exercise_summary += f" (+{ex_count - 5} more)"

    return (
        f"planned: Today's workout ({focus}) is not yet completed. "
        f"{done}/{ex_count} exercises done. Exercises: {exercise_summary}."
    )


# Quick manual test: python -m tools.today_status
if __name__ == "__main__":
    print(get_today_workout_status.invoke({}))
