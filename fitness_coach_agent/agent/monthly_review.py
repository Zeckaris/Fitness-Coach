"""Monthly review pipeline — V8."""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


from pydantic import BaseModel, Field
import logging
from langfuse.langchain import CallbackHandler
from langchain_core.tools import tool

from db.mongo_client import get_month_plans_collection
from db.guards import mongo_guarded, MONGO_FALLBACK_MESSAGE
from tools.backlog import sync_backlog
from tools.month_plans import _current_month_id
from tools.week_plans import _weeks_in_month, _calculate_week_targets
from tools.progress import calculate_progress
from agent.prompts import MONTHLY_REVIEW_PROMPT, THEME_PATH_PROMPT
from auth.context import get_current_user_id
from agent.llm import build_review_llm
from agent.error_handling import call_structured_llm_with_reprompt, StructuredOutputFailed
from utils.theme_defaults import default_theme_path
from agent.prompts import WEEK_BLOCK_PROMPT

LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

langfuse_handler = CallbackHandler()


def _previous_month_id() -> str:
    today = datetime.now(LOCAL_TZ).date()
    first_day = today.replace(day=1)
    prev_month = first_day - timedelta(days=1)
    return prev_month.strftime("%Y-%m")


class MonthlyReviewOutput(BaseModel):
    narrative: str = Field(description="2-4 sentence internal record of what happened last month and why.")
    coaching_context: str = Field(
        description="Concrete notes for next month's goal-setting: intensity, reps, rounds, or "
        "exercise adjustments based on this month's adherence and progress. Written for another coach."
    )


def _generate_review(summary: dict) -> dict:
    if summary.get("goal_status") != "confirmed":
        return {"narrative": "No confirmed goal last month; nothing to review.", "coaching_context": ""}

    prompt = MONTHLY_REVIEW_PROMPT.format(
        goal_description=summary.get("goal_description") or "unspecified",
        adherence=summary.get("adherence"),
        volume_progress=summary.get("volume_progress"),
        metric_progress=summary.get("metric_progress"),
    )

    try:
        result = call_structured_llm_with_reprompt(
            build_review_llm, prompt, MonthlyReviewOutput,
            config={"callbacks": [langfuse_handler]},
        )
        return {"narrative": result.narrative, "coaching_context": result.coaching_context}
    except StructuredOutputFailed:
        logger.exception("Monthly review narrative generation failed after retry + re-prompt")
        return {
            "narrative": "This month's review couldn't be generated automatically.",
            "coaching_context": "",
        }


@mongo_guarded
def close_out_month(month_id: str) -> dict:
    """
    Compute final adherence/metric delta, then LLM-generate narrative + coaching context.

    @mongo_guarded per V9.2: hard-fails cleanly on a Mongo dependency
    failure (no silent partial write — the guard stops the function
    before update_one runs if find_one already failed, or before any
    later step if update_one itself fails). NOTE: on that failure path
    this returns MONGO_FALLBACK_MESSAGE (a str), not the usual dict
    shape ({"status": ...} / {"narrative": ..., ...}) — currently
    harmless since run_monthly_review() discards this function's return
    value, but if a future caller starts reading fields off the result,
    it needs to handle the str case explicitly.
    """
    month_plans = get_month_plans_collection()
    doc = month_plans.find_one({"user_id": get_current_user_id(), "month_id": month_id})
    if not doc:
        return {"status": "no_doc"}

    goal = doc.get("goal") or {}
    progress = calculate_progress(month_id=month_id) if (goal and goal.get("status") == "confirmed") else None

    summary = {
        "month_id": month_id,
        "goal_description": goal.get("description") if goal else None,
        "goal_status": goal.get("status") if goal else None,
        "adherence": progress["adherence"] if progress else None,
        "volume_progress": progress["volume_progress"] if progress else [],
        "metric_progress": progress["metric_progress"] if progress else None,
        "closed_at": datetime.now(ZoneInfo("UTC")),
    }

    review = _generate_review(summary)
    summary["narrative"] = review["narrative"]
    summary["coaching_context"] = review["coaching_context"]

    month_plans.update_one(
        {"user_id": get_current_user_id(), "month_id": month_id},
        {"$set": {"close_out_summary": summary, "updated_at": datetime.now(ZoneInfo("UTC"))}},
    )
    return summary


