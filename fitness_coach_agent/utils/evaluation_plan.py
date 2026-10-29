"""Deterministic main-exercise selection for untracked evaluation plans.

Untracked sessions in the last week of a goal-free month are evaluation days:
they cover push, lower body, core and cardio with bodyweight movements, plus a
pull-up (bar required) and jump rope (rope required) when available. The LLM
still assembles warm-up and cool-down; everything in this module is pure code.

The first appearance of a claim-bearing movement in the window becomes an
evaluation pair: one entry at the stored registration claim and one easier
entry (about 75%). Later appearances use a per-session amount derived from the
same Foundation dosing logic as the monthly planner.
"""

import re
from typing import Optional

from tools.workout_library import WorkoutQuery, _matches, get_exercise_metadata
from utils.baseline_targets import (
    BEGINNER_BASELINE_BY_AREA,
    BEGINNER_BASELINE_CORE_SECONDS,
    INTENSITY_FACTOR,
    baseline_value_from_markers,
    calculate_exercise_session_target,
)
from utils.evaluation_round import (
    MOVEMENT_PLANK,
    MOVEMENT_PULL_UP,
    MOVEMENT_PUSH,
    MOVEMENT_SQUAT,
    movement_for_exercise,
    preferred_exercise_for_movement,
)

_PUSH_POOL = [
    "Push-Up",
    "Incline Push-Up",
    "Kneeling Push-Up",
    "Close-Grip Push-Up",
    "Decline Push-Up",
    "Side Push-Up",
]
_LOWER_POOL = [
    "Squat to Overhead Reach",
    "Split Squats",
    "Walking Lunge",
    "Low Glute Bridge on Floor",
    "Glute Bridge March",
    "Sissy Squat",
    "Bodyweight Standing Calf Raise",
    "One Leg Floor Calf Raise",
    "Walking High Knees Lunge",
    "Standing Single Leg Curl",
]
_CORE_POOL = [
    "Bodyweight Incline Side Plank",
    "Dead Bug",
    "Russian Twist",
    "Front Plank with Twist",
    "Reverse Crunch",
    "L-Sit on Floor",
]
_CARDIO_POOL = [
    "Mountain Climber",
    "Star Jump (Male)",
    "Skater Hops",
    "Astride Jumps (Male)",
    "Burpee",
    "Jack Burpee",
]
_PULL_POOL = ["Pull-Up"]
_ROPE_POOL = ["Jump Rope"]

_FAMILY_POOLS = {
    "push": _PUSH_POOL,
    "lower_body": _LOWER_POOL,
    "core": _CORE_POOL,
    "cardio": _CARDIO_POOL,
    "pull_up": _PULL_POOL,
    "jump_rope": _ROPE_POOL,
}

# Priority order used when eligible movements are scarce.
_FAMILY_PRIORITY = ("push", "lower_body", "core", "cardio", "pull_up", "jump_rope")
_REQUIRED_FAMILIES = ("push", "lower_body", "core", "cardio")

# Only these movements use the registration claim for their first appearance.
# The exercise-to-movement aliases (including real Squat / Plank when present)
# live in utils.evaluation_round so they are easy to change in one place.
_CLAIM_MARKER_SPECS = {
    MOVEMENT_PUSH: ("upper_body", "push_ups", "reps"),
    MOVEMENT_SQUAT: ("lower_body", "squats", "reps"),
    MOVEMENT_PLANK: ("core", "plank_seconds", "seconds"),
    MOVEMENT_PULL_UP: (None, "pull_ups", "reps"),
}


def _claim_spec(name: str) -> Optional[tuple[Optional[str], str, str]]:
    movement = movement_for_exercise(name)
    return _CLAIM_MARKER_SPECS.get(movement)

_PATTERN_FAMILIES = {
    "push": {"push", "horizontal_push", "vertical_push", "push_up", "dip"},
    "lower_body": {
        "squat", "lunge", "hip_hinge", "lower_body_push", "bridge",
        "calf_raise", "hip_extension", "step_up",
    },
    "core": {
        "anti_extension", "anti_rotation", "core_stability", "plank",
        "rollout", "isometric", "rotation", "lateral_stability",
    },
    "cardio": {"jump", "plyometric", "explosive", "locomotion", "cardio"},
}
_NO_EQUIPMENT_TOKENS = {"body weight", "bodyweight", "none", ""}


