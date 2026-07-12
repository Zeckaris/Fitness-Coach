"""
The agent graph itself.
 
V2 scope: adds tool-calling (the ReAct loop). The coach's LLM is now bound
to search_workout_library, so it can decide per-message whether to call the
tool or just respond directly. A conditional edge checks the LLM's output:
if it requested a tool call, route to the "tools" node, run the tool, feed
the result back to the coach, and repeat - until the LLM responds with
plain text instead of a tool call, at which point we're done.
"""
import os
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage

from agent.state import CoachState
from agent.prompts import SYSTEM_PROMPT
from tools.workout_library import search_workout_library

GEMINI_API_KEY= os.environ.get("GEMINI_API_KEY")

# All tools available to the coach.
TOOLS = [search_workout_library]

def get_llm()->ChatGoogleGenerativeAI:
    llm = ChatGoogleGenerativeAI( model="gemini-3.5-flash", temperature=0.4, api_key=GEMINI_API_KEY)
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
    return compiled_workflow

