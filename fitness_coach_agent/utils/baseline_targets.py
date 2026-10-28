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

import re
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

# Beginner defaults for core exercises:
# Distinct defaults for timed (seconds) vs rep-based core exercises.
# Reps and seconds are fundamentally different units and must never be mixed.
BEGINNER_BASELINE_CORE_SECONDS = 30
BEGINNER_BASELINE_CORE_REPS = 12

# ---------------------------------------------------------------------------
# Per-exercise target tunables (single place for all goal-volume Knobs)
# ---------------------------------------------------------------------------

# Bodyweight movements use the user's own baseline for that movement type,
# then scale it by the exercise's difficulty. Easier variations get MORE reps
# (> 1.0), harder variations get FEWER (< 1.0). Names are lower-case.
BODYWEIGHT_DIFFICULTY_FACTORS: dict[str, float] = {
    "incline push-up": 1.35,
    "bench dip": 1.15,
    "push-up": 1.0,
    "close-grip push-up": 0.9,
    "decline push-up": 0.85,
    "split squats": 0.9,
    "squat to overhead reach": 1.0,
}
# Weighted, pull, isolation, core and rep-based cardio exercises start from the
# workout library's own default reps for that exercise. The library's
# difficulty labels are not trusted for these categories, so experience only
# applies a small adjustment around the library default.
LIBRARY_REP_EXPERIENCE_FACTOR: dict[ExperienceLevel, float] = {
    "beginner": 0.9,
    "intermediate": 1.0,
    "advanced": 1.1,
}

# Focus and maintenance volume. Exercises in the goal's focus area are scaled
# by FOCUS_VOLUME_MULTIPLIER (default unchanged), and exercises outside it get
# MAINTENANCE_VOLUME_MULTIPLIER of their focus-equivalent volume. Balanced
# goals treat every exercise as full volume.
FOCUS_VOLUME_MULTIPLIER = 1.0
MAINTENANCE_VOLUME_MULTIPLIER = 0.5

# Pull-ups and goblet squats have unreliable library labels, so they are
# identified explicitly by name (and by movement pattern for other pulls).
PULL_EXERCISE_NAMES = {
    "pull-up",
    "band assisted pull-up",
    "biceps pull-up",
}
GOBLET_SQUAT_EXERCISE_NAMES = {
    "dumbbell goblet squat",
    "kettlebell goblet squat",
}
_EXERCISE_NAME_ALIASES = {
    "push-ups": "push-up",
    "pushups": "push-up",
    "incline push-ups": "incline push-up",
    "decline push-ups": "decline push-up",
    "close-grip push-ups": "close-grip push-up",
    "split squat": "split squats",
}

_TIMED_UNITS = {"seconds", "sec", "s"}
_DISTANCE_UNITS = {"km", "m", "mi"}
_NO_EQUIPMENT_TOKENS = {"body weight", "bodyweight", "none"}


def _is_timed_unit(unit: str) -> bool:
    """True if the unit represents timed duration (seconds/sec/s)."""
    return (unit or "").strip().lower() in _TIMED_UNITS


def _is_distance_unit(unit: str) -> bool:
    """True if the unit represents continuous distance (km/mi/m)."""
    return (unit or "").strip().lower() in _DISTANCE_UNITS

# Maps each balance area to the onboarding raw marker that best represents
# the user's single-effort capacity for that area. These are the fields
# captured by the onboarding form and stored under
# baseline_assessment.answers.raw_markers.
#
#   upper_body -> push_ups        (max consecutive push-ups, reps)
#   lower_body -> squats          (max consecutive bodyweight squats, reps)
#   cardio     -> run_distance_km (distance of the longest continuous run, km)
#   core       -> plank_seconds   (longest front plank hold, seconds; for timed core only)
#
# JUDGMENT CALLS:
#   - core maps to plank_seconds for TIMED core exercises. Rep-based core moves
#     never use plank_seconds and fall back to BEGINNER_BASELINE_CORE_REPS (12).
#   - cardio uses run_distance_km, NOT run_minutes. Duration (minutes) and
#     distance (km) are different units; the cardio "Continuous Run" exercise
#     tracks km, so its volume baseline must come from a distance marker. The
#     duration marker (run_minutes) is left untouched for experience-level
#     scoring. Assessments recorded before this distance question existed have
#     no run_distance_km, so cardio falls back to the beginner default.
AREA_TO_RAW_MARKER: dict[BalanceArea, Optional[str]] = {
    "upper_body": "push_ups",
    "lower_body": "squats",
    "core": "plank_seconds",
    "cardio": "run_distance_km",
}


