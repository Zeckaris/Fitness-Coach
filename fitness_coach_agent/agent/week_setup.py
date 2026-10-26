"""One-pass weekly setup: save themes, then fill missing daily plans."""

import logging
import threading
from typing import Callable, Optional

from auth.context import get_current_user_id
from db.mongo_client import get_month_plans_collection, get_plans_collection
from agent.monthly_review import run_monthly_review
from tools.month_plans import has_theme_path_for_current_month
from tools.plans import ensure_week_plans
from utils.calendar_weeks import (
    current_month_id,
    get_plannable_dates,
    get_week_for_date,
    today_local,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]

_USER_SETUP_LOCKS: dict[str, threading.Lock] = {}
_USER_SETUP_LOCKS_GUARD = threading.Lock()


def _get_setup_lock() -> threading.Lock:
    """One setup lock per user, so users never block each other."""
    user_id = get_current_user_id()
    with _USER_SETUP_LOCKS_GUARD:
        lock = _USER_SETUP_LOCKS.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _USER_SETUP_LOCKS[user_id] = lock
        return lock


def _existing_plan_dates(dates: list[str]) -> set:
    if not dates:
        return set()
    docs = get_plans_collection().find(
        {"user_id": get_current_user_id(), "date": {"$in": list(dates)}},
        {"date": 1},
    )
    return {doc["date"] for doc in docs if doc.get("date")}


def get_week_setup_state() -> dict:
    """Read saved theme and daily-plan state without mutating anything."""
    today = today_local()
    today_str = today.strftime("%Y-%m-%d")
    window_dates = get_plannable_dates(today)
    month_doc = get_month_plans_collection().find_one(
        {"user_id": get_current_user_id(), "month_id": current_month_id()}
    )
    theme_path = (month_doc or {}).get("week_plan_path") or []
    week_number = get_week_for_date(today_str)["week_number"]
    current_week_theme = next(
        (t["theme"] for t in theme_path if t.get("week_number") == week_number),
        None,
    )
    existing_dates = _existing_plan_dates(window_dates)
    missing_dates = [d for d in window_dates if d not in existing_dates]

    return {
        "today": today_str,
        "window_start": window_dates[0],
        "window_end": window_dates[-1],
        "window_dates": window_dates,
        "themes_set": bool(theme_path),
        "current_week_theme": current_week_theme,
        "existing_dates": sorted(existing_dates),
        "missing_dates": missing_dates,
        "plans_complete": not missing_dates,
    }


def format_week_setup_context() -> str:
    """Fresh saved theme/plan state for the coach prompt."""
    try:
        state = get_week_setup_state()
    except Exception:
        logger.exception("Could not load saved week theme/plan state for coach context")
        return (
            "Saved week themes and daily-plan state are temporarily unavailable; "
            "do not claim a theme or that plans exist."
        )
    if state["themes_set"]:
        if state["current_week_theme"]:
            theme_line = (
                "Week themes are saved; the saved theme for this week is "
                f"{state['current_week_theme']}."
            )
        else:
            theme_line = (
                "Week themes are saved, but no saved theme exists for the current week."
            )
    else:
        theme_line = "Week themes are not saved yet."

    total = len(state["window_dates"])
    if state["plans_complete"]:
        plans_line = (
            f"Daily plans exist for every date in the current window "
            f"({state['window_start']} through {state['window_end']})."
        )
    elif state["existing_dates"]:
        plans_line = (
            f"Daily plans exist for {len(state['existing_dates'])} of {total} dates "
            f"in the current window; missing: {', '.join(state['missing_dates'])}."
        )
    else:
        plans_line = (
            f"No daily plans exist for the current window "
            f"({state['window_start']} through {state['window_end']})."
        )

    return f"{theme_line} {plans_line}"


def _result(
    *,
    ok: bool,
    themes_saved: bool,
    created_dates: Optional[list[str]] = None,
    missing_dates: Optional[list[str]] = None,
    plans_complete: bool = False,
    message: str,
) -> dict:
    return {
        "ok": ok,
        "themes_saved": themes_saved,
        "created_dates": created_dates or [],
        "missing_dates": missing_dates or [],
        "plans_complete": plans_complete,
        "message": message,
    }


def _report(on_progress: Optional[ProgressCallback], message: str) -> None:
    if on_progress:
        on_progress(message)


