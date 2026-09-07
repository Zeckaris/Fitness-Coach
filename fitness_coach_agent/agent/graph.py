"""
The agent graph itself.
"""

import os
import logging
from datetime import datetime, timedelta

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_core.messages import SystemMessage, AIMessage
from langfuse.langchain import CallbackHandler
from pymongo import MongoClient
from langgraph.checkpoint.mongodb import MongoDBSaver
from agent.context_trim import trim

from agent.state import CoachState
from agent.prompts import SYSTEM_PROMPT
from agent.llm import build_coach_llm
from agent.error_handling import call_llm_with_retry, LLMCallFailed
from tools.workout_library import search_workout_library
from tools.knowledge_base import search_fitness_knowledge_base
from tools.checkins import record_checkin
from tools.checkin_history import get_recent_checkins, fetch_checkin, yesterday_str
from tools.plans import update_daily_plans, generate_today_plan
from tools.plan_history import get_current_plan, get_past_plans
from tools.backlog import sync_backlog, get_backlog, mark_backlog_reinserted
from tools.metrics import log_metric
from tools.progress import get_progress_summary
from tools.today_status import get_today_workout_status
from tools.week_plans import get_current_week_plan, get_week_focus_for_date, update_week_plan
from tools.month_plans import (
    get_current_month_plan,
    get_current_goal_summary,
    stage_month_goal,
    confirm_month_goal,
    calculate_volume_target,
    get_previous_month_review_context
)
from tools.user_profile import set_equipment_override
from agent.monthly_review import refresh_week_themes
from utils.calendar_weeks import LOCAL_TZ

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY_2")
MONGO_URI = os.environ.get("MONGO_URI")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME")

langfuse_handler = CallbackHandler()


TOOLS = [
    search_workout_library,
    search_fitness_knowledge_base,
    record_checkin,
    get_recent_checkins,
    get_current_plan,
    get_past_plans,
    update_daily_plans,
    get_backlog,
    mark_backlog_reinserted,
    log_metric,
    get_progress_summary,
    get_today_workout_status,
    get_current_week_plan,
    get_current_month_plan,
    get_previous_month_review_context,
    stage_month_goal,
    calculate_volume_target, 
    confirm_month_goal,
    update_week_plan,
    generate_today_plan,
    refresh_week_themes,
    set_equipment_override,
]

def _coach_llm_factory(api_key: str):
    return build_coach_llm(api_key).bind_tools(TOOLS)

def history_check_node(state: CoachState) -> dict:
    """
    Fetches yesterday's check-in context once per session.

    Returns the retrieved context if available. On failure, marks the
    history check as complete and continues without yesterday context.
    """
    if state.get("history_checked"):
        return {}

    try:
        summary = fetch_checkin(yesterday_str())
    except Exception:
        logger.exception("history_check_node: fetch_checkin failed, continuing without yesterday context")
        return {
            "history_checked": True,
            "yesterday_context": None,
        }

    return {
        "history_checked": True,
        "yesterday_context": summary,
    }


def backlog_sync_node(state: CoachState) -> dict:
    """
    Deterministic, non-LLM node. Runs sync_backlog() on EVERY invoke -
    no guard. Backlog must stay fresh because the user may have completed
    exercises since the last turn. Pure side effect against the backlog
    collection, no LLM, no prompt injection.
    """
    sync_backlog()
    return {}


def goal_context_node(state: CoachState) -> dict:
    if state.get("goal_context_checked"):
        return {}

    today_str = datetime.now(LOCAL_TZ).date().strftime("%Y-%m-%d")
    tomorrow = (datetime.now(LOCAL_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
    goal_summary = get_current_goal_summary()
    week_focus = get_week_focus_for_date(today_str) or get_week_focus_for_date(tomorrow)
    prev_review = get_previous_month_review_context()

    parts = []
    if goal_summary:
        parts.append(f"Current goal: {goal_summary}")
    if week_focus:
        parts.append(f"This week's focus: {week_focus}")
    if prev_review:
        parts.append(f"Last month's review context: {prev_review}")

    return {
        "goal_context_checked": True,
        "goal_context": ". ".join(parts) if parts else None,
    }



def coach_node(state: CoachState) -> dict:
    system_content = SYSTEM_PROMPT
    yesterday_context = state.get("yesterday_context")
    if yesterday_context:
        system_content += f"\n\nContext from yesterday: {yesterday_context}"

    goal_context = state.get("goal_context")
    if goal_context:
        system_content += f"\n\n{goal_context}"

    messages = [SystemMessage(content=system_content)] + trim(state["messages"])

    try:
        response = call_llm_with_retry(_coach_llm_factory, messages)
    except LLMCallFailed:
        response = AIMessage(
            content="I'm having trouble thinking this through right now — give me a moment and try again."
        )

    return {"messages": [response]}



def build_graph():
    """
    Builds and compiles the LangGraph workflow.

    The workflow runs the history, backlog, and goal context nodes before
    the coach node, then loops between the coach and tool nodes as needed.
    Uses a MongoDB-backed checkpointer to persist conversation state.
    """

    workflow= StateGraph(CoachState)
    # add nodes
    workflow.add_node("history_check_node", history_check_node)
    workflow.add_node("backlog_sync_node", backlog_sync_node)
    workflow.add_node("goal_context_node", goal_context_node)
    workflow.add_node("coach_node", coach_node)
    workflow.add_node("tools", ToolNode(TOOLS))
    #add edges
    workflow.add_edge(START, "history_check_node")
    workflow.add_edge("history_check_node", "backlog_sync_node")
    workflow.add_edge("backlog_sync_node", "goal_context_node")
    workflow.add_edge("goal_context_node", "coach_node")
    workflow.add_conditional_edges(
        "coach_node",
        tools_condition,
        {"tools": "tools", END: END},
    )
    workflow.add_edge("tools", "coach_node")

    mongo_client = MongoClient(MONGO_URI)
    checkpointer = MongoDBSaver(mongo_client, db_name=MONGO_DB_NAME)
    compiled_workflow = workflow.compile(checkpointer=checkpointer)



    return compiled_workflow.with_config({"callbacks": [langfuse_handler]})