def _required_equipment(workout: dict) -> set[str]:
    return {
        str(item).strip().lower()
        for item in (workout.get("equipment") or [])
        if str(item).strip().lower() not in _NO_EQUIPMENT_TOKENS
    }


def is_bodyweight_exercise(workout: dict) -> bool:
    return not _required_equipment(workout)


def has_user_equipment(resolved_equipment: Optional[list[str]], token: str) -> bool:
    """True when an override grants everything or the profile lists the token."""
    if resolved_equipment is None:
        return True
    norm_token = re.sub(r"[\s_-]+", "", token.lower())
    for item in resolved_equipment:
        norm_item = re.sub(r"[\s_-]+", "", str(item).lower())
        if norm_token in norm_item or norm_item in norm_token:
            return True
    return False


def movement_family(name: str) -> Optional[str]:
    """Classify a library movement into the evaluation families."""
    for family, pool in _FAMILY_POOLS.items():
        if name in pool:
            return family

    workout = get_exercise_metadata(name)
    if not workout:
        return None

    equipment = _required_equipment(workout)
    if "rope" in equipment:
        return "jump_rope"
    if "pull_up_bar" in equipment:
        return "pull_up"

    patterns = {
        str(p).strip().lower() for p in (workout.get("movement_patterns") or [])
    }
    workout_type = str(workout.get("type") or "").strip().lower()
    if workout_type in ("cardio", "plyometric") or patterns & _PATTERN_FAMILIES["cardio"]:
        return "cardio"
    if patterns & _PATTERN_FAMILIES["push"]:
        return "push"
    if patterns & _PATTERN_FAMILIES["lower_body"]:
        return "lower_body"
    if (
        patterns & _PATTERN_FAMILIES["core"]
        or str(workout.get("primary_target_area") or "").lower() in ("abs", "core")
    ):
        return "core"
    return None


def _all_library_names() -> list[str]:
    from tools.workout_library import _WORKOUTS

    return [w.get("name") for w in _WORKOUTS if w.get("name")]


def _candidate_names(family: str) -> list[str]:
    """Preferred names first, then a deterministic fallback from the library.

    A real Squat / Plank is preferred over its substitute whenever the library
    contains one; otherwise the substitute pool below is used unchanged.
    """
    preferred_movement = {
        "lower_body": MOVEMENT_SQUAT,
        "core": MOVEMENT_PLANK,
    }.get(family)
    preferred: list[str] = []
    if preferred_movement:
        real_name = preferred_exercise_for_movement(preferred_movement)
        if real_name:
            preferred.append(real_name)

    explicit = [
        name for name in (_FAMILY_POOLS.get(family) or [])
        if name not in preferred
    ]
    fallback = sorted(
        name for name in _all_library_names()
        if name not in explicit and name not in preferred and movement_family(name) == family
    )
    return preferred + explicit + fallback


def _is_eligible(
    name: str,
    *,
    resolved_equipment: Optional[list[str]],
    avoid_body_parts: Optional[list[str]],
    require_bodyweight: bool,
) -> bool:
    workout = get_exercise_metadata(name)
    if not workout:
        return False
    if require_bodyweight and not is_bodyweight_exercise(workout):
        return False
    query = WorkoutQuery(avoid_body_parts=avoid_body_parts or None)
    return _matches(workout, query, resolved_equipment=resolved_equipment)


def _pick_movement(
    family: str,
    day_index: int,
    *,
    resolved_equipment: Optional[list[str]],
    avoid_body_parts: Optional[list[str]],
    excluded_names: set[str],
    fallback_repeat: bool = False,
) -> Optional[str]:
    """Rotate through a family's stable candidate order for the given day."""
    names = _candidate_names(family)
    if not names:
        return None
    require_bodyweight = family in _REQUIRED_FAMILIES
    start = day_index % len(names)
    exclusion_passes = (excluded_names, set()) if fallback_repeat else (excluded_names,)
    for blocked in exclusion_passes:
        for offset in range(len(names)):
            name = names[(start + offset) % len(names)]
            if name in blocked:
                continue
            if _is_eligible(
                name,
                resolved_equipment=resolved_equipment,
                avoid_body_parts=avoid_body_parts,
                require_bodyweight=require_bodyweight,
            ):
                return name
    return None


