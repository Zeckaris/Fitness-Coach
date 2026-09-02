"""
Deterministic (non-LLM) baseline-to-month-target calculator, used only
by the FIRST-TIME goal-setup path (stage_month_goal's volume_targets).
Month-over-month progression for users who already have a confirmed
goal is a separate, already-solved problem — see utils/goal_progression.py
(progress_goal) — which this module does not touch or duplicate.

The LLM is responsible for two things only:
  1. Extracting a baseline_value from what the user actually said
     (e.g. "I can do 30 pushups"), or omitting it if the user never
     said anything for that movement — this module fills the gap with
     a conservative beginner default, never the LLM.
  2. Picking experience_level from context clues (explicit statement,
     or inferred from baseline magnitude) — again, only a label; this
     module still requires that label to select the correct intensity
     factor.

Everything downstream of those two LLM judgment calls — the actual
month_target number — is pure arithmetic. No LLM call is involved in
producing month_target.
"""

from typing import Literal, Optional

BalanceArea = Literal["upper_body", "lower_body", "core", "cardio"]
ExperienceLevel = Literal["beginner", "intermediate", "advanced"]

# Used ONLY when the LLM has no baseline_value for this exercise (user
# never mentioned it). Deliberately conservative — these represent a
# realistic beginner single-set/single-effort capacity, not a target.
BEGINNER_BASELINE_BY_AREA: dict[BalanceArea, float] = {
    "upper_body": 8,     # e.g. push-ups, rows: reps in one set
    "lower_body": 10,    # e.g. lunges, squats: reps in one set
    "core": 12,          # e.g. plank-with-twist reps, sit-ups: reps in one set
    "cardio": 1.5,       # km covered in one continuous effort
}

# Fraction of single-set/single-effort max used per *working* set.
INTENSITY_FACTOR: dict[ExperienceLevel, float] = {
    "beginner": 0.75,
    "intermediate": 0.80,
    "advanced": 0.85,
}

DEFAULT_SESSIONS_PER_WEEK = 4
DEFAULT_SETS_PER_SESSION_BY_UNIT = {
    "reps": 3,
    "seconds": 3,
}
_CONTINUOUS_UNITS = {"km", "m", "mi"}
WeekThemeEnum = Literal["Foundation", "Volume", "Intensity", "Peak", "Deload"]

THEME_WEIGHT_MATRIX: dict[str, float] = {
    "Foundation": 1.00,
    "Volume": 1.15,
    "Intensity": 1.00,
    "Peak": 1.25,
    "Deload": 0.60,
}

THEME_SETS_CONFIG: dict[str, int] = {
    "Foundation": 3,
    "Volume": 5,
    "Intensity": 2,
    "Peak": 4,
    "Deload": 2,
}


def get_theme_dosing_structure(theme: Optional[str], target_qty: int) -> tuple[int, int]:
    """
    Returns (sets, reps_per_set) based on week theme and target quantity.
    """
    sets = THEME_SETS_CONFIG.get(theme or "Foundation", 3)
    if target_qty <= 0:
        return (sets, 0)
    reps_per_set = max(1, round(target_qty / sets))
    return (sets, reps_per_set)


def calculate_month_target(
    unit: str,
    balance_area: BalanceArea,
    experience_level: ExperienceLevel = "beginner",
    baseline_value: Optional[float] = None,
    sessions_per_week: int = DEFAULT_SESSIONS_PER_WEEK,
    sets_per_session: Optional[int] = None,
    weeks_in_month: float = 4.35,
) -> dict:
    """
    Pure function. Returns:
        {
            "month_target": float,
            "baseline_used": float,
            "baseline_source": "provided" | "beginner_default",
            "intensity_factor": float,
            "sets_per_session": int,
            "sessions_per_week": int,
            "weeks_in_month": float,
        }

    Formula: month_target = baseline * intensity_factor * sets_per_session
                             * sessions_per_week * weeks_in_month
    """
    if baseline_value is not None:
        baseline_used = baseline_value
        baseline_source = "provided"
    else:
        baseline_used = BEGINNER_BASELINE_BY_AREA[balance_area]
        baseline_source = "beginner_default"

    if sets_per_session is None:
        if unit in _CONTINUOUS_UNITS:
            sets_per_session = 1
        else:
            sets_per_session = DEFAULT_SETS_PER_SESSION_BY_UNIT.get(unit, 3)

    intensity = INTENSITY_FACTOR[experience_level]

    month_target = (
        baseline_used * intensity * sets_per_session * sessions_per_week * weeks_in_month
    )

    if unit in _CONTINUOUS_UNITS:
        month_target = round(month_target, 1)
    else:
        month_target = round(month_target)

    return {
        "month_target": month_target,
        "baseline_used": baseline_used,
        "baseline_source": baseline_source,
        "intensity_factor": intensity,
        "sets_per_session": sets_per_session,
        "sessions_per_week": sessions_per_week,
        "weeks_in_month": weeks_in_month,
    }