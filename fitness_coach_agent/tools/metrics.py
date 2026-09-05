"""Metrics tools — V7."""

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from db.mongo_client import get_metrics_collection
from db.guards import mongo_guarded
from auth.context import get_current_user_id



class BloodPressure(BaseModel):
    systolic: int = Field(description="Systolic pressure (top number)")
    diastolic: int = Field(description="Diastolic pressure (bottom number)")

class LogMetricInput(BaseModel):
    metric_name: str = Field(description="e.g. 'body_weight', 'height', 'blood_pressure'")
    value: Optional[float] = Field(default=None, description="Numeric for most metrics (omit for blood pressure)")
    unit: Optional[str] = Field(default=None, description="e.g. 'kg', 'cm' (omit for blood pressure)")
    blood_pressure: Optional[BloodPressure] = Field(default=None, description="Provide only if metric_name is 'blood_pressure'")
    date: str = Field(default=None, description="YYYY-MM-DD, omit for today")


@tool(args_schema=LogMetricInput)
@mongo_guarded
def log_metric(metric_name: str, value: float = None, unit: str = None, blood_pressure: dict = None, date: str = None) -> str:
    """Log a measurement. Call when user shares weight, distance, lift numbers, height, blood pressure, etc."""
    collection = get_metrics_collection()
    if date is None:
        date = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d")

    now = datetime.now(ZoneInfo("UTC"))
    
    set_fields = {"updated_at": now}
    if value is not None:
        set_fields["value"] = value
    if unit is not None:
        set_fields["unit"] = unit
    if blood_pressure is not None:
        bp_dict = blood_pressure if isinstance(blood_pressure, dict) else blood_pressure.model_dump()
        set_fields["blood_pressure"] = bp_dict

    collection.update_one(
        {"user_id": get_current_user_id(), "metric_name": metric_name, "date": date},
        {
            "$set": set_fields,
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    
    if blood_pressure is not None:
        bp_dict = set_fields["blood_pressure"]
        return f"Logged {metric_name}: {bp_dict['systolic']}/{bp_dict['diastolic']} on {date}."
    else:
        return f"Logged {metric_name}: {value} {unit} on {date}."


def get_latest_metric(metric_name: str) -> dict | None:
    """Latest entry for a metric. Used by calculate_progress()."""
    collection = get_metrics_collection()
    doc = collection.find_one(
        {"user_id": get_current_user_id(), "metric_name": metric_name},
        sort=[("date", -1)],
    )
    if not doc:
        return None
        
    res = {"date": doc["date"]}
    if "value" in doc:
        res["value"] = doc["value"]
    if "unit" in doc:
        res["unit"] = doc["unit"]
    if "blood_pressure" in doc:
        res["blood_pressure"] = doc["blood_pressure"]
        
    return res


if __name__ == "__main__":
    print(log_metric.invoke({
        "metric_name": "body_weight",
        "value": 76.5,
        "unit": "kg",
    }))
    print(get_latest_metric("body_weight"))