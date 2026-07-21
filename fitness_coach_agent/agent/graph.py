"""
The agent graph itself.
"""

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage
from langfuse.langchain import CallbackHandler
from pymongo import MongoClient
from langgraph.checkpoint.mongodb import MongoDBSaver
from agent.context_trim import trim

from agent.state import CoachState
from agent.prompts import SYSTEM_PROMPT
from tools.workout_library import search_workout_library
from tools.knowledge_base import search_fitness_knowledge_base
from tools.checkins import record_checkin
from tools.checkin_history import get_recent_checkins, fetch_checkin, yesterday_str
from tools.plans import update_three_day_plan
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
    get_previous_month_review_context
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MONGO_URI = os.environ.get("MONGO_URI")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME")

LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")

langfuse_handler = CallbackHandler()



TOOLS = [
    search_workout_library,
    search_fitness_knowledge_base,
    record_checkin,
    get_recent_checkins,
    get_current_plan,
    get_past_plans,
    update_three_day_plan,
    get_backlog,
    mark_backlog_reinserted,
    log_metric,
    get_progress_summary,
    get_today_workout_status,
    get_current_week_plan,
    get_current_month_plan,
    get_previous_month_review_context,
    stage_month_goal,
    confirm_month_goal,
    update_week_plan,
]

def get_llm()->ChatGoogleGenerativeAI:
    llm = ChatGoogleGenerativeAI( model="gemini-3.5-flash", temperature=0.4, api_key=GEMINI_API_KEY)
    return llm.bind_tools(TOOLS)



def history_check_node(state: CoachState) -> dict:
    """
    Deterministic, non-LLM node. Runs before coach_node on every invoke.
    Fires the yesterday-check exactly once per session (per thread_id) -
    controlled by history_checked in state, never by LLM judgment.
    """
    if state.get("history_checked"):
        return {}

    summary = fetch_checkin(yesterday_str())
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

    tomorrow = (datetime.now(LOCAL_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
    goal_summary = get_current_goal_summary()
    week_focus = get_week_focus_for_date(tomorrow)
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
    llm = get_llm()

    system_content = SYSTEM_PROMPT
    yesterday_context = state.get("yesterday_context")
    if yesterday_context:
        system_content += f"\n\nContext from yesterday: {yesterday_context}"

    goal_context = state.get("goal_context")
    if goal_context:
        system_content += f"\n\n{goal_context}"

    messages = [SystemMessage(content=system_content)] + trim(state["messages"])
    response = llm.invoke(messages)
    return {
        "messages": [response]
    }



def build_graph():
    """
    Builds and compiles the graph:

        START -> history_check_node -> backlog_sync_node -> goal_context_node
              -> coach_node -> [tools_condition] -> tools -> coach_node -> ...
                                        |
                                        v
                                       END

    The three pre-nodes each run before coach_node on every invoke.
    history_check_node and goal_context_node are guarded by their own
    checked-flags in state (once per session). backlog_sync_node runs
    unguarded on every invoke to keep backlog fresh.

    None of them touch state["messages"], so none permanently grow the
    conversation's token footprint.

    A MongoDB-backed checkpointer persists CoachState per thread_id, so a
    conversation's message history and all checked-flags survive across
    invokes (and app restarts) as long as the same thread_id is passed in
    the invoke config.
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