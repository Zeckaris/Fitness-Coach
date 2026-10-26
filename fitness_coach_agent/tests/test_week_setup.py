"""Tests for one-pass week setup and missing-only plan generation."""

import os
import sys
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("GEMINI_API_KEY", "dummy_key_for_testing")

import agent.week_setup as week_setup
import tools.plans as plans
from auth.context import set_current_user_id


@pytest.fixture(autouse=True)
def _set_test_user():
    set_current_user_id("test_user")


DATES = [
    "2026-10-26",
    "2026-10-27",
    "2026-10-28",
    "2026-10-29",
    "2026-10-30",
    "2026-10-31",
]


def _state(missing_dates, existing_dates=(), theme="Volume", themes_set=True):
    return {
        "today": DATES[0],
        "window_start": DATES[0],
        "window_end": DATES[-1],
        "window_dates": DATES,
        "themes_set": themes_set,
        "current_week_theme": theme,
        "existing_dates": list(existing_dates),
        "missing_dates": list(missing_dates),
        "plans_complete": not missing_dates,
    }


def test_ensure_week_plans_only_fills_missing_dates():
    collection = MagicMock()
    collection.find.return_value = [
        {"date": DATES[0]},
        {"date": DATES[2]},
    ]

    with patch("tools.plans.today_local", return_value=date(2026, 10, 26)), \
         patch("tools.plans.get_plannable_dates", return_value=DATES), \
         patch("tools.plans.get_plans_collection", return_value=collection), \
         patch("tools.plans.get_current_user_id", return_value="test_user"), \
         patch("tools.plans._generate_plans_for_dates", return_value="ok") as generate:
        result = plans.ensure_week_plans()

    assert result == "ok"
    generate.assert_called_once()
    assert generate.call_args.args[1] == [DATES[1], DATES[3], DATES[4], DATES[5]]
    assert generate.call_args.kwargs["create_only"] is True


def test_ensure_week_plans_skips_generation_when_every_date_exists():
    collection = MagicMock()
    collection.find.return_value = [{"date": d} for d in DATES]

    with patch("tools.plans.today_local", return_value=date(2026, 10, 26)), \
         patch("tools.plans.get_plannable_dates", return_value=DATES), \
         patch("tools.plans.get_plans_collection", return_value=collection), \
         patch("tools.plans.get_current_user_id", return_value="test_user"), \
         patch("tools.plans._generate_plans_for_dates") as generate:
        result = plans.ensure_week_plans()

    generate.assert_not_called()
    assert "All daily plans already exist" in result


def test_generate_today_plan_still_refuses_when_today_exists():
    collection = MagicMock()
    collection.find_one.return_value = {"date": DATES[0]}

    with patch("tools.plans.today_local", return_value=date(2026, 10, 26)), \
         patch("tools.plans.get_plannable_dates", return_value=DATES), \
         patch("tools.plans._has_confirmed_goal", return_value=True), \
         patch("tools.plans.get_plans_collection", return_value=collection), \
         patch("tools.plans.get_current_user_id", return_value="test_user"), \
         patch("tools.plans._generate_plans_for_dates") as generate:
        result = plans.generate_today_plan.invoke({})

    generate.assert_not_called()
    assert "already exists" in result


def test_run_week_setup_double_click_does_not_regenerate():
    progress = []
    state = _state(DATES)
    complete = _state([], existing_dates=DATES)

    with patch("agent.week_setup.run_monthly_review") as review, \
         patch(
             "agent.week_setup.has_theme_path_for_current_month",
             return_value=True,
         ), \
         patch("agent.week_setup.ensure_week_plans", return_value="ok") as generate, \
         patch(
             "agent.week_setup.get_week_setup_state",
             side_effect=[state, complete, complete],
         ):
        first = week_setup.run_week_setup(on_progress=progress.append)
        second = week_setup.run_week_setup(on_progress=progress.append)

    assert first["ok"] is True
    assert first["plans_complete"] is True
    assert first["created_dates"] == DATES
    assert second["ok"] is True
    assert "no plans were regenerated" in second["message"]
    assert generate.call_count == 1
    assert review.call_count == 2
    assert any("Themes saved. Building" in p for p in progress)
    assert any("Done: daily plans ready" in p for p in progress)