def baseline_value_from_markers(
    raw_markers: Optional[dict],
    balance_area: BalanceArea,
    unit: Optional[str] = None,
) -> Optional[float]:
    """
    Resolve the user's stored single-effort baseline for a balance area from
    their onboarding raw_markers.

    Returns None when the area has no mapped marker (or marker isn't applicable
    to the exercise unit), when the marker is missing/unset, or when the recorded
    value is non-positive (0 push-ups, 0 plank seconds, etc.) — in every such case
    the caller should fall back to the conservative beginner default rather than
    build a target off a zero.

    For 'core', plank_seconds is a timed hold and is only used for timed core
    exercises (unit seconds/sec/s). Rep-based core moves must never be seeded from
    plank seconds.
    """
    if balance_area == "core":
        if unit is not None and not _is_timed_unit(unit):
            return None
        marker_key = "plank_seconds"
    elif balance_area == "cardio":
        if unit is not None and not _is_distance_unit(unit):
            return None
        marker_key = "run_distance_km"
    else:
        marker_key = AREA_TO_RAW_MARKER.get(balance_area)

    if not marker_key:
        return None
    value = (raw_markers or {}).get(marker_key)
    if value is None and marker_key == "plank_seconds":
        value = (raw_markers or {}).get("plank")
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


def _normalize_exercise_name(exercise: str) -> str:
    name = re.sub(r"\s+", " ", (exercise or "").strip().lower())
    return _EXERCISE_NAME_ALIASES.get(name, name)


def _workout_is_bodyweight(workout: Optional[dict]) -> bool:
    if not workout:
        return False
    required = {
        str(e).strip().lower() for e in workout.get("equipment", [])
        if str(e).strip().lower() not in _NO_EQUIPMENT_TOKENS
    }
    return len(required) == 0


def _is_pull_exercise(name: str, workout: Optional[dict]) -> bool:
    if name in PULL_EXERCISE_NAMES or "pull-up" in name or "pull up" in name:
        return True
    patterns = {str(p).strip().lower() for p in (workout or {}).get("movement_patterns", [])}
    return bool(patterns & {"pull", "vertical_pull", "horizontal_pull"})


def _is_cardio_workout(workout: Optional[dict]) -> bool:
    if not workout:
        return False
    if str(workout.get("type", "")).strip().lower() in ("cardio", "plyometric"):
        return True
    if str(workout.get("primary_target_area", "")).strip().lower() == "full_body":
        return True
    if "cardio" in {str(t).strip().lower() for t in workout.get("tags", [])}:
        return True
    patterns = {str(p).strip().lower() for p in workout.get("movement_patterns", [])}
    return "cardio" in patterns


def _parse_library_default_reps(workout: Optional[dict]) -> Optional[float]:
    """Return a single representative rep/seconds value from the library's
    baseline.reps field, e.g. '8-12' -> 10, '20' -> 20, '20-30' -> 25."""
    raw = ((workout or {}).get("baseline") or {}).get("reps")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(raw))]
    if not numbers:
        return None
    numbers = [n for n in numbers if n > 0]
    if not numbers:
        return None
    if len(numbers) >= 2:
        return (numbers[0] + numbers[1]) / 2.0
    return numbers[0]


def _resolve_baseline_with_source(
    raw_markers: Optional[dict],
    balance_area: BalanceArea,
    unit: str,
    provided: Optional[float],
    default_baseline: float,
) -> tuple[float, str]:
    if provided is not None:
        return provided, "provided"
    marker = baseline_value_from_markers(raw_markers, balance_area, unit=unit)
    if marker is not None:
        return marker, "stored_baseline"
    return default_baseline, "beginner_default"


