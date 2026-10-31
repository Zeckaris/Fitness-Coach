"""
Tests for monthly goal focus selection, deterministic composition, maintenance coverage,
equipment fallbacks, exercise swapping, and new-month carryover/change.
"""

import unittest
from datetime import date
from unittest.mock import patch, MagicMock

from utils.goal_composition import (
    compose_goal_exercise_list,
    select_exercise_for_group,
    select_cardio_exercises,
    select_maintenance_exercise_for_area,
    FOCUS_MAJOR_GROUPS,
    GROUP_TO_BALANCE_AREA,
    VALID_FOCUSES,
    BALANCE_AREAS,
    _WORKOUTS,
    _WORKOUTS_BY_NAME,
)
import tools.month_plans as mp


class TestFocusSelectionAndCoverage(unittest.TestCase):
    """Test focus selection and muscle-group coverage for each focus."""

    def test_upper_body_focus_coverage(self):
        result = compose_goal_exercise_list(focus="upper_body", available_equipment=None, experience_level="beginner")
        self.assertEqual(result["focus"], "upper_body")
        exercises = result["exercises"]
        # upper body has 5 major groups + 3 maintenance (lower, core, cardio) = 8
        self.assertEqual(len(exercises), 8)

        # Check muscle groups for upper_body focus
        ub_exercises = [e for e in exercises if e["balance_area"] == "upper_body"]
        self.assertEqual(len(ub_exercises), 5)
        ub_groups = {e["muscle_group"] for e in ub_exercises}
        self.assertEqual(ub_groups, {"chest", "back", "shoulders", "biceps", "triceps"})

        # Check that all 4 balance areas are present
        areas = {e["balance_area"] for e in exercises}
        self.assertEqual(areas, {"upper_body", "lower_body", "core", "cardio"})

    def test_lower_body_focus_coverage(self):
        result = compose_goal_exercise_list(focus="lower_body", available_equipment=None, experience_level="beginner")
        self.assertEqual(result["focus"], "lower_body")
        exercises = result["exercises"]
        # lower body has 4 major groups + 3 maintenance (upper, core, cardio) = 7
        self.assertEqual(len(exercises), 7)

        lb_exercises = [e for e in exercises if e["balance_area"] == "lower_body"]
        self.assertEqual(len(lb_exercises), 4)
        lb_groups = {e["muscle_group"] for e in lb_exercises}
        self.assertEqual(lb_groups, {"quads", "hamstrings", "glutes", "calves"})

        areas = {e["balance_area"] for e in exercises}
        self.assertEqual(areas, {"upper_body", "lower_body", "core", "cardio"})

    def test_core_focus_coverage(self):
        result = compose_goal_exercise_list(focus="core", available_equipment=None, experience_level="beginner")
        self.assertEqual(result["focus"], "core")
        exercises = result["exercises"]
        # core has 3 major groups + 3 maintenance (upper, lower, cardio) = 6
        self.assertEqual(len(exercises), 6)

        core_exercises = [e for e in exercises if e["balance_area"] == "core"]
        self.assertEqual(len(core_exercises), 3)
        core_groups = {e["muscle_group"] for e in core_exercises}
        self.assertEqual(core_groups, {"abs", "core", "lower_back"})

        areas = {e["balance_area"] for e in exercises}
        self.assertEqual(areas, {"upper_body", "lower_body", "core", "cardio"})

    def test_cardio_focus_coverage(self):
        result = compose_goal_exercise_list(focus="cardio", available_equipment=None, experience_level="beginner")
        self.assertEqual(result["focus"], "cardio")
        exercises = result["exercises"]
        # cardio gets 2-3 exercises + 3 maintenance (upper, lower, core)
        cardio_exercises = [e for e in exercises if e["balance_area"] == "cardio"]
        self.assertIn(len(cardio_exercises), [2, 3])

        areas = {e["balance_area"] for e in exercises}
        self.assertEqual(areas, {"upper_body", "lower_body", "core", "cardio"})

    def test_balanced_focus_coverage(self):
        result = compose_goal_exercise_list(focus="balanced", available_equipment=None, experience_level="beginner")
        self.assertEqual(result["focus"], "balanced")
        exercises = result["exercises"]
        # balanced gets exactly 1 exercise per area = 4
        self.assertEqual(len(exercises), 4)

        areas = [e["balance_area"] for e in exercises]
        self.assertEqual(len(areas), 4)
        self.assertEqual(set(areas), {"upper_body", "lower_body", "core", "cardio"})

    def test_default_focus_when_unspecified(self):
        result = compose_goal_exercise_list(focus="", available_equipment=None)
        self.assertEqual(result["focus"], "balanced")
        self.assertEqual(len(result["exercises"]), 4)


