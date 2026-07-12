"""
CoachState — the data contract for our agent's graph.
 
V1 scope: we ONLY track conversation messages. No plans, no logs, no memory
across sessions yet — that's V3/V4 territory. Keeping this minimal on purpose
so the graph in this version is as simple as possible.
"""

from langgraph.graph.message import add_messages
from typing import TypedDict, Annotated

class CoachState(TypedDict):
    
    messages : Annotated[list, add_messages]
    