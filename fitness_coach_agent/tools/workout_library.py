"""
V2 scope: the LLM extracts structured filter fields from the user's message
(equipment, target_area, etc.) and calls this tool with them. The tool does
ONLY deterministic filtering over data/workouts.json - no NLU, no fuzzy
text matching.
Real semantic search (embeddings) is still V3's job - this stays deterministic field filtering by design.
"""

import json
import os
from typing import List, Literal, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

_WORKOUTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "workouts.json"
)


def _load_workouts() -> list[dict]:
    with open(_WORKOUTS_PATH, "r") as f:
        return json.load(f)



_WORKOUTS = _load_workouts()

# Controlled vocabulary, derived from what's actually in data/workouts.json.
TargetArea = Literal[
    "abs", "back", "biceps", "calves", "chest", "forearms",
    "glutes", "hamstrings", "lower_back", "quads", "shoulders", "triceps",
]
Equipment = Literal["none", "dumbbells", "chair"]
Difficulty = Literal["beginner", "intermediate"]
RequiredSpace = Literal["minimal"]


class WorkoutQuery(BaseModel):
    """Structured filter for searching the workout library."""

    equipment: Optional[Equipment] = Field(
        default=None,
        description="Equipment available to the user. Use 'none' for bodyweight-only "
        "(e.g. traveling, no gym access). Omit if equipment isn't a constraint.",
    )
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
        description="Maximum time available, in minutes, e.g. if the user says "
        "'I only have 15 minutes'. Omit if no time constraint was mentioned.",
    )
    required_space: Optional[RequiredSpace] = Field(
        default=None,
        description="Space constraint. Omit if not relevant.",
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


def _matches(workout: dict, q: WorkoutQuery) -> bool:
    """Deterministic AND-filtering: a workout must satisfy every field the
    LLM actually specified. Unspecified (None) fields are not filtered on."""

    if q.equipment is not None and workout.get("equipment") != q.equipment:
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
        if workout.get("duration_minutes", 0) > q.max_duration_minutes:
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
    equipment: Optional[str] = None,
    target_area: Optional[List[str]] = None,
    movement_patterns: Optional[List[str]] = None,
    difficulty: Optional[str] = None,
    max_duration_minutes: Optional[int] = None,
    required_space: Optional[str] = None,
    avoid_body_parts: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
) -> str:
    """
    Search the workout library using structured filters.

    Extract relevant fields from the user's message before calling this -
    e.g. a mentioned body part, an injury to avoid, equipment availability,
    or a time constraint. Only include fields you actually have information
    for; omit the rest.
    """
    query = WorkoutQuery(
        equipment=equipment,
        target_area=target_area,
        movement_patterns=movement_patterns,
        difficulty=difficulty,
        max_duration_minutes=max_duration_minutes,
        required_space=required_space,
        avoid_body_parts=avoid_body_parts,
        tags=tags,
    )

    matches = [w for w in _WORKOUTS if _matches(w, query)]

    if not matches:
        return "No workouts found matching those filters. Try relaxing a constraint."

    lines = []
    for w in matches[:5]:
        secondary = w.get("secondary_target_areas") or []
        secondary_str = f", also works: {', '.join(secondary)}" if secondary else ""
        lines.append(
            f"- {w['name']} (primary: {w['primary_target_area']}{secondary_str}; "
            f"{w['equipment']}, {w['difficulty']}, ~{w['duration_minutes']} min): {w['description']}"
        )
    return "\n".join(lines)


# Quick manual test: python tools/workout_library.py
if __name__ == "__main__":
    print(search_workout_library.invoke({"equipment": "none", "target_area": ["chest"]}))
    print()
    print(search_workout_library.invoke({"avoid_body_parts": ["shoulder"], "equipment": "dumbbells"}))
    print()
    print(search_workout_library.invoke({"max_duration_minutes": 4}))
    print()
    print(search_workout_library.invoke({"target_area": ["chest", "triceps"]}))