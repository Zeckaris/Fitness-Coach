"""Monthly review pipeline — V8."""

import calendar
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

from db.mongo_client import get_month_plans_collection, get_plans_collection
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
from tools.user_profile import get_progression_multipliers, set_progression_multipliers

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

langfuse_handler = CallbackHandler()

# ---------------------------------------------------------------------------
# Deterministic progression-overload constants.
# Both are named module-level so they can be adjusted in one place.
#   CAP  = 2.0  — doubling the original baseline is the natural ceiling;
#                 beyond this a reassessment is the right lever.
#   FLOOR = 0.5 — halving gives meaningful relief without being demoralising;
#                 further reduction is better handled via coaching context.
# ---------------------------------------------------------------------------
PROGRESSION_MULTIPLIER_CAP: float = 2.0
PROGRESSION_MULTIPLIER_FLOOR: float = 0.5

# Minimum number of goal-tracked exercise occurrences in the closed month
# before an area's result is trusted enough to change its multiplier.
# Below this we hold: too little data must never trigger an increase or
# decrease. Four occurrences is roughly one week of a typical goal exercise.
MIN_PROGRESSION_DATA_POINTS: int = 4


def _previous_month_id() -> str:
    today = datetime.now(LOCAL_TZ).date()
    first_day = today.replace(day=1)
    prev_month = first_day - timedelta(days=1)
    return prev_month.strftime("%Y-%m")


def _month_date_bounds(month_id: str) -> tuple[str, str]:
    """Return the inclusive first/last YYYY-MM-DD dates for a month_id."""
    year, month = map(int, month_id.split("-"))
    last_day = calendar.monthrange(year, month)[1]
    return f"{month_id}-01", f"{month_id}-{last_day:02d}"


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


def _is_exercise_complete(ex: dict) -> bool:
    """Mirror of tools/progress._is_exercise_complete — kept private here
    so monthly_review has no import dependency on that module's internals."""
    target = ex.get("target_quantity")
    if target is not None:
        return ex.get("completed_quantity", 0) >= target
    return ex.get("completed", False)


def _calculate_per_area_adherence(goal: dict, month_id: Optional[str] = None) -> dict:
    """
    Compute adherence per balance area over the month being closed, scoped
    exclusively to goal-tracked exercises (i.e. exercises present in
    goal["volume_targets"]).

    Filler exercises are intentionally excluded — they are not subject to
    progression multipliers, so counting them here would dilute the signal.

    Returns a dict keyed by balance_area:
        {"upper_body": {"planned_count": N, "completed_count": M, "adherence_pct": P|None}, ...}

    Only areas that appear in the goal's volume_targets are returned; areas
    with no goal-tracked exercises are omitted entirely (the caller skips them).

    adherence_pct is None when planned_count == 0 for that area during the
    month. The caller treats None as insufficient data and holds.

    month_id defaults to the current local month for standalone callers;
    close_out_month always passes the month being closed so adherence cannot
    silently drift to the seven days before today.
    """
    volume_targets = goal.get("volume_targets") or []
    if not volume_targets:
        return {}

    # Build exercise -> balance_area lookup from goal
    exercise_to_area: dict = {}
    for vt in volume_targets:
        ex_name = vt.get("exercise")
        ba = vt.get("balance_area")
        if ex_name and ba:
            exercise_to_area[ex_name] = ba

    if not exercise_to_area:
        return {}

    # Identify which areas actually have tracked exercises
    areas_in_goal: set = set(exercise_to_area.values())

    plans = get_plans_collection()
    target_month = month_id or current_month_id()
    month_start, month_end = _month_date_bounds(target_month)

    docs = plans.find({
        "user_id": get_current_user_id(),
        "status": {"$in": ["planned", "completed"]},
        "date": {"$gte": month_start, "$lte": month_end},
    })

    # Initialise counters only for areas present in the goal
    area_planned: dict = {area: 0 for area in areas_in_goal}
    area_completed: dict = {area: 0 for area in areas_in_goal}

    for doc in docs:
        is_plan_completed = doc.get("status") == "completed"
        for ex in doc.get("exercises", []):
            name = ex.get("name")
            area = exercise_to_area.get(name)
            if area is None:
                continue  # filler or non-goal exercise — correctly excluded
            area_planned[area] += 1
            if is_plan_completed or _is_exercise_complete(ex):
                area_completed[area] += 1

    result = {}
    for area in areas_in_goal:
        planned = area_planned[area]
        completed = area_completed[area]
        pct = (completed / planned * 100) if planned else None
        result[area] = {
            "planned_count": planned,
            "completed_count": completed,
            "adherence_pct": pct,
        }

    return result


