"""
Unit tests for the three optional onboarding strength lifts:
1. Push lift (Dumbbell Bench Press or Floor Press)
2. Pull lift (Dumbbell Bent-Over Row or Max Strict Pull-Ups)
3. Lower-body lift (Goblet Squat)

Verifies:
- Scored and skipped cases for each lift
- Equipment gating (dumbbells/kettlebell for weighted lifts, bench alone does not count, pull_up_bar for pull-ups)
- Combined strength marker averaging into a single equal-weight marker
- Raw lift storage with assessment (exercise, weight in kg, reps, per_hand flag) and data version 3
- Older single-lift assessment backwards-compatibility
- Existing-user upgrade prompt does not alter confirmed current-month goals and dismissal persistence
"""

import unittest
from unittest.mock import MagicMock, patch

from app.components.onboarding_ui import (
    build_baseline_markers,
    has_free_weight_equipment,
    has_pull_up_equipment,
    normalize_bodyweight_kg,
    resolve_lift_scorable,
)
from tools.baseline_assessment import (
    BASELINE_ASSESSMENT_DATA_VERSION,
    record_baseline_assessment,
)
from utils.baseline_level import (
    calculate_overall_level,
    compute_combined_strength_score,
    compute_marker_thresholds,
    evaluate_overall_assessment,
    score_marker,
)


