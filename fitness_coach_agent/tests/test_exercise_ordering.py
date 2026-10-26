"""
Tests for utils.exercise_ordering.reorder_phase.

Run locally (not staged):
    cd fitness_coach_agent
    python -m pytest tests/test_exercise_ordering.py -v

These tests inject a fake metadata_lookup so no real library file or DB is needed.
"""

import sys
import os

# Ensure the package root is on the path when running directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.exercise_ordering import (
    reorder_phase,
    _rotation_key,
    _movement_family,
    _conflicts_with,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ex(name: str, category: str, primary: str, patterns: list[str], focus: str = "") -> dict:
    """Build a minimal plan exercise dict."""
    return {
        "name": name,
        "category": category,
        "focus": focus or primary,
        "sets": 3,
        "reps": "10",
        "completed_quantity": 0,
        "completed": False,
    }


def fake_lookup(library: dict):
    """Return a metadata_lookup callable backed by a plain dict."""
    def _lookup(name: str):
        return library.get(name)
    return _lookup


def build_library(*entries) -> dict:
    """Build a fake library dict from (name, primary, patterns) tuples."""
    lib = {}
    for name, primary, patterns in entries:
        lib[name] = {"primary_target_area": primary, "movement_patterns": patterns}
    return lib


def count_consecutive_conflicts(exercises: list[dict], lookup) -> int:
    """Count adjacent main-phase pairs that conflict by primary area or movement family."""
    main_exs = [e for e in exercises if e.get("category") == "main"]
    return sum(
        1
        for prev, curr in zip(main_exs, main_exs[1:])
        if _conflicts_with(_rotation_key(prev, lookup), _rotation_key(curr, lookup))
    )


# ---------------------------------------------------------------------------
# Test 1: Even split 2-2-2
# ---------------------------------------------------------------------------

def test_even_split_no_consecutive_pairs():
    lib = build_library(
        ("ChestA", "chest", ["push", "horizontal_push"]),
        ("ChestB", "chest", ["push", "horizontal_push"]),
        ("BackA",  "back",  ["pull", "horizontal_pull"]),
        ("BackB",  "back",  ["pull", "horizontal_pull"]),
        ("CoreA",  "core",  ["anti_extension", "core_stability"]),
        ("CoreB",  "core",  ["anti_extension", "plank"]),
    )
    lookup = fake_lookup(lib)
    exercises = [
        make_ex("ChestA", "main", "chest", ["push", "horizontal_push"]),
        make_ex("ChestB", "main", "chest", ["push", "horizontal_push"]),
        make_ex("BackA",  "main", "back",  ["pull", "horizontal_pull"]),
        make_ex("BackB",  "main", "back",  ["pull", "horizontal_pull"]),
        make_ex("CoreA",  "main", "core",  ["anti_extension", "core_stability"]),
        make_ex("CoreB",  "main", "core",  ["anti_extension", "plank"]),
    ]
    result = reorder_phase(exercises, metadata_lookup=lookup)
    assert len(result) == 6
    assert count_consecutive_conflicts(result, lookup) == 0, (
        f"Expected 0 consecutive same-group pairs, got some in: "
        f"{[e['name'] for e in result if e['category']=='main']}"
    )


# ---------------------------------------------------------------------------
# Test 2: Majority bucket chest:4, back:2, core:1  (required by review)
# ---------------------------------------------------------------------------

def test_majority_bucket_4_2_1_no_consecutive_pairs():
    lib = build_library(
        ("ChestA", "chest", ["push", "horizontal_push"]),
        ("ChestB", "chest", ["push", "horizontal_push"]),
        ("ChestC", "chest", ["push", "horizontal_push"]),
        ("ChestD", "chest", ["push", "vertical_push"]),
        ("BackA",  "back",  ["pull", "horizontal_pull"]),
        ("BackB",  "back",  ["pull", "vertical_pull"]),
        ("CoreA",  "core",  ["anti_extension", "core_stability"]),
    )
    lookup = fake_lookup(lib)
    exercises = [
        make_ex("ChestA", "main", "chest", ["push", "horizontal_push"]),
        make_ex("ChestB", "main", "chest", ["push", "horizontal_push"]),
        make_ex("ChestC", "main", "chest", ["push", "horizontal_push"]),
        make_ex("ChestD", "main", "chest", ["push", "vertical_push"]),
        make_ex("BackA",  "main", "back",  ["pull", "horizontal_pull"]),
        make_ex("BackB",  "main", "back",  ["pull", "vertical_pull"]),
        make_ex("CoreA",  "main", "core",  ["anti_extension", "core_stability"]),
    ]
    result = reorder_phase(exercises, metadata_lookup=lookup)
    assert len(result) == 7
    assert count_consecutive_conflicts(result, lookup) == 0, (
        f"Expected 0 consecutive same-group pairs for chest:4/back:2/core:1, got some in: "
        f"{[e['name'] for e in result if e['category']=='main']}"
    )


