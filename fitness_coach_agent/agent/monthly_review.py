"""Monthly review pipeline — V8."""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


from pydantic import BaseModel, Field, model_validator
import logging
from langfuse.langchain import CallbackHandler
from langchain_core.tools import tool

from db.mongo_client import get_month_plans_collection
from db.guards import mongo_guarded
from tools.backlog import sync_backlog
from tools.week_plans import _calculate_week_targets
from utils.calendar_weeks import (
    weeks_in_month,
    compute_month_weeks,
    current_month_id,
    LOCAL_TZ,
)
from tools.progress import calculate_progress
from agent.prompts import MONTHLY_REVIEW_PROMPT, THEME_PATH_PROMPT
from auth.context import get_current_user_id
from agent.llm import build_review_llm
from agent.error_handling import call_structured_llm_with_reprompt, StructuredOutputFailed
from utils.theme_defaults import default_theme_path
from utils.baseline_targets import WeekThemeEnum

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
    week_number: int = Field(ge=1, description="1-indexed week number within the month.")
    theme: WeekThemeEnum


class ThemePathOutput(BaseModel):
    week_plan_path: List[WeekThemeOutput]

    @model_validator(mode="after")
    def _validate_week_numbers(self) -> "ThemePathOutput":
        nums = [w.week_number for w in self.week_plan_path]
        expected = set(range(1, len(self.week_plan_path) + 1))
        if set(nums) != expected:
            raise ValueError(
                f"Invalid week_number values in theme path: {nums}. "
                f"Must be a contiguous 1-indexed sequence from 1 to {len(self.week_plan_path)} with no gaps, duplicates, or out-of-range numbers."
            )
        return self

def generate_theme_path(
    prev_close_out: dict,
    current_goal: dict,
    total_weeks: int,
    week_descriptors: List[dict] | None = None,
) -> tuple[List[dict], str]:
    """
    Returns (week_plan_path, theme_path_source).

    theme_path_source values:
      "llm"                      -- LLM produced a valid, complete path
      "fallback_length_mismatch" -- LLM returned wrong number of themes
      "fallback_parse_failure"   -- StructuredOutputFailed

    The caller is responsible for writing theme_path_source alongside
    week_plan_path in the month document.
    """
    # Build week structure context for the LLM if descriptors are available.
    if week_descriptors:
        week_lines = "\n".join(
            f"  Week {w['week_number']}: {w['start_date']} to {w['end_date']} ({w['day_count']} days)"
            for w in week_descriptors
        )
        week_structure_note = (
            f"\nWeek structure for this month:\n{week_lines}\n"
            "For short boundary weeks (3-4 days), prefer Foundation or Deload. "
            "Avoid Peak or Volume for very short weeks."
        )
    else:
        week_structure_note = ""

    prompt = THEME_PATH_PROMPT.format(
        total_weeks=total_weeks,
        goal_description=current_goal.get("description", "unspecified"),
        last_month_narrative=prev_close_out.get("narrative", "No prior review available."),
        last_month_adherence=prev_close_out.get("adherence"),
        week_structure_note=week_structure_note,
    )

    try:
        result = call_structured_llm_with_reprompt(
            build_review_llm, prompt, ThemePathOutput,
            config={"callbacks": [langfuse_handler]},
        )
        themes = sorted(result.week_plan_path, key=lambda t: t.week_number)
        if len(themes) != total_weeks:
            logger.warning(
                "Theme path generation returned %d themes, expected %d "
                "(total_weeks=%s), using fallback",
                len(themes), total_weeks, total_weeks,
            )
            return default_theme_path(total_weeks), "fallback_length_mismatch"
        return [{"week_number": i + 1, "theme": t.theme} for i, t in enumerate(themes)], "llm"
    except StructuredOutputFailed:
        logger.exception(
            "Theme path generation failed after retry + re-prompt "
            "(total_weeks=%s), using fallback",
            total_weeks,
        )
        return default_theme_path(total_weeks), "fallback_parse_failure"




def build_week_plan_data(
    week_id: str,
    week_dates: List[str],
    week_theme: str,
    month_goal: dict,
    week_number: int,
    total_weeks: int,
) -> dict:
    """
    Plain function (no decorators): builds week plan structure for the
    V9.3 backfill path. Mirrors generate_theme_path's shape — pure
    logic, no Mongo I/O, no @mongo_guarded.
    The caller (generate_today_plan, in tools/plans.py) does its own
    single @mongo_guarded wrap and its own write to week_plans_collection
    — this function never touches Mongo directly.

    Hard numbers (per-exercise week/daily volume targets) come from
    _calculate_week_targets(). The qualitative focus simply reuses
    the week's theme directly, no LLM call required.

    Returns a dict shaped for save_week_plan(week_id, focus,
    daily_volume_targets, week_volume_targets, rationale=...):
        {"focus": str, "daily_volume_targets": [...], "week_volume_targets": [...], "rationale": str}
    """
    day_count = len(week_dates)
    week_targets = _calculate_week_targets(
        month_goal, week_number, total_weeks,
        week_id=week_id, day_count=day_count,
    )

    daily_volume_targets = []
    for date_str in week_dates:
        targets_for_day = [
            {"exercise": name, "unit": data["unit"], "daily_target": data["daily_target"]}
            for name, data in week_targets.items()
        ]
        daily_volume_targets.append({"date": date_str, "targets": targets_for_day})

    week_volume_targets = [
        {"exercise": name, "unit": data["unit"], "week_target": data["week_target"]}
        for name, data in week_targets.items()
    ] if week_targets else None

    return {
        "focus": week_theme,
        "daily_volume_targets": daily_volume_targets,
        "week_volume_targets": week_volume_targets,
        "rationale": f"Auto-generated during backfill; focus set to week theme ({week_theme}).",
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
    current_month = current_month_id()
    doc = month_plans.find_one({"user_id": get_current_user_id(), "month_id": current_month})

    if not doc or not doc.get("goal") or doc["goal"].get("status") != "confirmed":
        return "No confirmed goal for this month. Set a goal first."

    total_weeks = weeks_in_month(current_month)
    week_descriptors = compute_month_weeks(current_month)
    prev_doc = month_plans.find_one({"user_id": get_current_user_id(), "month_id": _previous_month_id()})
    prev_close_out = (prev_doc or {}).get("close_out_summary") or {}

    week_plan_path, source = generate_theme_path(
        prev_close_out, doc["goal"], total_weeks,
        week_descriptors=week_descriptors,
    )

    month_plans.update_one(
        {"user_id": get_current_user_id(), "month_id": current_month},
        {"$set": {
            "week_plan_path": week_plan_path,
            "theme_path_source": source,
            "updated_at": datetime.now(ZoneInfo("UTC")),
        }}
    )
    theme_str = " → ".join(t["theme"] for t in week_plan_path)
    return (
        f"Week themes set for {current_month} ({total_weeks} weeks): {theme_str} "
        f"[source: {source}]"
    )


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
        result = _refresh_week_themes_impl()
        return f"Closed out {prev_month}.\n{result}"
    except Exception:
        logger.exception("run_monthly_review failed")
        raise


if __name__ == "__main__":
    print(run_monthly_review())