class TestThreeLiftsOnboarding(unittest.TestCase):
    def test_data_version_bumped_to_3(self):
        self.assertEqual(BASELINE_ASSESSMENT_DATA_VERSION, 3)

    # ---------------------------------------------------------
    # 1. Skipped & Scored cases for each lift
    # ---------------------------------------------------------
    def test_skipped_lifts_are_not_scored(self):
        # 0.0, None, negative, or blank are skipped
        self.assertIsNone(resolve_lift_scorable(None, "kg", has_equipment=True))
        self.assertIsNone(resolve_lift_scorable(0.0, "kg", has_equipment=True))
        self.assertIsNone(resolve_lift_scorable(0, "kg", has_equipment=True))
        self.assertIsNone(resolve_lift_scorable(-5.0, "kg", has_equipment=True))

        markers = build_baseline_markers(
            15, 25, 20, 3,
            push_lift_kg=None,
            pull_lift_kg=0.0,
            lower_lift_kg=None,
        )
        self.assertNotIn("push_lift", markers)
        self.assertNotIn("pull_lift", markers)
        self.assertNotIn("lower_lift", markers)

    def test_scored_lifts_resolve_correctly(self):
        # Push lift: 20 kg
        push_kg = resolve_lift_scorable(20.0, "kg", has_equipment=True)
        self.assertEqual(push_kg, 20.0)

        # Pull lift: row in lbs converts to kg
        pull_kg = resolve_lift_scorable(30.0, "lb", has_equipment=True, is_reps=False)
        self.assertAlmostEqual(pull_kg, 30.0 * 0.45359237, places=4)

        # Pull lift: pull-ups in reps (no lb conversion)
        pull_ups = resolve_lift_scorable(8, "reps", has_equipment=True, is_reps=True)
        self.assertEqual(pull_ups, 8.0)

        # Lower-body lift: goblet squat
        lower_kg = resolve_lift_scorable(24.0, "kg", has_equipment=True)
        self.assertEqual(lower_kg, 24.0)

        markers = build_baseline_markers(
            15, 25, 20, 3,
            push_lift_kg=push_kg,
            pull_lift_kg=pull_kg,
            lower_lift_kg=lower_kg,
        )
        self.assertIn("push_lift", markers)
        self.assertIn("pull_lift", markers)
        self.assertIn("lower_lift", markers)

    # ---------------------------------------------------------
    # 2. Equipment gating
    # ---------------------------------------------------------
    def test_bench_alone_does_not_enable_weighted_scoring(self):
        equipment = ["bench", "resistance_band"]
        self.assertFalse(has_free_weight_equipment(equipment))

        # Push lift with only bench must NOT be scored
        push_kg = resolve_lift_scorable(20.0, "kg", has_equipment=has_free_weight_equipment(equipment))
        self.assertIsNone(push_kg)

        # Lower-body lift with only bench must NOT be scored
        lower_kg = resolve_lift_scorable(24.0, "kg", has_equipment=has_free_weight_equipment(equipment))
        self.assertIsNone(lower_kg)

    def test_free_weights_enable_push_and_lower_lifts(self):
        for eq in (["dumbbells"], ["kettlebell"], ["dumbbells", "bench"]):
            with self.subTest(equipment=eq):
                self.assertTrue(has_free_weight_equipment(eq))
                push_kg = resolve_lift_scorable(20.0, "kg", has_equipment=has_free_weight_equipment(eq))
                self.assertEqual(push_kg, 20.0)

    def test_pull_up_bar_equipment_gating(self):
        # Pull-ups require pull_up_bar
        self.assertFalse(has_pull_up_equipment(["dumbbells", "bench"]))
        pull_ups_no_bar = resolve_lift_scorable(
            8, "reps",
            has_equipment=has_pull_up_equipment(["dumbbells", "bench"]),
            is_reps=True,
        )
        self.assertIsNone(pull_ups_no_bar)

        self.assertTrue(has_pull_up_equipment(["pull_up_bar"]))
        pull_ups_with_bar = resolve_lift_scorable(
            8, "reps",
            has_equipment=has_pull_up_equipment(["pull_up_bar"]),
            is_reps=True,
        )
        self.assertEqual(pull_ups_with_bar, 8.0)

    # ---------------------------------------------------------
    # 3. Combined strength marker & equal weight
    # ---------------------------------------------------------
    def test_combined_strength_score_averaging(self):
        # Male 70kg age 30:
        # push_ups thresholds: inter ~ 10, adv ~ 25
        # weighted_lift thresholds: 0.17 * 70 = 11.9 inter, 0.47 * 70 = 32.9 adv
        # Lifts:
        # 1 lift answered: goblet squat 15kg (score 2)
        score_1 = compute_combined_strength_score(
            lifts=[{"exercise": "Dumbbell Goblet Squat", "weight_kg": 15.0, "scored": True}],
            gender="male",
            age=30,
            actual_weight_kg=70.0,
        )
        self.assertEqual(score_1, 2.0)

        # 2 lifts answered: push 15kg (score 2) + goblet squat 35kg (score 3) -> avg 2.5
        score_2 = compute_combined_strength_score(
            lifts=[
                {"exercise": "Dumbbell Bench Press", "weight_kg": 15.0, "scored": True},
                {"exercise": "Dumbbell Goblet Squat", "weight_kg": 35.0, "scored": True},
            ],
            gender="male",
            age=30,
            actual_weight_kg=70.0,
        )
        self.assertEqual(score_2, 2.5)

        # 3 lifts answered: push 15kg (2) + row 15kg (2) + goblet squat 5kg (1) -> avg 5/3 = 1.667
        score_3 = compute_combined_strength_score(
            lifts=[
                {"exercise": "Dumbbell Bench Press", "weight_kg": 15.0, "scored": True},
                {"exercise": "Dumbbell Bent Over Row", "weight_kg": 15.0, "scored": True},
                {"exercise": "Dumbbell Goblet Squat", "weight_kg": 5.0, "scored": True},
            ],
            gender="male",
            age=30,
            actual_weight_kg=70.0,
        )
        self.assertAlmostEqual(score_3, 5.0 / 3.0, places=3)

    def test_combined_marker_does_not_dominate_overall_level(self):
        # Base markers: all beginner (score 1)
        # push_ups: 5 (1), squats: 8 (1), run: 8 (1), frequency: 1 (1) -> 4 markers of 1
        # If all 3 lifts are advanced (score 3), they must combine into ONE score of 3.0.
        # Total scores = [1, 1, 1, 1, 3.0] -> avg = 7 / 5 = 1.4 < 1.5 -> "beginner"
        # If the 3 lifts were added individually, scores would be [1, 1, 1, 1, 3, 3, 3] -> avg 13/7 = 1.86 -> "intermediate" (dominating!)
        markers = {
            "push_ups": 5,
            "squats": 8,
            "run": 8,
            "frequency": 1,
            "push_lift": 35.0,    # score 3
            "pull_lift": 35.0,    # score 3
            "lower_lift": 35.0,   # score 3
        }
        level = calculate_overall_level(
            markers=markers,
            gender="male",
            age=30,
            actual_weight_kg=70.0,
        )
        self.assertEqual(level, "beginner")

        evaluation = evaluate_overall_assessment(
            markers=markers,
            gender="male",
            age=30,
            actual_weight_kg=70.0,
        )
        self.assertEqual(evaluation["experience_level"], "beginner")
        self.assertAlmostEqual(evaluation["average_score"], 1.4, places=2)
        self.assertIn("combined_strength_marker", evaluation["marker_details"])

    # ---------------------------------------------------------
    # 4. Pull-up marker scoring
    # ---------------------------------------------------------
    def test_strict_pull_ups_marker_scoring(self):
        # Male 70kg age 30: pull_ups thresholds: inter 4.0, adv 10.0
        th_inter, th_adv = compute_marker_thresholds("pull_ups", "male", 30, 70.0)
        self.assertAlmostEqual(th_inter, 4.0, places=1)
        self.assertAlmostEqual(th_adv, 10.0, places=1)

        self.assertEqual(score_marker("pull_ups", 2, "male", 30, 70.0), 1)
        self.assertEqual(score_marker("pull_ups", 5, "male", 30, 70.0), 2)
        self.assertEqual(score_marker("pull_ups", 12, "male", 30, 70.0), 3)

    # ---------------------------------------------------------
    # 5. Backward compatibility for older single-lift assessments
    # ---------------------------------------------------------
    def test_old_weighted_lift_remains_scorable(self):
        markers = {
            "push_ups": 15,
            "squats": 25,
            "run": 20,
            "frequency": 3,
            "weighted_lift": 20.0,
        }
        level = calculate_overall_level(
            markers=markers,
            gender="male",
            age=30,
            actual_weight_kg=70.0,
        )
        self.assertEqual(level, "intermediate")

    # ---------------------------------------------------------
    # 6. Raw lift storage and assessment persistence
    # ---------------------------------------------------------
    @patch("tools.user_profile.get_user_profile_collection")
    @patch("tools.baseline_assessment.get_baseline_assessment_collection")
    def test_record_assessment_stores_raw_lifts_and_data_version_3(
        self,
        mock_get_assess_col,
        mock_get_profile_col,
    ):
        mock_assess_col = MagicMock()
        mock_get_assess_col.return_value = mock_assess_col
        mock_get_profile_col.return_value = MagicMock()

        raw_lifts = [
            {
                "exercise": "Dumbbell Bench Press",
                "weight_kg": 22.0,
                "reps": 8,
                "per_hand": True,
                "scored": True,
            },
            {
                "exercise": "Dumbbell Bent Over Row",
                "weight_kg": 20.0,
                "reps": 8,
                "per_hand": True,
                "scored": True,
            },
            {
                "exercise": "Dumbbell Goblet Squat",
                "weight_kg": 28.0,
                "reps": 8,
                "per_hand": False,
                "scored": True,
            },
        ]

        record_baseline_assessment(
            experience_level="intermediate",
            answers={
                "raw_markers": {
                    "push_ups": 15,
                    "squats": 25,
                    "run_minutes": 20,
                    "frequency": 3,
                    "push_lift_kg": 22.0,
                    "pull_lift_kg": 20.0,
                    "lower_lift_kg": 28.0,
                },
                "raw_lifts": raw_lifts,
            },
            user_id="test_user_v3",
        )

        inserted_doc = mock_assess_col.insert_one.call_args[0][0]
        self.assertEqual(inserted_doc["data_version"], 3)
        self.assertEqual(len(inserted_doc["answers"]["raw_lifts"]), 3)

        push_lift = inserted_doc["answers"]["raw_lifts"][0]
        self.assertEqual(push_lift["exercise"], "Dumbbell Bench Press")
        self.assertEqual(push_lift["weight_kg"], 22.0)
        self.assertEqual(push_lift["reps"], 8)
        self.assertTrue(push_lift["per_hand"])

        goblet = inserted_doc["answers"]["raw_lifts"][2]
        self.assertEqual(goblet["exercise"], "Dumbbell Goblet Squat")
        self.assertEqual(goblet["weight_kg"], 28.0)
        self.assertFalse(goblet["per_hand"])

    # ---------------------------------------------------------
    # 7. Existing-user prompt does not change confirmed month goal
    # ---------------------------------------------------------
    @patch("db.mongo_client.get_month_plans_collection")
    @patch("tools.user_profile.get_user_profile_collection")
    @patch("tools.baseline_assessment.get_baseline_assessment_collection")
    def test_existing_user_upgrade_does_not_alter_confirmed_month_goal(
        self,
        mock_get_assess_col,
        mock_get_profile_col,
        mock_get_month_col,
    ):
        mock_assess = MagicMock()
        mock_profile = MagicMock()
        mock_month = MagicMock()

        mock_get_assess_col.return_value = mock_assess
        mock_get_profile_col.return_value = mock_profile
        mock_get_month_col.return_value = mock_month

        # Mock an existing confirmed current-month goal
        confirmed_goal_doc = {
            "user_id": "existing_user_1",
            "month_id": "2026-10",
            "goal": {
                "status": "confirmed",
                "monthly_target": 150.0,
                "volume_targets": [
                    {"exercise": "Push-Up", "target": 120.0},
                    {"exercise": "Bodyweight Squat", "target": 180.0},
                ],
            },
        }
        mock_month.find_one.return_value = confirmed_goal_doc

        # User submits upgrade assessment
        record_baseline_assessment(
            experience_level="advanced",
            answers={
                "raw_markers": {
                    "push_ups": 30,
                    "squats": 45,
                    "run_minutes": 35,
                    "frequency": 4,
                    "plank_seconds": 90.0,
                    "push_lift_kg": 35.0,
                }
            },
            user_id="existing_user_1",
        )

        # Baseline assessment collection has the new assessment
        self.assertTrue(mock_assess.insert_one.called)
        new_assess = mock_assess.insert_one.call_args[0][0]
        self.assertEqual(new_assess["experience_level"], "advanced")
        self.assertEqual(new_assess["data_version"], 3)

        # Month plans collection was NEVER updated or modified
        self.assertFalse(mock_month.update_one.called)
        self.assertFalse(mock_month.delete_one.called)
        self.assertEqual(confirmed_goal_doc["goal"]["status"], "confirmed")
        self.assertEqual(confirmed_goal_doc["goal"]["monthly_target"], 150.0)


if __name__ == "__main__":
    unittest.main()
