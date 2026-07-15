"""
record_checkin — V4's persistence tool.

V4 scope: extracts structured fields from the user's daily update and
upserts them into MongoDB, one document per (user_id, date). Existing
scalar fields are overwritten only when a new value is provided this turn;
beverages_consumed is appended to, not replaced, so multiple mentions in
the same day accumulate instead of overwriting each other.

Write-only for V4 - reading check-in history back is V5's job.

V5 fix: _today_str() now computes the date in the user's local timezone
(hardcoded to Africa/Addis_Ababa for now - single user, no profile yet)
instead of UTC. 

KNOWN DEBT: once multi-user support exists, this hardcoded timezone must
become a per-user setting (see user_id, same placeholder situation) -
tracked alongside it as a known TO DO for a future version.

V6 fix: beverages_consumed now defaults to an empty array ([]) on first
insert, not None. It's a list field, so null was never a semantically
correct "nothing yet" - and it broke on the very next check-in that DID
mention a beverage: MongoDB's $push requires the target field to already
be an array, so pushing into a null field raised a WriteError ("field
'beverages_consumed' must be an array but is of type null"). Any doc
created before this fix still has beverages_consumed: null baked in and
needs a one-time manual correction (set it to [] in Mongo, or delete and
let it regenerate) - this fix only prevents the bug going forward.
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
    name: str = Field(description="Beverage name, e.g. 'coke', 'orange_juice', 'black_coffee'.")
    amount_ml: int = Field(description="Volume consumed in this instance, in milliliters.")


class CheckinInput(BaseModel):
    """Structured fields extracted from the user's check-in message."""

    sickness: Optional[str] = Field(
        default=None, description="Sickness mentioned, e.g. 'cold', 'fever'. Omit if none."
    )
    injury: Optional[str] = Field(
        default=None, description="Injury or pain mentioned, e.g. 'shoulder pain'. Omit if none."
    )
    fatigue: Optional[str] = Field(
        default=None,
        description="Fatigue/sleep issue in the user's own words, e.g. 'slept 4 hours', "
        "'feeling exhausted'. Omit if not mentioned.",
    )
    equipment_note: Optional[str] = Field(
        default=None,
        description="Situational equipment constraint for TODAY only, e.g. 'traveling, no "
        "gym access'. Do NOT use for the user's normally owned equipment.",
    )
    time_constraint_minutes: Optional[int] = Field(
        default=None, description="Minutes available for a workout today, if mentioned."
    )
    beverages_consumed: Optional[List[Beverage]] = Field(
        default=None,
        description="Tracked beverages consumed, each as its own entry with volume in ml. "
        "E.g. two separate cokes -> two separate entries, not one combined entry.",
    )
    protein_adequate: Optional[bool] = Field(
        default=None, description="Whether the user reports adequate protein intake."
    )
    water_glasses: Optional[int] = Field(
        default=None, description="Number of glasses of water the user reports drinking."
    )
    raw_message: str = Field(description="The user's original message, verbatim.")


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
    Record or update today's check-in with any new info from the user's message.

    Call this whenever the user shares something loggable about their day -
    sickness, injury, fatigue, time constraints, equipment situation,
    beverages, protein, or water intake. Only include fields you actually
    have information for; omit the rest. Skip this tool entirely for pure
    small talk with nothing loggable in it.
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