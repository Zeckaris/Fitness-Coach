"""
LLM-based generation of workout plans for today and the next 3 days.

Contains the prompt assembly and structured LLM call. Database access
and persistence are handled by the caller.
"""

import logging

from langfuse.langchain import CallbackHandler

from agent.llm import build_review_llm
from agent.error_handling import call_structured_llm_with_reprompt, StructuredOutputFailed
from agent.prompts import BACKFILL_DAY_PLAN_PROMPT
from utils.exercise_ordering import reorder_phase

logger = logging.getLogger(__name__)

langfuse_handler = CallbackHandler()


def pre_schedule_goal_exercises(
    dates_to_plan: list[str],
    goal_exercise_names: list[str],
    goal_workouts: list[dict],
    user_id: str,
    week_id: str,
) -> tuple[dict[str, list[str]], str]:
    """
    Deterministically pre-schedules active goal-tracked exercises across available candidate dates.
    Uses sessions_in_week = max(1, round(4 * day_count / 7)) from tools.week_plans so the pre-scheduler
    and daily target dosing share the exact same target day calculation.
    Returns:
      (pre_scheduled_map: {date: [exercise_names]}, goal_pool_str: formatted prompt text)
    """
    if not goal_exercise_names or not dates_to_plan:
        return {}, "None for this goal."

    from utils.calendar_weeks import date_range, get_week_for_date
    from utils.exercise_ordering import _movement_family
    from db.mongo_client import get_plans_collection
    from tools.week_plans import _find_week_doc_for_date

    week_info = get_week_for_date(dates_to_plan[0])
    all_week_dates = date_range(week_info["start_date"], week_info["end_date"])
    day_count = len(all_week_dates)
    sessions_in_week = max(1, round(4 * day_count / 7))

    week_doc = _find_week_doc_for_date(dates_to_plan[0]) or {}
    day_focuses = week_doc.get("day_focuses") or {}

    def _is_rest_day(d_str: str) -> bool:
        df = day_focuses.get(d_str) or {}
        if df.get("is_rest_day"):
            return True
        focus_txt = (df.get("focus") or "").lower()
        if "rest" in focus_txt or "recovery" in focus_txt:
            return True
        return False

    candidate_dates = [d for d in dates_to_plan if not _is_rest_day(d)]
    if not candidate_dates:
        candidate_dates = list(dates_to_plan)

    candidate_len = len(candidate_dates)

    plans = get_plans_collection()
    past_docs = list(plans.find({
        "user_id": user_id,
        "date": {"$gte": week_info["start_date"], "$lt": dates_to_plan[0]},
    }))

    past_completed_counts = {name: 0 for name in goal_exercise_names}
    for doc in past_docs:
        for ex in doc.get("exercises", []):
            name = ex.get("name")
            if name in past_completed_counts:
                target = ex.get("target_quantity")
                completed_qty = ex.get("completed_quantity", 0)
                ex_status = ex.get("status", "")
                is_done = (
                    ex.get("completed") is True
                    or ex_status == "completed"
                    or (target is not None and completed_qty >= target)
                )
                if is_done:
                    past_completed_counts[name] += 1

    workout_map = {w["name"]: w for w in goal_workouts} if goal_workouts else {}

    pre_scheduled_map = {d: [] for d in dates_to_plan}
    date_family_counts = {d: {} for d in dates_to_plan}

    for name in goal_exercise_names:
        past_done = past_completed_counts.get(name, 0)
        needed = max(0, min(sessions_in_week, candidate_len) - past_done)
        if needed <= 0:
            continue

        meta = workout_map.get(name) or {}
        patterns = meta.get("movement_patterns") or []
        fam = _movement_family(patterns)

        def _date_sort_key(d: str):
            assigned_count = len(pre_scheduled_map[d])
            fam_count = date_family_counts[d].get(fam, 0)
            return (assigned_count >= 10, assigned_count, fam_count, d)

        ranked_dates = sorted(candidate_dates, key=_date_sort_key)
        selected_dates = ranked_dates[:needed]

        for d in selected_dates:
            pre_scheduled_map[d].append(name)
            date_family_counts[d][fam] = date_family_counts[d].get(fam, 0) + 1

    lines = []
    for d in dates_to_plan:
        assigned = pre_scheduled_map[d]
        if assigned:
            ex_list_str = ", ".join(assigned)
            lines.append(f"Date {d}: Must include in main phase -> {ex_list_str}")
        else:
            lines.append(f"Date {d}: No pre-assigned goal exercises (use general filler pool).")

    goal_pool_str = "\n".join(lines)
    return pre_scheduled_map, goal_pool_str


