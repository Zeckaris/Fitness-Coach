"""
Tests for the deterministic progressive-overload / difficulty-adjustment feature.

Covers:
  - get_progression_multipliers: lazy default init, partial field handling
  - calculate_month_target: progression_multiplier parameter
  - _calculate_per_area_adherence: correct per-area scoping, filler exclusion
  - _adjust_user_baselines: increase / hold / decrease (volume) / decrease (adherence)
                            / cap / floor / skip-area-with-no-exercises
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock, call

# Make sure the project root is on sys.path so imports resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.baseline_targets import calculate_month_target
from agent.monthly_review import (
    _calculate_per_area_adherence,
    _adjust_user_baselines,
    PROGRESSION_MULTIPLIER_CAP,
    PROGRESSION_MULTIPLIER_FLOOR,
    MIN_PROGRESSION_DATA_POINTS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_goal(areas: list[str]) -> dict:
    """
    Build a minimal goal dict with one exercise per listed balance area.
    Exercise names are synthetic but unique per area so the join works.
    """
    exercise_map = {
        "upper_body": "Push-ups",
        "lower_body": "Squats",
        "core": "Plank",
        "cardio": "Continuous Run",
    }
    volume_targets = [
        {"exercise": exercise_map[a], "unit": "reps", "month_target": 100, "balance_area": a}
        for a in areas
    ]
    return {"volume_targets": volume_targets, "status": "confirmed"}


def _make_progress(area_pcts: dict[str, float]) -> dict:
    """
    Build a minimal progress dict with one volume_progress entry per area.
    area_pcts maps balance_area -> pct.
    """
    exercise_map = {
        "upper_body": "Push-ups",
        "lower_body": "Squats",
        "core": "Plank",
        "cardio": "Continuous Run",
    }
    volume_progress = [
        {
            "exercise": exercise_map[area],
            "unit": "reps",
            "month_target": 100,
            "completed_so_far": pct,
            "pct": pct,
        }
        for area, pct in area_pcts.items()
    ]
    return {
        "adherence": {"adherence_pct": 75.0},
        "volume_progress": volume_progress,
    }


# ---------------------------------------------------------------------------
# 1. get_progression_multipliers — lazy default / partial field
# ---------------------------------------------------------------------------

class TestGetProgressionMultipliers(unittest.TestCase):
    """Tests live in tools/user_profile but we exercise them here end-to-end."""

    @patch("tools.user_profile.get_current_user_id", return_value="u1")
    @patch("tools.user_profile.get_user_profile_collection")
    def test_missing_field_returns_all_ones(self, mock_col, _mock_uid):
        """Profile without progression_multipliers -> all four areas default to 1.0."""
        mock_col.return_value.find_one.return_value = {
            "user_id": "u1",
            "experience_level": "beginner",
            # no progression_multipliers field
        }
        from tools.user_profile import get_progression_multipliers
        result = get_progression_multipliers()
        self.assertEqual(result, {
            "upper_body": 1.0,
            "lower_body": 1.0,
            "core": 1.0,
            "cardio": 1.0,
        })

    @patch("tools.user_profile.get_current_user_id", return_value="u1")
    @patch("tools.user_profile.get_user_profile_collection")
    def test_partial_field_fills_missing_with_ones(self, mock_col, _mock_uid):
        """Profile with only some areas set -> missing areas fill with 1.0."""
        mock_col.return_value.find_one.return_value = {
            "user_id": "u1",
            "progression_multipliers": {"upper_body": 1.2, "cardio": 0.8},
        }
        from tools.user_profile import get_progression_multipliers
        result = get_progression_multipliers()
        self.assertAlmostEqual(result["upper_body"], 1.2)
        self.assertAlmostEqual(result["cardio"], 0.8)
        self.assertAlmostEqual(result["lower_body"], 1.0)
        self.assertAlmostEqual(result["core"], 1.0)

    @patch("tools.user_profile.get_current_user_id", return_value="u1")
    @patch("tools.user_profile.get_user_profile_collection")
    def test_no_profile_returns_all_ones(self, mock_col, _mock_uid):
        """No profile document at all -> all four areas default to 1.0."""
        mock_col.return_value.find_one.return_value = None
        from tools.user_profile import get_progression_multipliers
        result = get_progression_multipliers()
        self.assertEqual(result, {
            "upper_body": 1.0,
            "lower_body": 1.0,
            "core": 1.0,
            "cardio": 1.0,
        })


# ---------------------------------------------------------------------------
# 2. set_progression_multipliers — only canonical keys written
# ---------------------------------------------------------------------------

class TestSetProgressionMultipliers(unittest.TestCase):

    @patch("tools.user_profile.get_current_user_id", return_value="u1")
    @patch("tools.user_profile.get_user_profile_collection")
    def test_writes_only_canonical_keys(self, mock_col, _mock_uid):
        """Extra keys in the input dict must be silently dropped."""
        mock_update = MagicMock()
        mock_col.return_value.update_one = mock_update

        from tools.user_profile import set_progression_multipliers
        set_progression_multipliers({
            "upper_body": 1.3,
            "lower_body": 0.9,
            "core": 1.0,
            "cardio": 1.1,
            "extra_area": 999.0,  # must be dropped
        })

        self.assertTrue(mock_update.called)
        set_doc = mock_update.call_args[0][1]["$set"]["progression_multipliers"]
        self.assertNotIn("extra_area", set_doc)
        self.assertAlmostEqual(set_doc["upper_body"], 1.3)
        self.assertAlmostEqual(set_doc["cardio"], 1.1)


# ---------------------------------------------------------------------------
# 3. calculate_month_target — progression_multiplier parameter
# ---------------------------------------------------------------------------

class TestCalculateMonthTargetMultiplier(unittest.TestCase):

    def test_multiplier_scales_result(self):
        """A multiplier of 1.1 must produce exactly 1.1× the base target (before rounding)."""
        base = calculate_month_target(
            unit="reps",
            balance_area="upper_body",
            experience_level="beginner",
            baseline_value=10,
            sessions_per_week=4,
            sets_per_session=3,
            weeks_in_month=4.0,
            progression_multiplier=1.0,
        )
        scaled = calculate_month_target(
            unit="reps",
            balance_area="upper_body",
            experience_level="beginner",
            baseline_value=10,
            sessions_per_week=4,
            sets_per_session=3,
            weeks_in_month=4.0,
            progression_multiplier=1.1,
        )
        # scaled target = round(base_raw * 1.1); just verify it's strictly greater
        self.assertGreater(scaled["month_target"], base["month_target"])
        self.assertAlmostEqual(scaled["progression_multiplier"], 1.1)

    def test_identity_multiplier_unchanged(self):
        """Default multiplier of 1.0 must not change the result."""
        without = calculate_month_target(
            unit="reps", balance_area="lower_body", experience_level="intermediate",
            baseline_value=20, sessions_per_week=3, sets_per_session=3, weeks_in_month=4.35,
        )
        with_one = calculate_month_target(
            unit="reps", balance_area="lower_body", experience_level="intermediate",
            baseline_value=20, sessions_per_week=3, sets_per_session=3, weeks_in_month=4.35,
            progression_multiplier=1.0,
        )
        self.assertEqual(without["month_target"], with_one["month_target"])

    def test_floor_multiplier_reduces_target(self):
        """A multiplier below 1.0 must reduce the target."""
        base = calculate_month_target(
            unit="reps", balance_area="core", experience_level="advanced",
            baseline_value=15, sessions_per_week=4, sets_per_session=3, weeks_in_month=4.0,
            progression_multiplier=1.0,
        )
        reduced = calculate_month_target(
            unit="reps", balance_area="core", experience_level="advanced",
            baseline_value=15, sessions_per_week=4, sets_per_session=3, weeks_in_month=4.0,
            progression_multiplier=0.9,
        )
        self.assertLess(reduced["month_target"], base["month_target"])

    def test_continuous_unit_multiplier(self):
        """Multiplier works correctly for distance-unit (km) exercises (1 dp rounding)."""
        base = calculate_month_target(
            unit="km", balance_area="cardio", experience_level="beginner",
            baseline_value=5.0, sessions_per_week=3, sets_per_session=1, weeks_in_month=4.0,
            progression_multiplier=1.0,
        )
        scaled = calculate_month_target(
            unit="km", balance_area="cardio", experience_level="beginner",
            baseline_value=5.0, sessions_per_week=3, sets_per_session=1, weeks_in_month=4.0,
            progression_multiplier=1.1,
        )
        self.assertGreater(scaled["month_target"], base["month_target"])
        # km targets are rounded to 1 dp
        self.assertEqual(scaled["month_target"], round(scaled["month_target"], 1))


# ---------------------------------------------------------------------------
# 4. _calculate_per_area_adherence — per-area scoping and filler exclusion
# ---------------------------------------------------------------------------

def _make_plan_doc(date: str, status: str, exercises: list[dict]) -> dict:
    return {"date": date, "status": status, "exercises": exercises}


class TestCalculatePerAreaAdherence(unittest.TestCase):
    """
    We patch get_plans_collection and get_current_user_id to avoid real DB calls.
    """

    def _run(self, goal: dict, plan_docs: list[dict]) -> dict:
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(return_value=iter(plan_docs))
        with (
            patch("agent.monthly_review.get_plans_collection") as mock_plans_col,
            patch("agent.monthly_review.get_current_user_id", return_value="u1"),
        ):
            mock_plans_col.return_value.find.return_value = mock_cursor
            return _calculate_per_area_adherence(goal)

    def test_no_volume_targets_returns_empty(self):
        result = self._run({"volume_targets": []}, [])
        self.assertEqual(result, {})

    def test_uses_closed_month_bounds_not_rolling_window(self):
        """The query must cover the whole closed month, not the last 7 days."""
        goal = _make_goal(["upper_body"])
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(return_value=iter([]))
        with (
            patch("agent.monthly_review.get_plans_collection") as mock_plans_col,
            patch("agent.monthly_review.get_current_user_id", return_value="u1"),
        ):
            mock_plans_col.return_value.find.return_value = mock_cursor
            _calculate_per_area_adherence(goal, "2026-09")

        query = mock_plans_col.return_value.find.call_args[0][0]
        self.assertEqual(
            query["date"],
            {"$gte": "2026-09-01", "$lte": "2026-09-30"},
        )

    def test_filler_exercises_excluded(self):
        """Exercises not in volume_targets must be excluded from the count."""
        goal = _make_goal(["upper_body"])
        plans = [
            _make_plan_doc("2026-09-10", "completed", [
                {"name": "Push-ups", "completed_quantity": 10, "target_quantity": 10},
                {"name": "BurpeeFiller", "completed_quantity": 5, "target_quantity": 5},
            ])
        ]
        result = self._run(goal, plans)
        # Only Push-ups is counted; BurpeeFiller is filler and excluded
        self.assertEqual(result["upper_body"]["planned_count"], 1)
        self.assertEqual(result["upper_body"]["completed_count"], 1)
        self.assertAlmostEqual(result["upper_body"]["adherence_pct"], 100.0)

    def test_per_area_split(self):
        """upper_body and lower_body counts must be independent."""
        goal = _make_goal(["upper_body", "lower_body"])
        plans = [
            _make_plan_doc("2026-09-10", "planned", [
                # upper body: completed
                {"name": "Push-ups", "completed_quantity": 10, "target_quantity": 10},
                # lower body: NOT completed
                {"name": "Squats", "completed_quantity": 3, "target_quantity": 10},
            ]),
        ]
        result = self._run(goal, plans)
        self.assertAlmostEqual(result["upper_body"]["adherence_pct"], 100.0)
        self.assertAlmostEqual(result["lower_body"]["adherence_pct"], 0.0)

    def test_zero_planned_in_month_gives_none_pct(self):
        """If no goal-tracked exercises appear in the month for an area, pct is None."""
        goal = _make_goal(["upper_body"])
        # Plans exist but contain only filler — no Push-ups in the month
        plans = [
            _make_plan_doc("2026-09-10", "completed", [
                {"name": "SomeFiller", "completed_quantity": 5, "target_quantity": 5},
            ])
        ]
        result = self._run(goal, plans)
        self.assertIsNone(result["upper_body"]["adherence_pct"])
        self.assertEqual(result["upper_body"]["planned_count"], 0)

    def test_completed_plan_marks_all_exercises_complete(self):
        """When plan status='completed', all exercises in it count as completed."""
        goal = _make_goal(["core"])
        plans = [
            _make_plan_doc("2026-09-10", "completed", [
                # completed_quantity is 0 but plan is completed — should still count
                {"name": "Plank", "completed_quantity": 0, "target_quantity": 60},
            ])
        ]
        result = self._run(goal, plans)
        self.assertEqual(result["core"]["planned_count"], 1)
        self.assertEqual(result["core"]["completed_count"], 1)


# ---------------------------------------------------------------------------
# 5. _adjust_user_baselines — all branches + cap/floor + skip
# ---------------------------------------------------------------------------

class TestAdjustUserBaselines(unittest.TestCase):
    """
    Patches:
      - get_progression_multipliers -> controllable starting multipliers
      - set_progression_multipliers -> capture what gets written
      - _calculate_per_area_adherence -> controllable per-area adherence
    """

    def _run(
        self,
        goal_areas: list[str],
        area_volume_pcts: dict[str, float],
        area_adherence_pcts: dict[str, float | None],
        starting_multipliers: dict | None = None,
    ) -> tuple[list, dict]:
        """
        Returns (adjustments_list, written_multipliers).
        """
        goal = _make_goal(goal_areas)
        progress = _make_progress(area_volume_pcts)

        if starting_multipliers is None:
            starting_multipliers = {a: 1.0 for a in ["upper_body", "lower_body", "core", "cardio"]}

        adherence_return = {
            area: {"planned_count": 10, "completed_count": 7, "adherence_pct": pct}
            for area, pct in area_adherence_pcts.items()
        }

        written = {}

        def _capture_write(m, user_id=None):
            written.update(m)

        with (
            patch("agent.monthly_review.get_progression_multipliers", return_value=starting_multipliers),
            patch("agent.monthly_review.set_progression_multipliers", side_effect=_capture_write),
            patch("agent.monthly_review._calculate_per_area_adherence", return_value=adherence_return),
        ):
            adjustments = _adjust_user_baselines(progress, goal)

        return adjustments, written

    # --- Increase branch ---

    def test_increase_when_both_thresholds_met(self):
        """volume >= 85 AND adherence >= 70 -> multiply by 1.1."""
        adjustments, written = self._run(
            goal_areas=["upper_body"],
            area_volume_pcts={"upper_body": 90.0},
            area_adherence_pcts={"upper_body": 80.0},
            starting_multipliers={"upper_body": 1.0, "lower_body": 1.0, "core": 1.0, "cardio": 1.0},
        )
        self.assertEqual(len(adjustments), 1)
        adj = adjustments[0]
        self.assertEqual(adj["action"], "increase")
        self.assertAlmostEqual(adj["new_multiplier"], 1.1, places=5)
        self.assertAlmostEqual(written["upper_body"], 1.1, places=5)

    def test_increase_exactly_at_thresholds(self):
        """Boundary values: volume == 85 AND adherence == 70 -> increase."""
        adjustments, written = self._run(
            goal_areas=["lower_body"],
            area_volume_pcts={"lower_body": 85.0},
            area_adherence_pcts={"lower_body": 70.0},
        )
        self.assertEqual(adjustments[0]["action"], "increase")

    # --- Hold branch ---

    def test_hold_in_middle_band(self):
        """volume in [50,85) AND adherence in [40,70) -> hold."""
        adjustments, written = self._run(
            goal_areas=["core"],
            area_volume_pcts={"core": 65.0},
            area_adherence_pcts={"core": 55.0},
            starting_multipliers={"upper_body": 1.0, "lower_body": 1.0, "core": 1.2, "cardio": 1.0},
        )
        adj = adjustments[0]
        self.assertEqual(adj["action"], "hold")
        self.assertAlmostEqual(adj["new_multiplier"], adj["old_multiplier"], places=6)
        # Verify the unchanged multiplier is persisted correctly
        self.assertAlmostEqual(written["core"], 1.2, places=5)

    # --- Decrease branch: volume trigger ---

    def test_decrease_on_low_volume(self):
        """volume < 50 (regardless of adherence) -> multiply by 0.9."""
        adjustments, _ = self._run(
            goal_areas=["cardio"],
            area_volume_pcts={"cardio": 30.0},
            area_adherence_pcts={"cardio": 75.0},  # adherence is fine, volume is low
        )
        adj = adjustments[0]
        self.assertEqual(adj["action"], "decrease")
        self.assertAlmostEqual(adj["new_multiplier"], 0.9, places=5)
        self.assertIn("volume_pct", adj["reason"])

    # --- Decrease branch: adherence trigger ---

    def test_decrease_on_low_adherence(self):
        """adherence < 40 (regardless of volume) -> multiply by 0.9."""
        adjustments, _ = self._run(
            goal_areas=["upper_body"],
            area_volume_pcts={"upper_body": 60.0},  # volume is fine
            area_adherence_pcts={"upper_body": 20.0},
        )
        adj = adjustments[0]
        self.assertEqual(adj["action"], "decrease")
        self.assertAlmostEqual(adj["new_multiplier"], 0.9, places=5)
        self.assertIn("adherence_pct", adj["reason"])

    # --- Hold: missing adherence is insufficient data, not failure ---

    def test_hold_when_adherence_is_none(self):
        """None adherence (no logged goal sessions) must hold, never decrease."""
        adjustments, written = self._run(
            goal_areas=["lower_body"],
            area_volume_pcts={"lower_body": 60.0},
            area_adherence_pcts={"lower_body": None},
            starting_multipliers={
                "upper_body": 1.0, "lower_body": 1.2, "core": 1.0, "cardio": 1.0,
            },
        )
        self.assertEqual(adjustments[0]["action"], "hold")
        self.assertAlmostEqual(written["lower_body"], 1.2, places=5)
        self.assertIn("insufficient data", adjustments[0]["reason"])

    def test_hold_when_planned_count_below_minimum(self):
        """Too few goal-tracked occurrences must hold even with high volume."""
        goal = _make_goal(["upper_body"])
        progress = _make_progress({"upper_body": 95.0})
        written = {}

        low_data = {
            "upper_body": {
                "planned_count": MIN_PROGRESSION_DATA_POINTS - 1,
                "completed_count": MIN_PROGRESSION_DATA_POINTS - 1,
                "adherence_pct": 100.0,
            }
        }
        with (
            patch("agent.monthly_review.get_progression_multipliers",
                  return_value={"upper_body": 1.0, "lower_body": 1.0,
                                "core": 1.0, "cardio": 1.0}),
            patch("agent.monthly_review.set_progression_multipliers",
                  side_effect=lambda m, user_id=None: written.update(m)),
            patch("agent.monthly_review._calculate_per_area_adherence",
                  return_value=low_data),
        ):
            adjustments = _adjust_user_baselines(progress, goal, "2026-09")

        self.assertEqual(adjustments[0]["action"], "hold")
        self.assertAlmostEqual(written["upper_body"], 1.0, places=5)
        self.assertIn("insufficient data", adjustments[0]["reason"])

    # --- Cap ---

    def test_cap_prevents_exceeding_maximum(self):
        """Multiplier at 1.95: after x1.1 it would be 2.145 -> must be capped at 2.0."""
        starting = {"upper_body": 1.95, "lower_body": 1.0, "core": 1.0, "cardio": 1.0}
        adjustments, written = self._run(
            goal_areas=["upper_body"],
            area_volume_pcts={"upper_body": 95.0},
            area_adherence_pcts={"upper_body": 85.0},
            starting_multipliers=starting,
        )
        adj = adjustments[0]
        self.assertEqual(adj["action"], "increase")
        self.assertAlmostEqual(adj["new_multiplier"], PROGRESSION_MULTIPLIER_CAP, places=5)
        self.assertAlmostEqual(written["upper_body"], PROGRESSION_MULTIPLIER_CAP, places=5)
        self.assertIn("capped", adj["reason"])

    def test_already_at_cap_stays_at_cap(self):
        """Multiplier already at CAP: increase action still stays at CAP."""
        starting = {"upper_body": PROGRESSION_MULTIPLIER_CAP,
                    "lower_body": 1.0, "core": 1.0, "cardio": 1.0}
        adjustments, written = self._run(
            goal_areas=["upper_body"],
            area_volume_pcts={"upper_body": 95.0},
            area_adherence_pcts={"upper_body": 80.0},
            starting_multipliers=starting,
        )
        self.assertAlmostEqual(written["upper_body"], PROGRESSION_MULTIPLIER_CAP, places=5)

    # --- Floor ---

    def test_floor_prevents_going_below_minimum(self):
        """Multiplier at 0.54: after x0.9 it would be 0.486 -> must be floored at 0.5."""
        starting = {"upper_body": 1.0, "lower_body": 1.0, "core": 0.54, "cardio": 1.0}
        adjustments, written = self._run(
            goal_areas=["core"],
            area_volume_pcts={"core": 20.0},   # < 50 -> decrease
            area_adherence_pcts={"core": 50.0},
            starting_multipliers=starting,
        )
        adj = adjustments[0]
        self.assertEqual(adj["action"], "decrease")
        self.assertAlmostEqual(adj["new_multiplier"], PROGRESSION_MULTIPLIER_FLOOR, places=5)
        self.assertAlmostEqual(written["core"], PROGRESSION_MULTIPLIER_FLOOR, places=5)
        self.assertIn("floored", adj["reason"])

    def test_already_at_floor_stays_at_floor(self):
        """Multiplier already at FLOOR: decrease stays at FLOOR."""
        starting = {"upper_body": 1.0, "lower_body": 1.0,
                    "core": PROGRESSION_MULTIPLIER_FLOOR, "cardio": 1.0}
        adjustments, written = self._run(
            goal_areas=["core"],
            area_volume_pcts={"core": 10.0},
            area_adherence_pcts={"core": 10.0},
            starting_multipliers=starting,
        )
        self.assertAlmostEqual(written["core"], PROGRESSION_MULTIPLIER_FLOOR, places=5)

    # --- Skip area with no goal-tracked exercises ---

    def test_skip_area_not_in_goal(self):
        """
        An area that has no exercises in volume_targets must not appear in the
        adjustments list and its multiplier must remain unchanged.
        """
        # Goal only covers upper_body and lower_body; core and cardio have no exercises.
        starting = {
            "upper_body": 1.0, "lower_body": 1.0,
            "core": 1.3,  # should be untouched
            "cardio": 0.8,  # should be untouched
        }
        adjustments, written = self._run(
            goal_areas=["upper_body", "lower_body"],
            area_volume_pcts={"upper_body": 90.0, "lower_body": 70.0},
            area_adherence_pcts={"upper_body": 80.0, "lower_body": 60.0},
            starting_multipliers=starting,
        )
        areas_adjusted = {a["area"] for a in adjustments}
        self.assertNotIn("core", areas_adjusted)
        self.assertNotIn("cardio", areas_adjusted)

        # Untouched areas must be written back at their original values
        self.assertAlmostEqual(written["core"], 1.3, places=5)
        self.assertAlmostEqual(written["cardio"], 0.8, places=5)

    def test_no_volume_targets_returns_empty_list(self):
        """Goal with no volume_targets -> empty adjustments list, no DB write."""
        written = {}

        def _capture(m, user_id=None):
            written.update(m)

        with (
            patch("agent.monthly_review.get_progression_multipliers",
                  return_value={a: 1.0 for a in ["upper_body", "lower_body", "core", "cardio"]}),
            patch("agent.monthly_review.set_progression_multipliers", side_effect=_capture),
            patch("agent.monthly_review._calculate_per_area_adherence", return_value={}),
        ):
            result = _adjust_user_baselines({"volume_progress": []}, {"volume_targets": []})

        self.assertEqual(result, [])

    # --- Multiple areas in one call ---

    def test_multiple_areas_independent_decisions(self):
        """
        upper_body: volume=90%, adherence=80% -> increase
        lower_body: volume=40%, adherence=50% -> decrease (volume<50)
        core:       volume=60%, adherence=55% -> hold
        """
        adjustments, written = self._run(
            goal_areas=["upper_body", "lower_body", "core"],
            area_volume_pcts={"upper_body": 90.0, "lower_body": 40.0, "core": 60.0},
            area_adherence_pcts={"upper_body": 80.0, "lower_body": 50.0, "core": 55.0},
        )
        by_area = {a["area"]: a for a in adjustments}
        self.assertEqual(by_area["upper_body"]["action"], "increase")
        self.assertEqual(by_area["lower_body"]["action"], "decrease")
        self.assertEqual(by_area["core"]["action"], "hold")

    # --- Return shape ---

    def test_adjustment_record_has_required_keys(self):
        """Every adjustment record must carry the documented keys."""
        adjustments, _ = self._run(
            goal_areas=["upper_body"],
            area_volume_pcts={"upper_body": 90.0},
            area_adherence_pcts={"upper_body": 80.0},
        )
        required_keys = {
            "area", "old_multiplier", "new_multiplier",
            "action", "reason", "area_volume_pct", "area_adherence_pct",
        }
        self.assertTrue(required_keys.issubset(adjustments[0].keys()))


if __name__ == "__main__":
    unittest.main()