def test_run_week_setup_partial_failure_then_retry():
    partial_before = _state(
        [DATES[2], DATES[3], DATES[4], DATES[5]],
        existing_dates=[DATES[0], DATES[1]],
    )
    partial_after = _state(
        [DATES[4], DATES[5]],
        existing_dates=[DATES[0], DATES[1], DATES[2], DATES[3]],
    )
    retry_before = partial_after
    retry_after = _state([], existing_dates=DATES)

    with patch("agent.week_setup.run_monthly_review"), \
         patch(
             "agent.week_setup.has_theme_path_for_current_month",
             return_value=True,
         ), \
         patch(
             "agent.week_setup.ensure_week_plans",
             side_effect=["partial", "ok"],
         ) as generate, \
         patch(
             "agent.week_setup.get_week_setup_state",
             side_effect=[
                 partial_before,
                 partial_after,
                 retry_before,
                 retry_after,
             ],
         ):
        first = week_setup.run_week_setup()
        second = week_setup.run_week_setup()

    assert first["ok"] is False
    assert first["themes_saved"] is True
    assert first["created_dates"] == [DATES[2], DATES[3]]
    assert DATES[4] in first["message"] and DATES[5] in first["message"]
    assert "retry" in first["message"]
    assert "won't be overwritten" in first["message"]
    assert second["ok"] is True
    assert second["created_dates"] == [DATES[4], DATES[5]]
    assert generate.call_count == 2


def test_run_week_setup_does_not_generate_when_theme_save_fails():
    with patch(
        "agent.week_setup.run_monthly_review",
        side_effect=RuntimeError("review failed"),
    ), patch(
        "agent.week_setup.has_theme_path_for_current_month",
        return_value=False,
    ), patch("agent.week_setup.ensure_week_plans") as generate:
        result = week_setup.run_week_setup()

    assert result["ok"] is False
    assert result["themes_saved"] is False
    assert "no daily plans were generated" in result["message"]
    generate.assert_not_called()


def test_run_week_setup_aborts_when_review_fails_even_if_themes_exist():
    with patch(
        "agent.week_setup.run_monthly_review",
        side_effect=RuntimeError("review failed"),
    ), patch(
        "agent.week_setup.has_theme_path_for_current_month",
        return_value=True,
    ), patch("agent.week_setup.ensure_week_plans") as generate:
        result = week_setup.run_week_setup()

    assert result["ok"] is False
    assert result["themes_saved"] is True
    assert "no new daily plans were generated" in result["message"]
    assert "existing week themes are still saved" in result["message"]
    generate.assert_not_called()


def test_run_week_setup_requires_confirmed_goal():
    with patch(
        "agent.week_setup.run_monthly_review",
        return_value=(
            "Closed out 2026-09.\nNo confirmed goal for this month. Set a goal first."
        ),
    ), patch(
        "agent.week_setup.has_theme_path_for_current_month",
        return_value=False,
    ), patch("agent.week_setup.ensure_week_plans") as generate:
        result = week_setup.run_week_setup()

    assert result["ok"] is False
    assert "Set a goal first" in result["message"]
    generate.assert_not_called()


def test_run_week_setup_busy_lock_prevents_parallel_run():
    with patch(
        "agent.week_setup.get_current_user_id",
        return_value="test_user",
    ):
        setup_lock = week_setup._get_setup_lock()
        setup_lock.acquire()
        try:
            with patch("agent.week_setup.run_monthly_review") as review:
                result = week_setup.run_week_setup()
        finally:
            setup_lock.release()

    assert result["ok"] is False
    assert "already running" in result["message"]
    review.assert_not_called()


def test_run_week_setup_locks_are_per_user():
    with patch("agent.week_setup.get_current_user_id", return_value="user_a"):
        lock_a = week_setup._get_setup_lock()
    with patch("agent.week_setup.get_current_user_id", return_value="user_b"):
        lock_b = week_setup._get_setup_lock()

    assert lock_a is not lock_b


def test_format_week_setup_context_uses_only_saved_state():
    saved = _state(
        [DATES[2], DATES[3]],
        existing_dates=[DATES[0], DATES[1]],
        theme="Deload",
    )
    with patch("agent.week_setup.get_week_setup_state", return_value=saved):
        text = week_setup.format_week_setup_context()

    assert "saved theme for this week is Deload" in text
    assert "2 of 6 dates" in text
    assert DATES[2] in text and DATES[3] in text

    unsaved = _state(DATES, theme=None, themes_set=False)
    with patch("agent.week_setup.get_week_setup_state", return_value=unsaved):
        text = week_setup.format_week_setup_context()

    assert "Week themes are not saved yet." in text
    assert "Deload" not in text