def validate_and_enforce_pre_assigned_goals(
    result,
    pre_scheduled_map: dict[str, list[str]],
    goal_workouts: list[dict],
):
    """
    Post-generation enforcement (Step 4b): Inspects generated day plans against pre_scheduled_map.
    If the LLM omitted any pre-assigned goal exercise, force-inserts it into that day's main phase.
    If insertion causes main-phase count > 12, trims the lowest-priority non-goal filler exercise.

    `result` can be either:
      - A BackfillPlanOutput (or any object with a .days attribute), or
      - A plain list of BackfillDayPlanInput objects.
    """
    if not pre_scheduled_map or not result:
        return

    # Support both BackfillPlanOutput (has .days) and plain list
    if isinstance(result, list):
        days = result
    else:
        days = getattr(result, "days", None)
    if not days:
        return

    from tools.plans import ExercisePlanItem
    workout_map = {w["name"]: w for w in goal_workouts} if goal_workouts else {}

    for day in days:
        if day.status == "rest" or not day.exercises:
            continue

        assigned = set(pre_scheduled_map.get(day.date, []))
        if not assigned:
            continue

        gen_main_names = {e.name for e in day.exercises if e.category == "main"}
        missing = assigned - gen_main_names

        if missing:
            logger.info("Enforcing pre-assigned goal exercises for date %s, missing: %s", day.date, missing)
            for m_name in missing:
                meta = workout_map.get(m_name, {})
                focus = meta.get("primary_target_area") or day.focus_area or "full_body"
                new_item = ExercisePlanItem.model_construct(
                    name=m_name,
                    focus=focus,
                    category="main",
                    sets=None,
                    reps=None,
                    equipment=meta.get("equipment") or ["none"],
                    duration_minutes=meta.get("duration_minutes") or 5,
                    target_quantity=None,
                    unit=None,
                    completed_quantity=0,
                    completed=False,
                    set_group_id=None,
                )
                day.exercises.append(new_item)

            # Re-evaluate target dosing for any force-inserted exercise (best-effort — may
            # not apply if day is being built via model_construct without full field population)
            try:
                day._validate_status_and_phases()
            except Exception:
                pass  # Skip dosing re-evaluation if validation fails (e.g. in test context)

            # Trim non-goal filler exercises if main phase > 12
            main_items = [e for e in day.exercises if e.category == "main"]
            if len(main_items) > 12:
                overflow = len(main_items) - 12
                fillers_to_trim = [e for e in reversed(day.exercises) if e.category == "main" and e.name not in assigned]
                for f_item in fillers_to_trim[:overflow]:
                    day.exercises.remove(f_item)


def generate_backfill_days(
    dates_to_plan: list,
    goal_description: str,
    week_focus: str,
    backlog_items: str,
    past_plans_context: str,
    knowledge_context: str,
    available_exercises: str,
    pre_scheduled_map: dict = None,
    goal_workouts: list = None,
):
    """
    Generates workout plans for the specified dates (today through Saturday)
    using a structured LLM call.

    Assembles the prompt from the provided context and returns a validated
    BackfillPlanOutput with exercises within each day's main phase reordered
    to avoid consecutive same-group (primary_target_area / movement_family)
    exercises.

    Raises:
        StructuredOutputFailed: If structured output generation fails.
    """
    from tools.plans import BackfillPlanOutput, ExercisePlanItem, split_exercise_sets

    dates_to_plan_str = ", ".join(dates_to_plan)
    num_days = len(dates_to_plan)

    prompt = BACKFILL_DAY_PLAN_PROMPT.format(
        num_days=num_days,
        dates_to_plan_str=dates_to_plan_str,
        goal_description=goal_description,
        week_focus=week_focus,
        backlog_items=backlog_items,
        past_plans_context=past_plans_context,
        knowledge_context=knowledge_context,
        available_exercises=available_exercises,
    )

    try:
        result = call_structured_llm_with_reprompt(
            build_review_llm, prompt, BackfillPlanOutput,
            config={"callbacks": [langfuse_handler]},
        )
    except StructuredOutputFailed:
        logger.exception(
            "Backfill day-plan generation failed after retry + re-prompt (dates=%s)",
            dates_to_plan_str,
        )
        raise

    # Step 4b: Post-generation enforcement of pre-assigned goal exercises
    if pre_scheduled_map and goal_workouts:
        validate_and_enforce_pre_assigned_goals(result, pre_scheduled_map, goal_workouts)

    # Step 6 & 7: Split main-phase exercises exceeding max_consecutive_sets and reorder
    # exercises within each day's main phase to form circuit rotation.
    for day in result.days:
        if day.exercises:
            ex_dicts = [e.model_dump() for e in day.exercises]
            split_dicts = split_exercise_sets(ex_dicts)
            reordered_dicts = reorder_phase(split_dicts)
            day.exercises = [ExercisePlanItem.model_construct(**ex) for ex in reordered_dicts]

    return result