def _representative_reps(workout: dict) -> int:
    raw = ((workout or {}).get("baseline") or {}).get("reps")
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(raw or "")) if float(n) > 0]
    if not numbers:
        return 10
    value = sum(numbers[:2]) / len(numbers[:2])
    return max(1, int(round(value)))


def _claim_amount(name: str, raw_markers: Optional[dict]) -> int:
    spec = _claim_spec(name)
    if spec is None:
        return 1
    area, marker, unit = spec
    value = None
    if area is not None:
        value = baseline_value_from_markers(raw_markers, area, unit=unit)
    if value is None:
        value = (raw_markers or {}).get(marker)

    if value is None:
        if area == "upper_body":
            value = BEGINNER_BASELINE_BY_AREA["upper_body"]
        elif area == "lower_body":
            value = BEGINNER_BASELINE_BY_AREA["lower_body"]
        elif area == "core":
            value = BEGINNER_BASELINE_CORE_SECONDS
        else:
            value = _representative_reps(get_exercise_metadata(name) or {})

    try:
        return max(1, int(round(float(value))))
    except (TypeError, ValueError):
        return 1


def _easier_amount(claim: int, experience_level: str) -> int:
    factor = INTENSITY_FACTOR.get(experience_level, 0.75)
    if not (0.70 <= factor <= 0.75):
        factor = 0.75
    easier = max(1, int(claim * factor))
    if claim > 1 and easier >= claim:
        easier = claim - 1
    return easier


def _amount_text(amount, unit: str) -> str:
    if unit == "seconds":
        return f"{amount} sec"
    if unit == "reps_per_side":
        return f"{amount} reps per side"
    if unit in ("km", "mi", "m"):
        return f"{amount} {unit}"
    return f"{amount} reps"


def _base_entry(name: str) -> dict:
    workout = get_exercise_metadata(name) or {}
    return {
        "name": name,
        "focus": workout.get("primary_target_area") or "full_body",
        "category": "main",
        "sets": None,
        "reps": None,
        "equipment": workout.get("equipment") or ["none"],
        "duration_minutes": workout.get("duration_minutes") or 5,
        "target_quantity": None,
        "unit": ((workout.get("baseline") or {}).get("unit") or "reps"),
        "completed_quantity": 0,
        "completed": False,
        "set_group_id": None,
        "pair_id": None,
        "pair_role": None,
    }