class WeekThemeOutput(BaseModel):
    week_number: int
    theme: str


class ThemePathOutput(BaseModel):
    week_plan_path: List[WeekThemeOutput]


def generate_theme_path(prev_close_out: dict, current_goal: dict, total_weeks: int) -> List[dict]:
    prompt = THEME_PATH_PROMPT.format(
        total_weeks=total_weeks,
        goal_description=current_goal.get("description", "unspecified"),
        last_month_narrative=prev_close_out.get("narrative", "No prior review available."),
        last_month_adherence=prev_close_out.get("adherence"),
    )

    try:
        result = call_structured_llm_with_reprompt(
            build_review_llm, prompt, ThemePathOutput,
            config={"callbacks": [langfuse_handler]},
        )
        themes = sorted(result.week_plan_path, key=lambda t: t.week_number)
        if len(themes) != total_weeks:
            logger.warning(
                "Theme path generation returned %d themes, expected %d (total_weeks=%s), using fallback",
                len(themes), total_weeks, total_weeks,
            )
            return default_theme_path(total_weeks)
        return [{"week_number": i + 1, "theme": t.theme} for i, t in enumerate(themes)]
    except StructuredOutputFailed:
        logger.exception(
            "Theme path generation failed after retry + re-prompt (total_weeks=%s), using fallback",
            total_weeks,
        )
        return default_theme_path(total_weeks)




class WeekBlockOutput(BaseModel):
    block_1_focus: str = Field(description="Training focus label for Block 1 (days 1-3 of the week).")
    block_2_focus: str = Field(description="Training focus label for Block 2 (days 4-6 of the week).")
    rationale: str = Field(description="1-2 sentence rationale for these focus choices.")


def build_week_blocks(
    week_id: str,
    block_1_dates: List[str],
    block_2_dates: List[str],
    week_theme: str,
    month_goal: dict,
    week_number: int,
    total_weeks: int,
) -> dict:
    """
    Plain function (no decorators): builds week block structure for the
    V9.3 backfill path. Mirrors generate_theme_path's shape — pure
    logic + one structured LLM call, no Mongo I/O, no @mongo_guarded.
    The caller (generate_today_plan, in tools/plans.py) does its own
    single @mongo_guarded wrap and its own write to week_plans_collection
    — this function never touches Mongo directly.

    Hard numbers (per-exercise week/block volume targets) come from
    _calculate_week_targets(), NOT the LLM — reusing existing,
    previously-dead-code math rather than asking the LLM to redo
    arithmetic. The LLM's only job is choosing the two blocks'
    qualitative focus labels and a short rationale, informed by the
    week's theme and the goal description.

    Returns a dict shaped for save_week_plan(week_id, blocks,
    week_volume_targets, rationale, require_theme_path=...):
        {"blocks": [...], "week_volume_targets": [...], "rationale": str}
    """
    week_targets = _calculate_week_targets(month_goal, week_number, total_weeks)

    prompt = WEEK_BLOCK_PROMPT.format(
        week_theme=week_theme,
        goal_description=month_goal.get("description", "unspecified"),
        week_targets=week_targets,
    )

    try:
        result = call_structured_llm_with_reprompt(
            build_review_llm, prompt, WeekBlockOutput,
            config={"callbacks": [langfuse_handler]},
        )
        block_1_focus = result.block_1_focus
        block_2_focus = result.block_2_focus
        rationale = result.rationale
    except StructuredOutputFailed:
        logger.exception(
            "Week block focus generation failed after retry + re-prompt (week_id=%s), using theme as fallback focus",
            week_id,
        )
        block_1_focus = week_theme
        block_2_focus = week_theme
        rationale = f"Auto-generated during backfill; focus defaulted to week theme ({week_theme}) after generation failure."

    def _block_volume_targets(scope: str) -> List[dict]:
        # scope is "block_target" per _calculate_week_targets' return shape
        return [
            {"exercise": name, "unit": data["unit"], "block_target": data["block_target"]}
            for name, data in week_targets.items()
        ]

    week_volume_targets = [
        {"exercise": name, "unit": data["unit"], "block_target": data["week_target"]}
        for name, data in week_targets.items()
    ] if week_targets else None

    blocks = [
        {
            "block_number": 1,
            "dates": block_1_dates,
            "focus": block_1_focus,
            "block_volume_targets": _block_volume_targets("block_1") if week_targets else None,
        },
        {
            "block_number": 2,
            "dates": block_2_dates,
            "focus": block_2_focus,
            "block_volume_targets": _block_volume_targets("block_2") if week_targets else None,
        },
    ]

    return {
        "blocks": blocks,
        "week_volume_targets": week_volume_targets,
        "rationale": rationale,
    }