class TestMaintenanceExercises(unittest.TestCase):
    """Test that non-focus areas get exactly one maintenance exercise."""

    def test_one_maintenance_per_non_focus_area(self):
        for focus in ("upper_body", "lower_body", "core", "cardio"):
            result = compose_goal_exercise_list(focus=focus, available_equipment=None)
            exercises = result["exercises"]
            for area in BALANCE_AREAS:
                if area != focus:
                    count = len([e for e in exercises if e["balance_area"] == area])
                    self.assertEqual(count, 1, f"Focus {focus} should have exactly 1 maintenance exercise for {area}")

    def test_no_duplicate_exercises_selected(self):
        for focus in VALID_FOCUSES:
            result = compose_goal_exercise_list(focus=focus, available_equipment=None)
            names = [e["name"] for e in result["exercises"]]
            self.assertEqual(len(names), len(set(names)), f"Duplicates found in {focus} selection: {names}")


class TestEquipmentFallbacks(unittest.TestCase):
    """Test fallbacks to bodyweight and reporting of short groups."""

    def test_bodyweight_only_fallbacks_succeed_for_all_groups(self):
        # Even with no equipment tokens at all, all groups should find exercises
        result = compose_goal_exercise_list(focus="upper_body", available_equipment=[])
        self.assertEqual(len(result["short_groups"]), 0)
        self.assertEqual(len(result["exercises"]), 8)

    def test_short_group_reporting_when_completely_ruled_out(self):
        # Test synthetic scenario where a group has no matching primary or secondary
        fake_workouts = [
            {
                "name": "Barbell Bench Press",
                "primary_target_area": "chest",
                "equipment": ["bench", "dumbbells"],
                "difficulty": "intermediate",
                "avoid_if_injured": [],
                "baseline": {"unit": "reps"},
            }
        ]
        # User has only rope
        ex, short_g = select_exercise_for_group(
            group="chest",
            available_equipment=["rope"],
            avoid_body_parts=None,
            workouts=fake_workouts,
        )
        self.assertIsNone(ex)
        self.assertEqual(short_g, "chest")

    def test_avoid_body_parts_filtering(self):
        # An exercise that aggravates shoulder injury should be excluded
        result_normal = compose_goal_exercise_list(focus="upper_body", available_equipment=None, avoid_body_parts=None)
        names_normal = {e["name"] for e in result_normal["exercises"]}

        # Adding shoulder injury should avoid exercises that list shoulder under avoid_if_injured
        result_injured = compose_goal_exercise_list(focus="upper_body", available_equipment=None, avoid_body_parts=["shoulder"])
        for e in result_injured["exercises"]:
            meta = _WORKOUTS_BY_NAME.get(e["name"])
            if meta:
                self.assertNotIn("shoulder", meta.get("avoid_if_injured", []))


class TestSelectionDeterminism(unittest.TestCase):
    """Test deterministic selection and experience level preferences."""

    def test_determinism_across_multiple_runs(self):
        run1 = compose_goal_exercise_list(focus="upper_body", available_equipment=["dumbbells"], experience_level="beginner")
        run2 = compose_goal_exercise_list(focus="upper_body", available_equipment=["dumbbells"], experience_level="beginner")
        self.assertEqual([e["name"] for e in run1["exercises"]], [e["name"] for e in run2["exercises"]])

    def test_experience_level_preference(self):
        # Beginner preference vs Advanced preference
        beg = compose_goal_exercise_list(focus="balanced", available_equipment=None, experience_level="beginner")
        adv = compose_goal_exercise_list(focus="balanced", available_equipment=None, experience_level="advanced")
        # Both return valid goals, deterministic
        self.assertEqual(len(beg["exercises"]), 4)
        self.assertEqual(len(adv["exercises"]), 4)


