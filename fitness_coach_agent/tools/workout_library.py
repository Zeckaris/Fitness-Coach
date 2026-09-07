import json
import os
import random
import re
from typing import List, Literal, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

_WORKOUTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "workouts.json"
)

_NO_EQUIPMENT_TOKENS = {"body weight", "none", "bodyweight"}


def _load_workouts() -> list[dict]:
    with open(_WORKOUTS_PATH, "r") as f:
        data = json.load(f)
    # New build format wraps the list: {"version", "generated_at", "total_exercises", "exercises": [...]}
    if isinstance(data, dict) and "exercises" in data:
        return data["exercises"]
    return data


_WORKOUTS = _load_workouts()
_WORKOUTS_BY_NAME: dict[str, dict] = {w["name"]: w for w in _WORKOUTS}

# Controlled vocabulary, derived from the rebuilt workout library (V9.5).
TargetArea = Literal[
    "abs", "back", "biceps", "calves", "chest", "forearms",
    "glutes", "hamstrings", "lower_back", "quads", "shoulders", "triceps",
    "full_body", "core", "neck"
]
Difficulty = Literal["beginner", "intermediate", "advanced"]
RequiredSpace = Literal["indoor", "outdoor"]


def _parse_rep_number(reps: str) -> Optional[float]:
    """Extract a representative number from a reps string like '10-12', '30', '10 per side'."""
    if not reps:
        return None
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", reps)]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)  # average if it's a range


def _estimate_duration_minutes(baseline: dict) -> Optional[float]:
    """Rough estimate of time-to-complete from baseline sets/reps/rest_seconds/unit."""
    if not baseline:
        return None
    sets = baseline.get("sets")
    rest = baseline.get("rest_seconds", 0)
    unit = baseline.get("unit", "reps")
    reps = baseline.get("reps", "")

    if sets is None:
        return None

    rep_number = _parse_rep_number(reps)

    if unit == "seconds":
        active_per_set = rep_number if rep_number is not None else 30
    else:  # reps or reps_per_side
        active_per_set = (rep_number if rep_number is not None else 10) * 3  # ~3 sec/rep

    total_seconds = (active_per_set * sets) + (rest * max(sets - 1, 0))
    return round(total_seconds / 60, 1)


def get_workouts_by_names(names: List[str]) -> List[dict]:
    """Look up exercises by exact name, preserving input order. Names not found
    in the library are silently skipped."""
    return [_WORKOUTS_BY_NAME[n] for n in names if n in _WORKOUTS_BY_NAME]


def format_workout_lines(workouts: List[dict]) -> str:
    """Render a list of workout dicts into the standard bullet-point string
    used by search_workout_library and callers that need identical formatting."""
    lines = []
    for w in workouts:
        secondary = w.get("secondary_target_areas") or []
        secondary_str = f", also works: {', '.join(secondary)}" if secondary else ""
        equipment_str = ", ".join(w.get("equipment", [])) or "none"
        est_duration = _estimate_duration_minutes(w.get("baseline", {}))
        duration_str = f"~{est_duration:g} min" if est_duration is not None else "duration n/a"
        lines.append(
            f"- {w['name']} (primary: {w['primary_target_area']}{secondary_str}; "
            f"{equipment_str}, {w['difficulty']}, {duration_str}): {w['description']}"
        )
    return "\n".join(lines)


EQUIPMENT_VOCABULARY = [
    "ab_wheel", "bench", "dumbbells", "kettlebell",
    "pull_up_bar", "resistance_band", "rope", "stability_ball"
]


