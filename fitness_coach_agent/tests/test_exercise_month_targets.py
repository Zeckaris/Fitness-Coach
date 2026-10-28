"""
Tests for per-exercise monthly targets, difficulty factors, focus/maintenance
volume, balanced goals, unit handling, swaps, and mid-month prorating.
"""

import json
import os
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from utils.baseline_targets import (
    BODYWEIGHT_DIFFICULTY_FACTORS,
    MAINTENANCE_VOLUME_MULTIPLIER,
    calculate_exercise_month_target,
)
from utils.calendar_weeks import compute_goal_scaling
import tools.month_plans as mp


_WORKOUTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "workouts.json"
)
with open(_WORKOUTS_PATH, "r") as _f:
    _WORKOUTS = json.load(_f)["exercises"]
_WORKOUTS_BY_NAME = {w["name"]: w for w in _WORKOUTS}

_MARKERS = {
    "beginner": {"push_ups": 10, "squats": 20, "plank_seconds": 30, "run_distance_km": 1.5},
    "intermediate": {"push_ups": 25, "squats": 40, "plank_seconds": 45, "run_distance_km": 3.0},
    "advanced": {"push_ups": 40, "squats": 60, "plank_seconds": 75, "run_distance_km": 5.0},
}


def _calc(exercise, balance_area, experience="beginner", markers=None, volume_multiplier=1.0):
    workout = _WORKOUTS_BY_NAME[exercise]
    return calculate_exercise_month_target(
        exercise=exercise,
        unit=workout["baseline"]["unit"],
        balance_area=balance_area,
        experience_level=experience,
        workout=workout,
        raw_markers=markers if markers is not None else _MARKERS[experience],
        sessions_per_week=4,
        weeks_in_month=4.0,
        volume_multiplier=volume_multiplier,
    )


def _stage(experience, focus, markers, today=date(2026, 11, 1), month_id="2026-11"):
    col = MagicMock()
    col.find_one.return_value = None
    col.update_one.return_value = MagicMock()
    with (
        patch.object(mp, "get_month_plans_collection", return_value=col),
        patch.object(mp, "get_current_user_id", return_value="test_user"),
        patch.object(mp, "today_local", return_value=today),
        patch.object(mp, "get_progression_multipliers", return_value={}),
        patch.object(mp, "resolve_user_equipment", return_value=(None, {})),
        patch.object(mp, "get_user_profile", return_value={"experience_level": experience}),
        patch.object(
            mp,
            "get_latest_baseline_assessment",
            return_value={"answers": {"raw_markers": markers}},
        ),
    ):
        result = mp.stage_month_goal.func(description="Test goal", focus=focus, month_id=month_id)
    goal = col.update_one.call_args[0][1]["$set"]["goal"]
    return goal, result


class TestPerExerciseDifficulty(unittest.TestCase):
    def test_different_exercises_same_area_get_different_targets(self):
        markers = _MARKERS["intermediate"]
        targets = {
            name: _calc(name, "upper_body", "intermediate", markers)["month_target"]
            for name in ("Push-Up", "Decline Push-Up", "Pull-Up")
        }
        self.assertEqual(len(set(targets.values())), 3, targets)

    def test_difficulty_factors_order_incline_above_pushup_above_decline(self):
        markers = _MARKERS["intermediate"]
        incline = _calc("Incline Push-Up", "upper_body", "intermediate", markers)["month_target"]
        pushup = _calc("Push-Up", "upper_body", "intermediate", markers)["month_target"]
        decline = _calc("Decline Push-Up", "upper_body", "intermediate", markers)["month_target"]
        self.assertGreater(incline, pushup)
        self.assertGreater(pushup, decline)
        self.assertGreater(BODYWEIGHT_DIFFICULTY_FACTORS["incline push-up"], 1.0)
        self.assertLess(BODYWEIGHT_DIFFICULTY_FACTORS["decline push-up"], 1.0)

    def test_bodyweight_factor_changes_count_for_same_baseline(self):
        markers = _MARKERS["beginner"]
        bench = _calc("Bench Dip", "upper_body", "beginner", markers)["month_target"]
        pushup = _calc("Push-Up", "upper_body", "beginner", markers)["month_target"]
        self.assertGreater(bench, pushup)


