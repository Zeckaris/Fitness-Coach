"""Chat interface: renders history and drives the agent graph per turn."""

import logging

import streamlit as st

logger = logging.getLogger(__name__)

COACH_FALLBACK_MESSAGE = (
    "I'm having trouble getting things ready right now — please try again in a moment."
)


def extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)


def _run_turn(user_text: str) -> str:
    """
    Invoke the graph for one turn. Catches any exception that propagates
    up from a "critical, halt" node (backlog_sync_node, goal_context_node
    — see agent/graph.py) or anywhere else in the graph that isn't
    already handled internally (e.g. coach_node's own LLMCallFailed
    handling). This is the boundary the V9.2 design doc describes for
    those nodes: render a coach-voice fallback directly in the UI
    without ever reaching coach_node.
    """
    config = {"configurable": {"thread_id": st.session_state.thread_id}}
    try:
        result = st.session_state.graph.invoke(
            {"messages": [{"role": "user", "content": user_text}]},
            config=config,
        )
        return extract_text(result["messages"][-1].content)
    except Exception:
        logger.exception("Graph invocation failed for thread_id=%s", st.session_state.thread_id)
        return COACH_FALLBACK_MESSAGE


def render_chat():
    for msg in st.session_state.history:
        role = "user" if msg["role"] == "user" else "assistant"
        with st.chat_message(role):
            st.markdown(msg["content"])

    if st.session_state.get("pending_message"):
        msg = st.session_state.pending_message
        st.session_state.pending_message = None
        st.session_state.history.append({"role": "user", "content": msg})
        with st.chat_message("user"):
            st.markdown(msg)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                response_text = _run_turn(msg)
                st.markdown(response_text)

        st.session_state.history.append({"role": "assistant", "content": response_text})
        st.rerun()

    user_input = st.chat_input("How's your day going? Any updates that affect your fitness?")

    if user_input:
        st.session_state.history.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                response_text = _run_turn(user_input)
                st.markdown(response_text)

        st.session_state.history.append({"role": "assistant", "content": response_text})
        st.rerun()