def _adjust_user_baselines(progress: dict, goal: dict, month_id: Optional[str] = None) -> list:
    """
    Deterministic, LLM-free progression-overload adjustment.

    For each balance area that has at least one goal-tracked exercise this
    month, computes:
      - area_volume_pct  : mean of that area's volume_progress[].pct values
      - area_adherence_pct: per-area adherence over the month being closed
                            (goal-tracked exercises only)

    Rules (applied in order):
      HOLD     : fewer than MIN_PROGRESSION_DATA_POINTS goal-tracked exercise
                 occurrences, or no volume/adherence result -> no change
      INCREASE : area_volume_pct >= 85 AND area_adherence_pct >= 70
                 -> multiply by 1.1, capped at PROGRESSION_MULTIPLIER_CAP
      DECREASE : area_volume_pct < 50  OR  area_adherence_pct < 40
                 -> multiply by 0.9, floored at PROGRESSION_MULTIPLIER_FLOOR
      HOLD     : otherwise (no change)

    Insufficient data always holds. None volume/adherence values are never
    treated as 0% because "no evidence" is not evidence of failure.

    Persists updated multipliers to user_profile before returning so that the
    subsequent _generate_review / coaching_context generation can reference
    the change if needed.

    Returns a list of dicts — one per processed area — for inclusion in
    close_out_summary["progression_adjustments"]:
        [
          {
            "area": str,
            "old_multiplier": float,
            "new_multiplier": float,
            "reason": str,   # human-readable for recap
            "action": "increase" | "decrease" | "hold",
            "area_volume_pct": float | None,
            "area_adherence_pct": float | None,
          },
          ...
        ]

    Areas with no goal-tracked exercises are silently skipped (no entry in
    the returned list and multiplier unchanged).
    """
    volume_targets = goal.get("volume_targets") or []
    if not volume_targets:
        logger.info("_adjust_user_baselines: no volume_targets in goal, skipping.")
        return []

    # Build exercise -> balance_area from the goal
    exercise_to_area: dict = {}
    for vt in volume_targets:
        ex_name = vt.get("exercise")
        ba = vt.get("balance_area")
        if ex_name and ba:
            exercise_to_area[ex_name] = ba

    # Group volume_progress by area
    area_volume_pcts: dict = {}
    for vp in (progress.get("volume_progress") or []):
        area = exercise_to_area.get(vp.get("exercise"))
        if area is None:
            continue
        pct = vp.get("pct")
        area_volume_pcts.setdefault(area, []).append(pct)

    # Per-area adherence over the month being closed (goal-tracked exercises)
    per_area_adherence = _calculate_per_area_adherence(goal, month_id)

    # Read current multipliers (lazy-defaulted to 1.0 for all missing areas)
    current_multipliers = get_progression_multipliers()
    updated_multipliers = dict(current_multipliers)  # mutable copy

    adjustments = []

    # Only process areas that have at least one goal-tracked exercise
    areas_with_exercises = set(area_volume_pcts.keys())

    for area in areas_with_exercises:
        pct_list = area_volume_pcts[area]
        valid_pcts = [pct for pct in pct_list if pct is not None]
        area_volume_pct = (sum(valid_pcts) / len(valid_pcts)) if valid_pcts else None

        adherence_stats = per_area_adherence.get(area, {})
        area_adherence_pct = adherence_stats.get("adherence_pct")
        planned_count = adherence_stats.get("planned_count") or 0

        old_multiplier = current_multipliers.get(area, 1.0)

        # Insufficient data must hold. Never infer failure from missing data.
        if (
            planned_count < MIN_PROGRESSION_DATA_POINTS
            or area_volume_pct is None
            or area_adherence_pct is None
        ):
            new_multiplier = old_multiplier
            action = "hold"
            volume_label = f"{area_volume_pct:.1f}%" if area_volume_pct is not None else "n/a"
            adherence_label = (
                f"{area_adherence_pct:.1f}%" if area_adherence_pct is not None else "n/a"
            )
            reason = (
                f"insufficient data (planned_count={planned_count} < "
                f"{MIN_PROGRESSION_DATA_POINTS}, volume_pct={volume_label}, "
                f"adherence_pct={adherence_label}) \u2192 hold"
            )

        # Apply progression rule
        elif area_volume_pct >= 85.0 and area_adherence_pct >= 70.0:
            raw = old_multiplier * 1.1
            new_multiplier = min(raw, PROGRESSION_MULTIPLIER_CAP)
            action = "increase"
            reason = (
                f"volume_pct={area_volume_pct:.1f}% \u2265 85 AND "
                f"adherence_pct={area_adherence_pct:.1f}% \u2265 70 "
                f"\u2192 x1.1"
                + (f" (capped at {PROGRESSION_MULTIPLIER_CAP})" if new_multiplier < raw else "")
            )
        elif area_volume_pct < 50.0 or area_adherence_pct < 40.0:
            raw = old_multiplier * 0.9
            new_multiplier = max(raw, PROGRESSION_MULTIPLIER_FLOOR)
            action = "decrease"
            triggers = []
            if area_volume_pct < 50.0:
                triggers.append(f"volume_pct={area_volume_pct:.1f}% < 50")
            if area_adherence_pct < 40.0:
                triggers.append(f"adherence_pct={area_adherence_pct:.1f}% < 40")
            reason = (
                " OR ".join(triggers) + f" \u2192 x0.9"
                + (f" (floored at {PROGRESSION_MULTIPLIER_FLOOR})" if new_multiplier > raw else "")
            )
        else:
            new_multiplier = old_multiplier
            action = "hold"
            reason = (
                f"volume_pct={area_volume_pct:.1f}% in [50,85) AND "
                f"adherence_pct={area_adherence_pct:.1f}% in [40,70) \u2192 hold"
            )

        updated_multipliers[area] = new_multiplier

        adjustments.append({
            "area": area,
            "old_multiplier": round(old_multiplier, 6),
            "new_multiplier": round(new_multiplier, 6),
            "action": action,
            "reason": reason,
            "area_volume_pct": round(area_volume_pct, 2) if area_volume_pct is not None else None,
            "area_adherence_pct": (
                round(area_adherence_pct, 2) if area_adherence_pct is not None else None
            ),
        })

        logger.info(
            "_adjust_user_baselines: area=%s action=%s old=%.4f new=%.4f (%s)",
            area, action, old_multiplier, new_multiplier, reason,
        )

    # Persist the updated multipliers before the LLM call so coaching_context
    # can optionally reference the change.
    set_progression_multipliers(updated_multipliers)

    return adjustments


