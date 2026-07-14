"""
The agent graph itself.

V4 scope: adds a third tool, record_checkin, alongside V3's
search_workout_library and search_fitness_knowledge_base. The coach's LLM
is now bound to all three, so in a single turn it can look up a concrete
exercise, search the knowledge base, persist a check-in to MongoDB, any
combination of these, or just respond directly. The conditional
agent<->tools loop from V2 already handles any number of bound tools - no
structural graph changes needed here, same as when V3 added its second
tool.

Every graph run is traced to Langfuse (LLM calls, tool calls, full agent
loop) so we can see which tool(s) (if any) the agent chose and why.
"""
import os
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage
from langfuse.langchain import CallbackHandler

from agent.state import CoachState
from agent.prompts import SYSTEM_PROMPT
from tools.workout_library import search_workout_library
from tools.knowledge_base import search_fitness_knowledge_base
from tools.checkins import record_checkin

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

langfuse_handler = CallbackHandler()



TOOLS = [search_workout_library, search_fitness_knowledge_base, record_checkin]

def get_llm()->ChatGoogleGenerativeAI:
    llm = ChatGoogleGenerativeAI( model="gemini-2.5-flash", temperature=0.4, api_key=GEMINI_API_KEY)
    return llm.bind_tools(TOOLS)

def coach_node(state:CoachState)->dict:
    llm = get_llm()
    messages=[SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response= llm .invoke(messages)
    return {
        "messages" : [response]
    }

def build_graph():
    """
    Builds and compiles the graph:
        START -> coach_node -> [tools_condition] -> tools -> coach_node -> ...
                                       |
                                       v
                                      END

    tools_condition inspects the last message coach_node produced: if it
    contains tool calls, route to "tools"; if it's a plain text response,
    route to END. After 'tools' runs, we always loop back to coach_node so
    it can see the tool's result and either respond or call another tool."""
    
    workflow= StateGraph(CoachState)
    # add nodes
    workflow.add_node("coach_node", coach_node)
    workflow.add_node("tools", ToolNode(TOOLS))
    #add edges
    workflow.add_edge(START, "coach_node")
    workflow.add_conditional_edges(
        "coach_node",
        tools_condition,
        {"tools": "tools", END: END},
    )
    workflow.add_edge("tools", "coach_node")
    compiled_workflow= workflow.compile()
    
    
    
    return compiled_workflow.with_config({"callbacks": [langfuse_handler]})