def run_week_setup(on_progress: Optional[ProgressCallback] = None) -> dict:
    """Save the month's themes, then fill only missing daily plans.

    Reuses ``run_monthly_review`` unchanged. Result state is verified from
    Mongo, not inferred from tool return strings.
    """
    setup_lock = _get_setup_lock()
    if not setup_lock.acquire(blocking=False):
        return _result(
            ok=False,
            themes_saved=False,
            message=(
                "Weekly setup is already running. Please wait for it to finish."
            ),
        )

    try:
        _report(on_progress, "Setting week themes and closing out last month...")
        review_result = ""
        review_failed = False
        try:
            review_result = run_monthly_review() or ""
        except Exception:
            logger.exception("Monthly review failed during weekly setup")
            review_failed = True

        try:
            themes_saved = has_theme_path_for_current_month()
        except Exception:
            logger.exception("Could not verify saved week themes")
            return _result(
                ok=False,
                themes_saved=False,
                message=(
                    "I couldn't verify whether the week themes were saved. "
                    "Please retry Set Week Themes."
                ),
            )

        if review_failed:
            if themes_saved:
                message = (
                    "The monthly review couldn't be completed, so no new daily "
                    "plans were generated. Your existing week themes are still "
                    "saved. Please retry Set Week Themes."
                )
            else:
                message = (
                    "The monthly review couldn't be completed, so no week themes "
                    "were saved and no daily plans were generated. Please retry "
                    "Set Week Themes."
                )
            return _result(
                ok=False,
                themes_saved=themes_saved,
                message=message,
            )

        if isinstance(review_result, str) and "No confirmed goal" in review_result:
            return _result(
                ok=False,
                themes_saved=themes_saved,
                message=(
                    "No confirmed goal exists yet, so week themes and daily plans "
                    "weren't prepared. Set a goal first, then click Set Week Themes."
                ),
            )

        if not themes_saved:
            detail = f" {review_result}" if review_result else ""
            return _result(
                ok=False,
                themes_saved=False,
                message=(
                    "Themes could not be saved, so no daily plans were generated."
                    f"{detail} Please retry Set Week Themes after fixing the issue."
                ),
            )

        _report(on_progress, "Themes saved. Checking this week's daily plans...")
        try:
            before = get_week_setup_state()
        except Exception:
            logger.exception("Could not inspect daily plan state after saving themes")
            return _result(
                ok=False,
                themes_saved=True,
                message=(
                    "Themes were saved, but I couldn't inspect this week's daily "
                    "plans. Please retry Set Week Themes; existing plans won't be "
                    "overwritten."
                ),
            )

        if before["plans_complete"]:
            _report(
                on_progress,
                f"Done: daily plans already exist for {before['window_start']} "
                f"through {before['window_end']}.",
            )
            return _result(
                ok=True,
                themes_saved=True,
                plans_complete=True,
                message=(
                    "Themes saved. Daily plans already exist for "
                    f"{before['window_start']} through {before['window_end']}; "
                    "no plans were regenerated."
                ),
            )

        _report(on_progress, "Themes saved. Building this week's daily plans...")
        try:
            ensure_week_plans()
        except Exception:
            logger.exception("Daily plan generation failed during weekly setup")

        try:
            after = get_week_setup_state()
        except Exception:
            logger.exception("Could not verify daily plans after generation")
            return _result(
                ok=False,
                themes_saved=True,
                missing_dates=before["missing_dates"],
                message=(
                    "Themes were saved, but I couldn't verify this week's daily "
                    "plans. Please retry Set Week Themes; existing plans won't be "
                    "overwritten."
                ),
            )

        created_dates = [
            d for d in before["missing_dates"] if d not in after["missing_dates"]
        ]
        if after["plans_complete"]:
            _report(
                on_progress,
                f"Done: daily plans ready for {after['window_start']} "
                f"through {after['window_end']}.",
            )
            return _result(
                ok=True,
                themes_saved=True,
                created_dates=created_dates,
                plans_complete=True,
                message=(
                    "Themes saved and daily plans are ready for "
                    f"{after['window_start']} through {after['window_end']}."
                ),
            )

        if created_dates:
            message = (
                "Themes were saved, and plans were created for "
                f"{', '.join(created_dates)}. Still missing: "
                f"{', '.join(after['missing_dates'])}. Click Set Week Themes "
                "to retry; existing plans won't be overwritten."
            )
        else:
            message = (
                "Themes were saved, but this week's daily plans could not be "
                "generated. Click Set Week Themes to retry; existing plans "
                "won't be overwritten."
            )

        return _result(
            ok=False,
            themes_saved=True,
            created_dates=created_dates,
            missing_dates=after["missing_dates"],
            message=message,
        )
    finally:
        setup_lock.release()
