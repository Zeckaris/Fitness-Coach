"""Tests for untracked evaluation plan generation and dosing."""

import os
import sys
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("GEMINI_API_KEY", "dummy_key_for_testing")

for _mod in ("langfuse", "langfuse.langchain"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

for _mod in ("agent.llm", "tools.knowledge_base"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import tools.plans as plans
from utils.baseline_targets import (
    calculate_exercise_month_target,
    calculate_exercise_session_target,
)
from utils.evaluation_plan import (
    build_evaluation_main_exercises,
    has_user_equipment,
    is_bodyweight_exercise,
    movement_family,
)
from utils.exercise_ordering import reorder_phase
from utils.exercise_prescription import format_exercise_prescription


class TestEvaluationPlan(unittest.TestCase):
    def setUp(self):
        self.intermediate_markers = {
            "push_ups": 25.0,
            "squats": 30.0,
            "plank_seconds": 45.0,
            "pull_ups": 8.0,
        }

    # 1. Family coverage per day
    def test_family_coverage_per_day(self):
        dates = ["2026-10-28", "2026-10-29", "2026-10-30"]
        plan = build_evaluation_main_exercises(
            dates=dates,
            experience_level="intermediate",
            raw_markers=self.intermediate_markers,
            resolved_equipment=["pull_up_bar", "rope"],
        )
        for d in dates:
            day_exercises = plan[d]
            families = {movement_family(ex["name"]) for ex in day_exercises}
            # Every day must cover the four required families
            self.assertTrue({"push", "lower_body", "core", "cardio"}.issubset(families))

    # 2. Equipment gating for pull-up and rope
    def test_equipment_gating_pull_up_and_rope(self):
        dates = ["2026-10-28"]
        # With neither pull-up bar nor rope
        plan_none = build_evaluation_main_exercises(
            dates=dates,
            resolved_equipment=[],
        )
        names_none = {ex["name"] for ex in plan_none["2026-10-28"]}
        self.assertNotIn("Pull-Up", names_none)
        self.assertNotIn("Jump Rope", names_none)

        # With pull-up bar only
        plan_bar = build_evaluation_main_exercises(
            dates=dates,
            resolved_equipment=["pull_up_bar"],
        )
        names_bar = {ex["name"] for ex in plan_bar["2026-10-28"]}
        self.assertIn("Pull-Up", names_bar)
        self.assertNotIn("Jump Rope", names_bar)

        # With rope only
        plan_rope = build_evaluation_main_exercises(
            dates=dates,
            resolved_equipment=["rope"],
        )
        names_rope = {ex["name"] for ex in plan_rope["2026-10-28"]}
        self.assertNotIn("Pull-Up", names_rope)
        self.assertIn("Jump Rope", names_rope)

    # 3. Bodyweight-only users
    def test_bodyweight_only_users(self):
        dates = ["2026-10-28", "2026-10-29", "2026-10-30"]
        plan = build_evaluation_main_exercises(
            dates=dates,
            experience_level="beginner",
            raw_markers=None,
            resolved_equipment=[],
        )
        from tools.workout_library import get_exercise_metadata

        for d in dates:
            for ex in plan[d]:
                meta = get_exercise_metadata(ex["name"]) or {}
                self.assertTrue(
                    is_bodyweight_exercise(meta),
                    f"{ex['name']} is not a bodyweight exercise",
                )
                self.assertNotEqual(ex["name"], "Pull-Up")
                self.assertNotEqual(ex["name"], "Jump Rope")

    # 4. Short windows (1-day, 2-day)
    def test_short_windows(self):
        # 1-day window
        plan_1 = build_evaluation_main_exercises(
            dates=["2026-10-28"],
            experience_level="beginner",
            resolved_equipment=[],
        )
        self.assertIn("2026-10-28", plan_1)
        # Minimum 4 main exercises (with pairs, 7 items)
        self.assertGreaterEqual(len(plan_1["2026-10-28"]), 4)

        # 2-day window with equipment
        plan_2 = build_evaluation_main_exercises(
            dates=["2026-10-28", "2026-10-29"],
            experience_level="intermediate",
            raw_markers=self.intermediate_markers,
            resolved_equipment=["pull_up_bar", "rope"],
        )
        self.assertIn("2026-10-28", plan_2)
        self.assertIn("2026-10-29", plan_2)
        self.assertGreaterEqual(len(plan_2["2026-10-28"]), 4)
        self.assertGreaterEqual(len(plan_2["2026-10-29"]), 4)

    # 5. Evaluation pairs repeat on every remaining day
    def test_evaluation_pairs_repeat_every_day(self):
        dates = [f"2026-10-{d:02d}" for d in range(25, 31)]  # 6 days
        plan = build_evaluation_main_exercises(
            dates=dates,
            experience_level="intermediate",
            raw_markers=self.intermediate_markers,
            resolved_equipment=[],
        )
        movement_names = {
            "push": "Push-Up",
            "lower_body": "Squat to Overhead Reach",
            "core": "Bodyweight Incline Side Plank",
        }
        claim_amounts = {}
        for d in dates:
            day = plan[d]
            for family, name in movement_names.items():
                entries = [ex for ex in day if ex["name"] == name]
                self.assertEqual(len(entries), 2, f"{name} pair missing on {d}")
                claim = next(e for e in entries if e["pair_role"] == "claim")
                easier = next(e for e in entries if e["pair_role"] == "easier")
                self.assertIsNotNone(claim["pair_id"])
                self.assertEqual(claim["pair_id"], easier["pair_id"])
                claim_amounts.setdefault(family, set()).add(claim["target_quantity"])
        # The same claimed/lower amounts repeat across the whole window.
        for amounts in claim_amounts.values():
            self.assertEqual(len(amounts), 1)

    # 6. Determinism
    def test_determinism(self):
        dates = ["2026-10-28", "2026-10-29", "2026-10-30"]
        plan_a = build_evaluation_main_exercises(
            dates=dates,
            experience_level="intermediate",
            raw_markers=self.intermediate_markers,
            resolved_equipment=["pull_up_bar", "rope"],
        )
        plan_b = build_evaluation_main_exercises(
            dates=dates,
            experience_level="intermediate",
            raw_markers=self.intermediate_markers,
            resolved_equipment=["pull_up_bar", "rope"],
        )
        self.assertEqual(plan_a, plan_b)

    # 7. Pair structure and display
    def test_pair_structure_and_display(self):
        dates = ["2026-10-28", "2026-10-29"]
        plan = build_evaluation_main_exercises(
            dates=dates,
            experience_level="intermediate",
            raw_markers=self.intermediate_markers,
            resolved_equipment=["pull_up_bar"],
        )
        day1 = plan["2026-10-28"]
        push_pairs = [e for e in day1 if e["name"] == "Push-Up"]
        self.assertEqual(len(push_pairs), 2)
        claim_e, easier_e = push_pairs[0], push_pairs[1]

        # Shared pair marker
        self.assertTrue(claim_e["pair_id"])
        self.assertEqual(claim_e["pair_id"], easier_e["pair_id"])

        # Roles
        self.assertEqual(claim_e["pair_role"], "claim")
        self.assertEqual(easier_e["pair_role"], "easier")

        # Amounts
        self.assertEqual(claim_e["target_quantity"], 25)
        self.assertEqual(claim_e["sets"], 1)
        self.assertEqual(easier_e["target_quantity"], 18)
        self.assertEqual(easier_e["sets"], 1)

        # Display formatting
        claim_display = format_exercise_prescription(claim_e)
        easier_display = format_exercise_prescription(easier_e)
        self.assertIn("claimed amount - do one of the two", claim_display)
        self.assertIn("easier option - do one of the two", easier_display)

        # Reordering keeps pairs contiguous
        reordered = reorder_phase(day1)
        indices = [i for i, e in enumerate(reordered) if e.get("pair_id") == claim_e["pair_id"]]
        self.assertEqual(len(indices), 2)
        self.assertEqual(indices[1], indices[0] + 1)
        self.assertEqual(reordered[indices[0]]["pair_role"], "claim")
        self.assertEqual(reordered[indices[1]]["pair_role"], "easier")

        # The pair repeats on the later day too, with the same amounts.
        day2 = plan["2026-10-29"]
        push_day2 = [e for e in day2 if movement_family(e["name"]) == "push"]
        self.assertEqual(len(push_day2), 2)
        day2_claim = next(e for e in push_day2 if e["pair_role"] == "claim")
        day2_easier = next(e for e in push_day2 if e["pair_role"] == "easier")
        self.assertEqual(day2_claim["target_quantity"], 25)
        self.assertEqual(day2_easier["target_quantity"], 18)
        self.assertNotEqual(day2_claim["pair_id"], claim_e["pair_id"])

    # 8. Jump rope without a pair
    def test_jump_rope_without_a_pair(self):
        dates = ["2026-10-28"]
        plan = build_evaluation_main_exercises(
            dates=dates,
            resolved_equipment=["rope"],
        )
        rope_entries = [e for e in plan["2026-10-28"] if e["name"] == "Jump Rope"]
        self.assertEqual(len(rope_entries), 1)
        rope = rope_entries[0]
        self.assertIsNone(rope.get("pair_id"))
        self.assertIsNone(rope.get("pair_role"))
        self.assertEqual(rope["unit"], "reps")
        self.assertGreater(rope["target_quantity"], 0)

    # 9. Per-session amounts for each experience level
    def test_per_session_amounts_for_experience_levels(self):
        for exp in ("beginner", "intermediate", "advanced"):
            res = calculate_exercise_session_target(
                exercise="Incline Push-Up",
                unit="reps",
                balance_area="upper_body",
                experience_level=exp,
            )
            self.assertIn("session_target", res)
            self.assertGreater(res["session_target"], 0)
            self.assertEqual(res["sets"], 3)
            self.assertGreater(res["reps_per_set"], 0)
            # Must not include monthly multipliers or weeks in month
            self.assertNotIn("weeks_in_month", res)
            self.assertNotIn("sessions_per_week", res)

    # 10. Goal-based planning unchanged
    def test_goal_based_planning_unchanged(self):
        # calculate_exercise_month_target still calculates full monthly math
        res = calculate_exercise_month_target(
            exercise="Push-Up",
            unit="reps",
            balance_area="upper_body",
            experience_level="intermediate",
            baseline_value=25.0,
            sessions_per_week=3,
            weeks_in_month=4.35,
            progression_multiplier=1.1,
            volume_multiplier=1.2,
        )
        self.assertIn("month_target", res)
        self.assertIn("weeks_in_month", res)
        self.assertEqual(res["sessions_per_week"], 3)
        self.assertEqual(res["progression_multiplier"], 1.1)
        self.assertEqual(res["volume_multiplier"], 1.2)
        # 25 * 0.80 * 3 * 3 * 4.35 * 1.1 * 1.2
        expected = int(round(25 * 0.80 * 3 * 3 * 4.35 * 1.1 * 1.2))
        self.assertEqual(res["month_target"], expected)


if __name__ == "__main__":
    unittest.main()

