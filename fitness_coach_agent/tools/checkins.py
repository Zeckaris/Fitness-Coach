"""
record_checkin — Stores today's structured check-in.

Updates one document per (user_id, date). Provided scalar fields overwrite
previous values; beverages_consumed is appended.

Write-only. Reading past check-ins is handled by get_recent_checkins.
"""

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from db.mongo_client import get_checkins_collection

# Placeholder until multi-user support exists.
DEFAULT_USER_ID = "default_user"

# Hardcoded until per-user timezone support exists (see module docstring).
LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")

# Scalar fields that default to null on first creation of a day's doc.
# beverages_consumed is handled separately (see record_checkin below) since
# it's a list field and must default to [], not None.
_SCALAR_FIELDS = [
    "sickness", "injury", "fatigue", "equipment_note",
    "time_constraint_minutes", "protein_adequate", "water_glasses",
]


class Beverage(BaseModel):
    name: str = Field(description="Beverage name.")
    amount_ml: int = Field(description="Amount in milliliters.")


class CheckinInput(BaseModel):
    """Structured check-in fields."""

    sickness: Optional[str] = Field(
        default=None, description="Sickness."
    )
    injury: Optional[str] = Field(
        default=None, description="Injury or pain."
    )
    fatigue: Optional[str] = Field(
        default=None,
        description="Fatigue or sleep issue.",
    )
    equipment_note: Optional[str] = Field(
        default=None,
        description="Today's equipment limitation.",
    )
    time_constraint_minutes: Optional[int] = Field(
        default=None, description="Workout time available today."
    )
    beverages_consumed: Optional[List[Beverage]] = Field(
        default=None,
        description="One entry per beverage consumed.",
    )
    protein_adequate: Optional[bool] = Field(
        default=None, description="Adequate protein intake."
    )
    water_glasses: Optional[int] = Field(
        default=None, description="Glasses of water consumed."
    )
    raw_message: str = Field(description="User's original message.")


def _today_str() -> str:
    """Today's date string in the user's local timezone (see module docstring)."""
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


@tool(args_schema=CheckinInput)
def record_checkin(
    raw_message: str,
    sickness: Optional[str] = None,
    injury: Optional[str] = None,
    fatigue: Optional[str] = None,
    equipment_note: Optional[str] = None,
    time_constraint_minutes: Optional[int] = None,
    beverages_consumed: Optional[List[Beverage]] = None,
    protein_adequate: Optional[bool] = None,
    water_glasses: Optional[int] = None,
) -> str:
    """
    Record today's check-in.

    Include only fields mentioned by the user. Skip for messages with no
    loggable information.
    """
    collection = get_checkins_collection()
    date = _today_str()

    scalar_values = {
        "sickness": sickness,
        "injury": injury,
        "fatigue": fatigue,
        "equipment_note": equipment_note,
        "time_constraint_minutes": time_constraint_minutes,
        "protein_adequate": protein_adequate,
        "water_glasses": water_glasses,
    }
    scalar_updates = {k: v for k, v in scalar_values.items() if v is not None}

    set_fields = {
        "user_id": DEFAULT_USER_ID,
        "date": date,
        "timestamp": datetime.now(ZoneInfo("UTC")),
        "raw_message": raw_message,
        **scalar_updates,
    }

    setoninsert_fields = [f for f in _SCALAR_FIELDS if f not in scalar_updates]
    setoninsert_doc = {f: None for f in setoninsert_fields}
    if not beverages_consumed:
        # List field - "nothing yet" is [], never null. Without this, the
        # first $push on a later turn fails (see V6 fix note above).
        setoninsert_doc["beverages_consumed"] = []

    update_doc = {
        "$set": set_fields,
        "$setOnInsert": setoninsert_doc,
    }

    if beverages_consumed:
        update_doc["$push"] = {
            "beverages_consumed": {"$each": [b.model_dump() for b in beverages_consumed]}
        }

    collection.update_one(
        {"user_id": DEFAULT_USER_ID, "date": date},
        update_doc,
        upsert=True,
    )

    logged = list(scalar_updates.keys())
    if beverages_consumed:
        logged.append("beverages_consumed")

    return f"Check-in recorded for {date}. Logged: {', '.join(logged) if logged else 'nothing new'}."


# Quick manual test: python -m tools.checkins
if __name__ == "__main__":
    print(
        record_checkin.invoke(
            {
                "raw_message": "I have a cold and drank a Fanta",
                "sickness": "cold",
                "beverages_consumed": [{"name": "fanta", "amount_ml": 330}],
            }
        )
    )