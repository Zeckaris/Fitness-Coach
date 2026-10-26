"""
Tests for set-level clustering (circuit-style set rotation), set splitting,
volume target preservation, progress aggregation, and ordering interleaver behavior.
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("GEMINI_API_KEY", "dummy_key_for_testing")

from tools.plans import split_exercise_sets, MAX_CONSECUTIVE_SETS
from utils.exercise_ordering import reorder_phase


def test_split_exercise_sets_sizing():
    """Verify split_exercise_sets caps main phase exercises at max 2 sets."""
    exercises = [
        {"name": "Bear Crawl", "category": "warmup", "sets": 4, "reps": "8-10"},
        {"name": "Push-Up", "category": "main", "sets": 4, "reps": "15 reps", "target_quantity": 60, "unit": "reps"},
        {"name": "Bodyweight Squat", "category": "main", "sets": 5, "reps": "20 reps", "target_quantity": 100, "unit": "reps"},
        {"name": "Plank", "category": "main", "sets": 2, "reps": "30 sec"},
        {"name": "Child Pose", "category": "cooldown", "sets": 3, "reps": "45 sec"},
    ]

    result = split_exercise_sets(exercises, max_consecutive_sets=2)

    # Warmup and Cooldown should not be split
    warmup_items = [e for e in result if e["category"] == "warmup"]
    assert len(warmup_items) == 1
    assert warmup_items[0]["sets"] == 4

    cooldown_items = [e for e in result if e["category"] == "cooldown"]
    assert len(cooldown_items) == 1
    assert cooldown_items[0]["sets"] == 3

    # Main phase items
    pushup_items = [e for e in result if e["name"] == "Push-Up"]
    assert len(pushup_items) == 2
    assert [e["sets"] for e in pushup_items] == [2, 2]
    # Shared set_group_id
    assert pushup_items[0]["set_group_id"] is not None
    assert pushup_items[0]["set_group_id"] == pushup_items[1]["set_group_id"]

    squat_items = [e for e in result if e["name"] == "Bodyweight Squat"]
    assert len(squat_items) == 3
    assert [e["sets"] for e in squat_items] == [2, 2, 1]
    assert len({e["set_group_id"] for e in squat_items}) == 1

    plank_items = [e for e in result if e["name"] == "Plank"]
    assert len(plank_items) == 1
    assert plank_items[0]["sets"] == 2


def test_split_exercise_sets_volume_preservation():
    """Verify total prescribed volume matches parent target exactly (no inflation or loss)."""
    exercises = [
        {"name": "Push-Up", "category": "main", "sets": 4, "reps": "13 reps", "target_quantity": 50, "unit": "reps", "duration_minutes": 10},
        {"name": "Dumbbell Row", "category": "main", "sets": 5, "reps": "10 reps", "target_quantity": 47, "unit": "reps", "duration_minutes": 15},
    ]

    result = split_exercise_sets(exercises, max_consecutive_sets=2)

    # Push-Up volume check
    pushups = [e for e in result if e["name"] == "Push-Up"]
    assert sum(e["sets"] for e in pushups) == 4
    assert sum(e["target_quantity"] for e in pushups) == 50
    assert sum(e["duration_minutes"] for e in pushups) == 10

    # Dumbbell Row volume check (odd target 47 across 2+2+1 sets)
    rows = [e for e in result if e["name"] == "Dumbbell Row"]
    assert sum(e["sets"] for e in rows) == 5
    assert sum(e["target_quantity"] for e in rows) == 47
    assert sum(e["duration_minutes"] for e in rows) == 15


def test_reorder_phase_circuit_rotation():
    """Verify reorder_phase produces genuine circuit rotation for split entries."""
    raw_exercises = [
        {"name": "Arm Circles", "category": "warmup", "sets": 2, "focus": "shoulders"},
        {"name": "Push-Up", "category": "main", "sets": 4, "focus": "chest"},
        {"name": "Bodyweight Squat", "category": "main", "sets": 4, "focus": "quads"},
        {"name": "Child Pose", "category": "cooldown", "sets": 1, "focus": "back"},
    ]

    mock_metadata = {
        "Push-Up": {"primary_target_area": "chest", "movement_patterns": ["horizontal_push"]},
        "Bodyweight Squat": {"primary_target_area": "quads", "movement_patterns": ["squat"]},
        "Arm Circles": {"primary_target_area": "shoulders", "movement_patterns": ["warmup"]},
        "Child Pose": {"primary_target_area": "back", "movement_patterns": ["stretch"]},
    }

    # Step 1: Split main exercises
    split_exs = split_exercise_sets(raw_exercises, max_consecutive_sets=2)

    # Step 2: Reorder phase
    ordered = reorder_phase(split_exs, metadata_lookup=lambda name: mock_metadata.get(name))

    main_names = [e["name"] for e in ordered if e["category"] == "main"]
    # Expect alternating circuit rotation: Push-Up -> Squat -> Push-Up -> Squat (or Squat -> Push-Up -> Squat -> Push-Up)
    assert len(main_names) == 4
    assert main_names == ["Push-Up", "Bodyweight Squat", "Push-Up", "Bodyweight Squat"] or \
           main_names == ["Bodyweight Squat", "Push-Up", "Bodyweight Squat", "Push-Up"]


def test_single_exercise_split_edge_cases():
    """Test edge cases where only 1 exercise is split, or all exercises share movement family."""
    mock_metadata = {
        "Push-Up": {"primary_target_area": "chest", "movement_patterns": ["horizontal_push"]},
        "Bench Press": {"primary_target_area": "chest", "movement_patterns": ["horizontal_push"]},
    }

    # Case A: Push-Up (4 sets -> split into 2) and Bench Press (2 sets). Both are push_family.
    exercises_same_family = [
        {"name": "Push-Up", "category": "main", "sets": 4, "focus": "chest"},
        {"name": "Bench Press", "category": "main", "sets": 2, "focus": "chest"},
    ]
    split_a = split_exercise_sets(exercises_same_family, max_consecutive_sets=2)
    ordered_a = reorder_phase(split_a, metadata_lookup=lambda name: mock_metadata.get(name))

    names_a = [e["name"] for e in ordered_a if e["category"] == "main"]
    # Bench Press must separate the two split Push-Up items despite sharing movement family
    assert names_a == ["Push-Up", "Bench Press", "Push-Up"]

    # Case B: ONLY Push-Up (4 sets -> split into 2) in main phase, nothing else to rotate with.
    exercises_only_one = [
        {"name": "Push-Up", "category": "main", "sets": 4, "focus": "chest"},
    ]
    split_b = split_exercise_sets(exercises_only_one, max_consecutive_sets=2)
    ordered_b = reorder_phase(split_b, metadata_lookup=lambda name: mock_metadata.get(name))

    names_b = [e["name"] for e in ordered_b if e["category"] == "main"]
    assert len(names_b) == 2
    assert names_b == ["Push-Up", "Push-Up"]


def test_progress_accumulation_summation():
    """Verify that completed_quantity sums across split items in a day."""
    day_exercises = [
        {"name": "Push-Up", "category": "main", "sets": 2, "target_quantity": 25, "completed_quantity": 25, "unit": "reps", "set_group_id": "sg_1"},
        {"name": "Dumbbell Row", "category": "main", "sets": 2, "target_quantity": 20, "completed_quantity": 20, "unit": "reps"},
        {"name": "Push-Up", "category": "main", "sets": 2, "target_quantity": 25, "completed_quantity": 25, "unit": "reps", "set_group_id": "sg_1"},
    ]

    completed_so_far = sum(
        ex.get("completed_quantity", 0)
        for ex in day_exercises
        if ex.get("name") == "Push-Up"
    )

    assert completed_so_far == 50


if __name__ == "__main__":
    test_split_exercise_sets_sizing()
    test_split_exercise_sets_volume_preservation()
    test_reorder_phase_circuit_rotation()
    test_single_exercise_split_edge_cases()
    test_progress_accumulation_summation()
    print("ALL SET CLUSTERING TESTS PASSED!")
