"""Tests for plan prescription finalisation and shared display formatting."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.exercise_prescription import (
    finalize_exercise,
    finalize_exercises,
    format_exercise_prescription,
)


class TestFinalisation(unittest.TestCase):
    def test_never_leaves_empty_sets_or_amount(self):
        exercises = [
            {"name": "Push-Up", "category": "main"},
            {"name": "Bear Crawl", "category": "warmup"},
            {"name": "Dead Bug", "category": "cooldown"},
            {"name": "Not In Library", "category": "main", "sets": "", "reps": ""},
            {"name": "Mountain Climber", "category": "main", "sets": 0, "reps": "None"},
        ]

        result = finalize_exercises(exercises)

        self.assertEqual(len(result), len(exercises))
        for exercise in result:
            self.assertIsInstance(exercise["sets"], int)
            self.assertGreater(exercise["sets"], 0)
            self.assertTrue(str(exercise["reps"]).strip())
            self.assertNotIn(str(exercise["reps"]).lower(), {"none", "null", "?"})
            self.assertTrue(exercise["unit"])

    def test_resolution_order_target_then_plan_then_library_then_generic(self):
        target = finalize_exercise(
            {
                "name": "Push-Up",
                "category": "main",
                "sets": 9,
                "reps": "99",
                "target_quantity": 45,
                "unit": "reps",
            },
            week_focus="Foundation",
        )
        self.assertEqual(target["sets"], 3)
        self.assertEqual(target["reps"], "15 reps")

        plan = finalize_exercise(
            {
                "name": "Push-Up",
                "category": "main",
                "sets": 4,
                "reps": "12-15",
            }
        )
        self.assertEqual(plan["sets"], 4)
        self.assertEqual(plan["reps"], "12-15")

        library = finalize_exercise({"name": "Push-Up", "category": "main"})
        self.assertEqual(library["sets"], 3)
        self.assertEqual(library["reps"], "8-15")
        self.assertEqual(library["unit"], "reps")

        generic = finalize_exercise({"name": "Not In Library", "category": "main"})
        self.assertEqual(generic["sets"], 1)
        self.assertEqual(generic["reps"], "10")
        self.assertEqual(generic["unit"], "reps")

    def test_target_map_fills_force_inserted_goal_exercise(self):
        result = finalize_exercise(
            {"name": "Push-Up", "category": "main"},
            week_focus="Foundation",
            daily_targets=[
                {"exercise": "Push-Up", "unit": "reps", "daily_target": 45}
            ],
        )

        self.assertEqual(result["target_quantity"], 45)
        self.assertEqual(result["sets"], 3)
        self.assertEqual(result["reps"], "15 reps")

    def test_warmup_and_cooldown_are_lighter(self):
        warmup = finalize_exercise(
            {
                "name": "Push-Up",
                "category": "warmup",
                "sets": 4,
                "reps": "10-15 reps",
                "target_quantity": 100,
                "unit": "reps",
            },
            week_focus="Foundation",
        )
        self.assertLessEqual(warmup["sets"], 2)
        self.assertEqual(warmup["reps"], "8")
        self.assertEqual(warmup["unit"], "reps")
        self.assertIsNone(warmup["target_quantity"])

        cooldown = finalize_exercise(
            {"name": "Push-Up", "category": "cooldown"},
            week_focus="Foundation",
        )
        self.assertLessEqual(cooldown["sets"], 2)
        self.assertEqual(cooldown["reps"], "8")
        self.assertEqual(cooldown["unit"], "reps")


class TestDisplayFormatting(unittest.TestCase):
    def test_reps_timed_and_per_side(self):
        self.assertEqual(
            format_exercise_prescription(
                {"name": "Push-Up", "sets": 2, "reps": "10", "unit": "reps"}
            ),
            "2x10 reps",
        )
        self.assertEqual(
            format_exercise_prescription(
                {
                    "name": "Bodyweight Incline Side Plank",
                    "sets": 2,
                    "reps": "30",
                    "unit": "seconds",
                }
            ),
            "2x30 sec",
        )
        self.assertEqual(
            format_exercise_prescription(
                {
                    "name": "Split Squats",
                    "sets": 2,
                    "reps": "10",
                    "unit": "reps_per_side",
                }
            ),
            "2x10 reps per side",
        )

    def test_ranges_are_kept(self):
        self.assertEqual(
            format_exercise_prescription(
                {"name": "Push-Up", "sets": 3, "reps": "8-15", "unit": "reps"}
            ),
            "3x8-15 reps",
        )

    def test_unit_is_not_duplicated(self):
        self.assertEqual(
            format_exercise_prescription(
                {"name": "Push-Up", "sets": 2, "reps": "10 reps", "unit": "reps"}
            ),
            "2x10 reps",
        )
        self.assertEqual(
            format_exercise_prescription(
                {
                    "name": "Bodyweight Incline Side Plank",
                    "sets": 2,
                    "reps": "30 sec",
                    "unit": "seconds",
                }
            ),
            "2x30 sec",
        )
        self.assertEqual(
            format_exercise_prescription(
                {"name": "Split Squats", "sets": 2, "reps": "10 reps", "unit": "reps_per_side"}
            ),
            "2x10 reps per side",
        )

    def test_distance_is_rendered_sensibly(self):
        self.assertEqual(
            format_exercise_prescription(
                {"name": "Push-Up", "sets": 1, "reps": "1.5", "unit": "km"}
            ),
            "1.5 km",
        )
        self.assertEqual(
            format_exercise_prescription(
                {"name": "Push-Up", "sets": 2, "reps": "1.5", "unit": "km"}
            ),
            "2x1.5 km",
        )

    def test_old_empty_plan_values_fall_back_to_library(self):
        self.assertEqual(
            format_exercise_prescription(
                {"name": "Push-Up", "sets": None, "reps": None}
            ),
            "3x8-15 reps",
        )
        self.assertEqual(
            format_exercise_prescription(
                {"name": "Bodyweight Incline Side Plank", "sets": "", "reps": ""}
            ),
            "3x20-30 sec",
        )
        self.assertEqual(
            format_exercise_prescription(
                {"name": "Split Squats", "sets": None, "reps": None}
            ),
            "3x10-12 reps per side",
        )


if __name__ == "__main__":
    unittest.main()
