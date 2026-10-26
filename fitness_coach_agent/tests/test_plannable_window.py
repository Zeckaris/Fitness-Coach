"""Tests for the shared plannable window and today-inclusive read paths."""

import os
import sys
from contextlib import ExitStack
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("GEMINI_API_KEY", "dummy_key_for_testing")

from utils.calendar_weeks import (
    format_coach_date_context,
    get_plannable_dates,
    get_plannable_window,
)
from tools.plan_history import get_current_plan
from app.components.dashboard_cards import get_upcoming_plan_docs
from auth.context import set_current_user_id
import tools.plans as plans


def test_window_includes_today_and_runs_to_week_end():
    window = get_plannable_window(date(2026, 10, 26))

    assert window["today"] == "2026-10-26"
    assert window["weekday"] == "Monday"
    assert window["start_date"] == "2026-10-26"
    assert window["end_date"] == "2026-10-31"
    assert window["dates"] == [
        "2026-10-26",
        "2026-10-27",
        "2026-10-28",
        "2026-10-29",
        "2026-10-30",
        "2026-10-31",
    ]


def test_window_handles_month_end_edges():
    assert get_plannable_dates(date(2026, 9, 30)) == ["2026-09-30"]
    assert get_plannable_dates(date(2026, 10, 31)) == ["2026-10-31"]


def test_every_date_inside_window_shares_the_same_end():
    window = get_plannable_window(date(2026, 10, 26))

    for day in window["dates"]:
        inner = get_plannable_window(day)
        assert inner["start_date"] == day
        assert inner["end_date"] == window["end_date"]


def test_coach_context_contains_today_and_window_bounds():
    text = format_coach_date_context(get_plannable_window(date(2026, 10, 26)))

    assert "Today is Monday, 2026-10-26" in text
    assert "2026-10-26 through 2026-10-31" in text


def _plan_doc(date_str: str, day_status: str = "planned") -> dict:
    return {
        "date": date_str,
        "status": day_status,
        "focus_area": "Upper Body",
        "exercises": [],
    }


def test_get_current_plan_includes_today_and_full_window():
    docs = {"2026-10-26": _plan_doc("2026-10-26")}
    collection = MagicMock()
    collection.find_one.side_effect = lambda query: docs.get(query["date"])

    with patch("utils.calendar_weeks.today_local", return_value=date(2026, 10, 26)), \
         patch("tools.plan_history.get_plans_collection", return_value=collection), \
         patch("tools.plan_history.get_current_user_id", return_value="test_user"):
        result = get_current_plan.invoke({})

    lines = result.splitlines()
    assert len(lines) == 6
    assert lines[0] == "2026-10-26: focus: Upper Body"
    assert lines[1] == "2026-10-27: no plan entry exists yet."
    assert lines[-1].startswith("2026-10-31:")


def test_dashboard_upcoming_excludes_today_and_caps_at_three():
    dates = [
        "2026-10-26",
        "2026-10-27",
        "2026-10-28",
        "2026-10-29",
        "2026-10-30",
        "2026-10-31",
    ]
    collection = MagicMock()
    collection.find_one.return_value = None

    with patch("app.components.dashboard_cards.get_plannable_dates", return_value=dates), \
         patch("app.components.dashboard_cards.get_plans_collection", return_value=collection), \
         patch("app.components.dashboard_cards.get_current_user_id", return_value="test_user"):
        docs = get_upcoming_plan_docs()

    assert [day for day, _ in docs] == ["2026-10-27", "2026-10-28", "2026-10-29"]


def test_dashboard_has_no_upcoming_days_on_window_end():
    with patch(
        "app.components.dashboard_cards.get_plannable_dates",
        return_value=["2026-10-31"],
    ), patch(
        "app.components.dashboard_cards.get_plans_collection",
        return_value=MagicMock(),
    ), patch(
        "app.components.dashboard_cards.get_current_user_id",
        return_value="test_user",
    ):
        assert get_upcoming_plan_docs() == []


def _run_generate_today_plan(week_plan_exists: bool):
    set_current_user_id("test_user")
    today = date(2026, 10, 26)
    dates_to_plan = get_plannable_dates(today)
    fake_days = [
        SimpleNamespace(
            date=day,
            focus_area="Upper Body",
            status="planned",
            duration_minutes=30,
            exercises=[],
            notes=None,
            avoid_body_parts=None,
            source_checkin_date=None,
        )
        for day in dates_to_plan
    ]
    plans_collection = MagicMock()
    plans_collection.find_one.return_value = None
    backlog_collection = MagicMock()
    backlog_collection.find.return_value = []

    with ExitStack() as stack:
        stack.enter_context(patch("utils.calendar_weeks.today_local", return_value=today))
        stack.enter_context(patch("tools.plans._has_confirmed_goal", return_value=True))
        stack.enter_context(
            patch("tools.plans.get_plans_collection", return_value=plans_collection)
        )
        stack.enter_context(patch("tools.plans.get_current_user_id", return_value="test_user"))
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
        stack.enter_context(
            patch("tools.plans.ensure_week_plan_exists", return_value=week_plan_exists)
        )
        stack.enter_context(patch("tools.plans.get_week_focus_for_date", return_value="Upper Body"))
        stack.enter_context(
            patch(
                "tools.plans.build_week_plan_data",
                return_value={
                    "focus": "Upper Body",
                    "daily_volume_targets": [],
                    "week_volume_targets": None,
                    "rationale": "test",
                },
            )
        )
        stack.enter_context(patch("tools.plans.save_week_plan", return_value="saved"))
        stack.enter_context(patch("tools.plans.get_past_plans", MagicMock()))
        stack.enter_context(patch("tools.plans.get_backlog", MagicMock()))
        stack.enter_context(patch("tools.plans.search_fitness_knowledge_base", MagicMock()))
        stack.enter_context(patch("tools.plans._calculate_volume_progress", return_value=[]))
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
        stack.enter_context(patch("tools.plans.get_per_day_filler_pools", return_value="none"))
        stack.enter_context(
            patch(
                "tools.plans.generate_backfill_days",
                return_value=SimpleNamespace(days=fake_days),
            )
        )
        stack.enter_context(
            patch("tools.plans.get_backlog_collection", return_value=backlog_collection)
        )
        result = plans.generate_today_plan.invoke({})

    assert result.startswith("Backfilled plan for 2026-10-26 through 2026-10-31.")
    assert plans_collection.update_one.call_count == len(dates_to_plan)


def test_generate_today_plan_starts_from_today_with_existing_week_plan():
    _run_generate_today_plan(week_plan_exists=True)


def test_generate_today_plan_starts_from_today_with_missing_week_plan():
    _run_generate_today_plan(week_plan_exists=False)
