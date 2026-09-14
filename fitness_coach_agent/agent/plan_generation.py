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


def generate_backfill_days(
    dates_to_plan: list,
    goal_description: str,
    week_focus: str,
    backlog_items: str,
    past_plans_context: str,
    knowledge_context: str,
    available_exercises: str,
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

    # Split main-phase exercises exceeding max_consecutive_sets and reorder
    # exercises within each day's main phase to form circuit rotation.
    # Pydantic validation and dose overrides (sets/reps via get_theme_dosing_structure)
    # have already run inside BackfillDayPlanInput._validate_status_and_phases;
    # we reconstruct ExercisePlanItem objects from the already-validated dicts so
    # those validators do not run a second time and the overrides are preserved.
    for day in result.days:
        if day.exercises:
            ex_dicts = [e.model_dump() for e in day.exercises]
            split_dicts = split_exercise_sets(ex_dicts)
            reordered_dicts = reorder_phase(split_dicts)
            day.exercises = [ExercisePlanItem.model_construct(**ex) for ex in reordered_dicts]

    return result