@mongo_guarded
def close_out_month(month_id: str) -> dict:
    """
    Compute final adherence/metric delta, then LLM-generate narrative + coaching context.

    Idempotent: if the month already has close_out_summary, returns it
    unchanged without re-applying progression adjustments.

    @mongo_guarded per V9.2: hard-fails cleanly on a Mongo dependency
    failure (no silent partial write — the guard stops the function
    before update_one runs if find_one already failed, or before any
    later step if update_one itself fails). NOTE: on that failure path
    this returns MONGO_FALLBACK_MESSAGE (a str), not the usual dict
    shape ({"status": ...} / {"narrative": ..., ...}). Callers that read
    fields off the result — including the scheduler — must handle the
    str case explicitly.
    """
    month_plans = get_month_plans_collection()
    doc = month_plans.find_one({"user_id": get_current_user_id(), "month_id": month_id})
    if not doc:
        return {"status": "no_doc"}

    # Idempotency: a month that already has a close-out summary must not apply
    # its progression adjustments again. Return the stored summary unchanged.
    if doc.get("close_out_summary") is not None:
        logger.info("close_out_month: %s already closed, returning existing summary.", month_id)
        return doc["close_out_summary"]

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

    # --- Deterministic progression adjustment (no LLM) ---
    # Runs after calculate_progress() and before _generate_review() so that:
    #   1. Multipliers are persisted to user_profile before the LLM call,
    #      allowing coaching_context to reference them if needed.
    #   2. progression_adjustments is embedded in close_out_summary for
    #      future user-facing recap and audit purposes.
    adjustments = _adjust_user_baselines(progress, goal, month_id) if progress else []

    review = _generate_review(summary)
    summary["narrative"] = review["narrative"]
    summary["coaching_context"] = review["coaching_context"]
    summary["progression_adjustments"] = adjustments

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

    # If confirm_month_goal already wrote a Foundation-first path (mid-month
    # start), preserve it. Re-running the LLM here would replace a correctly
    # sequenced path with one that may ignore the user's actual start point.
    if doc.get("theme_path_source") == "foundation_first_on_confirm" and doc.get("week_plan_path"):
        existing_themes = " → ".join(t["theme"] for t in doc["week_plan_path"])
        n_weeks = len(doc["week_plan_path"])
        return (
            f"Week themes already set for {current_month} ({n_weeks} weeks): "
            f"{existing_themes} [source: foundation_first_on_confirm]"
        )
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