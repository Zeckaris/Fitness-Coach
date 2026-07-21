"""Metrics tools — V7."""

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from db.mongo_client import get_metrics_collection

DEFAULT_USER_ID = "default_user"


class LogMetricInput(BaseModel):
    metric_name: str = Field(description="e.g. 'body_weight'")
    value: float = Field()
    unit: str = Field(description="e.g. 'kg'")
    date: str = Field(default=None, description="YYYY-MM-DD, omit for today")


@tool(args_schema=LogMetricInput)
def log_metric(metric_name: str, value: float, unit: str, date: str = None) -> str:
    """Log a measurement. Call when user shares weight, distance, lift numbers, etc."""
    collection = get_metrics_collection()
    if date is None:
        date = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d")

    now = datetime.now(ZoneInfo("UTC"))
    collection.update_one(
        {"user_id": DEFAULT_USER_ID, "metric_name": metric_name, "date": date},
        {
            "$set": {"value": value, "unit": unit, "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return f"Logged {metric_name}: {value} {unit} on {date}."


def get_latest_metric(metric_name: str) -> dict | None:
    """Latest entry for a metric. Used by calculate_progress()."""
    collection = get_metrics_collection()
    doc = collection.find_one(
        {"user_id": DEFAULT_USER_ID, "metric_name": metric_name},
        sort=[("date", -1)],
    )
    if not doc:
        return None
    return {"value": doc["value"], "unit": doc["unit"], "date": doc["date"]}


if __name__ == "__main__":
    print(log_metric.invoke({
        "metric_name": "body_weight",
        "value": 76.5,
        "unit": "kg",
    }))
    print(get_latest_metric("body_weight"))