# ---------------------------------------------------------------------------
# Test 3: Majority bucket push:3, pull:1, core:1  (the motivating case)
# ---------------------------------------------------------------------------

def test_majority_bucket_push3_pull1_core1_no_consecutive_pairs():
    # push_family exercises from different primaries (chest + triceps)
    lib = build_library(
        ("PushUp",       "chest",   ["push", "horizontal_push"]),
        ("ChestDip",     "chest",   ["push", "vertical_push"]),
        ("CloseGrip",    "triceps", ["push_up", "close_grip"]),   # push_family via push_up
        ("BentOverRow",  "back",    ["pull", "horizontal_pull"]),
        ("DeadBug",      "abs",     ["anti_extension", "core_stability"]),
    )
    lookup = fake_lookup(lib)
    exercises = [
        make_ex("PushUp",      "main", "chest",   ["push", "horizontal_push"]),
        make_ex("ChestDip",    "main", "chest",   ["push", "vertical_push"]),
        make_ex("CloseGrip",   "main", "triceps", ["push_up", "close_grip"]),
        make_ex("BentOverRow", "main", "back",    ["pull", "horizontal_pull"]),
        make_ex("DeadBug",     "main", "abs",     ["anti_extension", "core_stability"]),
    ]
    result = reorder_phase(exercises, metadata_lookup=lookup)
    assert len(result) == 5
    assert count_consecutive_conflicts(result, lookup) == 0, (
        f"Expected 0 consecutive same-group pairs for push:3/pull:1/core:1, got some in: "
        f"{[e['name'] for e in result if e['category']=='main']}"
    )


# ---------------------------------------------------------------------------
# Test 4: Homogeneous pool (all chest) — graceful degradation, no crash
# ---------------------------------------------------------------------------

def test_homogeneous_pool_no_crash():
    lib = build_library(
        ("ChestA", "chest", ["push", "horizontal_push"]),
        ("ChestB", "chest", ["push", "horizontal_push"]),
        ("ChestC", "chest", ["push", "horizontal_push"]),
    )
    lookup = fake_lookup(lib)
    exercises = [
        make_ex("ChestA", "main", "chest", ["push", "horizontal_push"]),
        make_ex("ChestB", "main", "chest", ["push", "horizontal_push"]),
        make_ex("ChestC", "main", "chest", ["push", "horizontal_push"]),
    ]
    result = reorder_phase(exercises, metadata_lookup=lookup)
    # Must return all 3 exercises, in some order — no crash
    assert len(result) == 3
    result_names = {e["name"] for e in result}
    assert result_names == {"ChestA", "ChestB", "ChestC"}


# ---------------------------------------------------------------------------
# Test 5: Movement-family conflict (chest/push + triceps/push) not consecutive
# ---------------------------------------------------------------------------

def test_movement_family_conflict_separated_when_alternatives_exist():
    """Chest Dip and Close-Grip Push-Up are both push_family — must not be adjacent."""
    lib = build_library(
        ("ChestDip",  "chest",   ["push", "vertical_push"]),
        ("CloseGrip", "triceps", ["push_up", "close_grip"]),  # push_family via push_up
        ("BackRow",   "back",    ["pull", "horizontal_pull"]),
    )
    lookup = fake_lookup(lib)
    exercises = [
        make_ex("ChestDip",  "main", "chest",   ["push", "vertical_push"]),
        make_ex("CloseGrip", "main", "triceps", ["push_up", "close_grip"]),
        make_ex("BackRow",   "main", "back",    ["pull", "horizontal_pull"]),
    ]
    result = reorder_phase(exercises, metadata_lookup=lookup)
    assert len(result) == 3

    # ChestDip and CloseGrip must NOT be adjacent
    main_names = [e["name"] for e in result if e["category"] == "main"]
    for i in range(len(main_names) - 1):
        pair = {main_names[i], main_names[i + 1]}
        assert pair != {"ChestDip", "CloseGrip"}, (
            f"ChestDip and CloseGrip (both push_family) should not be adjacent. "
            f"Order was: {main_names}"
        )


