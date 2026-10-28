"""Save-path tests for complete exercise prescriptions."""

import os
import sys
import unittest
from contextlib import ExitStack
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("GEMINI_API_KEY", "dummy_key_for_testing")

for _mod in ("langfuse", "langfuse.langchain"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# The Gemini client and Astra vector-store imports can be slow or prompt for
# credentials in some environments. These tests never call either.
for _mod in ("agent.llm", "tools.knowledge_base"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import tools.plans as plans
from app.components import workout_session as session_ui


def _exercise_dicts(targeted=False):
    rows = [
        ("Bear Crawl", "warmup"),
        ("Star Jump (Male)", "warmup"),
        ("Push-Up", "main"),
        ("Mountain Climber", "main"),
        ("Astride Jumps (Male)", "main"),
        ("Skater Hops", "main"),
        ("Dead Bug", "cooldown"),
    ]
    exercises = [
        {"name": name, "focus": "full_body", "category": category, "sets": None, "reps": None}
        for name, category in rows
    ]
    if targeted:
        exercises[2]["target_quantity"] = 45
        exercises[2]["unit"] = "reps"
    return exercises


def _exercise_items(targeted=False):
    return [
        plans.ExercisePlanItem.model_construct(
            name=item["name"],
            focus=item["focus"],
            category=item["category"],
            sets=item["sets"],
            reps=item["reps"],
            equipment=["none"],
            duration_minutes=5,
            target_quantity=item.get("target_quantity"),
            unit=item.get("unit"),
            completed_quantity=0,
            completed=False,
            set_group_id=None,
        )
        for item in _exercise_dicts(targeted=targeted)
    ]


def _assert_complete(test_case, exercises):
    test_case.assertTrue(exercises)
    for exercise in exercises:
        test_case.assertIsInstance(exercise["sets"], int)
        test_case.assertGreater(exercise["sets"], 0)
        test_case.assertTrue(str(exercise["reps"]).strip())
        test_case.assertTrue(exercise["unit"])


class TestSavePaths(unittest.TestCase):
    def test_update_daily_plans_saves_no_empty_exercises(self):
        collection = MagicMock()

        with ExitStack() as stack:
            stack.enter_context(
                patch("tools.plans._valid_plan_dates", return_value={"2026-10-28"})
            )
            stack.enter_context(
                patch("tools.plans.get_daily_volume_targets_for_date", return_value=[])
            )
            stack.enter_context(
                patch("tools.plans.get_week_focus_for_date", return_value="Foundation")
            )
            day = plans.DayPlanInput(
                date="2026-10-28",
                focus_area="Full Body",
                status="planned",
                duration_minutes=30,
                exercises=_exercise_dicts(),
            )
            stack.enter_context(patch("tools.plans._has_confirmed_goal", return_value=True))
            stack.enter_context(patch("tools.plans.ensure_week_plan_exists", return_value=True))
            stack.enter_context(patch("tools.plans.get_plans_collection", return_value=collection))
            stack.enter_context(
                patch("tools.plans.get_current_user_id", return_value="test_user")
            )
            result = plans.update_daily_plans.func(days=[day])

        self.assertIn("Plan updated", result)
        saved = collection.update_one.call_args[0][1]["$set"]["exercises"]
        _assert_complete(self, saved)

    def test_generate_plan_saves_no_empty_exercises(self):
        collection = MagicMock()
        backlog = MagicMock()
        backlog.find.return_value = []
        fake_day = SimpleNamespace(
            date="2026-10-28",
            focus_area="Full Body",
            status="planned",
            duration_minutes=30,
            exercises=_exercise_items(),
            notes=None,
            avoid_body_parts=None,
            source_checkin_date=None,
        )

        with ExitStack() as stack:
            stack.enter_context(
                patch("tools.plans.today_local", return_value=date(2026, 10, 28))
            )
            stack.enter_context(patch("tools.plans._has_confirmed_goal", return_value=True))
            stack.enter_context(
                patch(
                    "tools.plans._get_month_doc",
                    return_value={
                        "goal": {"description": "general fitness", "volume_targets": []},
                        "week_plan_path": [],
                    },
                )
            )
            stack.enter_context(
                patch("tools.plans.has_theme_path_for_current_month", return_value=True)
            )
            stack.enter_context(patch("tools.plans.ensure_week_plan_exists", return_value=True))
            stack.enter_context(
                patch("tools.plans.get_week_focus_for_date", return_value="Foundation")
            )
            stack.enter_context(patch("tools.plans.get_past_plans", MagicMock()))
            stack.enter_context(patch("tools.plans.get_backlog", MagicMock()))
            stack.enter_context(
                patch("tools.plans.search_fitness_knowledge_base", MagicMock())
            )
            stack.enter_context(
                patch("tools.plans._calculate_volume_progress", return_value=[])
            )
            stack.enter_context(patch("tools.plans.get_exercise_recency", return_value={}))
            stack.enter_context(
                patch("tools.plans.get_daily_volume_targets_for_date", return_value=[])
            )
            stack.enter_context(patch("tools.plans.get_workouts_by_names", return_value=[]))
            stack.enter_context(
                patch("tools.plans.pre_schedule_goal_exercises", return_value=({}, "none"))
            )
            stack.enter_context(
                patch("tools.user_profile.resolve_user_equipment", return_value=([], {}))
            )
            stack.enter_context(
                patch("tools.plans.get_per_day_filler_pools", return_value="none")
            )
            stack.enter_context(
                patch(
                    "tools.plans.generate_backfill_days",
                    return_value=SimpleNamespace(days=[fake_day]),
                )
            )
            stack.enter_context(
                patch("tools.plans.get_plans_collection", return_value=collection)
            )
            stack.enter_context(
                patch("tools.plans.get_current_user_id", return_value="test_user")
            )
            stack.enter_context(
                patch("tools.plans.get_backlog_collection", return_value=backlog)
            )
            result = plans._generate_plans_for_dates(
                date(2026, 10, 28), ["2026-10-28"]
            )

        self.assertIn("Plans created", result)
        saved = collection.update_one.call_args[0][1]["$set"]["exercises"]
        _assert_complete(self, saved)

    def test_untracked_plan_saves_no_empty_exercises(self):
        collection = MagicMock()
        fake_day = SimpleNamespace(
            date="2026-10-28",
            focus_area="Full Body",
            status="planned",
            duration_minutes=30,
            exercises=_exercise_items(targeted=True),
            notes=None,
            avoid_body_parts=None,
            source_checkin_date=None,
        )

        with ExitStack() as stack:
            stack.enter_context(
                patch("tools.plans.today_local", return_value=date(2026, 10, 28))
            )
            stack.enter_context(
                patch(
                    "utils.calendar_weeks.get_last_week_info",
                    return_value={"last_week_start": "2026-10-28", "last_week_end": "2026-10-31"},
                )
            )
            stack.enter_context(patch("tools.plan_history.get_past_plans", MagicMock()))
            stack.enter_context(patch("tools.backlog.get_backlog", MagicMock()))
            stack.enter_context(
                patch("tools.knowledge_base.search_fitness_knowledge_base", MagicMock())
            )
            stack.enter_context(
                patch("tools.user_profile.resolve_user_equipment", return_value=([], {}))
            )
            stack.enter_context(
                patch("tools.workout_library.get_per_day_filler_pools", return_value="pools")
            )
            stack.enter_context(
                patch("tools.user_profile.get_user_profile", return_value={"experience_level": "beginner"})
            )
            stack.enter_context(
                patch(
                    "agent.plan_generation.generate_backfill_days",
                    return_value=SimpleNamespace(days=[fake_day]),
                )
            )
            stack.enter_context(
                patch("tools.plans.get_plans_collection", return_value=collection)
            )
            stack.enter_context(
                patch("tools.plans.get_current_user_id", return_value="test_user")
            )
            result = plans._generate_untracked_plans_for_dates(
                ["2026-10-28"], focus_hint="general"
            )

        self.assertIn("Untracked plans created", result)
        set_fields = collection.update_one.call_args[0][1]["$set"]
        self.assertTrue(set_fields["untracked"])
        _assert_complete(self, set_fields["exercises"])
        for exercise in set_fields["exercises"]:
            self.assertIsNone(exercise.get("target_quantity"))

    def test_ui_quick_start_saves_no_empty_exercises(self):
        collection = MagicMock()

        with ExitStack() as stack:
            stack.enter_context(
                patch("app.components.workout_session.get_plans_collection", return_value=collection)
            )
            stack.enter_context(
                patch("app.components.workout_session.get_current_user_id", return_value="test_user")
            )
            stack.enter_context(
                patch("app.components.workout_session.get_today_str", return_value="2026-10-28")
            )
            result = session_ui.create_today_plan(
                [{"name": "Push-Up", "focus": "chest", "category": "main"}]
            )

        _assert_complete(self, result["exercises"])


class TestValidationLimits(unittest.TestCase):
    def _make_day(self, exercises):
        with ExitStack() as stack:
            stack.enter_context(
                patch("tools.plans._valid_plan_dates", return_value={"2026-10-28"})
            )
            stack.enter_context(
                patch("tools.plans.get_daily_volume_targets_for_date", return_value=[])
            )
            stack.enter_context(
                patch("tools.plans.get_week_focus_for_date", return_value="Foundation")
            )
            return plans.DayPlanInput(
                date="2026-10-28",
                focus_area="Full Body",
                status="planned",
                duration_minutes=30,
                exercises=exercises,
            )

    def test_seven_exercises_with_correct_phases_is_valid(self):
        day = self._make_day(_exercise_dicts())
        self.assertEqual(len(day.exercises), 7)

    def test_too_few_exercises_is_rejected(self):
        with self.assertRaises(ValueError):
            self._make_day(_exercise_dicts()[:6])

    def test_too_many_exercises_is_rejected(self):
        exercises = _exercise_dicts()
        exercises.extend(
            [
                {"name": "Push-Up", "focus": "chest", "category": "main"}
                for _ in range(14)
            ]
        )
        with self.assertRaises(ValueError):
            self._make_day(exercises)

    def test_missing_warmup_is_rejected(self):
        exercises = _exercise_dicts()
        exercises[0]["category"] = "main"
        with self.assertRaises(ValueError):
            self._make_day(exercises)

    def test_missing_cooldown_is_rejected(self):
        exercises = _exercise_dicts()
        exercises[-1]["category"] = "main"
        with self.assertRaises(ValueError):
            self._make_day(exercises)


if __name__ == "__main__":
    unittest.main()