class TestGoalStagingAndSwap(unittest.TestCase):
    """Test staging with focus and swapping staged exercises."""

    def setUp(self):
        self.mock_col = MagicMock()
        self.mock_col.find_one.return_value = None
        self.mock_col.update_one.return_value = MagicMock()

    def test_stage_month_goal_automatic_composition(self):
        with (
            patch.object(mp, "get_month_plans_collection", return_value=self.mock_col),
            patch.object(mp, "get_current_user_id", return_value="test_user"),
            patch.object(mp, "today_local", return_value=date(2026, 11, 1)),
            patch.object(mp, "get_progression_multipliers", return_value={}),
            patch.object(mp, "resolve_user_equipment", return_value=(["dumbbells"], {})),
            patch.object(mp, "get_user_profile", return_value={"experience_level": "intermediate"}),
            patch.object(mp, "get_latest_baseline_assessment", return_value=None),
        ):
            res = mp.stage_month_goal.func(description="Upper Body Focus", focus="upper_body")
            self.assertIn("Staged goal for", res)
            self.assertIn("Focus: upper_body", res)
            self.assertIn("Proposed volume targets:", res)

            # Check that update_one was called with 8 volume targets
            self.assertTrue(self.mock_col.update_one.called)
            call_args = self.mock_col.update_one.call_args[0]
            update_doc = call_args[1]["$set"]["goal"]
            self.assertEqual(update_doc["focus"], "upper_body")
            self.assertEqual(len(update_doc["volume_targets"]), 8)
            # Verify each volume target has month_target and unit
            for vt in update_doc["volume_targets"]:
                self.assertIn("exercise", vt)
                self.assertIn("month_target", vt)
                self.assertIn("unit", vt)
                self.assertGreater(vt["month_target"], 0)

    def test_swap_goal_exercise_success(self):
        staged_doc = {
            "user_id": "test_user",
            "month_id": "2026-11",
            "goal": {
                "status": "pending",
                "focus": "upper_body",
                "goal_start_date": "2026-11-01",
                "volume_targets": [
                    {
                        "exercise": "Push-up",
                        "unit": "reps",
                        "month_target": 400,
                        "full_month_target": 400,
                        "balance_area": "upper_body",
                    },
                    {
                        "exercise": "Bodyweight Squat",
                        "unit": "reps",
                        "month_target": 300,
                        "full_month_target": 300,
                        "balance_area": "lower_body",
                    },
                ],
            },
        }
        self.mock_col.find_one.return_value = staged_doc

        with (
            patch.object(mp, "get_month_plans_collection", return_value=self.mock_col),
            patch.object(mp, "get_current_user_id", return_value="test_user"),
            patch.object(mp, "today_local", return_value=date(2026, 11, 1)),
            patch.object(mp, "resolve_user_equipment", return_value=(None, {})),
            patch.object(mp, "get_user_profile", return_value={"experience_level": "beginner"}),
            patch.object(mp, "get_latest_baseline_assessment", return_value=None),
        ):
            # Swap Push-up with Dumbbell Bench Press (both chest)
            res = mp.swap_goal_exercise.func(old_exercise="Push-up", new_exercise="Dumbbell Bench Press", month_id="2026-11")
            self.assertIn("Swapped 'Push-up' with 'Dumbbell Bench Press'", res)
            self.assertTrue(self.mock_col.update_one.called)
            call_args = self.mock_col.update_one.call_args[0]
            updated_vts = call_args[1]["$set"]["goal.volume_targets"]
            names = [vt["exercise"] for vt in updated_vts]
            self.assertIn("Dumbbell Bench Press", names)
            self.assertNotIn("Push-up", names)

    def test_swap_goal_exercise_refuses_different_muscle_group(self):
        staged_doc = {
            "user_id": "test_user",
            "month_id": "2026-11",
            "goal": {
                "status": "pending",
                "focus": "upper_body",
                "volume_targets": [
                    {"exercise": "Push-up", "unit": "reps", "month_target": 400, "balance_area": "upper_body"},
                ],
            },
        }
        self.mock_col.find_one.return_value = staged_doc

        with (
            patch.object(mp, "get_month_plans_collection", return_value=self.mock_col),
            patch.object(mp, "get_current_user_id", return_value="test_user"),
            patch.object(mp, "today_local", return_value=date(2026, 11, 1)),
            patch.object(mp, "resolve_user_equipment", return_value=(None, {})),
            patch.object(mp, "get_user_profile", return_value={"experience_level": "beginner"}),
        ):
            # Push-up is chest; trying to swap with Split Squats (quads) must fail
            res = mp.swap_goal_exercise.func(old_exercise="Push-up", new_exercise="Split Squats", month_id="2026-11")
            self.assertIn("Cannot swap", res)
            self.assertIn("not in the same muscle group", res)

    def test_swap_goal_exercise_refuses_confirmed_goal(self):
        confirmed_doc = {
            "user_id": "test_user",
            "month_id": "2026-11",
            "goal": {
                "status": "confirmed",
                "volume_targets": [
                    {"exercise": "Push-up", "unit": "reps", "month_target": 400, "balance_area": "upper_body"},
                ],
            },
        }
        self.mock_col.find_one.return_value = confirmed_doc

        with (
            patch.object(mp, "get_month_plans_collection", return_value=self.mock_col),
            patch.object(mp, "get_current_user_id", return_value="test_user"),
            patch.object(mp, "today_local", return_value=date(2026, 11, 1)),
        ):
            res = mp.swap_goal_exercise.func(old_exercise="Push-up", new_exercise="Bench Press", month_id="2026-11")
            self.assertIn("already confirmed", res)