def calculate_exercise_month_target(
    exercise: str,
    unit: str,
    balance_area: BalanceArea,
    experience_level: ExperienceLevel,
    workout: Optional[dict] = None,
    baseline_value: Optional[float] = None,
    raw_markers: Optional[dict] = None,
    sessions_per_week: int = DEFAULT_SESSIONS_PER_WEEK,
    sets_per_session: Optional[int] = None,
    weeks_in_month: float = 4.35,
    progression_multiplier: float = 1.0,
    volume_multiplier: float = 1.0,
) -> dict:
    """Per-exercise monthly target.

    Categories:
      * Bodyweight compound movements: movement-type baseline * per-exercise
        difficulty factor (e.g. incline push-up 1.35, decline push-up 0.85).
      * Weighted / pull / isolation / rep-based core / rep-based cardio:
        the library's own default reps for that exercise, with a small
        experience adjustment.
      * Timed and per-side exercises keep their distinct units.

    volume_multiplier is the focus/maintenance/balanced adjustment supplied by
    the caller. It is applied once, to the fully-formed target, just like the
    per-area progression multiplier.
    """
    name = _normalize_exercise_name(exercise)
    is_timed = _is_timed_unit(unit)
    is_distance = _is_distance_unit(unit)

    if sets_per_session is None:
        if is_distance:
            sets_per_session = 1
        else:
            sets_per_session = DEFAULT_SETS_PER_SESSION_BY_UNIT.get(unit, 3)

    category: str
    difficulty_factor = 1.0
    experience_factor = 1.0
    baseline_source = "library_default"

    if is_distance:
        category = "distance_cardio"
        baseline_used, baseline_source = _resolve_baseline_with_source(
            raw_markers, balance_area, unit, baseline_value,
            BEGINNER_BASELINE_BY_AREA[balance_area],
        )
        experience_factor = INTENSITY_FACTOR[experience_level]
        starting_reps = baseline_used
    elif balance_area == "core":
        if is_timed:
            category = "timed_core"
            baseline_used, baseline_source = _resolve_baseline_with_source(
                raw_markers, balance_area, unit, baseline_value,
                BEGINNER_BASELINE_CORE_SECONDS,
            )
            experience_factor = INTENSITY_FACTOR[experience_level]
            starting_reps = baseline_used
        else:
            category = "rep_core"
            library_reps = _parse_library_default_reps(workout)
            if baseline_value is not None:
                baseline_used, baseline_source = baseline_value, "provided"
            elif library_reps is not None:
                baseline_used, baseline_source = library_reps, "library_default"
            else:
                baseline_used, baseline_source = BEGINNER_BASELINE_CORE_REPS, "beginner_default"
            experience_factor = LIBRARY_REP_EXPERIENCE_FACTOR[experience_level]
            starting_reps = baseline_used
    else:
        explicit_factor = BODYWEIGHT_DIFFICULTY_FACTORS.get(name)

        if explicit_factor is not None:
            category = "bodyweight"
            difficulty_factor = explicit_factor
            baseline_used, baseline_source = _resolve_baseline_with_source(
                raw_markers, balance_area, unit, baseline_value,
                BEGINNER_BASELINE_BY_AREA[balance_area],
            )
            experience_factor = INTENSITY_FACTOR[experience_level]
            starting_reps = baseline_used * difficulty_factor
        else:
            if _is_cardio_workout(workout):
                category = "rep_cardio"
            elif _is_pull_exercise(name, workout):
                category = "pull"
            else:
                category = "weighted_or_isolation"
            library_reps = _parse_library_default_reps(workout)
            if baseline_value is not None:
                baseline_used, baseline_source = baseline_value, "provided"
            elif library_reps is not None:
                baseline_used, baseline_source = library_reps, "library_default"
            else:
                baseline_used, baseline_source = BEGINNER_BASELINE_BY_AREA[balance_area], "beginner_default"
            experience_factor = LIBRARY_REP_EXPERIENCE_FACTOR[experience_level]
            starting_reps = baseline_used

    month_target = (
        starting_reps * experience_factor * sets_per_session * sessions_per_week
        * weeks_in_month * progression_multiplier * volume_multiplier
    )
    if is_distance:
        month_target = round(month_target, 1)
    else:
        month_target = int(round(month_target))

    return {
        "month_target": month_target,
        "baseline_used": baseline_used,
        "baseline_source": baseline_source,
        "intensity_factor": experience_factor,
        "experience_factor": experience_factor,
        "difficulty_factor": difficulty_factor,
        "sets_per_session": sets_per_session,
        "sessions_per_week": sessions_per_week,
        "weeks_in_month": weeks_in_month,
        "progression_multiplier": progression_multiplier,
        "volume_multiplier": volume_multiplier,
        "category": category,
    }


def calculate_month_target(
    unit: str,
    balance_area: BalanceArea,
    experience_level: ExperienceLevel,
    baseline_value: Optional[float] = None,
    sessions_per_week: int = DEFAULT_SESSIONS_PER_WEEK,
    sets_per_session: Optional[int] = None,
    weeks_in_month: float = 4.35,
    default_baseline: Optional[float] = None,
    progression_multiplier: float = 1.0,
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
            "progression_multiplier": float,
        }

    Formula: month_target = baseline * intensity_factor * sets_per_session
                             * sessions_per_week * weeks_in_month
                             * progression_multiplier

    progression_multiplier is the deterministic overload factor stored in
    user_profile.progression_multipliers[balance_area]. Defaults to 1.0
    (identity) so all existing callers that omit it are unaffected.

    When baseline_value is None and a non-None default_baseline is supplied, it
    is used in place of BEGINNER_BASELINE_BY_AREA[balance_area]. This lets
    callers override the area default for a specific sub-category (e.g.
    rep-based cardio exercises, or timed vs rep core exercises).
    """
    if baseline_value is not None:
        baseline_used = baseline_value
        baseline_source = "provided"
    else:
        if default_baseline is not None:
            baseline_used = default_baseline
        elif balance_area == "core":
            baseline_used = (
                BEGINNER_BASELINE_CORE_SECONDS
                if _is_timed_unit(unit)
                else BEGINNER_BASELINE_CORE_REPS
            )
        else:
            baseline_used = BEGINNER_BASELINE_BY_AREA[balance_area]
        baseline_source = "beginner_default"

    if sets_per_session is None:
        if unit in _CONTINUOUS_UNITS:
            sets_per_session = 1
        else:
            sets_per_session = DEFAULT_SETS_PER_SESSION_BY_UNIT.get(unit, 3)

    intensity = INTENSITY_FACTOR[experience_level]

    # Apply progression_multiplier as the final factor before rounding so it
    # scales the fully-computed target rather than the raw baseline. This keeps
    # the semantics clear: the multiplier adjusts the resulting monthly volume
    # commitment, not the user's physical capacity estimate.
    month_target = (
        baseline_used * intensity * sets_per_session * sessions_per_week * weeks_in_month
        * progression_multiplier
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
        "progression_multiplier": progression_multiplier,
    }