def _pair_id(name: str, date: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"eval_pair_{slug}_{date}"


def _make_pair(name: str, date: str, raw_markers: Optional[dict], experience_level: str) -> list[dict]:
    spec = _claim_spec(name)
    if spec is None:
        return []
    _area, _marker, unit = spec
    claim = _claim_amount(name, raw_markers)
    easier = _easier_amount(claim, experience_level)
    pair_id = _pair_id(name, date)

    claim_entry = _base_entry(name)
    claim_entry.update({
        "sets": 1,
        "reps": _amount_text(claim, unit),
        "target_quantity": claim,
        "unit": unit,
        "pair_id": pair_id,
        "pair_role": "claim",
    })

    easier_entry = _base_entry(name)
    easier_entry.update({
        "sets": 1,
        "reps": _amount_text(easier, unit),
        "target_quantity": easier,
        "unit": unit,
        "pair_id": pair_id,
        "pair_role": "easier",
    })
    return [claim_entry, easier_entry]


def _make_session_entry(
    name: str,
    family: str,
    experience_level: str,
    raw_markers: Optional[dict],
) -> dict:
    workout = get_exercise_metadata(name) or {}
    balance_area = {
        "push": "upper_body",
        "lower_body": "lower_body",
        "core": "core",
        "cardio": "cardio",
        "pull_up": "upper_body",
    }.get(family, "upper_body")
    unit = ((workout.get("baseline") or {}).get("unit") or "reps")

    resolved = calculate_exercise_session_target(
        exercise=name,
        unit=unit,
        balance_area=balance_area,
        experience_level=experience_level,
        workout=workout,
        raw_markers=raw_markers,
    )
    entry = _base_entry(name)
    entry.update({
        "sets": resolved["sets"],
        "reps": _amount_text(resolved["reps_per_set"], resolved["unit"]),
        "target_quantity": resolved["target_quantity"],
        "unit": resolved["unit"],
    })
    return entry


def _make_jump_rope_entry(name: str) -> dict:
    workout = get_exercise_metadata(name) or {}
    baseline = workout.get("baseline") or {}
    sets = max(1, int(baseline.get("sets") or 1))
    per_set = _representative_reps(workout)
    unit = baseline.get("unit") or "reps"
    entry = _base_entry(name)
    entry.update({
        "sets": sets,
        "reps": _amount_text(per_set, unit),
        "target_quantity": sets * per_set,
        "unit": unit,
    })
    return entry


def build_evaluation_main_exercises(
    dates: list[str],
    *,
    experience_level: str = "beginner",
    raw_markers: Optional[dict] = None,
    resolved_equipment: Optional[list[str]] = None,
    avoid_body_parts_by_date: Optional[dict[str, list[str]]] = None,
    include_optional: bool = True,
) -> dict[str, list[dict]]:
    """Build deterministic main-phase exercises for each date.

    Claim-bearing evaluation movements use the same movement every day, so the
    claimed/easier pair repeats across the whole remaining window (3 days left
    means the user sees the choices 3 times). Cardio and the optional jump rope
    keep their existing day-to-day behaviour; pull-up, when available, is an
    evaluation movement and repeats its pair too. Pair entries are always
    adjacent and in claim-then-easier order.
    """
    avoid_map = avoid_body_parts_by_date or {}
    has_pull_bar = has_user_equipment(resolved_equipment, "pull_up_bar")
    has_rope = has_user_equipment(resolved_equipment, "rope")
    optional_done: set[str] = set()
    previous_day_names: set[str] = set()
    result: dict[str, list[dict]] = {}

    # These families are the stable evaluation movements whose pair must repeat.
    stable_families = {"push", "lower_body", "core", "pull_up"}

    for day_index, date in enumerate(dates):
        avoid = avoid_map.get(date) or []
        chosen: list[tuple[str, str]] = []

        for family in _FAMILY_PRIORITY:
            if family in _REQUIRED_FAMILIES:
                pass
            elif family == "pull_up":
                if not include_optional or not has_pull_bar:
                    continue
            elif family == "jump_rope":
                if not include_optional or not has_rope or family in optional_done:
                    continue

            is_stable = family in stable_families
            name = _pick_movement(
                family,
                0 if is_stable else day_index,
                resolved_equipment=resolved_equipment,
                avoid_body_parts=avoid,
                excluded_names=(
                    {n for n, _ in chosen}
                    if is_stable
                    else previous_day_names | {n for n, _ in chosen}
                ),
            )
            if name is None:
                continue
            chosen.append((name, family))
            if family == "jump_rope":
                optional_done.add(family)

        # Defensive fallback: a filtered pool must still yield a usable main
        # phase (validator requires at least four main exercises).
        if len(chosen) < 4:
            for family in _REQUIRED_FAMILIES:
                if any(f == family for _, f in chosen):
                    continue
                is_stable = family in stable_families
                name = _pick_movement(
                    family,
                    0 if is_stable else day_index,
                    resolved_equipment=resolved_equipment,
                    avoid_body_parts=[],
                    excluded_names={n for n, _ in chosen},
                    fallback_repeat=True,
                )
                if name:
                    chosen.append((name, family))

        day_entries: list[dict] = []
        for name, family in chosen:
            if family == "jump_rope":
                day_entries.append(_make_jump_rope_entry(name))
            elif _claim_spec(name) is not None:
                day_entries.extend(_make_pair(name, date, raw_markers, experience_level))
            else:
                day_entries.append(_make_session_entry(name, family, experience_level, raw_markers))

        result[date] = day_entries
        previous_day_names = {name for name, _ in chosen}

    return result
