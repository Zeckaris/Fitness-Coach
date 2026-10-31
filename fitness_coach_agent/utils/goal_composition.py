"""
Deterministic exercise composition for monthly goals based on user focus.

Implements muscle-group coverage, maintenance coverage for non-focus areas,
experience-level matching, deterministic tie-breaking, and equipment fallbacks.
"""

import json
import os
from typing import List, Optional, Tuple, Dict, Set, Literal

from utils.exercise_ordering import _movement_family

_WORKOUTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "workouts.json"
)

with open(_WORKOUTS_PATH, "r") as _f:
    _workouts_data = json.load(_f)

_WORKOUTS: list[dict] = (
    _workouts_data["exercises"]
    if isinstance(_workouts_data, dict) and "exercises" in _workouts_data
    else _workouts_data
)

_WORKOUTS_BY_NAME: dict[str, dict] = {w["name"]: w for w in _WORKOUTS}
_NO_EQUIPMENT_TOKENS = {"body weight", "none", "bodyweight"}

FocusArea = Literal["upper_body", "lower_body", "core", "cardio", "balanced"]
VALID_FOCUSES = ("upper_body", "lower_body", "core", "cardio", "balanced")

# Major muscle groups per focus area as specified by requirements:
# - upper body: chest, back, shoulders, biceps, triceps
# - lower body: quads, hamstrings, glutes, calves
# - core: abs, core, lower back
# - cardio: two or three exercises
FOCUS_MAJOR_GROUPS: Dict[str, List[str]] = {
    "upper_body": ["chest", "back", "shoulders", "biceps", "triceps"],
    "lower_body": ["quads", "hamstrings", "glutes", "calves"],
    "core": ["abs", "core", "lower_back"],
}

GROUP_TO_BALANCE_AREA: Dict[str, str] = {
    "chest": "upper_body",
    "back": "upper_body",
    "shoulders": "upper_body",
    "biceps": "upper_body",
    "triceps": "upper_body",
    "quads": "lower_body",
    "hamstrings": "lower_body",
    "glutes": "lower_body",
    "calves": "lower_body",
    "abs": "core",
    "core": "core",
    "lower_back": "core",
}

BALANCE_AREAS = ["upper_body", "lower_body", "core", "cardio"]

# Compound preferences for maintenance / balanced selection
MAINTENANCE_PREFERENCES: Dict[str, List[str]] = {
    "upper_body": ["chest", "back", "shoulders", "biceps", "triceps"],
    "lower_body": ["quads", "glutes", "hamstrings", "calves"],
    "core": ["core", "abs", "lower_back"],
}

DIFFICULTY_RANKS = {
    "beginner": {"beginner": 0, "intermediate": 1, "advanced": 2},
    "intermediate": {"intermediate": 0, "beginner": 1, "advanced": 2},
    "advanced": {"advanced": 0, "intermediate": 1, "beginner": 2},
}


def _is_bodyweight(workout: dict) -> bool:
    eq_list = workout.get("equipment", [])
    required = {
        e.strip().lower() for e in eq_list
        if e.strip().lower() not in _NO_EQUIPMENT_TOKENS
    }
    return len(required) == 0


def _matches_equipment(workout: dict, resolved_equipment: Optional[List[str]]) -> bool:
    if resolved_equipment is None:
        return True
    required = {
        e.strip().lower() for e in workout.get("equipment", [])
        if e.strip().lower() not in _NO_EQUIPMENT_TOKENS
    }
    user_has = {e.strip().lower() for e in resolved_equipment}
    return required.issubset(user_has)


def _avoids_injury(workout: dict, avoid_body_parts: Optional[List[str]]) -> bool:
    if not avoid_body_parts:
        return True
    avoid_set = {b.strip().lower() for b in avoid_body_parts}
    workout_avoids = {b.strip().lower() for b in workout.get("avoid_if_injured", [])}
    return len(avoid_set & workout_avoids) == 0


def is_cardio_exercise(workout: dict) -> bool:
    """True if exercise represents cardio/plyometric movement."""
    if workout.get("type") in ("cardio", "plyometric"):
        return True
    if workout.get("primary_target_area") == "full_body":
        return True
    patterns = workout.get("movement_patterns", [])
    if _movement_family(patterns) == "cardio_family":
        return True
    if "cardio" in workout.get("tags", []):
        return True
    return False


def _exercise_sort_key(workout: dict, user_experience: str) -> tuple:
    exp = user_experience.lower() if user_experience else "beginner"
    ranks = DIFFICULTY_RANKS.get(exp, DIFFICULTY_RANKS["beginner"])
    diff = workout.get("difficulty", "beginner").lower()
    diff_score = ranks.get(diff, 1)
    return (diff_score, workout["name"].lower())


