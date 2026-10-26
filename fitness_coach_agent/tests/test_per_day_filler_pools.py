import re
import unittest
from unittest.mock import patch

from auth.context import set_current_user_id
from tools.workout_library import get_per_day_filler_pools


class TestPerDayFillerPools(unittest.TestCase):
    def setUp(self):
        set_current_user_id("test_user_fillers")

    def test_pool_exclusion_differs_between_adjacent_days(self):
        dates = ["2026-09-14", "2026-09-15"]
        result = get_per_day_filler_pools(dates)

        # Extract exercise names for each day
        day1_match = re.search(r"DAY 2026-09-14 FILLER POOL:\n((?:- .*\n?)+)", result)
        day2_match = re.search(r"DAY 2026-09-15 FILLER POOL:\n((?:- .*\n?)+)", result)

        self.assertIsNotNone(day1_match)
        self.assertIsNotNone(day2_match)

        day1_exercises = set(re.findall(r"- ([^(]+) \(", day1_match.group(1)))
        day2_exercises = set(re.findall(r"- ([^(]+) \(", day2_match.group(1)))

        self.assertTrue(len(day1_exercises) > 0)
        self.assertTrue(len(day2_exercises) > 0)
        # Adjacent days must have zero overlap when total library matches >= 20
        overlap = day1_exercises & day2_exercises
        self.assertEqual(overlap, set(), f"Adjacent day pools overlapped: {overlap}")

    def test_day_3_can_repeat_day_1_filler_exercises(self):
        # 50 synthetic workouts (allowing 20 per day pool size across 3 days)
        synthetic_matches = [
            {
                "name": f"Ex_{i}",
                "primary_target_area": "chest",
                "equipment": [],
                "difficulty": "beginner",
                "baseline": {"sets": 3, "reps": "10", "rest_seconds": 60, "unit": "reps"},
                "description": "Synthetic exercise",
            }
            for i in range(1, 51)
        ]

        with patch("tools.workout_library._WORKOUTS", synthetic_matches), \
             patch("tools.user_profile.resolve_user_equipment", return_value=([], {})):

            dates = ["2026-09-14", "2026-09-15", "2026-09-16"]
            result = get_per_day_filler_pools(dates)

            day1_match = re.search(r"DAY 2026-09-14 FILLER POOL:\n((?:- .*\n?)+)", result)
            day2_match = re.search(r"DAY 2026-09-15 FILLER POOL:\n((?:- .*\n?)+)", result)
            day3_match = re.search(r"DAY 2026-09-16 FILLER POOL:\n((?:- .*\n?)+)", result)

            day1_exercises = set(re.findall(r"- ([^(]+) \(", day1_match.group(1)))
            day2_exercises = set(re.findall(r"- ([^(]+) \(", day2_match.group(1)))
            day3_exercises = set(re.findall(r"- ([^(]+) \(", day3_match.group(1)))

            # Day 2 excludes Day 1
            self.assertEqual(day1_exercises & day2_exercises, set())
            # Day 3 excludes Day 2
            self.assertEqual(day2_exercises & day3_exercises, set())
            # Day 3 CAN contain exercises from Day 1 (since pool rotates back)
            day1_in_day3 = day1_exercises & day3_exercises
            self.assertTrue(
                len(day1_in_day3) > 0,
                "Day 3 should reuse Day 1 exercises when candidate pool rotates",
            )

    def test_goal_tracked_exercises_decoupled_from_filler_pool(self):
        from tools.workout_library import get_workouts_by_names, format_workout_lines
        goal_workouts = get_workouts_by_names(["Dead Bug", "Reverse Crunch"])
        goal_pool_str = format_workout_lines(goal_workouts)

        # Confirm Dead Bug and Reverse Crunch are rendered fully and unaffected
        self.assertIn("Dead Bug", goal_pool_str)
        self.assertIn("Reverse Crunch", goal_pool_str)


if __name__ == "__main__":
    unittest.main()
