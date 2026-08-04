"""
V9.4 — proactive unattended plan generation.

Invoked by a GitHub Actions scheduled workflow, independent of the
Streamlit process. Loops over every registered user, classifies each
into one of three eligibility cases, and — for eligible users — reuses
V9.3's generate_today_plan() unmodified by setting the same auth
contextvar Streamlit sets per-request.

Run manually with: python -m scripts.scheduled_plan_generation
"""

import logging
from datetime import datetime, timezone

from auth.user_store import list_all_user_ids
from auth.context import set_current_user_id, clear_current_user_id, get_current_user_id
from db.mongo_client import get_month_plans_collection
from db.guards import mongo_guarded
from tools.month_plans import (
    has_confirmed_goal_for_current_month,
    has_ever_had_confirmed_goal,
    has_any_goal_doc_for_current_month,
    get_most_recent_confirmed_goal,
    _current_month_id,
)
from utils.goal_performance import evaluate_goal_performance
from utils.goal_progression import progress_goal
from tools.plans import generate_today_plan

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@mongo_guarded
def _write_confirmed_goal_for_current_month(goal: dict) -> None:
    """
    Direct pre-confirmed write — no pending/confirm dance, since no
    user is in the loop to confirm anything. Same document shape
    stage_month_goal writes. week_plan_path deliberately left as [] on
    insert: generate_today_plan() already checks
    has_theme_path_for_current_month() and bootstraps a default theme
    path itself when absent, so no duplication needed here.
    """
    collection = get_month_plans_collection()
    now = datetime.now(timezone.utc)
    collection.update_one(
        {"user_id": get_current_user_id(), "month_id": _current_month_id()},
        {
            "$set": {"goal": goal, "updated_at": now},
            "$setOnInsert": {"created_at": now, "week_plan_path": []},
        },
        upsert=True,
    )


def run_for_user(user_id: str) -> str:
    """..."""
    set_current_user_id(user_id)
    logger.info("=== Processing user_id=%s ===", user_id)
    try:
        if has_confirmed_goal_for_current_month():
            logger.info("%s: has confirmed goal for current month -> case 2 (backfill only)", user_id)

        elif has_ever_had_confirmed_goal():
            logger.info("%s: no goal this month, but has history -> case 3 candidate", user_id)

            if has_any_goal_doc_for_current_month():
                logger.info("%s: pending goal already staged this month -> skipping", user_id)
                return f"{user_id}: pending goal in progress, skipped."

            recent = get_most_recent_confirmed_goal()
            if recent is None:
                logger.warning("%s: has_ever_had_confirmed_goal=True but get_most_recent_confirmed_goal=None", user_id)
                return f"{user_id}: no confirmed goal found despite history flag, skipped."

            month_id, prev_goal = recent
            logger.info("%s: most recent confirmed goal from %s: %s", user_id, month_id, prev_goal.get("description"))

            perf = evaluate_goal_performance(prev_goal, month_id)
            logger.info(
                "%s: performance eval -> tier=%s avg_pct=%s open_backlog=%s",
                user_id, perf["tier"], perf["avg_pct"], perf["open_backlog_count"],
            )

            new_goal = progress_goal(prev_goal, perf["tier"], perf["latest_metric_value"])
            logger.info("%s: new goal built: %s", user_id, new_goal)

            _write_confirmed_goal_for_current_month(new_goal)
            logger.info("%s: new goal written and confirmed for current month", user_id)

        else:
            logger.info("%s: no planning history at all -> case 1, skipping", user_id)
            return f"{user_id}: no planning history, skipped."

        logger.info("%s: calling generate_today_plan()", user_id)
        result = generate_today_plan.invoke({})
        logger.info("%s: generate_today_plan result: %s", user_id, result)
        return f"{user_id}: {result}"

    except Exception:
        logger.exception("Scheduled generation failed for user_id=%s", user_id)
        return f"{user_id}: FAILED (see logs)"

    finally:
        clear_current_user_id()


def main() -> None:
    user_ids = list_all_user_ids()
    logger.info("Starting scheduled plan generation for %d user(s).", len(user_ids))
    for user_id in user_ids:
        logger.info(run_for_user(user_id))
    logger.info("Scheduled plan generation complete.")


if __name__ == "__main__":
    main()