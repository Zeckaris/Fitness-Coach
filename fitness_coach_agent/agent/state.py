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

V7 scope: adds four more fields, same shape as the V5 pair, for the two
new deterministic pre-nodes.

- goal_context_checked: per-session flag set by goal_context_node once
  it has pulled the current goal + week focus into context.
- goal_context: the formatted summary fetched by goal_context_node, read
  by coach_node to append to the system prompt for that turn. Same
  transient-scratch-data role as yesterday_context - the actual goal and
  week plan data live in MongoDB (month_plans, week_plans).

V8 scope: removes backlog_synced. backlog_sync_node runs unguarded on every
invoke; the flag served no purpose.
"""

from langgraph.graph.message import add_messages
from typing import TypedDict, Annotated, Optional

class CoachState(TypedDict):

    messages : Annotated[list, add_messages]
    history_checked: bool
    yesterday_context: Optional[str]
    goal_context_checked: bool
    goal_context: Optional[str]