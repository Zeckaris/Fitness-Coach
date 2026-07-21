"""Progress tools — V7."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from langchain_core.tools import tool

from db.mongo_client import get_plans_collection, get_month_plans_collection
from tools.metrics import get_latest_metric

DEFAULT_USER_ID = "default_user"
LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")
ADHERENCE_WINDOW_DAYS = 7


def _current_month_id() -> str:
    return datetime.now(LOCAL_TZ).date().strftime("%Y-%m")


def _is_exercise_complete(ex: dict) -> bool:
    target = ex.get("target_quantity")
    if target is not None:
        return ex.get("completed_quantity", 0) >= target
    return ex.get("completed", False)


def _calculate_adherence() -> dict:
    plans = get_plans_collection()
    today = datetime.now(LOCAL_TZ).date()
    window_start = (today - timedelta(days=ADHERENCE_WINDOW_DAYS)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")

    docs = plans.find({
        "user_id": DEFAULT_USER_ID,
        "status": {"$in": ["planned", "completed"]},
        "date": {"$gte": window_start, "$lte": today_str},
    })

    planned_count = 0
    completed_count = 0
    for doc in docs:
        exercises = doc.get("exercises", [])
        is_completed = doc.get("status") == "completed"
        for ex in exercises:
            planned_count += 1
            if is_completed or _is_exercise_complete(ex):
                completed_count += 1

    pct = (completed_count / planned_count * 100) if planned_count else None
    return {
        "window_days": ADHERENCE_WINDOW_DAYS,
        "planned_count": planned_count,
        "completed_count": completed_count,
        "adherence_pct": pct,
    }


def _calculate_volume_progress(goal: dict, month_id: str) -> list:
    volume_targets = goal.get("volume_targets") or []
    if not volume_targets:
        return []

    plans = get_plans_collection()

    results = []
    for vt in volume_targets:
        docs = plans.find({
            "user_id": DEFAULT_USER_ID,
            "date": {"$regex": f"^{month_id}"},
            "exercises.name": vt["exercise"],
        })
        completed_so_far = 0
        for doc in docs:
            for ex in doc.get("exercises", []):
                if ex.get("name") == vt["exercise"]:
                    completed_so_far += ex.get("completed_quantity", 0)

        month_target = vt["month_target"]
        pct = (completed_so_far / month_target * 100) if month_target else None
        results.append({
            "exercise": vt["exercise"],
            "unit": vt["unit"],
            "month_target": month_target,
            "completed_so_far": completed_so_far,
            "pct": pct,
        })
    return results


def _calculate_metric_progress(goal: dict) -> Optional[dict]:
    metric_name = goal.get("metric_name")
    if not metric_name:
        return None

    baseline = goal.get("baseline_value")
    target = goal.get("target_value")
    latest = get_latest_metric(metric_name)

    if latest is None or baseline is None or target is None or target == 0:
        return {
            "metric_name": metric_name,
            "baseline_value": baseline,
            "target_value": target,
            "latest_value": latest["value"] if latest else None,
            "pct": None,
        }

    pct = (latest["value"] - baseline) / target * 100
    return {
        "metric_name": metric_name,
        "baseline_value": baseline,
        "target_value": target,
        "unit": goal.get("unit"),
        "latest_value": latest["value"],
        "latest_date": latest["date"],
        "pct": pct,
    }


def calculate_progress(month_id: str = None) -> dict:
    """
    Compute progress for a given month. Defaults to current month.
    """
    if month_id is None:
        month_id = _current_month_id()

    month_plans = get_month_plans_collection()
    doc = month_plans.find_one({"user_id": DEFAULT_USER_ID, "month_id": month_id})
    goal = doc.get("goal") if doc else None
    has_confirmed_goal = bool(goal) and goal.get("status") == "confirmed"

    return {
        "adherence": _calculate_adherence(),
        "has_confirmed_goal": has_confirmed_goal,
        "goal_description": goal.get("description") if has_confirmed_goal else None,
        "volume_progress": _calculate_volume_progress(goal, month_id) if has_confirmed_goal else [],
        "metric_progress": _calculate_metric_progress(goal) if has_confirmed_goal else None,
    }


def format_progress(data: dict) -> str:
    lines = []
    adherence = data["adherence"]
    if adherence["adherence_pct"] is None:
        lines.append(f"No planned exercises in the last {adherence['window_days']} days.")
    else:
        lines.append(
            f"Adherence (last {adherence['window_days']} days): "
            f"{adherence['completed_count']}/{adherence['planned_count']} exercises "
            f"({adherence['adherence_pct']:.0f}%)"
        )

    if not data["has_confirmed_goal"]:
        lines.append("No confirmed goal set for this month.")
        return "\n".join(lines)

    lines.append(f"Goal: {data['goal_description']}")

    for vp in data["volume_progress"]:
        pct_str = f"{vp['pct']:.0f}%" if vp["pct"] is not None else "n/a"
        lines.append(
            f"{vp['exercise']}: {vp['completed_so_far']}/{vp['month_target']} {vp['unit']} "
            f"this month ({pct_str})"
        )

    mp = data["metric_progress"]
    if mp:
        if mp["pct"] is None:
            lines.append(f"{mp['metric_name']}: no readings logged yet.")
        else:
            lines.append(
                f"{mp['metric_name']}: {mp['latest_value']} {mp.get('unit', '')} as of "
                f"{mp['latest_date']} ({mp['pct']:.0f}% of the way to target)"
            )

    return "\n".join(lines)


@tool
def get_progress_summary() -> str:
    """Adherence (last 7 days) + goal progress. Use when user asks how they are doing."""
    return format_progress(calculate_progress())


if __name__ == "__main__":
    print(get_progress_summary.invoke({}))