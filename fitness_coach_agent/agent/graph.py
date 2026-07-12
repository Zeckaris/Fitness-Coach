"""
The agent graph itself.
 
V1 scope: one node, one straight edge. No tools, no conditional routing,
no loops. 
"""

import os
from langgraph.graph import StateGraph, START, END
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage

from agent.state import CoachState
from agent.prompts import SYSTEM_PROMPT

GEMINI_API_KEY= os.environ.get("GEMINI_API_KEY")

def get_llm()->ChatGoogleGenerativeAI:
    llm = ChatGoogleGenerativeAI( model="gemini-3.5-flash", temperature=0.4, api_key=GEMINI_API_KEY)
    return llm

def coach_node(state:CoachState)->dict:
    llm = get_llm()
    messages=[SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response= llm .invoke(messages)
    return {
        "messages" : [response]
    }

def build_graph():
    """
    Builds and compiles the graph: START -> coach -> END.
    Returns a runnable graph object (`.invoke(...)` / `.stream(...)`).
    """
    
    workflow= StateGraph(CoachState)
    # add nodes
    workflow.add_node("coach_node", coach_node)
    #add edges
    workflow.add_edge(START, "coach_node")
    workflow.add_edge("coach_node", END)
    compiled_workflow= workflow.compile()
    return compiled_workflow

