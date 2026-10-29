"""Tests for evaluation-round tick results, fulfilment and baseline history."""

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("GEMINI_API_KEY", "dummy_key_for_testing")

from tools.evaluation import (
    _apply_round_baselines,
    recompute_pending_goal_for_next_month,
    should_recompute_staged_goal,
)
from utils.evaluation_round import (
    MOVEMENT_BASELINE_SPECS,
    MOVEMENT_CARDIO,
    MOVEMENT_SQUAT,
    apply_baseline_update,
    build_evaluation_round,
    evaluate_round,
    movement_for_exercise,
    pair_outcome,
    preferred_exercise_for_movement,
    resolve_movement_baseline,
)


def _pair(name, pair_id, claim, easier, *, tick_claim=False, tick_easier=False):
    """Two adjacent entries representing one claimed/easier evaluation movement."""
    return [
        {
            "name": name,
            "category": "main",
            "pair_id": pair_id,
            "pair_role": "claim",
            "target_quantity": claim,
            "completed_quantity": claim if tick_claim else 0,
            "completed": tick_claim,
        },
        {
            "name": name,
            "category": "main",
            "pair_id": pair_id,
            "pair_role": "easier",
            "target_quantity": easier,
            "completed_quantity": easier if tick_easier else 0,
            "completed": tick_easier,
        },
    ]


def _cardio(name="Mountain Climber", *, ticked=False):
    return {
        "name": name,
        "category": "main",
        "target_quantity": 20,
        "completed_quantity": 20 if ticked else 0,
        "completed": ticked,
    }


def _day(date_str, exercises):
    return {
        "date": date_str,
        "untracked": True,
        "status": "planned",
        "exercises": exercises,
    }


def _movement(
    date_str,
    *,
    movement="push",
    name="Push-Up",
    claim=20,
    easier=15,
    tick_claim=False,
    tick_easier=False,
):
    return {
        "movement": movement,
        "kind": "pair",
        "date": date_str,
        "entries": _pair(
            name,
            f"p-{date_str}",
            claim,
            easier,
            tick_claim=tick_claim,
            tick_easier=tick_easier,
        ),
    }


class TestPairRules(unittest.TestCase):
    def test_both_ticked_uses_claimed_higher_amount(self):
        entries = _pair("Push-Up", "p1", 20, 15, tick_claim=True, tick_easier=True)
        outcome = pair_outcome(entries)
        self.assertTrue(outcome["ticked"])
        self.assertEqual(outcome["value"], 20)
        self.assertEqual(outcome["role"], "claim")

    def test_only_easier_ticked_uses_lower_amount(self):
        entries = _pair("Push-Up", "p1", 20, 15, tick_easier=True)
        outcome = pair_outcome(entries)
        self.assertTrue(outcome["ticked"])
        self.assertEqual(outcome["value"], 15)
        self.assertEqual(outcome["role"], "easier")

    def test_only_claim_ticked_uses_claimed_amount(self):
        entries = _pair("Push-Up", "p1", 20, 15, tick_claim=True)
        outcome = pair_outcome(entries)
        self.assertEqual(outcome["value"], 20)

    def test_neither_ticked_changes_nothing(self):
        entries = _pair("Push-Up", "p1", 20, 15)
        outcome = pair_outcome(entries)
        self.assertFalse(outcome["ticked"])
        self.assertIsNone(outcome["value"])


class TestSubstitutes(unittest.TestCase):
    def test_current_substitutes_map_to_squat_and_plank_movements(self):
        self.assertEqual(movement_for_exercise("Squat to Overhead Reach"), MOVEMENT_SQUAT)
        self.assertEqual(movement_for_exercise("Bodyweight Incline Side Plank"), "plank")
        self.assertEqual(movement_for_exercise("Mountain Climber"), MOVEMENT_CARDIO)

    def test_real_squat_and_plank_are_preferred_when_present(self):
        names = ["Squat to Overhead Reach", "Squat", "Bodyweight Incline Side Plank", "Plank"]
        self.assertEqual(preferred_exercise_for_movement("squat", names), "Squat")
        self.assertEqual(preferred_exercise_for_movement("plank", names), "Plank")

    def test_cardio_has_no_baseline_spec(self):
        self.assertNotIn(MOVEMENT_CARDIO, MOVEMENT_BASELINE_SPECS)