def _refresh_week_themes_impl() -> str:
    """Plain function: the actual refresh-week-themes logic. Exists
    separately from the @tool wrapper below so run_monthly_review()
    can call it directly and get real exception propagation (its own
    docstring requires this), instead of going through
    refresh_week_themes.invoke({}), which — being @mongo_guarded —
    would swallow a Mongo dependency failure into a fallback STRING
    rather than raising. Same pattern as generate_theme_path /
    build_week_blocks."""
    month_plans = get_month_plans_collection()
    current_month = _current_month_id()
    doc = month_plans.find_one({"user_id": get_current_user_id(), "month_id": current_month})

    if not doc or not doc.get("goal") or doc["goal"].get("status") != "confirmed":
        return "No confirmed goal for this month. Set a goal first."

    total_weeks = _weeks_in_month(current_month)
    prev_doc = month_plans.find_one({"user_id": get_current_user_id(), "month_id": _previous_month_id()})
    prev_close_out = (prev_doc or {}).get("close_out_summary") or {}

    week_plan_path = generate_theme_path(prev_close_out, doc["goal"], total_weeks)

    month_plans.update_one(
        {"user_id": get_current_user_id(), "month_id": current_month},
        {"$set": {
            "week_plan_path": week_plan_path,
            "updated_at": datetime.now(ZoneInfo("UTC"))
        }}
    )
    theme_str = " → ".join(t["theme"] for t in week_plan_path)
    return f"Week themes set for {current_month} ({total_weeks} weeks): {theme_str}"


@tool
@mongo_guarded
def refresh_week_themes() -> str:
    """Generate this month's week theme path. Call only when no theme path exists yet (recovery case) — not part of normal goal confirmation."""
    return _refresh_week_themes_impl()


def run_monthly_review() -> str:
    """
    Manual Streamlit admin action (not yet scheduled — see V9.4).
    sync_backlog() is deliberately unguarded here too (same reasoning as
    backlog_sync_node in agent/graph.py): a Mongo failure should halt
    this whole run rather than proceed with a stale backlog. This
    function's own try/except re-raises rather than swallowing, so the
    Streamlit component calling this is the actual boundary that should
    catch LLMCallFailed / StructuredOutputFailed / Mongo failures and
    show a plain status message instead of a stack trace.
    """
    try:
        sync_backlog()
        prev_month = _previous_month_id()
        summary = close_out_month(prev_month)
        result = refresh_week_themes()
        return f"Closed out {prev_month}.\n{result}"
    except Exception:
        logger.exception("run_monthly_review failed")
        raise


if __name__ == "__main__":
    print(run_monthly_review())