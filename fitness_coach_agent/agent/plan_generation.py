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

logger = logging.getLogger(__name__)

langfuse_handler = CallbackHandler()


def generate_backfill_days(
    today_date: str,
    tomorrow_date: str,
    day_plus_2_date: str,
    day_plus_3_date: str,
    goal_description: str,
    week_focus: str,
    backlog_items: str,
    past_plans_context: str,
    knowledge_context: str,
    available_exercises: str,
):
    """
    Generates workout plans for today and the next 3 days using a
    structured LLM call.

    Assembles the prompt from the provided context and returns a validated
    BackfillPlanOutput.

    Raises:
        StructuredOutputFailed: If structured output generation fails.
    """
    from tools.plans import BackfillPlanOutput

    prompt = BACKFILL_DAY_PLAN_PROMPT.format(
        today_date=today_date,
        tomorrow_date=tomorrow_date,
        day_plus_2_date=day_plus_2_date,
        day_plus_3_date=day_plus_3_date,
        goal_description=goal_description,
        week_focus=week_focus,
        backlog_items=backlog_items,
        past_plans_context=past_plans_context,
        knowledge_context=knowledge_context,
        available_exercises=available_exercises,
    )

    try:
        return call_structured_llm_with_reprompt(
            build_review_llm, prompt, BackfillPlanOutput,
            config={"callbacks": [langfuse_handler]},
        )
    except StructuredOutputFailed:
        logger.exception(
            "Backfill day-plan generation failed after retry + re-prompt (today=%s)",
            today_date,
        )
        raise