"""Backlog tools — V7."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from db.mongo_client import get_plans_collection, get_backlog_collection

DEFAULT_USER_ID = "default_user"
LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")


def sync_backlog() -> None:
    """Sweep past planned days for incomplete exercises. Idempotent."""
    plans = get_plans_collection()
    backlog = get_backlog_collection()
    today_str = datetime.now(LOCAL_TZ).date().strftime("%Y-%m-%d")
    now = datetime.now(ZoneInfo("UTC"))

    past_days = plans.find({
        "user_id": DEFAULT_USER_ID,
        "status": "planned",
        "date": {"$lt": today_str},
    })

    for day in past_days:
        for ex in day.get("exercises", []):
            target = ex.get("target_quantity")
            completed_qty = ex.get("completed_quantity", 0)
            completed_bool = ex.get("completed", False)

            if target is not None:
                deficit = target - completed_qty
                is_incomplete = deficit > 0
            else:
                deficit = None
                is_incomplete = not completed_bool

            existing = backlog.find_one({
                "user_id": DEFAULT_USER_ID,
                "exercise_name": ex["name"],
                "status": {"$in": ["open", "reinserted"]},
            })

            if is_incomplete:
                if existing is None:
                    backlog.insert_one({
                        "user_id": DEFAULT_USER_ID,
                        "source_date": day["date"],
                        "exercise_name": ex["name"],
                        "focus": ex.get("focus"),
                        "day_focus_area": day.get("focus_area"),
                        "target_quantity": target,
                        "unit": ex.get("unit"),
                        "completed_quantity": completed_qty,
                        "deficit": deficit,
                        "status": "open",
                        "attempts": 0,
                        "reinserted_date": None,
                        "created_at": now,
                        "updated_at": now,
                        "resolved_at": None,
                    })
                elif existing["status"] == "reinserted":
                    backlog.update_one(
                        {"_id": existing["_id"]},
                        {
                            "$set": {
                                "status": "open",
                                "source_date": day["date"],
                                "completed_quantity": completed_qty,
                                "deficit": deficit,
                                "reinserted_date": None,
                                "updated_at": now,
                            },
                            "$inc": {"attempts": 1},
                        },
                    )
                else:
                    backlog.update_one(
                        {"_id": existing["_id"]},
                        {"$set": {
                            "completed_quantity": completed_qty,
                            "deficit": deficit,
                            "updated_at": now,
                        }},
                    )
            else:
                if existing is not None:
                    backlog.update_one(
                        {"_id": existing["_id"]},
                        {"$set": {
                            "status": "resolved",
                            "resolved_at": now,
                            "updated_at": now,
                        }},
                    )


def format_backlog_item(doc: dict) -> str:
    if doc.get("target_quantity") is not None:
        return f"{doc['exercise_name']}: {doc['deficit']} {doc['unit']} owed (missed {doc['source_date']}, attempt {doc['attempts']})"
    return f"{doc['exercise_name']}: not completed (missed {doc['source_date']}, attempt {doc['attempts']})"


@tool
def get_backlog() -> str:
    """Open backlog items. Call during plan generation (step c), after get_past_plans. Cap at 2 per day."""
    backlog = get_backlog_collection()
    items = list(backlog.find({"user_id": DEFAULT_USER_ID, "status": "open"}).sort("attempts", -1))
    if not items:
        return "No open backlog items."
    return "\n".join(format_backlog_item(i) for i in items)


class MarkBacklogReinsertedInput(BaseModel):
    exercise_name: str = Field(description="Exact name from get_backlog")
    reinserted_date: str = Field(description="Plan date YYYY-MM-DD")


@tool(args_schema=MarkBacklogReinsertedInput)
def mark_backlog_reinserted(exercise_name: str, reinserted_date: str) -> str:
    """Mark backlog item reinserted after placing it in a plan."""
    backlog = get_backlog_collection()
    result = backlog.update_one(
        {"user_id": DEFAULT_USER_ID, "exercise_name": exercise_name, "status": "open"},
        {"$set": {
            "status": "reinserted",
            "reinserted_date": reinserted_date,
            "updated_at": datetime.now(ZoneInfo("UTC")),
        }},
    )
    if result.matched_count == 0:
        return f"No open backlog item for '{exercise_name}'."
    return f"'{exercise_name}' marked reinserted for {reinserted_date}."


if __name__ == "__main__":
    sync_backlog()
    print("Backlog after sync:")
    print(get_backlog.invoke({}))