class TestNewMonthFocusChangeAndCarryover(unittest.TestCase):
    """Test carryover of focus and exercises into new month, and changing focus."""

    def setUp(self):
        self.mock_col = MagicMock()
        self.mock_col.find_one.return_value = None
        self.mock_col.update_one.return_value = MagicMock()

    def test_new_month_carries_over_previous_focus_and_exercises_by_default(self):
        prev_confirmed_goal = {
            "status": "confirmed",
            "focus": "upper_body",
            "volume_targets": [
                {"exercise": "Push-up", "unit": "reps", "month_target": 400, "balance_area": "upper_body"},
                {"exercise": "Pull-up", "unit": "reps", "month_target": 150, "balance_area": "upper_body"},
                {"exercise": "Bodyweight Squat", "unit": "reps", "month_target": 300, "balance_area": "lower_body"},
                {"exercise": "Plank", "unit": "seconds", "month_target": 600, "balance_area": "core"},
                {"exercise": "Mountain Climber", "unit": "reps", "month_target": 500, "balance_area": "cardio"},
            ],
        }

        with (
            patch.object(mp, "get_month_plans_collection", return_value=self.mock_col),
            patch.object(mp, "get_current_user_id", return_value="test_user"),
            patch.object(mp, "today_local", return_value=date(2026, 12, 1)),
            patch.object(mp, "get_progression_multipliers", return_value={}),
            patch.object(mp, "get_most_recent_confirmed_goal", return_value=("2026-11", prev_confirmed_goal)),
            patch.object(mp, "resolve_user_equipment", return_value=(None, {})),
            patch.object(mp, "get_user_profile", return_value={"experience_level": "intermediate"}),
            patch.object(mp, "get_latest_baseline_assessment", return_value=None),
        ):
            # No focus provided -> carries over previous focus and exercises
            res = mp.stage_month_goal.func(description="New Month Training", focus=None, month_id="2026-12")
            self.assertIn("Focus: upper_body", res)
            call_args = self.mock_col.update_one.call_args[0]
            staged_goal = call_args[1]["$set"]["goal"]
            self.assertEqual(staged_goal["focus"], "upper_body")
            carried_names = [vt["exercise"] for vt in staged_goal["volume_targets"]]
            self.assertEqual(carried_names, ["Push-up", "Pull-up", "Bodyweight Squat", "Plank", "Mountain Climber"])

    def test_new_month_focus_change_reruns_selection(self):
        prev_confirmed_goal = {
            "status": "confirmed",
            "focus": "upper_body",
            "volume_targets": [
                {"exercise": "Push-up", "unit": "reps", "month_target": 400, "balance_area": "upper_body"},
            ],
        }

        with (
            patch.object(mp, "get_month_plans_collection", return_value=self.mock_col),
            patch.object(mp, "get_current_user_id", return_value="test_user"),
            patch.object(mp, "today_local", return_value=date(2026, 12, 1)),
            patch.object(mp, "get_progression_multipliers", return_value={}),
            patch.object(mp, "get_most_recent_confirmed_goal", return_value=("2026-11", prev_confirmed_goal)),
            patch.object(mp, "resolve_user_equipment", return_value=(None, {})),
            patch.object(mp, "get_user_profile", return_value={"experience_level": "intermediate"}),
            patch.object(mp, "get_latest_baseline_assessment", return_value=None),
        ):
            # Explicitly changing focus to lower_body re-runs selection
            res = mp.stage_month_goal.func(description="Leg Month", focus="lower_body", month_id="2026-12")
            self.assertIn("Focus: lower_body", res)
            call_args = self.mock_col.update_one.call_args[0]
            staged_goal = call_args[1]["$set"]["goal"]
            self.assertEqual(staged_goal["focus"], "lower_body")
            # lower_body focus yields 7 exercises (4 quads/hamstrings/glutes/calves + 3 maintenance)
            self.assertEqual(len(staged_goal["volume_targets"]), 7)


if __name__ == "__main__":
    unittest.main()