# ---------------------------------------------------------------------------
# Test 6: Phase boundary preservation
# ---------------------------------------------------------------------------

def test_phase_boundaries_preserved():
    lib = build_library(
        ("WarmA",  "full_body", ["locomotion"]),
        ("WarmB",  "full_body", ["jump"]),
        ("MainA",  "chest",     ["push", "horizontal_push"]),
        ("MainB",  "chest",     ["push", "horizontal_push"]),
        ("MainC",  "back",      ["pull", "horizontal_pull"]),
        ("MainD",  "core",      ["anti_extension"]),
        ("CoolA",  "lower_back", ["stretch"]),
    )
    lookup = fake_lookup(lib)
    exercises = [
        make_ex("WarmA", "warmup",   "full_body", ["locomotion"]),
        make_ex("WarmB", "warmup",   "full_body", ["jump"]),
        make_ex("MainA", "main",     "chest",     ["push", "horizontal_push"]),
        make_ex("MainB", "main",     "chest",     ["push", "horizontal_push"]),
        make_ex("MainC", "main",     "back",      ["pull", "horizontal_pull"]),
        make_ex("MainD", "main",     "core",      ["anti_extension"]),
        make_ex("CoolA", "cooldown", "lower_back", ["stretch"]),
    ]
    result = reorder_phase(exercises, metadata_lookup=lookup)

    assert len(result) == 7

    # Warmup items must be first
    assert result[0]["name"] == "WarmA"
    assert result[1]["name"] == "WarmB"

    # Cooldown item must be last
    assert result[-1]["name"] == "CoolA"

    # All categories unchanged
    for ex in result:
        if ex["name"] in ("WarmA", "WarmB"):
            assert ex["category"] == "warmup"
        elif ex["name"] == "CoolA":
            assert ex["category"] == "cooldown"
        else:
            assert ex["category"] == "main"

    # No consecutive same-group pairs in main
    assert count_consecutive_conflicts(result, lookup) == 0


# ---------------------------------------------------------------------------
# Test 7: Single exercise — returns as-is
# ---------------------------------------------------------------------------

def test_single_exercise_returns_as_is():
    lib = build_library(("OnlyEx", "chest", ["push"]))
    lookup = fake_lookup(lib)
    exercises = [make_ex("OnlyEx", "main", "chest", ["push"])]
    result = reorder_phase(exercises, metadata_lookup=lookup)
    assert len(result) == 1
    assert result[0]["name"] == "OnlyEx"


# ---------------------------------------------------------------------------
# Test 8: Empty list — returns empty
# ---------------------------------------------------------------------------

def test_empty_list_returns_empty():
    result = reorder_phase([], metadata_lookup=lambda n: None)
    assert result == []


# ---------------------------------------------------------------------------
# Test 9: Unknown exercise name — falls back to focus field, no crash
# ---------------------------------------------------------------------------

def test_unknown_exercise_name_fallback_no_crash():
    # Name not in the fake library — should fall back to ex['focus']
    lookup = fake_lookup({})  # empty library
    exercises = [
        make_ex("CustomMove", "main", "chest", []),
        make_ex("AnotherMove", "main", "back", []),
        make_ex("ThirdMove", "main", "core", []),
    ]
    result = reorder_phase(exercises, metadata_lookup=lookup)
    assert len(result) == 3
    result_names = {e["name"] for e in result}
    assert result_names == {"CustomMove", "AnotherMove", "ThirdMove"}


# ---------------------------------------------------------------------------
# Test 10: _movement_family unit tests
# ---------------------------------------------------------------------------

def test_movement_family_push_patterns():
    assert _movement_family(["push", "horizontal_push"]) == "push_family"
    assert _movement_family(["push_up", "close_grip"]) == "push_family"
    assert _movement_family(["dip", "compound"]) == "push_family"