class TestFocusAndMaintenanceVolume(unittest.TestCase):
    def test_maintenance_below_focus_of_same_unit_family(self):
        goal, _ = _stage("beginner", "upper_body", _MARKERS["beginner"])
        focus_family_max = {}
        for vt in goal["volume_targets"]:
            if vt["balance_area"] == "upper_body":
                family = mp._unit_family(vt["unit"])
                focus_family_max[family] = max(
                    focus_family_max.get(family, 0), vt["month_target"]
                )
        maintenance = [vt for vt in goal["volume_targets"] if vt["balance_area"] != "upper_body"]
        self.assertTrue(maintenance)
        for vt in maintenance:
            family = mp._unit_family(vt["unit"])
            if family in focus_family_max:
                self.assertLessEqual(
                    vt["month_target"],
                    focus_family_max[family] * MAINTENANCE_VOLUME_MULTIPLIER,
                    vt,
                )

    def test_focus_multiplier_scales_only_focus_exercises(self):
        markers = _MARKERS["intermediate"]
        baseline_goal, _ = _stage("intermediate", "upper_body", markers)
        with patch.object(mp, "FOCUS_VOLUME_MULTIPLIER", 2.0):
            boosted_goal, _ = _stage("intermediate", "upper_body", markers)

        base_map = {vt["exercise"]: vt for vt in baseline_goal["volume_targets"]}
        for vt in boosted_goal["volume_targets"]:
            base = base_map[vt["exercise"]]
            if vt["balance_area"] == "upper_body":
                self.assertGreater(vt["month_target"], base["month_target"], vt["exercise"])
            else:
                self.assertEqual(vt["month_target"], base["month_target"], vt["exercise"])

    def test_balanced_goal_every_exercise_is_full_volume(self):
        goal, _ = _stage("beginner", "balanced", _MARKERS["beginner"])
        self.assertEqual(goal["focus"], "balanced")
        self.assertEqual(len(goal["volume_targets"]), 4)
        for vt in goal["volume_targets"]:
            self.assertEqual(mp._role_for_area("balanced", vt["balance_area"]), "balanced")
            expected = _calc(
                vt["exercise"],
                vt["balance_area"],
                "beginner",
                _MARKERS["beginner"],
            )["month_target"]
            self.assertEqual(vt["month_target"], expected)


class TestUnitsAndSwap(unittest.TestCase):
    def test_timed_and_per_side_units_are_preserved(self):
        timed = _calc("Bodyweight Incline Side Plank", "core", "intermediate")
        per_side = _calc("Split Squats", "lower_body", "intermediate")
        self.assertEqual(timed["month_target"], int(timed["month_target"]))
        self.assertEqual(per_side["month_target"], int(per_side["month_target"]))
        self.assertEqual(
            _WORKOUTS_BY_NAME["Bodyweight Incline Side Plank"]["baseline"]["unit"], "seconds"
        )
        self.assertEqual(_WORKOUTS_BY_NAME["Split Squats"]["baseline"]["unit"], "reps_per_side")
        self.assertNotEqual(timed["category"], per_side["category"])

    def test_swap_gives_replacement_its_own_target(self):
        decline = _calc(
            "Decline Push-Up", "upper_body", "intermediate", _MARKERS["intermediate"]
        )
        staged_doc = {
            "user_id": "test_user",
            "month_id": "2026-11",
            "goal": {
                "status": "pending",
                "focus": "upper_body",
                "goal_start_date": "2026-11-01",
                "volume_targets": [
                    {
                        "exercise": "Decline Push-Up",
                        "unit": "reps",
                        "month_target": decline["month_target"],
                        "full_month_target": decline["month_target"],
                        "balance_area": "upper_body",
                    }
                ],
            },
        }
        col = MagicMock()
        col.find_one.return_value = staged_doc
        col.update_one.return_value = MagicMock()
        with (
            patch.object(mp, "get_month_plans_collection", return_value=col),
            patch.object(mp, "get_current_user_id", return_value="test_user"),
            patch.object(mp, "today_local", return_value=date(2026, 11, 1)),
            patch.object(mp, "resolve_user_equipment", return_value=(None, {})),
            patch.object(mp, "get_user_profile", return_value={"experience_level": "intermediate"}),
            patch.object(
                mp,
                "get_latest_baseline_assessment",
                return_value={"answers": {"raw_markers": _MARKERS["intermediate"]}},
            ),
        ):
            result = mp.swap_goal_exercise.func(
                old_exercise="Decline Push-Up",
                new_exercise="Incline Push-Up",
                month_id="2026-11",
            )

        self.assertIn("Swapped", result)
        updated = col.update_one.call_args[0][1]["$set"]["goal.volume_targets"]
        self.assertEqual(updated[0]["exercise"], "Incline Push-Up")
        self.assertNotEqual(updated[0]["month_target"], decline["month_target"])


class TestProrating(unittest.TestCase):
    def test_mid_month_goal_is_still_prorated_and_keeps_full_target(self):
        goal, result = _stage(
            "intermediate",
            "upper_body",
            _MARKERS["intermediate"],
            today=date(2026, 11, 16),
        )
        self.assertIn("Mid-month", result)
        scaling = compute_goal_scaling(date(2026, 11, 16), "2026-11")
        self.assertLess(scaling["scale"], 1.0)
        for vt in goal["volume_targets"]:
            self.assertIn("full_month_target", vt)
            self.assertGreaterEqual(vt["month_target"], 0)
            self.assertLessEqual(vt["month_target"], vt["full_month_target"])
            expected = int(round(vt["full_month_target"] * scaling["scale"]))
            self.assertEqual(vt["month_target"], expected)


if __name__ == "__main__":
    unittest.main()