def select_exercise_for_group(
    group: str,
    available_equipment: Optional[List[str]],
    avoid_body_parts: Optional[List[str]],
    experience_level: str = "beginner",
    exclude_names: Optional[Set[str]] = None,
    workouts: Optional[List[dict]] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    Deterministically select an exercise for a specific muscle group.

    Fallback hierarchy when equipment rules out every option:
      1. Primary match matching user's equipment.
      2. Bodyweight primary match.
      3. Secondary-area match with user's equipment.
      4. Secondary-area bodyweight match.
      5. None (group is short).

    Returns:
      (selected_workout_dict, short_group_name_or_None)
    """
    if exclude_names is None:
        exclude_names = set()
    pool = workouts if workouts is not None else _WORKOUTS

    # Tier 1: Primary match with user's equipment
    primary_candidates = [
        w for w in pool
        if w.get("primary_target_area") == group
        and w["name"] not in exclude_names
        and _avoids_injury(w, avoid_body_parts)
    ]

    tier1 = [w for w in primary_candidates if _matches_equipment(w, available_equipment)]
    if tier1:
        tier1.sort(key=lambda w: _exercise_sort_key(w, experience_level))
        return tier1[0], None

    # Tier 2a: Bodyweight primary option fallback
    tier2a = [w for w in primary_candidates if _is_bodyweight(w)]
    if tier2a:
        tier2a.sort(key=lambda w: _exercise_sort_key(w, experience_level))
        return tier2a[0], None

    # Tier 2b: Secondary-area match
    secondary_candidates = [
        w for w in pool
        if group in w.get("secondary_target_areas", [])
        and w["name"] not in exclude_names
        and _avoids_injury(w, avoid_body_parts)
    ]

    tier2b_eq = [w for w in secondary_candidates if _matches_equipment(w, available_equipment)]
    if tier2b_eq:
        tier2b_eq.sort(key=lambda w: _exercise_sort_key(w, experience_level))
        return tier2b_eq[0], None

    tier2b_bw = [w for w in secondary_candidates if _is_bodyweight(w)]
    if tier2b_bw:
        tier2b_bw.sort(key=lambda w: _exercise_sort_key(w, experience_level))
        return tier2b_bw[0], None

    # Tier 3: Nothing exists for this group
    return None, group


def select_cardio_exercises(
    count: int,
    available_equipment: Optional[List[str]],
    avoid_body_parts: Optional[List[str]],
    experience_level: str = "beginner",
    exclude_names: Optional[Set[str]] = None,
    workouts: Optional[List[dict]] = None,
) -> Tuple[List[dict], bool]:
    """
    Select up to `count` distinct cardio exercises deterministically.
    Returns (selected_list, is_short).
    """
    if exclude_names is None:
        exclude_names = set()
    pool = workouts if workouts is not None else _WORKOUTS

    cardio_pool = [
        w for w in pool
        if is_cardio_exercise(w)
        and w["name"] not in exclude_names
        and _avoids_injury(w, avoid_body_parts)
    ]

    # Primary with equipment
    tier1 = [w for w in cardio_pool if _matches_equipment(w, available_equipment)]
    tier1.sort(key=lambda w: _exercise_sort_key(w, experience_level))

    selected = []
    for w in tier1:
        if len(selected) >= count:
            break
        selected.append(w)

    # Fallback to bodyweight if we still need more
    if len(selected) < count:
        already = {w["name"] for w in selected}
        tier2_bw = [w for w in cardio_pool if _is_bodyweight(w) and w["name"] not in already]
        tier2_bw.sort(key=lambda w: _exercise_sort_key(w, experience_level))
        for w in tier2_bw:
            if len(selected) >= count:
                break
            selected.append(w)

    is_short = len(selected) < count
    return selected, is_short


def select_maintenance_exercise_for_area(
    area: str,
    available_equipment: Optional[List[str]],
    avoid_body_parts: Optional[List[str]],
    experience_level: str = "beginner",
    exclude_names: Optional[Set[str]] = None,
    workouts: Optional[List[dict]] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    Select exactly 1 exercise for an area to maintain balance.
    Prefers compound movements where possible.
    """
    if area == "cardio":
        cardio_list, is_short = select_cardio_exercises(
            count=1,
            available_equipment=available_equipment,
            avoid_body_parts=avoid_body_parts,
            experience_level=experience_level,
            exclude_names=exclude_names,
            workouts=workouts,
        )
        if cardio_list:
            return cardio_list[0], None
        return None, "cardio"

    groups = MAINTENANCE_PREFERENCES.get(area, FOCUS_MAJOR_GROUPS.get(area, [area]))
    for g in groups:
        ex, _ = select_exercise_for_group(
            group=g,
            available_equipment=available_equipment,
            avoid_body_parts=avoid_body_parts,
            experience_level=experience_level,
            exclude_names=exclude_names,
            workouts=workouts,
        )
        if ex:
            return ex, None

    # Fallback: any exercise with primary or secondary in area
    pool = workouts if workouts is not None else _WORKOUTS
    fallback_cands = [
        w for w in pool
        if (GROUP_TO_BALANCE_AREA.get(w.get("primary_target_area")) == area)
        and (exclude_names is None or w["name"] not in exclude_names)
        and _avoids_injury(w, avoid_body_parts)
    ]
    eq_cands = [w for w in fallback_cands if _matches_equipment(w, available_equipment)]
    if eq_cands:
        eq_cands.sort(key=lambda w: _exercise_sort_key(w, experience_level))
        return eq_cands[0], None
    bw_cands = [w for w in fallback_cands if _is_bodyweight(w)]
    if bw_cands:
        bw_cands.sort(key=lambda w: _exercise_sort_key(w, experience_level))
        return bw_cands[0], None

    return None, area


def compose_goal_exercise_list(
    focus: str,
    available_equipment: Optional[List[str]] = None,
    avoid_body_parts: Optional[List[str]] = None,
    experience_level: str = "beginner",
    workouts: Optional[List[dict]] = None,
) -> Dict[str, object]:
    """
    Compose the list of goal exercises based on focus area and user constraints.

    Returns dict with:
      - focus: canonical focus string
      - exercises: list of dicts {name, balance_area, muscle_group, unit}
      - short_groups: list of groups that could not be filled
    """
    clean_focus = (focus or "balanced").strip().lower()
    if clean_focus not in VALID_FOCUSES:
        clean_focus = "balanced"

    chosen_exercises: List[dict] = []
    chosen_names: Set[str] = set()
    short_groups: List[str] = []

    if clean_focus in ("upper_body", "lower_body", "core"):
        major_groups = FOCUS_MAJOR_GROUPS[clean_focus]
        # 1 exercise per major muscle group in focus area
        for group in major_groups:
            ex, short_g = select_exercise_for_group(
                group=group,
                available_equipment=available_equipment,
                avoid_body_parts=avoid_body_parts,
                experience_level=experience_level,
                exclude_names=chosen_names,
                workouts=workouts,
            )
            if ex:
                chosen_exercises.append({
                    "name": ex["name"],
                    "balance_area": clean_focus,
                    "muscle_group": group,
                    "unit": ex.get("baseline", {}).get("unit", "reps"),
                })
                chosen_names.add(ex["name"])
            elif short_g:
                short_groups.append(short_g)

        # 1 maintenance exercise for every OTHER balance area
        for other_area in BALANCE_AREAS:
            if other_area == clean_focus:
                continue
            ex, short_a = select_maintenance_exercise_for_area(
                area=other_area,
                available_equipment=available_equipment,
                avoid_body_parts=avoid_body_parts,
                experience_level=experience_level,
                exclude_names=chosen_names,
                workouts=workouts,
            )
            if ex:
                chosen_exercises.append({
                    "name": ex["name"],
                    "balance_area": other_area,
                    "muscle_group": ex.get("primary_target_area", other_area),
                    "unit": ex.get("baseline", {}).get("unit", "reps"),
                })
                chosen_names.add(ex["name"])
            elif short_a:
                short_groups.append(short_a)

    elif clean_focus == "cardio":
        # 3 cardio exercises (or 2 if only 2 available)
        cardio_picks, is_short = select_cardio_exercises(
            count=3,
            available_equipment=available_equipment,
            avoid_body_parts=avoid_body_parts,
            experience_level=experience_level,
            exclude_names=chosen_names,
            workouts=workouts,
        )
        if len(cardio_picks) < 2:
            short_groups.append("cardio")

        for ex in cardio_picks:
            chosen_exercises.append({
                "name": ex["name"],
                "balance_area": "cardio",
                "muscle_group": ex.get("primary_target_area", "cardio"),
                "unit": ex.get("baseline", {}).get("unit", "reps"),
            })
            chosen_names.add(ex["name"])

        # 1 maintenance exercise for each of upper_body, lower_body, core
        for other_area in ("upper_body", "lower_body", "core"):
            ex, short_a = select_maintenance_exercise_for_area(
                area=other_area,
                available_equipment=available_equipment,
                avoid_body_parts=avoid_body_parts,
                experience_level=experience_level,
                exclude_names=chosen_names,
                workouts=workouts,
            )
            if ex:
                chosen_exercises.append({
                    "name": ex["name"],
                    "balance_area": other_area,
                    "muscle_group": ex.get("primary_target_area", other_area),
                    "unit": ex.get("baseline", {}).get("unit", "reps"),
                })
                chosen_names.add(ex["name"])
            elif short_a:
                short_groups.append(short_a)

    else:  # balanced
        # Exactly 1 exercise per balance area
        for area in BALANCE_AREAS:
            ex, short_a = select_maintenance_exercise_for_area(
                area=area,
                available_equipment=available_equipment,
                avoid_body_parts=avoid_body_parts,
                experience_level=experience_level,
                exclude_names=chosen_names,
                workouts=workouts,
            )
            if ex:
                chosen_exercises.append({
                    "name": ex["name"],
                    "balance_area": area,
                    "muscle_group": ex.get("primary_target_area", area),
                    "unit": ex.get("baseline", {}).get("unit", "reps"),
                })
                chosen_names.add(ex["name"])
            elif short_a:
                short_groups.append(short_a)

    return {
        "focus": clean_focus,
        "exercises": chosen_exercises,
        "short_groups": short_groups,
    }
