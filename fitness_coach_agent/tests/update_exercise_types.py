"""
Script: update_exercise_types.py
Location: fitness_coach_agent/tests/update_exercise_types.py

Purpose:
--------
Updates the `type` field of each of the 89 exercises in `data/workouts.json`
based on the V9.6 user-approved reclassification mapping:
  - strength: 76 exercises
  - mobility: 1 exercise
  - stretch: 3 exercises
  - cardio: 3 exercises
  - plyometric: 6 exercises
  - warmup: 0
  - cooldown: 0

Usage:
------
    python fitness_coach_agent/tests/update_exercise_types.py
"""

import json
import os
import sys

# Define explicit mapping from Exercise ID -> New Type
TYPE_MAPPING = {
    # Mobility (1)
    "quads_001": "mobility",

    # Stretch (3)
    "lower_back_004": "stretch",
    "neck_001": "stretch",
    "neck_002": "stretch",

    # Cardio (3)
    "full_body_003": "cardio",
    "full_body_005": "cardio",
    "shoulders_001": "cardio",

    # Plyometric (6)
    "full_body_002": "plyometric",
    "full_body_004": "plyometric",
    "full_body_006": "plyometric",
    "full_body_008": "plyometric",
    "full_body_009": "plyometric",
    "full_body_010": "plyometric",
}

# All remaining 76 exercises default to "strength" as specified by user
STRENGTH_IDS = {
    "abs_001", "abs_002", "abs_003", "abs_004", "abs_005", "abs_006",
    "back_001", "back_002", "back_003", "back_004", "back_005", "back_006", "back_007", "back_008",
    "biceps_001", "biceps_002", "biceps_003", "biceps_004", "biceps_005", "biceps_006",
    "calves_001", "calves_002", "calves_003", "calves_004", "calves_005", "calves_006",
    "chest_001", "chest_002", "chest_003", "chest_004", "chest_005", "chest_006", "chest_007", "chest_008", "chest_009", "chest_010",
    "core_001", "core_002", "core_003", "core_004", "core_005",
    "forearms_001", "forearms_002", "forearms_003", "forearms_004",
    "full_body_001", "full_body_007",
    "glutes_001", "glutes_002", "glutes_003", "glutes_004", "glutes_005", "glutes_006",
    "hamstrings_001", "hamstrings_002", "hamstrings_003", "hamstrings_004", "hamstrings_005",
    "lower_back_001", "lower_back_002", "lower_back_003",
    "quads_002", "quads_003", "quads_004", "quads_005", "quads_006",
    "shoulders_002", "shoulders_003", "shoulders_004", "shoulders_005",
    "triceps_001", "triceps_002", "triceps_003", "triceps_004", "triceps_005", "triceps_006"
}

DATA_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "workouts.json"
))

def main():
    if not os.path.exists(DATA_PATH):
        print(f"Error: {DATA_PATH} does not exist.")
        sys.exit(1)

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and "exercises" in data:
        exercises = data["exercises"]
    else:
        exercises = data

    print(f"Loaded {len(exercises)} exercises from {DATA_PATH}\n")

    updated_counts = {
        "strength": 0,
        "warmup": 0,
        "mobility": 0,
        "stretch": 0,
        "cooldown": 0,
        "cardio": 0,
        "plyometric": 0
    }

    unmapped = []

    for ex in exercises:
        ex_id = ex["id"]
        if ex_id in TYPE_MAPPING:
            new_type = TYPE_MAPPING[ex_id]
        elif ex_id in STRENGTH_IDS:
            new_type = "strength"
        else:
            new_type = "strength"  # fallback
            unmapped.append(ex_id)

        ex["type"] = new_type
        if new_type in updated_counts:
            updated_counts[new_type] += 1
        else:
            updated_counts[new_type] = 1

    # Optionally update version tag to 9.6 if dict format
    if isinstance(data, dict):
        data["version"] = "9.6"

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print("Successfully updated exercises type field in data/workouts.json!")
    print("\nSummary Breakdown of Exercise Types (V9.6):")
    print("-" * 45)
    for type_name, count in updated_counts.items():
        print(f"  - {type_name:<12}: {count} exercises")
    print("-" * 45)
    print(f"  Total Exercises : {sum(updated_counts.values())}")

    if unmapped:
        print(f"\nWarning: {len(unmapped)} exercise IDs were not in the explicit list: {unmapped}")

if __name__ == "__main__":
    main()
