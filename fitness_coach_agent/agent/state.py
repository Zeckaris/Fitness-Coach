"""
CoachState — the data contract for our agent's graph.
"""

from langgraph.graph.message import add_messages
from typing import TypedDict, Annotated, Optional

class CoachState(TypedDict):

    messages : Annotated[list, add_messages]
    history_checked: bool
    yesterday_context: Optional[str]
    goal_context_checked: bool
    goal_context: Optional[str]