class TestMovementBaselineRule(unittest.TestCase):
    def test_exactly_half_claim_ticks_becomes_baseline(self):
        movements = [
            _movement("2026-10-29", tick_claim=True),
            _movement("2026-10-30"),
        ]
        result = resolve_movement_baseline(movements, "push")
        self.assertEqual(result["shown_days"], 2)
        self.assertEqual(result["claim_ticked_days"], 1)
        self.assertEqual(result["role"], "claim")
        self.assertEqual(result["value"], 20)

    def test_below_half_falls_back_to_lower_when_ticked(self):
        movements = [
            _movement("2026-10-29", tick_claim=True),
            _movement("2026-10-30", tick_easier=True),
            _movement("2026-10-31"),
        ]
        result = resolve_movement_baseline(movements, "push")
        self.assertEqual(result["claim_ticked_days"], 1)
        self.assertEqual(result["lower_ticked_days"], 1)
        self.assertEqual(result["role"], "easier")
        self.assertEqual(result["value"], 15)

    def test_no_ticks_for_a_movement_changes_nothing(self):
        movements = [
            _movement("2026-10-29"),
            _movement("2026-10-30"),
            _movement("2026-10-31"),
        ]
        result = resolve_movement_baseline(movements, "push")
        self.assertFalse(result["ticked"])
        self.assertIsNone(result["value"])

    def test_one_day_round_is_decided_by_that_day(self):
        claim_day = [_movement("2026-10-31", tick_claim=True)]
        self.assertEqual(resolve_movement_baseline(claim_day, "push")["value"], 20)

        lower_day = [_movement("2026-10-31", tick_easier=True)]
        lower_result = resolve_movement_baseline(lower_day, "push")
        self.assertEqual(lower_result["role"], "easier")
        self.assertEqual(lower_result["value"], 15)

    def test_round_level_application_is_running_and_avoids_duplicate_history(self):
        at1 = datetime(2026, 10, 29, tzinfo=timezone.utc)
        at2 = datetime(2026, 10, 30, tzinfo=timezone.utc)
        at3 = datetime(2026, 10, 31, tzinfo=timezone.utc)
        days = [_day("2026-10-29", _pair("Push-Up", "p1", 20, 15, tick_claim=True))]
        baselines = {}

        _apply_round_baselines(
            build_evaluation_round(days),
            raw_markers={},
            baselines=baselines,
            now=at1,
        )
        self.assertEqual(baselines["push"]["value"], 20)

        days.append(_day("2026-10-30", _pair("Push-Up", "p2", 20, 15)))
        updates = _apply_round_baselines(
            build_evaluation_round(days),
            raw_markers={},
            baselines=baselines,
            now=at2,
        )
        self.assertEqual(updates, [])
        self.assertEqual(baselines["push"]["value"], 20)

        days.append(_day("2026-10-31", _pair("Push-Up", "p3", 20, 15, tick_easier=True)))
        _apply_round_baselines(
            build_evaluation_round(days),
            raw_markers={},
            baselines=baselines,
            now=at3,
        )
        self.assertEqual(baselines["push"]["value"], 15)
        self.assertEqual([h["value"] for h in baselines["push"]["history"]], [20])

    def test_running_update_with_days_passed_so_far(self):
        days = [_movement("2026-10-29", tick_claim=True)]
        self.assertEqual(resolve_movement_baseline(days, "push")["role"], "claim")

        days.append(_movement("2026-10-30"))
        # Exactly half of two days still keeps the claim.
        self.assertEqual(resolve_movement_baseline(days, "push")["role"], "claim")

        days.append(_movement("2026-10-31", tick_easier=True))
        result = resolve_movement_baseline(days, "push")
        self.assertEqual(result["shown_days"], 3)
        self.assertEqual(result["role"], "easier")
        self.assertEqual(result["value"], 15)


