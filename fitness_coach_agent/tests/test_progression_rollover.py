"""Tests for multiplier-based goal rollover and idempotent close-out."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.monthly_review import close_out_month
from utils.goal_progression import build_goal_from_multipliers
import scripts.scheduled_plan_generation as scheduler


def _goal(volume_targets, snapshot=None, metric=None, target=None, baseline=None):
    return {
        "description": "Test goal",
        "metric_name": "body_weight" if metric else None,
        "target_value": target,
        "unit": "kg" if metric else None,
        "baseline_value": baseline,
        "volume_targets": volume_targets,
        "progression_multipliers": snapshot,
        "status": "confirmed",
    }


class TestBuildGoalFromMultipliers(unittest.TestCase):

    def test_legacy_goal_applies_current_multiplier_once(self):
        previous = _goal([
            {"exercise": "Push-ups", "unit": "reps", "month_target": 100,
             "balance_area": "upper_body"},
        ])
        new = build_goal_from_multipliers(previous, {"upper_body": 1.1})
        self.assertEqual(new["volume_targets"][0]["month_target"], 110)
        self.assertEqual(new["progression_multipliers"]["upper_body"], 1.1)

    def test_snapshot_prevents_cumulative_double_apply(self):
        previous = _goal(
            [{"exercise": "Push-ups", "unit": "reps", "month_target": 110,
              "balance_area": "upper_body"}],
            snapshot={"upper_body": 1.1, "lower_body": 1.0,
                      "core": 1.0, "cardio": 1.0},
        )
        new = build_goal_from_multipliers(previous, {"upper_body": 1.21})
        # 110 * (1.21 / 1.1) == 121, not 110 * 1.21 == 133.1
        self.assertEqual(new["volume_targets"][0]["month_target"], 121)

    def test_continuous_unit_rounding(self):
        previous = _goal([
            {"exercise": "Run", "unit": "km", "month_target": 10.0,
             "balance_area": "cardio"},
        ])
        new = build_goal_from_multipliers(previous, {"cardio": 1.05})
        self.assertEqual(new["volume_targets"][0]["month_target"], 10.5)

    def test_metric_carries_forward_without_latest_reading(self):
        previous = _goal([], metric=True, target=75.0, baseline=80.0)
        new = build_goal_from_multipliers(previous, {})
        self.assertEqual(new["baseline_value"], 80.0)
        self.assertEqual(new["target_value"], 75.0)

    def test_metric_uses_latest_reading_and_preserves_delta(self):
        previous = _goal([], metric=True, target=75.0, baseline=80.0)
        new = build_goal_from_multipliers(previous, {}, latest_metric_value=77.0)
        self.assertEqual(new["baseline_value"], 77.0)
        self.assertEqual(new["target_value"], 72.0)


class TestCloseOutIdempotency(unittest.TestCase):

    def test_existing_summary_returns_without_adjusting_again(self):
        existing = {"month_id": "2026-09", "progression_adjustments": [{"area": "core"}]}
        mock_col = MagicMock()
        mock_col.find_one.return_value = {
            "user_id": "u1", "month_id": "2026-09", "close_out_summary": existing,
        }
        with (
            patch("agent.monthly_review.get_month_plans_collection", return_value=mock_col),
            patch("agent.monthly_review.get_current_user_id", return_value="u1"),
            patch("agent.monthly_review._adjust_user_baselines") as mock_adjust,
            patch("agent.monthly_review._generate_review") as mock_review,
        ):
            result = close_out_month("2026-09")

        self.assertEqual(result, existing)
        mock_adjust.assert_not_called()
        mock_review.assert_not_called()
        mock_col.update_one.assert_not_called()


class TestSchedulerRollover(unittest.TestCase):

    def test_scheduler_closes_previous_month_then_uses_multipliers(self):
        prev_goal = _goal([
            {"exercise": "Push-ups", "unit": "reps", "month_target": 100,
             "balance_area": "upper_body"},
        ])
        written = {}

        with (
            patch.object(scheduler, "set_current_user_id"),
            patch.object(scheduler, "clear_current_user_id"),
            patch.object(scheduler, "has_confirmed_goal_for_current_month",
                         return_value=False),
            patch.object(scheduler, "has_ever_had_confirmed_goal",
                         return_value=True),
            patch.object(scheduler, "has_any_goal_doc_for_current_month",
                         return_value=False),
            patch.object(scheduler, "get_most_recent_confirmed_goal",
                         return_value=("2026-09", prev_goal)),
            patch.object(scheduler, "close_out_month",
                         return_value={"month_id": "2026-09"}) as close_out,
            patch.object(scheduler, "get_progression_multipliers",
                         return_value={"upper_body": 1.1}),
            patch.object(scheduler, "get_latest_metric", return_value=None),
            patch.object(scheduler, "_write_confirmed_goal_for_current_month",
                         side_effect=lambda g: written.update(g)),
            patch.object(scheduler, "generate_today_plan",
                         MagicMock(invoke=MagicMock(return_value="plan generated"))),
        ):
            result = scheduler.run_for_user("u1")

        close_out.assert_called_once_with("2026-09")
        self.assertEqual(written["volume_targets"][0]["month_target"], 110)
        self.assertIn("plan generated", result)


if __name__ == "__main__":
    unittest.main()
