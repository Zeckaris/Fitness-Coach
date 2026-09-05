"""
Deterministic (non-LLM) baseline-to-month-target calculator, used only
by the FIRST-TIME goal-setup path (stage_month_goal's volume_targets).
Month-over-month progression for users who already have a confirmed
goal is a separate, already-solved problem — see utils/goal_progression.py
(progress_goal) — which this module does not touch or duplicate.

The LLM is responsible for one thing only:
  1. Extracting a baseline_value from what the user actually said
     (e.g. "I can do 30 pushups"), or omitting it if the user never
     said anything for that movement — this module fills the gap with
     a conservative beginner default, never the LLM.

experience_level is authoritatively supplied from the user's stored
profile (populated during onboarding baseline assessment), never inferred
by the LLM or defaulted to beginner.

Everything downstream — the actual month_target number — is pure arithmetic.
No LLM call is involved in producing month_target.
"""

from typing import Literal, Optional

BalanceArea = Literal["upper_body", "lower_body", "core", "cardio"]
ExperienceLevel = Literal["beginner", "intermediate", "advanced"]

# Used ONLY when neither the LLM nor the user's stored onboarding baseline
# provides a value for this area (see baseline_value_from_markers). Deliberately
# conservative — these represent a realistic beginner single-set/single-effort
# capacity, not a target.
BEGINNER_BASELINE_BY_AREA: dict[BalanceArea, float] = {
    "upper_body": 8,     # e.g. push-ups, rows: reps in one set
    "lower_body": 10,    # e.g. lunges, squats: reps in one set
    "core": 12,          # e.g. plank-with-twist reps, sit-ups: reps in one set
    "cardio": 1.5,       # km covered in one continuous effort
}

# Beginner single-set default for REP-BASED cardio exercises (e.g. Mountain
# Climber, jump rope, Astride Jumps). Distinct from BEGINNER_BASELINE_BY_AREA
# ["cardio"] (1.5), which is a running-distance in km and must never be reused
# as a rep count. Chosen by comparison with the core default (12 reps for a
# single set of a bodyweight move): full-body bodyweight cardio movements are
# sustained at higher rep counts than isometric/strength core moves, and 20
# matches the workout library's own canonical per-set baseline for Mountain
# Climber (3 sets x 20 reps). 20 reps is a realistic moderate single set for a
# beginner doing this class of movement.
BEGINNER_BASELINE_CARDIO_REPS = 20

# Maps each balance area to the onboarding raw marker that best represents
# the user's single-effort capacity for that area. These are the fields
# captured by the onboarding form and stored under
# baseline_assessment.answers.raw_markers.
#
#   upper_body -> push_ups        (max consecutive push-ups, reps)
#   lower_body -> squats          (max consecutive bodyweight squats, reps)
#   cardio     -> run_distance_km (distance of the longest continuous run, km)
#   core       -> None            (no core/plank question exists at onboarding;
#                                  falls back to the beginner default)
#
# JUDGMENT CALLS:
#   - core has no direct marker, so it deliberately maps to None and inherits
#     the conservative BEGINNER_BASELINE_BY_AREA["core"] default.
#   - cardio uses run_distance_km, NOT run_minutes. Duration (minutes) and
#     distance (km) are different units; the cardio "Continuous Run" exercise
#     tracks km, so its volume baseline must come from a distance marker. The
#     duration marker (run_minutes) is left untouched for experience-level
#     scoring. Assessments recorded before this distance question existed have
#     no run_distance_km, so cardio falls back to the beginner default (same
#     behavior as the core marker gap).
AREA_TO_RAW_MARKER: dict[BalanceArea, Optional[str]] = {
    "upper_body": "push_ups",
    "lower_body": "squats",
    "core": None,
    "cardio": "run_distance_km",
}


def baseline_value_from_markers(
    raw_markers: Optional[dict], balance_area: BalanceArea
) -> Optional[float]:
    """
    Resolve the user's stored single-effort baseline for a balance area from
    their onboarding raw_markers.

    Returns None when the area has no mapped marker (e.g. core), when the
    marker is missing/unset, or when the recorded value is non-positive
    (0 push-ups etc.) — in every such case the caller should fall back to the
    conservative beginner default rather than build a target off a zero.
    """
    marker_key = AREA_TO_RAW_MARKER.get(balance_area)
    if not marker_key:
        return None
    value = (raw_markers or {}).get(marker_key)
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value

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
    experience_level: ExperienceLevel,
    baseline_value: Optional[float] = None,
    sessions_per_week: int = DEFAULT_SESSIONS_PER_WEEK,
    sets_per_session: Optional[int] = None,
    weeks_in_month: float = 4.35,
    default_baseline: Optional[float] = None,
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

    When baseline_value is None and a non-None default_baseline is supplied, it
    is used in place of BEGINNER_BASELINE_BY_AREA[balance_area]. This lets
    callers override the area default for a specific sub-category (e.g.
    rep-based cardio exercises, which need a rep-count default, not the
    distance-in-km cardio default).
    """
    if baseline_value is not None:
        baseline_used = baseline_value
        baseline_source = "provided"
    else:
        baseline_used = (
            default_baseline
            if default_baseline is not None
            else BEGINNER_BASELINE_BY_AREA[balance_area]
        )
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