class TestRoundFulfilment(unittest.TestCase):
    def _three_day_round(self):
        days = [
            _day("2026-10-29", _pair("Push-Up", "p1", 20, 15) + [_cardio()]),
            _day("2026-10-30", _pair("Push-Up", "p2", 20, 15) + [_cardio()]),
            _day("2026-10-31", _pair("Push-Up", "p3", 20, 15) + [_cardio()]),
        ]
        return build_evaluation_round(days, month_id="2026-10")

    @staticmethod
    def _tick_movement(movement, ticked):
        for ex in movement["entries"]:
            ex["completed"] = ticked
            ex["completed_quantity"] = ex["target_quantity"] if ticked else 0

    def test_half_rule_with_three_days_left_counts_movements_not_days(self):
        round_obj = self._three_day_round()
        self.assertEqual(len(round_obj["dates"]), 3)
        self.assertEqual(evaluate_round(round_obj)["scheduled_movements"], 6)
        self.assertEqual(
            sum(1 for m in round_obj["movements"] if m["movement"] == "push"), 3
        )

        # 3 of 6 ticked, spread over 2 of 3 days -> exactly half, fulfilled.
        for movement in round_obj["movements"][:3]:
            self._tick_movement(movement, True)
        self.assertTrue(evaluate_round(round_obj)["fulfilled"])

        # 2 of 6 ticked -> not fulfilled.
        self._tick_movement(round_obj["movements"][2], False)
        self.assertFalse(evaluate_round(round_obj)["fulfilled"])

    def test_half_rule_does_not_block_a_one_day_round(self):
        day = _day(
            "2026-10-31",
            _pair("Push-Up", "p1", 20, 15, tick_claim=True) + [_cardio()],
        )
        round_obj = build_evaluation_round([day], month_id="2026-10")
        self.assertEqual(len(round_obj["dates"]), 1)
        self.assertEqual(evaluate_round(round_obj)["scheduled_movements"], 2)
        self.assertTrue(evaluate_round(round_obj)["fulfilled"])

        self._tick_movement(round_obj["movements"][0], False)
        self.assertFalse(evaluate_round(build_evaluation_round([day]))["fulfilled"])


class TestBaselineHistory(unittest.TestCase):
    def test_previous_baseline_is_kept_in_history_and_never_overwritten(self):
        at1 = datetime(2026, 10, 29, tzinfo=timezone.utc)
        at2 = datetime(2026, 10, 30, tzinfo=timezone.utc)

        first = apply_baseline_update(
            {"value": 20, "unit": "reps", "history": []},
            25,
            unit="reps",
            source_exercise="Push-Up",
            updated_at=at1,
        )
        second = apply_baseline_update(
            first,
            32,
            unit="reps",
            source_exercise="Push-Up",
            updated_at=at2,
        )

        self.assertEqual(second["value"], 32)
        self.assertEqual([h["value"] for h in second["history"]], [20, 25])
        self.assertEqual(second["history"][0]["value"], 20)
        self.assertEqual(first["history"][0]["value"], 20)


    def test_same_result_does_not_add_duplicate_history(self):
        at1 = datetime(2026, 10, 29, tzinfo=timezone.utc)
        at2 = datetime(2026, 10, 30, tzinfo=timezone.utc)

        first = apply_baseline_update(
            {"value": 20, "unit": "reps", "history": []},
            25,
            unit="reps",
            source_exercise="Push-Up",
            updated_at=at1,
        )
        second = apply_baseline_update(
            first,
            25,
            unit="reps",
            source_exercise="Push-Up",
            updated_at=at2,
        )

        self.assertEqual(second["value"], 25)
        self.assertEqual([h["value"] for h in second["history"]], [20])


class TestConfirmedGoalLocked(unittest.TestCase):
    def test_should_recompute_only_unconfirmed_goals(self):
        self.assertFalse(should_recompute_staged_goal({"status": "confirmed"}))
        self.assertTrue(should_recompute_staged_goal({"status": "pending"}))
        self.assertFalse(should_recompute_staged_goal(None))

    def test_confirmed_next_month_goal_is_never_touched(self):
        collection = MagicMock()
        collection.find_one.return_value = {
            "goal": {"status": "confirmed", "description": "Locked goal"}
        }
        stage = MagicMock()

        result = recompute_pending_goal_for_next_month(
            "2026-10",
            user_id="test_user",
            collection=collection,
            stage=stage,
        )

        self.assertFalse(result["recomputed"])
        stage.assert_not_called()

    def test_pending_next_month_goal_is_recomputed(self):
        collection = MagicMock()
        collection.find_one.return_value = {
            "goal": {
                "status": "pending",
                "description": "Next month",
                "focus": "balanced",
            }
        }
        stage = MagicMock(return_value="staged")

        result = recompute_pending_goal_for_next_month(
            "2026-10",
            user_id="test_user",
            collection=collection,
            stage=stage,
        )

        self.assertTrue(result["recomputed"])
        self.assertEqual(result["target_month"], "2026-11")
        stage.assert_called_once()
        self.assertEqual(stage.call_args.kwargs["month_id"], "2026-11")


if __name__ == "__main__":
    unittest.main()
