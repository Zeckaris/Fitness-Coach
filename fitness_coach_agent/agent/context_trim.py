"""
agent/context_trim.py

V6.2 — view-time trimming of state["messages"] for coach_node's LLM call.

This module is a pure function: deterministic, no LLM call, no side effects,
never mutates its input. It is recomputed fresh on every coach_node call from
the untouched state["messages"]; nothing is ever written back to state or to
the MongoDB checkpointer.
"""

import re
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage


UNTRIMMED_TOOLS = {
    "record_checkin",
    "update_three_day_plan",
    "get_recent_checkins",
}


def _placeholder_for(tool_name: str, args: dict, content: str) -> str:
    """
    Build a short synthetic placeholder for a trim-eligible tool result.

    This is a full replacement of the content, not a truncation - the
    original text is discarded entirely and replaced with a fabricated
    sentence built from cheap identifiers (names/dates/query+sources),
    so the model retains awareness the call happened without carrying
    the payload forward.
    """
    if tool_name == "search_workout_library":
        names = [n.strip() for n in re.findall(r"^- ([^(]+) \(", content, flags=re.MULTILINE)]
        shown = ", ".join(names) if names else "no matches"
        return (
            f"[Earlier search_workout_library call: suggested {shown} - "
            f"full details already delivered to user]"
        )

    if tool_name == "search_fitness_knowledge_base":
        query = args.get("query", "unknown query")
        sources = sorted(set(re.findall(r"^\[From: ([^,\]]+)", content, flags=re.MULTILINE)))
        source_str = ", ".join(sources) if sources else "no sources"
        return (
            f'[Earlier search_fitness_knowledge_base call: queried "{query}" - '
            f"results from {source_str} - full content already delivered to user]"
        )

    if tool_name in ("get_current_plan", "get_past_plans"):
        dates = re.findall(r"^(\d{4}-\d{2}-\d{2}):", content, flags=re.MULTILINE)
        date_str = ", ".join(dates) if dates else "no entries"
        return f"[Earlier {tool_name} call: checked {date_str}]"

    # Fallback for any future tool not yet given a specific placeholder format.
    return f"[Earlier {tool_name} call: result already delivered to user]"


def _is_concluded(turn: list) -> bool:
    """
    A turn is concluded once it ends in a final AIMessage with no further
    tool_calls (i.e. tools_condition routed to END for that turn).
    """
    last = turn[-1]
    return isinstance(last, AIMessage) and not getattr(last, "tool_calls", None)


def _trim_turn(turn: list) -> list:
    """
    Walk one concluded turn's messages. Whenever a tool-call AIMessage is
    found, its paired ToolMessage(s) are either kept as-is (untrimmed
    tools) or replaced in place with a synthetic placeholder (trim-eligible
    tools). The tool-call AIMessage itself is never modified - its
    tool_calls[].id must stay stable, since the replacement ToolMessage
    still needs to match it via tool_call_id. HumanMessages and final-reply
    AIMessages pass through unchanged.
    """
    out = []
    i = 0
    while i < len(turn):
        msg = turn[i]

        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            call_by_id = {tc["id"]: tc for tc in msg.tool_calls}

            j = i + 1
            paired = []
            while (
                j < len(turn)
                and isinstance(turn[j], ToolMessage)
                and turn[j].tool_call_id in call_by_id
            ):
                paired.append(turn[j])
                j += 1

            out.append(msg)  # tool-call request: always kept, never rewritten
            for tm in paired:
                call = call_by_id.get(tm.tool_call_id, {})
                tool_name = call.get("name", getattr(tm, "name", ""))

                if tool_name in UNTRIMMED_TOOLS:
                    out.append(tm)  # kept exactly as-is
                else:
                    placeholder = _placeholder_for(tool_name, call.get("args", {}), tm.content)
                    out.append(
                        ToolMessage(
                            content=placeholder,
                            tool_call_id=tm.tool_call_id,
                            name=tool_name,
                        )
                    )
            i = j
        else:
            out.append(msg)
            i += 1

    return out


def trim(messages: list) -> list:
    """
    Build a reduced view of state["messages"] for one LLM call.

    - Segments messages into turns, split on HumanMessage boundaries.
    - The last turn is left completely untouched if it has not concluded
      yet (mid tool-loop within the current user message) - trimming an
      in-progress turn's own tool results would break the reasoning that
      is actively using them, not just save tokens on a future unrelated
      turn.
    - Every other (concluded) turn is passed through _trim_turn, which
      replaces trim-eligible ToolMessage content in place while leaving
      HumanMessages, tool-call AIMessages, and final-reply AIMessages
      untouched.

    Never mutates `messages`. Never writes anything back to state - this
    is purely the view assembled for a single LLM call, same pattern as
    yesterday_context in history_check_node.
    """
    turns = []
    current = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            if current:
                turns.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        turns.append(current)

    result = []
    last_idx = len(turns) - 1
    for idx, turn in enumerate(turns):
        if idx == last_idx and not _is_concluded(turn):
            result.extend(turn)
        else:
            result.extend(_trim_turn(turn))

    return result