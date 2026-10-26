"""
Tests for mid-month goal scaling and related invariants.

Coverage:
  - compute_goal_scaling: day-based scale factor, edge cases
  - is_last_week_of_month: cutoff detection
  - stage_month_goal: scaling applied, full_month_target stored, last-week redirect
  - confirm_month_goal: Foundation-first themes for 2- and 3-week remaining cases
  - build_goal_from_multipliers: rollover uses full_month_target, not scaled target
  - _calculate_volume_progress: compares against scaled month_target
  - last-week cutoff: close_out_month returns clean result with no confirmed goal
  - _refresh_week_themes_impl: preserves foundation_first_on_confirm path
"""

import os
import sys
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("GEMINI_API_KEY", "dummy_key_for_testing")

# Stub out langfuse so agent.monthly_review can be imported in environments
# where the langfuse package is not installed (e.g. CI without optional deps).
for _mod in ("langfuse", "langfuse.langchain"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from utils.calendar_weeks import (
    compute_goal_scaling,
    compute_month_weeks,
    is_last_week_of_month,
)
from utils.goal_progression import build_goal_from_multipliers
from auth.context import set_current_user_id


@pytest.fixture(autouse=True)
def _set_user():
    set_current_user_id("test_mid_month_user")


def _make_goal(month_target, full_month_target=None, balance_area="upper_body",
               unit="reps", snapshot=None):
    vt = {
        "exercise": "Push-Up",
        "unit": unit,
        "month_target": month_target,
        "balance_area": balance_area,
    }
    if full_month_target is not None:
        vt["full_month_target"] = full_month_target
    return {
        "description": "Test",
        "volume_targets": [vt],
        "progression_multipliers": snapshot or {},
        "status": "confirmed",
    }


# ---------------------------------------------------------------------------
# compute_goal_scaling
# ---------------------------------------------------------------------------

class TestComputeGoalScaling:

    def test_first_day_of_month_scale_is_1(self):
        d = date(2026, 10, 1)
        r = compute_goal_scaling(d, "2026-10")
        assert r["scale"] == pytest.approx(1.0)
        assert r["days_remaining"] == 31
        assert r["days_in_month"] == 31

    def test_last_day_of_month_scale_is_min(self):
        d = date(2026, 10, 31)
        r = compute_goal_scaling(d, "2026-10")
        assert r["days_remaining"] == 1
        assert r["scale"] == pytest.approx(1 / 31)

    def test_mid_month_scale_correct(self):
        # Oct has 31 days; Oct 17 through Oct 31 = 15 days remaining
        d = date(2026, 10, 17)
        r = compute_goal_scaling(d, "2026-10")
        assert r["days_remaining"] == 15
        assert r["scale"] == pytest.approx(15 / 31)

    def test_february_leap_year(self):
        d = date(2024, 2, 15)   # 2024 is a leap year: 29 days
        r = compute_goal_scaling(d, "2024-02")
        assert r["days_in_month"] == 29
        assert r["days_remaining"] == 15
        assert r["scale"] == pytest.approx(15 / 29)

    def test_goal_start_date_stored(self):
        d = date(2026, 10, 20)
        r = compute_goal_scaling(d, "2026-10")
        assert r["goal_start_date"] == "2026-10-20"

    def test_second_to_last_day_scale(self):
        # Oct 30: 2 days remaining
        d = date(2026, 10, 30)
        r = compute_goal_scaling(d, "2026-10")
        assert r["days_remaining"] == 2
        assert r["scale"] == pytest.approx(2 / 31)


# ---------------------------------------------------------------------------
# is_last_week_of_month
# ---------------------------------------------------------------------------

class TestIsLastWeekOfMonth:

    def test_first_day_of_month_not_last_week(self):
        assert not is_last_week_of_month(date(2026, 10, 1), "2026-10")

    def test_last_day_of_month_is_last_week(self):
        # Oct 31 2026 is Saturday -> definitely in the last week
        assert is_last_week_of_month(date(2026, 10, 31), "2026-10")

    def test_date_clearly_in_last_week(self):
        weeks = compute_month_weeks("2026-10")
        last = weeks[-1]
        last_start = date.fromisoformat(last["start_date"])
        last_end = date.fromisoformat(last["end_date"])
        assert is_last_week_of_month(last_start, "2026-10")
        assert is_last_week_of_month(last_end, "2026-10")

    def test_one_day_before_last_week_is_not_last_week(self):
        import datetime as _dt
        weeks = compute_month_weeks("2026-10")
        last = weeks[-1]
        day_before = date.fromisoformat(last["start_date"]) - _dt.timedelta(days=1)
        assert not is_last_week_of_month(day_before, "2026-10")


# ---------------------------------------------------------------------------
# stage_month_goal: scaling and storage
# Call the unwrapped function directly to bypass Pydantic input validation
# (VolumeTarget checks against the workout library) and pass VolumeTarget
# objects directly instead.
# ---------------------------------------------------------------------------

class TestStageMonthGoalScaling:

    # Real exercise names that exist in the workout library
    _VT_DATA = [
        ("Push-Up",                    "reps",    "upper_body", 1000),
        ("Mountain Climber",           "reps",    "cardio",      800),
        ("Bodyweight Incline Side Plank", "seconds", "core",    600),
        ("Reverse Crunch",             "reps",    "lower_body",  400),
    ]

    def _make_vts(self):
        from tools.month_plans import VolumeTarget
        return [
            VolumeTarget(exercise=ex, unit=unit, month_target=mt, balance_area=ba)
            for ex, unit, ba, mt in self._VT_DATA
        ]

    def _call_stage(self, today_date, volume_targets, month_id=None):
        """Call the raw stage_month_goal function bypassing the @tool decorator."""
        import tools.month_plans as mp
        col = MagicMock()
        col.find_one.return_value = None
        col.update_one.return_value = MagicMock()
        kwargs = dict(
            description="Test goal",
            volume_targets=volume_targets,
        )
        if month_id:
            kwargs["month_id"] = month_id
        with (
            patch.object(mp, "get_month_plans_collection", return_value=col),
            patch.object(mp, "get_current_user_id", return_value="test_mid_month_user"),
            patch.object(mp, "today_local", return_value=today_date),
            patch.object(mp, "get_progression_multipliers", return_value={}),
        ):
            # Call the raw function, not the @tool/.invoke() path
            result = mp.stage_month_goal.func(**kwargs)
        return col, result

    def test_full_month_start_no_scaling(self):
        vts = self._make_vts()
        col, result = self._call_stage(date(2026, 10, 1), vts)
        assert "Staged" in result
        assert "Mid-month" not in result
        goal = col.update_one.call_args[0][1]["$set"]["goal"]
        vt_map = {v["exercise"]: v for v in goal["volume_targets"]}
        assert vt_map["Push-Up"]["month_target"] == 1000
        assert vt_map["Push-Up"]["full_month_target"] == 1000.0

    def test_mid_month_start_scales_targets(self):
        vts = self._make_vts()
        col, result = self._call_stage(date(2026, 10, 17), vts)
        assert "Mid-month" in result
        assert "Staged" in result
        goal = col.update_one.call_args[0][1]["$set"]["goal"]
        vt_map = {v["exercise"]: v for v in goal["volume_targets"]}
        scale = 15 / 31
        assert vt_map["Push-Up"]["month_target"] == round(1000 * scale)
        assert vt_map["Push-Up"]["full_month_target"] == 1000.0
        assert vt_map["Mountain Climber"]["month_target"] == round(800 * scale)
        assert vt_map["Reverse Crunch"]["month_target"] == round(400 * scale)

    def test_goal_start_date_stored_in_goal_and_each_vt(self):
        vts = self._make_vts()
        col, _ = self._call_stage(date(2026, 10, 17), vts)
        goal = col.update_one.call_args[0][1]["$set"]["goal"]
        assert goal["goal_start_date"] == "2026-10-17"
        for vt in goal["volume_targets"]:
            assert vt["goal_start_date"] == "2026-10-17"

    def test_last_week_cutoff_redirects_to_next_month(self):
        vts = self._make_vts()
        weeks = compute_month_weeks("2026-10")
        last_start = date.fromisoformat(weeks[-1]["start_date"])
        col, result = self._call_stage(last_start, vts)
        assert "2026-11" in result
        filter_doc = col.update_one.call_args[0][0]
        assert filter_doc["month_id"] == "2026-11"

    def test_already_confirmed_goal_blocked(self):
        import tools.month_plans as mp
        col = MagicMock()
        col.find_one.return_value = {"goal": {"status": "confirmed"}}
        vts = self._make_vts()
        with (
            patch.object(mp, "get_month_plans_collection", return_value=col),
            patch.object(mp, "get_current_user_id", return_value="test_mid_month_user"),
            patch.object(mp, "today_local", return_value=date(2026, 10, 1)),
            patch.object(mp, "get_progression_multipliers", return_value={}),
        ):
            result = mp.stage_month_goal.func(
                description="Test", month_id="2026-10", volume_targets=vts
            )
        assert "already confirmed" in result
        col.update_one.assert_not_called()


# ---------------------------------------------------------------------------
# confirm_month_goal: Foundation-first themes
# ---------------------------------------------------------------------------

class TestConfirmMonthGoalThemes:

    def _invoke_confirm(self, month_id, goal_start_date_str):
        import tools.month_plans as mp
        col = MagicMock()
        pending_goal = {
            "status": "pending",
            "goal_start_date": goal_start_date_str,
            "description": "Test",
        }
        col.find_one.return_value = {"goal": pending_goal, "month_id": month_id}
        col.update_one.return_value = MagicMock()
        with (
            patch.object(mp, "get_month_plans_collection", return_value=col),
            patch.object(mp, "get_current_user_id", return_value="test_mid_month_user"),
        ):
            result = mp.confirm_month_goal.func(month_id=month_id)
        return col, result

    def _theme_path(self, col):
        return col.update_one.call_args[0][1]["$set"]["week_plan_path"]

    def test_full_month_foundation_first(self):
        col, _ = self._invoke_confirm("2026-10", "2026-10-01")
        themes = [p["theme"] for p in self._theme_path(col)]
        assert themes[0] == "Foundation"
        assert len(themes) == 5   # 2026-10 has 5 weeks

    def test_two_weeks_remaining_gives_foundation_volume(self):
        weeks = compute_month_weeks("2026-10")
        # Starting in week 4 -> weeks 4 and 5 are active (2 weeks)
        week4_start = weeks[3]["start_date"]
        col, _ = self._invoke_confirm("2026-10", week4_start)
        themes = [p["theme"] for p in self._theme_path(col)]
        assert themes == ["Foundation", "Volume"], f"Got: {themes}"

    def test_three_weeks_remaining_gives_foundation_volume_intensity(self):
        weeks = compute_month_weeks("2026-10")
        # Starting in week 3 -> weeks 3, 4, 5 are active (3 weeks)
        week3_start = weeks[2]["start_date"]
        col, _ = self._invoke_confirm("2026-10", week3_start)
        themes = [p["theme"] for p in self._theme_path(col)]
        assert themes == ["Foundation", "Volume", "Intensity"], f"Got: {themes}"

    def test_deload_does_not_appear_for_two_weeks_remaining(self):
        weeks = compute_month_weeks("2026-10")
        week4_start = weeks[3]["start_date"]
        col, _ = self._invoke_confirm("2026-10", week4_start)
        themes = [p["theme"] for p in self._theme_path(col)]
        assert "Deload" not in themes

    def test_theme_week_numbers_match_calendar_weeks(self):
        weeks = compute_month_weeks("2026-10")
        week4_start = weeks[3]["start_date"]
        col, _ = self._invoke_confirm("2026-10", week4_start)
        nums = [p["week_number"] for p in self._theme_path(col)]
        assert nums == [4, 5], f"Got: {nums}"

    def test_theme_path_source_is_foundation_first_on_confirm(self):
        col, _ = self._invoke_confirm("2026-10", "2026-10-01")
        source = col.update_one.call_args[0][1]["$set"]["theme_path_source"]
        assert source == "foundation_first_on_confirm"


# ---------------------------------------------------------------------------
# build_goal_from_multipliers: rollover uses full_month_target
# ---------------------------------------------------------------------------

class TestRolloverUsesFullMonthTarget:

    def test_scaled_goal_rolls_over_from_full_target(self):
        """A goal scaled to 50% for a mid-month start must roll over from
        the full target (1000), not the scaled one (500)."""
        prev_goal = {
            "description": "Test",
            "volume_targets": [{
                "exercise": "Push-Up",
                "unit": "reps",
                "month_target": 500,           # scaled for mid-month
                "full_month_target": 1000.0,   # original full-month amount
                "balance_area": "upper_body",
                "goal_start_date": "2026-10-17",
            }],
            "progression_multipliers": {"upper_body": 1.0},
            "status": "confirmed",
        }
        new_goal = build_goal_from_multipliers(prev_goal, {"upper_body": 1.1})
        new_vt = new_goal["volume_targets"][0]
        # 1000 * (1.1 / 1.0) = 1100, not 500 * 1.1 = 550
        assert new_vt["month_target"] == 1100, (
            f"Expected 1100 (full_month_target used), got {new_vt['month_target']}"
        )
        # Mid-month fields must be stripped from the new goal
        assert "full_month_target" not in new_vt
        assert "goal_start_date" not in new_vt

    def test_legacy_goal_without_full_month_target_still_works(self):
        """Goals created before this feature have no full_month_target — they
        fall back to month_target (same behaviour as before)."""
        prev_goal = _make_goal(month_target=1000, snapshot={"upper_body": 1.0})
        new_goal = build_goal_from_multipliers(prev_goal, {"upper_body": 1.1})
        assert new_goal["volume_targets"][0]["month_target"] == 1100

    def test_full_month_goal_rollover_unchanged(self):
        """full_month_target == month_target for full-month goals; result is identical either way."""
        prev_goal = _make_goal(month_target=1000, full_month_target=1000.0,
                               snapshot={"upper_body": 1.0})
        new_goal = build_goal_from_multipliers(prev_goal, {"upper_body": 1.0})
        assert new_goal["volume_targets"][0]["month_target"] == 1000

    def test_rollover_strips_mid_month_fields(self):
        """Mid-month fields must not bleed into future months."""
        prev_goal = {
            "description": "Test",
            "volume_targets": [{
                "exercise": "Push-Up",
                "unit": "reps",
                "month_target": 500,
                "full_month_target": 1000.0,
                "goal_start_date": "2026-10-17",
                "balance_area": "upper_body",
            }],
            "progression_multipliers": {"upper_body": 1.0},
            "status": "confirmed",
        }
        new_goal = build_goal_from_multipliers(prev_goal, {"upper_body": 1.0})
        new_vt = new_goal["volume_targets"][0]
        assert "full_month_target" not in new_vt
        assert "goal_start_date" not in new_vt


# ---------------------------------------------------------------------------
# _calculate_volume_progress uses scaled month_target (not full_month_target)
# ---------------------------------------------------------------------------

class TestProgressUsesScaledTarget:

    def test_progress_pct_against_scaled_target(self):
        """A user who completed 400 of a scaled 500-rep target should show 80%,
        not 40% (which would result from measuring against the 1000 full target)."""
        from tools.progress import _calculate_volume_progress

        goal = {
            "volume_targets": [{
                "exercise": "Push-Up",
                "unit": "reps",
                "month_target": 500,           # scaled — this is what we measure against
                "full_month_target": 1000.0,
                "balance_area": "upper_body",
            }]
        }
        mock_plans = MagicMock()
        mock_plans.find.return_value = [
            {"exercises": [{"name": "Push-Up", "completed_quantity": 400}]}
        ]
        with (
            patch("tools.progress.get_plans_collection", return_value=mock_plans),
            patch("tools.progress.get_current_user_id", return_value="test_mid_month_user"),
        ):
            results = _calculate_volume_progress(goal, "2026-10")

        assert len(results) == 1
        r = results[0]
        assert r["month_target"] == 500          # reports against scaled target
        assert r["completed_so_far"] == 400
        assert r["pct"] == pytest.approx(80.0)   # 400/500 = 80%, not 400/1000 = 40%


# ---------------------------------------------------------------------------
# Last-week cutoff: close_out_month clean behaviour
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="Requires langfuse / to be run in manual test suite")
class TestLastWeekCutoffCloseOut:

    def test_close_out_month_with_no_goal_doc(self):
        """A month with no document at all (last-week cutoff case) must return
        a clean status dict without raising or writing anything."""
        from agent.monthly_review import close_out_month
        mock_col = MagicMock()
        mock_col.find_one.return_value = None
        with (
            patch("agent.monthly_review.get_month_plans_collection", return_value=mock_col),
            patch("agent.monthly_review.get_current_user_id", return_value="test_mid_month_user"),
        ):
            result = close_out_month("2026-10")
        assert isinstance(result, dict)
        assert result.get("status") == "no_doc"
        mock_col.update_one.assert_not_called()

    def test_close_out_month_with_no_confirmed_goal(self):
        """A month with a pending (unconfirmed) goal must produce a clean summary
        with no progression adjustments applied."""
        from agent.monthly_review import close_out_month
        mock_col = MagicMock()
        mock_col.find_one.return_value = {
            "user_id": "test_mid_month_user",
            "month_id": "2026-10",
            "goal": {"status": "pending", "description": "Test"},
        }
        with (
            patch("agent.monthly_review.get_month_plans_collection", return_value=mock_col),
            patch("agent.monthly_review.get_current_user_id", return_value="test_mid_month_user"),
            patch("agent.monthly_review._adjust_user_baselines") as mock_adj,
            patch("agent.monthly_review._generate_review",
                  return_value={"narrative": "No confirmed goal.", "coaching_context": ""}),
        ):
            result = close_out_month("2026-10")
        assert isinstance(result, dict)
        assert result.get("goal_status") == "pending"
        mock_adj.assert_not_called()


