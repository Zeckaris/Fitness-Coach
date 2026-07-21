"""Monthly review pipeline — V7."""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from db.mongo_client import get_plans_collection, get_month_plans_collection, get_backlog_collection, get_checkins_collection, get_metrics_collection
from tools.backlog import sync_backlog
from tools.month_plans import update_month_plan, _current_month_id
from tools.progress import calculate_progress

DEFAULT_USER_ID = "default_user"
LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")


def _previous_month_id() -> str:
    today = datetime.now(LOCAL_TZ).date()
    first_day = today.replace(day=1)
    prev_month = first_day - timedelta(days=1)
    return prev_month.strftime("%Y-%m")


def close_out_month(month_id: str) -> dict:
    """Compute final adherence and metric delta for the closing month."""
    month_plans = get_month_plans_collection()
    doc = month_plans.find_one({"user_id": DEFAULT_USER_ID, "month_id": month_id})
    if not doc:
        return {"status": "no_doc"}

    goal = doc.get("goal") or {}
    progress = calculate_progress(month_id=month_id) if (goal and goal.get("status") == "confirmed") else None

    summary = {
        "month_id": month_id,
        "goal_description": goal.get("description") if goal else None,
        "goal_status": goal.get("status") if goal else None,
        "adherence": progress["adherence"] if progress else None,
        "volume_progress": progress["volume_progress"] if progress else [],
        "metric_progress": progress["metric_progress"] if progress else None,
        "closed_at": datetime.now(ZoneInfo("UTC")),
    }

    month_plans.update_one(
        {"user_id": DEFAULT_USER_ID, "month_id": month_id},
        {"$set": {"close_out_summary": summary, "updated_at": datetime.now(ZoneInfo("UTC"))}},
    )
    return summary


def refresh_week_themes() -> str:
    """Set the standard 4-week theme path for the current month."""
    month_plans = get_month_plans_collection()
    current_month = _current_month_id()
    doc = month_plans.find_one({"user_id": DEFAULT_USER_ID, "month_id": current_month})

    if not doc or not doc.get("goal") or doc["goal"].get("status") != "confirmed":
        return "No confirmed goal for this month. Set a goal first."

    week_plan_path = [
        {"week_number": 1, "theme": "Volume"},
        {"week_number": 2, "theme": "Intensity"},
        {"week_number": 3, "theme": "Deload"},
        {"week_number": 4, "theme": "Peak"},
    ]

    month_plans.update_one(
        {"user_id": DEFAULT_USER_ID, "month_id": current_month},
        {"$set": {
            "week_plan_path": week_plan_path,
            "updated_at": datetime.now(ZoneInfo("UTC"))
        }}
    )
    return f"Week themes set for {current_month}: Volume → Intensity → Deload → Peak"


def run_monthly_review() -> str:
    sync_backlog()
    prev_month = _previous_month_id()
    summary = close_out_month(prev_month)

    result = refresh_week_themes()
    return f"Closed out {prev_month}.\n{result}"


if __name__ == "__main__":
    print(run_monthly_review())