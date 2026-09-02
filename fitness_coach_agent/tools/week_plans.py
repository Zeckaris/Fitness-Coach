"""Week plan tools 
The agent generates week plans on-demand via update_week_plan (LLM tool).
ensure_week_plan_exists is read-only; it never creates.
"""

from datetime import datetime, timedelta
import calendar
from zoneinfo import ZoneInfo
from typing import List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from db.mongo_client import get_week_plans_collection, get_month_plans_collection
from db.guards import mongo_guarded, MONGO_FALLBACK_MESSAGE
from auth.context import get_current_user_id

LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")


def _current_month_id() -> str:
    return datetime.now(LOCAL_TZ).date().strftime("%Y-%m")


def _get_week_id_for_date(date_str: str) -> str:
    """Return the Sunday date (week_id) that this date belongs to."""
    date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    days_since_sunday = (date_obj.weekday() + 1) % 7
    sunday = date_obj - timedelta(days=days_since_sunday)
    return sunday.strftime("%Y-%m-%d")


def _weeks_in_month(month_id: str) -> int:
    """Number of 7-day windows in this month, day-1 anchored (4 or 5)."""
    year, month = map(int, month_id.split("-"))
    days_in_month = calendar.monthrange(year, month)[1]
    return -(-days_in_month // 7)  # ceil division


def _get_week_number_from_date(date_str: str) -> int:
    """Determine week number within the month, using the real week count (4 or 5)."""
    date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    month_start = date_obj.replace(day=1)
    days_since_month_start = (date_obj - month_start).days
    total_weeks = _weeks_in_month(date_obj.strftime("%Y-%m"))
    return min(total_weeks, (days_since_month_start // 7) + 1)


def _calculate_week_targets(month_goal: dict, week_number: int, total_weeks: Optional[int] = None) -> dict:
    """Calculate remaining volume targets for a given week."""
    volume_targets = month_goal.get("volume_targets") or []
    if not volume_targets:
        return {}

    if total_weeks is None:
        total_weeks = _weeks_in_month(_current_month_id())

    from db.mongo_client import get_plans_collection, get_month_plans_collection
    from utils.baseline_targets import THEME_WEIGHT_MATRIX
    plans = get_plans_collection()
    month_plans = get_month_plans_collection()
    month_id = _current_month_id()

    month_doc = month_plans.find_one({"user_id": get_current_user_id(), "month_id": month_id})
    week_plan_path = (month_doc or {}).get("week_plan_path") or []

    current_week_theme = next(
        (t["theme"] for t in week_plan_path if t.get("week_number") == week_number),
        "Foundation"
    )

    remaining_theme_weights = [
        THEME_WEIGHT_MATRIX.get(t.get("theme"), 1.00)
        for t in week_plan_path
        if t.get("week_number") >= week_number
    ]
    sum_remaining_weights = sum(remaining_theme_weights) if remaining_theme_weights else float(max(1, total_weeks - week_number + 1))
    current_theme_weight = THEME_WEIGHT_MATRIX.get(current_week_theme, 1.00)
    theme_fraction = current_theme_weight / sum_remaining_weights

    results = {}
    for vt in volume_targets:
        month_target = vt["month_target"]
        exercise = vt["exercise"]
        unit = vt["unit"]

        docs = plans.find({
            "user_id": get_current_user_id(),
            "date": {"$regex": f"^{month_id}"},
            "exercises.name": exercise,
        })
        completed_so_far = 0
        for doc in docs:
            for ex in doc.get("exercises", []):
                if ex.get("name") == exercise:
                    completed_so_far += ex.get("completed_quantity", 0)

        remaining = max(0, month_target - completed_so_far)
        week_target = remaining * theme_fraction

        # Returning both week_target and session daily_target (divided by
        # 4 active training sessions per week rather than 7 calendar days)
        # so active workout sessions cover 100% of the weekly target volume.
        sessions_per_week = 4
        _CONTINUOUS_UNITS = {"km", "m", "mi"}
        if unit in _CONTINUOUS_UNITS:
            # Distance: keep 1 decimal place
            wt = round(week_target, 1)
            dt = round(week_target / sessions_per_week, 2)
        else:
            # Reps / seconds: whole numbers only, no decimal points
            wt = int(round(week_target))
            dt = int(round(week_target / sessions_per_week))

        results[exercise] = {
            "unit": unit,
            "week_target": wt,
            "daily_target": dt,
        }

    return results


def ensure_week_plan_exists(target_date: str) -> bool:
    """
    Read-only check: does a week plan exist covering target_date?
    Never creates. The agent must use update_week_plan to create one.
    """
    week_id = _get_week_id_for_date(target_date)
    week_plans = get_week_plans_collection()

    existing = week_plans.find_one({
        "user_id": get_current_user_id(),
        "week_id": week_id
    })
    return existing is not None


def _find_week_doc_for_date(date_str: str) -> Optional[dict]:
    collection = get_week_plans_collection()
    doc = collection.find_one({"user_id": get_current_user_id(), "daily_volume_targets.date": date_str})
    if doc:
        return doc
    return collection.find_one({"user_id": get_current_user_id()}, sort=[("week_id", -1)])


def get_week_focus_for_date(date_str: str) -> Optional[str]:
    """NOT @mongo_guarded: called directly from goal_context_node
    (agent/graph.py), a "critical, halt" node — its failure must
    propagate to the .invoke() call site in app/components/chat.py,
    not be swallowed here."""
    doc = _find_week_doc_for_date(date_str)
    if not doc:
        return None
    return doc.get("focus")


def get_daily_volume_targets_for_date(date_str: str) -> Optional[list]:
    """NOT @mongo_guarded: called directly from generate_today_plan
    (tools/plans.py), a critical path whose failures must propagate to
    the caller, not be swallowed here — same rationale as
    get_week_focus_for_date.

    NOTE: Previously named get_week_block_targets_for_date.

    Returns the matching day's targets — a list of
    {exercise, unit, daily_target} dicts — or None if no doc exists,
    or the date is not found.
    """
    doc = _find_week_doc_for_date(date_str)
    if not doc:
        return None
    for daily_entry in doc.get("daily_volume_targets", []):
        if daily_entry.get("date") == date_str:
            return daily_entry.get("targets")
    return None


def format_week_plan(doc: Optional[dict]) -> str:
    if not doc:
        return "No week plan yet."
    lines = [f"Week of {doc['week_id']}:"]
    lines.append(f"Focus: {doc.get('focus')}")
    lines.append("Week Targets:")
    for vt in doc.get("week_volume_targets") or []:
        lines.append(f"  - {vt.get('exercise')}: {vt.get('week_target')} {vt.get('unit')}")
    if doc.get("rationale"):
        lines.append(f"Rationale: {doc['rationale']}")
    return "\n".join(lines)


@tool
@mongo_guarded
def get_current_week_plan() -> str:
    """Current week block structure. Use when user asks for week detail beyond context."""
    today_str = datetime.now(LOCAL_TZ).date().strftime("%Y-%m-%d")
    doc = _find_week_doc_for_date(today_str)
    return format_week_plan(doc)


class DailyVolumeTargetInput(BaseModel):
    exercise: str
    unit: str
    daily_target: float


class DailyVolumeTargetsInput(BaseModel):
    date: str = Field(description="YYYY-MM-DD")
    targets: List[DailyVolumeTargetInput]


class WeekVolumeTargetInput(BaseModel):
    exercise: str
    unit: str
    week_target: float


class UpdateWeekPlanInput(BaseModel):
    week_id: str = Field(
        description="Sunday date YYYY-MM-DD of the week to generate. Use the Sunday of the current calendar week."
    )
    rationale: Optional[str] = Field(
        default=None,
        description="Brief note on why this week's training focus was chosen (1-2 sentences)."
    )


def save_week_plan(
    week_id: str,
    focus: str,
    daily_volume_targets: List[DailyVolumeTargetsInput],
    week_volume_targets: Optional[List[WeekVolumeTargetInput]] = None,
    rationale: Optional[str] = None,
    require_theme_path: bool = True,
) -> str:
    """
    Validates and saves a weekly plan.

    Optionally requires a week theme path before saving.
    """
    if require_theme_path:
        month_plans = get_month_plans_collection()
        month_doc = month_plans.find_one({
            "user_id": get_current_user_id(),
            "month_id": _current_month_id()
        })
        week_plan_path = month_doc.get("week_plan_path") if month_doc else None
        if not week_plan_path:
            return (
                "ERROR: Week themes have not been set yet. "
                'Click the "📅 Set Week Themes" button in the app first, then ask me to generate your weekly plan.'
            )

    collection = get_week_plans_collection()
    now = datetime.now(ZoneInfo("UTC"))
    collection.update_one(
        {"user_id": get_current_user_id(), "week_id": week_id},
        {
            "$set": {
                "focus": focus,
                "daily_volume_targets": [d.model_dump() for d in daily_volume_targets],
                "week_volume_targets": [v.model_dump() for v in week_volume_targets] if week_volume_targets else None,
                "rationale": rationale,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return f"Week plan saved for {week_id}."


@tool(args_schema=UpdateWeekPlanInput)
@mongo_guarded
def update_week_plan(
    week_id: str,
    rationale: Optional[str] = None,
) -> str:
    """
    Create or replace the weekly plan structure.

    Volume targets and focus are computed deterministically from the confirmed
    month goal and the stored week theme — the LLM must NOT supply these numbers.
    Only week_id (Sunday YYYY-MM-DD) and an optional rationale are needed.
    """
    month_plans_col = get_month_plans_collection()
    month_id = _current_month_id()
    month_doc = month_plans_col.find_one({
        "user_id": get_current_user_id(),
        "month_id": month_id,
    })
    if not month_doc:
        return "ERROR: No confirmed month goal found. Ask the user to set a goal first."

    week_plan_path = month_doc.get("week_plan_path") or []
    if not week_plan_path:
        return (
            "ERROR: Week themes have not been set yet. "
            'Click the "📅 Set Week Themes" button in the app first.'
        )

    month_goal = (
        month_doc.get("confirmed_goal")
        or month_doc.get("staged_goal")
        or month_doc.get("goal")  # field name used by scheduled script & stage_month_goal
    )
    if not month_goal:
        return "ERROR: No confirmed goal data in month document. Ask the user to confirm their goal first."

    total_weeks = _weeks_in_month(month_id)
    week_number = _get_week_number_from_date(week_id)

    # Determine this week's theme from the stored path
    week_theme = next(
        (t["theme"] for t in week_plan_path if t.get("week_number") == week_number),
        "Foundation",
    )

    # Compute all volume targets deterministically — no LLM math
    week_targets = _calculate_week_targets(month_goal, week_number, total_weeks)
    if not week_targets:
        return "ERROR: Could not calculate week targets. Check that the month goal has volume_targets."

    # Build daily entries for each day of the week (Sun–Sat)
    week_id_date = datetime.strptime(week_id, "%Y-%m-%d").date()
    daily_volume_targets: List[DailyVolumeTargetsInput] = []
    for offset in range(7):
        date_str = (week_id_date + timedelta(days=offset)).strftime("%Y-%m-%d")
        targets_for_day = [
            DailyVolumeTargetInput(
                exercise=name,
                unit=data["unit"],
                daily_target=data["daily_target"],
            )
            for name, data in week_targets.items()
        ]
        daily_volume_targets.append(
            DailyVolumeTargetsInput(date=date_str, targets=targets_for_day)
        )

    week_volume_targets: List[WeekVolumeTargetInput] = [
        WeekVolumeTargetInput(
            exercise=name,
            unit=data["unit"],
            week_target=data["week_target"],
        )
        for name, data in week_targets.items()
    ]

    return save_week_plan(
        week_id=week_id,
        focus=week_theme,
        daily_volume_targets=daily_volume_targets,
        week_volume_targets=week_volume_targets,
        rationale=rationale or f"Auto-computed for {week_theme} week (W{week_number}/{total_weeks}).",
        require_theme_path=False,
    )


if __name__ == "__main__":
    print(update_week_plan.invoke({
        "week_id": "2026-07-19",
        "focus": "upper body + conditioning week",
        "daily_volume_targets": [
            {"date": "2026-07-20", "targets": [{"exercise": "pullups", "unit": "reps", "daily_target": 10.0}]},
            {"date": "2026-07-21", "targets": [{"exercise": "pullups", "unit": "reps", "daily_target": 10.0}]},
        ],
        "week_volume_targets": [
            {"exercise": "pullups", "unit": "reps", "week_target": 70.0}
        ],
        "rationale": "test entry",
    }))
    print(get_current_week_plan.invoke({}))
    print("Focus for 2026-07-20:", get_week_focus_for_date("2026-07-20"))
    print("Targets for 2026-07-20:", get_daily_volume_targets_for_date("2026-07-20"))