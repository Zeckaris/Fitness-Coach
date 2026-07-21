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

DEFAULT_USER_ID = "default_user"
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

    from db.mongo_client import get_plans_collection
    plans = get_plans_collection()
    month_id = _current_month_id()

    results = {}
    for vt in volume_targets:
        month_target = vt["month_target"]
        exercise = vt["exercise"]
        unit = vt["unit"]

        docs = plans.find({
            "user_id": DEFAULT_USER_ID,
            "date": {"$regex": f"^{month_id}"},
            "exercises.name": exercise,
        })
        completed_so_far = 0
        for doc in docs:
            for ex in doc.get("exercises", []):
                if ex.get("name") == exercise:
                    completed_so_far += ex.get("completed_quantity", 0)

        remaining = max(0, month_target - completed_so_far)
        remaining_weeks = max(1, total_weeks - week_number + 1)
        week_target = remaining / remaining_weeks
        block_target = week_target / 2

        results[exercise] = {
            "unit": unit,
            "week_target": round(week_target, 1),
            "block_target": round(block_target, 1),
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
        "user_id": DEFAULT_USER_ID,
        "week_id": week_id
    })
    return existing is not None


def _find_week_doc_for_date(date_str: str) -> Optional[dict]:
    collection = get_week_plans_collection()
    doc = collection.find_one({"user_id": DEFAULT_USER_ID, "blocks.dates": date_str})
    if doc:
        return doc
    return collection.find_one({"user_id": DEFAULT_USER_ID}, sort=[("week_id", -1)])


def get_week_focus_for_date(date_str: str) -> Optional[str]:
    doc = _find_week_doc_for_date(date_str)
    if not doc:
        return None
    for block in doc.get("blocks", []):
        if date_str in block.get("dates", []):
            return block.get("focus")
    return None


def format_week_plan(doc: Optional[dict]) -> str:
    if not doc:
        return "No week plan yet."
    lines = [f"Week of {doc['week_id']}:"]
    for block in doc.get("blocks", []):
        dr = f"{block['dates'][0]} to {block['dates'][-1]}"
        lines.append(f"Block {block['block_number']} ({dr}): {block['focus']}")
        for vt in block.get("block_volume_targets") or []:
            lines.append(f"  - {vt['exercise']}: {vt['block_target']} {vt['unit']}")
    if doc.get("rationale"):
        lines.append(f"Rationale: {doc['rationale']}")
    return "\n".join(lines)


@tool
def get_current_week_plan() -> str:
    """Current week block structure. Use when user asks for week detail beyond context."""
    today_str = datetime.now(LOCAL_TZ).date().strftime("%Y-%m-%d")
    doc = _find_week_doc_for_date(today_str)
    return format_week_plan(doc)


class VolumeTargetInput(BaseModel):
    exercise: str
    unit: str
    block_target: float


class BlockInput(BaseModel):
    block_number: int = Field(description="1 or 2")
    dates: List[str] = Field(description="3 dates YYYY-MM-DD")
    focus: str = Field(description="Training focus")
    block_volume_targets: Optional[List[VolumeTargetInput]] = Field(default=None)


class UpdateWeekPlanInput(BaseModel):
    week_id: str = Field(description="Sunday date YYYY-MM-DD")
    blocks: List[BlockInput] = Field(description="Two 3-day blocks")
    week_volume_targets: Optional[List[VolumeTargetInput]] = Field(default=None)
    rationale: Optional[str] = Field(default=None)


@tool(args_schema=UpdateWeekPlanInput)
def update_week_plan(
    week_id: str,
    blocks: List[BlockInput],
    week_volume_targets: Optional[List[VolumeTargetInput]] = None,
    rationale: Optional[str] = None,
) -> str:
    """Create or replace the weekly block structure."""
    # Guard: week_plan_path must be set by monthly review
    month_plans = get_month_plans_collection()
    month_doc = month_plans.find_one({
        "user_id": DEFAULT_USER_ID,
        "month_id": _current_month_id()
    })
    week_plan_path = month_doc.get("week_plan_path") if month_doc else None
    if not week_plan_path:
        return (
            "ERROR: Week themes have not been set yet. "
            'Click the "📅 Set Week Themes" button in the app first, then ask me to generate your weekly plan.'
        )

    if len(blocks) != 2:
        return "Need exactly 2 blocks."

    collection = get_week_plans_collection()
    now = datetime.now(ZoneInfo("UTC"))
    collection.update_one(
        {"user_id": DEFAULT_USER_ID, "week_id": week_id},
        {
            "$set": {
                "blocks": [b.model_dump() for b in blocks],
                "week_volume_targets": [v.model_dump() for v in week_volume_targets] if week_volume_targets else None,
                "rationale": rationale,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return f"Week plan saved for {week_id}."


if __name__ == "__main__":
    print(update_week_plan.invoke({
        "week_id": "2026-07-19",
        "blocks": [
            {"block_number": 1, "dates": ["2026-07-20", "2026-07-21", "2026-07-22"], "focus": "upper body volume"},
            {"block_number": 2, "dates": ["2026-07-23", "2026-07-24", "2026-07-25"], "focus": "lower body + conditioning"},
        ],
        "rationale": "test entry",
    }))
    print(get_current_week_plan.invoke({}))
    print("Focus for 2026-07-21:", get_week_focus_for_date("2026-07-21"))