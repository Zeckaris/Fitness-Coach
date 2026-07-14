"""
CoachState — the data contract for our agent's graph.

V5 scope: adds two fields.

- history_checked: per-session flag set by history_check_node once it has
  pulled yesterday's check-in into context. Prevents re-fetching on every
  turn 
- yesterday_context: the formatted summary fetched by history_check_node,
  read by coach_node to append to the system prompt for that turn. This is
  transient scratch data for prompt construction, not a durable record -
  the actual check-in data lives in MongoDB.
"""

from langgraph.graph.message import add_messages
from typing import TypedDict, Annotated, Optional

class CoachState(TypedDict):
    
    messages : Annotated[list, add_messages]
    history_checked: bool
    yesterday_context: Optional[str]
    