def test_format_week_setup_context_never_invents_state_on_read_failure():
    with patch(
        "agent.week_setup.get_week_setup_state",
        side_effect=RuntimeError("db down"),
    ):
        text = week_setup.format_week_setup_context()

    assert "temporarily unavailable" in text
    assert "do not claim a theme" in text


def test_run_week_setup_some_days_existing_fills_only_missing():
    before = _state(
        [DATES[2], DATES[3], DATES[4], DATES[5]],
        existing_dates=[DATES[0], DATES[1]],
    )
    after = _state([], existing_dates=DATES)

    with patch("agent.week_setup.run_monthly_review"), \
         patch(
             "agent.week_setup.has_theme_path_for_current_month",
             return_value=True,
         ), \
         patch("agent.week_setup.ensure_week_plans", return_value="ok") as generate, \
         patch(
             "agent.week_setup.get_week_setup_state",
             side_effect=[before, after],
         ):
        result = week_setup.run_week_setup()

    assert result["ok"] is True
    assert result["themes_saved"] is True
    assert result["plans_complete"] is True
    assert result["created_dates"] == [DATES[2], DATES[3], DATES[4], DATES[5]]
    generate.assert_called_once()


def test_run_week_setup_themes_saved_but_generation_fails_completely_offers_retry():
    before = _state(DATES, existing_dates=[])
    after = _state(DATES, existing_dates=[])  # 0 days created

    with patch("agent.week_setup.run_monthly_review"), \
         patch(
             "agent.week_setup.has_theme_path_for_current_month",
             return_value=True,
         ), \
         patch(
             "agent.week_setup.ensure_week_plans",
             side_effect=RuntimeError("LLM generation crashed"),
         ) as generate, \
         patch(
             "agent.week_setup.get_week_setup_state",
             side_effect=[before, after],
         ):
        result = week_setup.run_week_setup()

    assert result["ok"] is False
    assert result["themes_saved"] is True
    assert result["created_dates"] == []
    assert result["missing_dates"] == DATES
    assert "could not be generated" in result["message"]
    assert "Click Set Week Themes to retry" in result["message"]
    assert "won't be overwritten" in result["message"]
    generate.assert_called_once()


def test_run_week_setup_generation_fallback_string_handled_by_state_check():
    """Verify that if ensure_week_plans returns a fallback string rather than
    raising, run_week_setup verifies actual state from Mongo instead of
    assuming success."""
    fallback_message = "I couldn't reach your saved data just now — please try again in a moment."
    before = _state(DATES, existing_dates=[])
    after = _state(DATES, existing_dates=[])

    with patch("agent.week_setup.run_monthly_review"), \
         patch(
             "agent.week_setup.has_theme_path_for_current_month",
             return_value=True,
         ), \
         patch(
             "agent.week_setup.ensure_week_plans",
             return_value=fallback_message,
         ) as generate, \
         patch(
             "agent.week_setup.get_week_setup_state",
             side_effect=[before, after],
         ):
        result = week_setup.run_week_setup()

    assert result["ok"] is False
    assert result["themes_saved"] is True
    assert result["created_dates"] == []
    assert "could not be generated" in result["message"]
    assert "retry" in result["message"]
    generate.assert_called_once()


def test_run_week_setup_all_days_already_exist_reports_no_regeneration():
    already_complete = _state([], existing_dates=DATES)

    with patch("agent.week_setup.run_monthly_review") as review, \
         patch(
             "agent.week_setup.has_theme_path_for_current_month",
             return_value=True,
         ), \
         patch("agent.week_setup.ensure_week_plans") as generate, \
         patch(
             "agent.week_setup.get_week_setup_state",
             return_value=already_complete,
         ):
        result = week_setup.run_week_setup()

    assert result["ok"] is True
    assert result["themes_saved"] is True
    assert result["plans_complete"] is True
    assert result["created_dates"] == []
    assert "Daily plans already exist" in result["message"]
    assert "no plans were regenerated" in result["message"]
    generate.assert_not_called()
    review.assert_called_once()

