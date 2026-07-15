"""
The agent graph itself.

V5 scope: adds proper multi-turn memory via a MongoDB-backed checkpointer
(keyed by thread_id), plus a new deterministic node, history_check_node,
that runs before coach_node on every invoke. On the first turn of a
session (history_checked=False in state), it fetches yesterday's check-in
directly from Mongo (no LLM involved) and folds it into the system prompt
for that turn only - it is never written into state["messages"], keeping
permanent context growth in check. On every later turn, it's a no-op
passthrough.

get_recent_checkins is also bound as a fourth LLM tool (alongside V2's
search_workout_library, V3's search_fitness_knowledge_base, and V4's
record_checkin), for the separate case of the user asking about a date
further back than yesterday - that's genuine LLM judgment, unlike the
guaranteed yesterday-check which is graph-enforced and never left to the
model to remember.

Every graph run is traced to Langfuse (LLM calls, tool calls, full agent
loop) so we can see which tool(s) (if any) the agent chose and why.
"""

import os
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage
from langfuse.langchain import CallbackHandler
from pymongo import MongoClient
from langgraph.checkpoint.mongodb import MongoDBSaver

from agent.state import CoachState
from agent.prompts import SYSTEM_PROMPT
from tools.workout_library import search_workout_library
from tools.knowledge_base import search_fitness_knowledge_base
from tools.checkins import record_checkin
from tools.checkin_history import get_recent_checkins, fetch_checkin, yesterday_str
from tools.plans import update_three_day_plan
from tools.plan_history import get_current_plan, get_past_plans

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
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
    update_three_day_plan]

def get_llm()->ChatGoogleGenerativeAI:
    llm = ChatGoogleGenerativeAI( model="gemini-2.5-flash", temperature=0.4, api_key=GEMINI_API_KEY)
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




def coach_node(state:CoachState)->dict:
    llm = get_llm()

    system_content = SYSTEM_PROMPT
    yesterday_context = state.get("yesterday_context")
    if yesterday_context:
        system_content += f"\n\nContext from yesterday: {yesterday_context}"

    messages = [SystemMessage(content=system_content)] + state["messages"]
    response = llm.invoke(messages)
    return {
        "messages": [response]
    }
    
    

def build_graph():
    """
    Builds and compiles the graph:

        START -> history_check_node -> coach_node -> [tools_condition] -> tools -> coach_node -> ...
                                                              |
                                                              v
                                                             END

    history_check_node runs once per session (guarded by history_checked in
    state) and injects yesterday's check-in into coach_node's system prompt
    for that turn - it never touches state["messages"], so it doesn't
    permanently grow the conversation's token footprint.

    A MongoDB-backed checkpointer persists CoachState per thread_id, so a
    conversation's message history and history_checked flag survive across
    invokes (and app restarts) as long as the same thread_id is passed in
    the invoke config.
    """
    
    workflow= StateGraph(CoachState)
    # add nodes
    workflow.add_node("history_check_node", history_check_node)
    workflow.add_node("coach_node", coach_node)
    workflow.add_node("tools", ToolNode(TOOLS))
    #add edges
    workflow.add_edge(START, "history_check_node")
    workflow.add_edge("history_check_node", "coach_node")
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