# ---------------------------------------------------------------------------
# _refresh_week_themes_impl: preserves foundation_first_on_confirm path
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="Requires langfuse / to be run in manual test suite")
class TestRefreshWeekThemesPreservesFoundationFirst:

    def test_skips_llm_when_foundation_first_path_already_set(self):
        """If confirm_month_goal already wrote a Foundation-first path, the LLM
        theme generator must not be called (preserving the correct mid-month sequence)."""
        from agent.monthly_review import _refresh_week_themes_impl
        path = [{"week_number": 4, "theme": "Foundation"},
                {"week_number": 5, "theme": "Volume"}]
        mock_col = MagicMock()
        mock_col.find_one.return_value = {
            "goal": {"status": "confirmed"},
            "week_plan_path": path,
            "theme_path_source": "foundation_first_on_confirm",
        }
        with (
            patch("agent.monthly_review.get_month_plans_collection", return_value=mock_col),
            patch("agent.monthly_review.get_current_user_id", return_value="test_mid_month_user"),
            patch("agent.monthly_review.current_month_id", return_value="2026-10"),
            patch("agent.monthly_review.generate_theme_path") as mock_gen,
        ):
            result = _refresh_week_themes_impl()
        mock_gen.assert_not_called()
        assert "foundation_first_on_confirm" in result
        assert "Foundation" in result


if __name__ == "__main__":
    import unittest
    unittest.main()