def test_movement_family_pull_patterns():
    assert _movement_family(["pull", "vertical_pull"]) == "pull_family"
    assert _movement_family(["curl", "isolation"]) == "pull_family"


def test_movement_family_lower_patterns():
    assert _movement_family(["squat", "lower_body_push"]) == "lower_family"
    assert _movement_family(["lunge", "unilateral"]) == "lower_family"
    assert _movement_family(["hip_hinge", "pull"]) == "lower_family"  # first match wins


def test_movement_family_core_patterns():
    assert _movement_family(["anti_extension", "core_stability"]) == "core_family"
    assert _movement_family(["plank", "isometric"]) == "core_family"


def test_movement_family_cardio_patterns():
    assert _movement_family(["jump", "plyometric"]) == "cardio_family"
    assert _movement_family(["explosive", "unilateral"]) == "cardio_family"


def test_movement_family_unknown_patterns():
    assert _movement_family([]) == "other_family"
    assert _movement_family(["raise", "isolation"]) == "other_family"


# ---------------------------------------------------------------------------
# Test 11: Verify library exercises used in the reported failure case
# ---------------------------------------------------------------------------

def test_reported_failure_case_push_chain_broken():
    """
    Simulates the actual chest/triceps/shoulders chain that motivated this fix:
    Push-Up -> Close-Grip Push-Up -> Decline Push-Up -> Chest Dip
    All are push_family. If a pull/core exists in the pool, they must be interleaved.
    """
    lib = build_library(
        ("Push-Up",           "chest",   ["push", "horizontal_push"]),
        ("Close-Grip Push-Up","triceps", ["push_up", "close_grip", "compound"]),
        ("Decline Push-Up",   "chest",   ["push", "horizontal_push"]),
        ("Chest Dip",         "chest",   ["push", "vertical_push"]),
        ("Pull-Up",           "back",    ["pull", "vertical_pull"]),
        ("Dead Bug",          "abs",     ["anti_extension", "core_stability"]),
    )
    lookup = fake_lookup(lib)
    exercises = [
        make_ex("Push-Up",            "main", "chest",   ["push", "horizontal_push"]),
        make_ex("Close-Grip Push-Up", "main", "triceps", ["push_up", "close_grip"]),
        make_ex("Decline Push-Up",    "main", "chest",   ["push", "horizontal_push"]),
        make_ex("Chest Dip",          "main", "chest",   ["push", "vertical_push"]),
        make_ex("Pull-Up",            "main", "back",    ["pull", "vertical_pull"]),
        make_ex("Dead Bug",           "main", "abs",     ["anti_extension", "core_stability"]),
    ]
    result = reorder_phase(exercises, metadata_lookup=lookup)
    assert len(result) == 6
    # Four push-family exercises cannot be fully separated by only two
    # non-push exercises, so exactly one push-family adjacency is unavoidable.
    assert count_consecutive_conflicts(result, lookup) == 1, (
        f"Reported failure case: expected exactly 1 unavoidable same-group pair. "
        f"Order: {[e['name'] for e in result]}"
    )


# ---------------------------------------------------------------------------
# Test 12: Cross-key singleton conflict separation (pre-merged clusters)
# ---------------------------------------------------------------------------

def test_cross_key_movement_family_conflict_singletons_separated():
    """
    Tests multiple singleton buckets where one pair conflicts via movement_family
    despite different primary_target_area (e.g. Side Lying Curl [biceps/pull] and Pull-Up [back/pull]).
    Connected component pre-clustering must prevent these two singletons from being pushed together.
    """
    lib = build_library(
        ("SideLyingCurl", "biceps",  ["pull", "isolation"]),
        ("PullUp",        "back",    ["pull", "vertical_pull"]),
        ("CloseGrip",     "triceps", ["push_up", "close_grip"]),
        ("PushUp",        "chest",   ["push", "horizontal_push"]),
        ("Plank",         "core",    ["anti_extension", "core_stability"]),
    )
    lookup = fake_lookup(lib)
    exercises = [
        make_ex("CloseGrip",     "main", "triceps", ["push_up", "close_grip"]),
        make_ex("Plank",         "main", "core",    ["anti_extension", "core_stability"]),
        make_ex("PushUp",        "main", "chest",   ["push", "horizontal_push"]),
        make_ex("SideLyingCurl", "main", "biceps",  ["pull", "isolation"]),
        make_ex("PullUp",        "main", "back",    ["pull", "vertical_pull"]),
    ]
    result = reorder_phase(exercises, metadata_lookup=lookup)
    assert len(result) == 5
    assert count_consecutive_conflicts(result, lookup) == 0, (
        f"Expected 0 consecutive conflicts for cross-key singletons, got order: "
        f"{[e['name'] for e in result]}"
    )

    main_names = [e["name"] for e in result if e["category"] == "main"]
    for i in range(len(main_names) - 1):
        pair = {main_names[i], main_names[i + 1]}
        assert pair != {"SideLyingCurl", "PullUp"}, (
            f"SideLyingCurl and PullUp (both pull_family) should not be adjacent. "
            f"Order was: {main_names}"
        )