class WorkoutQuery(BaseModel):

    target_area: Optional[List[TargetArea]] = Field(
        default=None,
        description="Muscle/body areas to target, e.g. ['chest', 'triceps'] for "
        "'chest and triceps workout'. Matches exercises where any of these appear "
        "as either the primary or a secondary target area. Omit if the user "
        "didn't specify a target area.",
    )
    movement_patterns: Optional[List[str]] = Field(
        default=None,
        description="Movement patterns to match, e.g. ['push'], ['squat', 'hip_hinge']. "
        "Omit if not relevant.",
    )
    difficulty: Optional[Difficulty] = Field(
        default=None,
        description="Exercise difficulty level. Omit if not specified by the user.",
    )
    max_duration_minutes: Optional[int] = Field(
        default=None,
        description="Maximum time available, in minutes. This is an estimate derived from "
        "sets/reps/rest, not exact. Omit if no time constraint was mentioned.",
    )
    required_space: Optional[RequiredSpace] = Field(
        default=None,
        description="Whether the exercise can be done indoors or requires outdoor space. "
        "Omit if not relevant.",
    )
    avoid_body_parts: Optional[List[str]] = Field(
        default=None,
        description="Body parts to avoid straining, e.g. ['shoulder'] if the user "
        "mentioned an injury there. Workouts that list this area under "
        "avoid_if_injured will be excluded.",
    )
    tags: Optional[List[str]] = Field(
        default=None,
        description="Free-form tags to match, e.g. ['travel', 'beginner_friendly']. "
        "Omit if not relevant.",
    )


def _matches(workout: dict, q: WorkoutQuery, resolved_equipment: Optional[List[str]] = None) -> bool:

    if resolved_equipment is not None:
        required = {
            e for e in workout.get("equipment", [])
            if e.strip().lower() not in _NO_EQUIPMENT_TOKENS
        }
        user_has = {e.strip().lower() for e in resolved_equipment}
        required_lower = {e.strip().lower() for e in required}
        if not required_lower.issubset(user_has):
            return False

    if q.target_area:
        primary = workout.get("primary_target_area")
        secondary = workout.get("secondary_target_areas", [])
        exercise_areas = {primary, *secondary}
        if not set(q.target_area) & exercise_areas:
            return False

    if q.movement_patterns:
        if not set(q.movement_patterns) & set(workout.get("movement_patterns", [])):
            return False

    if q.difficulty is not None and workout.get("difficulty") != q.difficulty:
        return False

    if q.max_duration_minutes is not None:
        est = _estimate_duration_minutes(workout.get("baseline", {}))
        if est is not None and est > q.max_duration_minutes:
            return False

    if q.required_space is not None and workout.get("required_space") != q.required_space:
        return False

    if q.avoid_body_parts:
        if set(q.avoid_body_parts) & set(workout.get("avoid_if_injured", [])):
            return False

    if q.tags:
        if not set(q.tags) & set(workout.get("tags", [])):
            return False

    return True


@tool(args_schema=WorkoutQuery)
def search_workout_library(
    target_area: Optional[List[str]] = None,
    movement_patterns: Optional[List[str]] = None,
    difficulty: Optional[str] = None,
    max_duration_minutes: Optional[int] = None,
    required_space: Optional[str] = None,
    avoid_body_parts: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
) -> str:
    """Search the workout library using structured filters. Equipment filtering is performed automatically based on the user's profile and active overrides."""
    query = WorkoutQuery(
        target_area=target_area,
        movement_patterns=movement_patterns,
        difficulty=difficulty,
        max_duration_minutes=max_duration_minutes,
        required_space=required_space,
        avoid_body_parts=avoid_body_parts,
        tags=tags,
    )

    from tools.user_profile import resolve_user_equipment
    resolved_equipment, _ = resolve_user_equipment()

    matches = [w for w in _WORKOUTS if _matches(w, query, resolved_equipment=resolved_equipment)]

    if not matches:
        return "No workouts found matching those filters. Try relaxing a constraint."

    sampled = random.sample(matches, min(len(matches), 10))
    return format_workout_lines(sampled)


# Quick manual test: python tools/workout_library.py
if __name__ == "__main__":
    print(search_workout_library.invoke({"target_area": ["chest"]}))
    print()
    print(search_workout_library.invoke({"avoid_body_parts": ["shoulder"]}))
    print()
    print(search_workout_library.invoke({"max_duration_minutes": 4}))
    print()
    print(search_workout_library.invoke({"target_area": ["chest", "triceps"]}))