def test_tarik_reported_days_zero_conflicts():
    """
    Validates exact main-phase pools from 09-13, 09-15, 09-17 tarik@gmail.com verification.
    """
    lib = build_library(
        ("Close-Grip Push-Up",                "triceps",   ["push_up", "close_grip"]),
        ("Front Plank with Twist",            "core",      ["anti_extension", "core_stability"]),
        ("Push-Up",                           "chest",     ["push", "horizontal_push"]),
        ("Bodyweight Side Lying Biceps Curl", "biceps",    ["pull"]),
        ("Pull-Up",                           "back",      ["pull", "vertical_pull"]),
        ("Left Hook (Boxing)",                "shoulders", ["anti_extension", "core_stability"]),
        ("Bodyweight Standing Row",           "back",      ["pull", "horizontal_pull"]),
        ("Kneeling Push-Up",                  "chest",     ["push", "horizontal_push"]),
    )
    lookup = fake_lookup(lib)

    # Day 09-13 pool
    d13 = [
        make_ex("Close-Grip Push-Up",                "main", "triceps",   ["push_up", "close_grip"]),
        make_ex("Front Plank with Twist",            "main", "core",      ["anti_extension", "core_stability"]),
        make_ex("Push-Up",                           "main", "chest",     ["push", "horizontal_push"]),
        make_ex("Bodyweight Side Lying Biceps Curl", "main", "biceps",    ["pull"]),
        make_ex("Pull-Up",                           "main", "back",      ["pull", "vertical_pull"]),
    ]
    res13 = reorder_phase(d13, metadata_lookup=lookup)
    assert count_consecutive_conflicts(res13, lookup) == 0

    # Day 09-15 pool
    d15 = [
        make_ex("Left Hook (Boxing)",                "main", "shoulders", ["anti_extension", "core_stability"]),
        make_ex("Push-Up",                           "main", "chest",     ["push", "horizontal_push"]),
        make_ex("Front Plank with Twist",            "main", "core",      ["anti_extension", "core_stability"]),
        make_ex("Bodyweight Side Lying Biceps Curl", "main", "biceps",    ["pull"]),
        make_ex("Bodyweight Standing Row",           "main", "back",      ["pull", "horizontal_pull"]),
    ]
    res15 = reorder_phase(d15, metadata_lookup=lookup)
    assert count_consecutive_conflicts(res15, lookup) == 0

    # Day 09-17 pool
    d17 = [
        make_ex("Push-Up",                           "main", "chest",     ["push", "horizontal_push"]),
        make_ex("Front Plank with Twist",            "main", "core",      ["anti_extension", "core_stability"]),
        make_ex("Kneeling Push-Up",                  "main", "chest",     ["push", "horizontal_push"]),
        make_ex("Bodyweight Side Lying Biceps Curl", "main", "biceps",    ["pull"]),
        make_ex("Pull-Up",                           "main", "back",      ["pull", "vertical_pull"]),
    ]
    res17 = reorder_phase(d17, metadata_lookup=lookup)
    assert count_consecutive_conflicts(res17, lookup) == 0


if __name__ == "__main__":
    test_even_split_no_consecutive_pairs()
    test_majority_bucket_4_2_1_no_consecutive_pairs()
    test_majority_bucket_push3_pull1_core1_no_consecutive_pairs()
    test_homogeneous_pool_no_crash()
    test_movement_family_conflict_separated_when_alternatives_exist()
    print("ALL EXERCISE ORDERING TESTS PASSED!")

