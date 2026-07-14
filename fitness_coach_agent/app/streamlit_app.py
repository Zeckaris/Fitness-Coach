"""
Streamlit UI for V5.

V5 scope: the UI now has a real notion of "session" - each browser session
gets a thread_id (a UUID), passed to the graph on every invoke via
config={"configurable": {"thread_id": ...}}. Combined with the MongoDB
checkpointer now attached in agent/graph.py, this means:
- Multi-turn memory actually works: the graph itself remembers earlier
  turns in this thread, not just what's displayed on screen.
- A new "New Conversation" sidebar button starts a fresh thread_id, which
  starts the agent fresh too - including re-triggering history_check_node
  (V5's deterministic yesterday-check-in pull) for the new session.

Since the checkpointer already accumulates messages per thread_id, we only
ever need to pass the NEW user message into invoke() - not the full
history - the graph loads prior state itself using thread_id.
"""

import sys
import os
import uuid

# Streamlit adds this script's own directory (app/) to sys.path, not the
# project root - so without this, `agent` is not importable. We add the
# parent directory (project root) explicitly.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# GEMINI_API_KEY (and LANGFUSE_* keys) from the environment at import time.
load_dotenv()

import streamlit as st
from agent.graph import build_graph

def extract_text(content) -> str:
    """
    Gemini (via langchain-google-genai) can return `.content` as either:
      - a plain string, or
      - a list of content blocks, e.g. [{"type": "text", "text": "...", "extras": {...}}]
    This normalizes both cases into a plain string for display.
    """
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


def start_new_conversation():
    """Resets the session to a fresh thread_id and clears displayed history."""
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.history = []


st.set_page_config(page_title="AI Fitness Coach - V5", page_icon="🏋️")
st.title("🏋️ AI Fitness Coach (V5)")
st.caption(
    "Version 5: multi-turn memory within a conversation (MongoDB-backed), "
    "plus automatic recall of yesterday's check-in at the start of each "
    "new conversation."
)

if "graph" not in st.session_state:
    st.session_state.graph = build_graph()

if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())

if "history" not in st.session_state:
    st.session_state.history = []

with st.sidebar:
    st.button("🆕 New Conversation", on_click=start_new_conversation)
    st.caption(f"Session: {st.session_state.thread_id[:8]}")

# Render existing history
for msg in st.session_state.history:
    role = "user" if msg["role"] == "user" else "assistant"
    with st.chat_message(role):
        st.markdown(msg["content"])

# Chat input
user_input = st.chat_input("How's your day going? Any updates that affect your fitness?")

if user_input:
    # Show the user's message immediately
    st.session_state.history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            config = {"configurable": {"thread_id": st.session_state.thread_id}}
            result = st.session_state.graph.invoke(
                {"messages": [{"role": "user", "content": user_input}]},
                config=config,
            )
            response_text = extract_text(result["messages"][-1].content)
            st.markdown(response_text)

    st.session_state.history.append({"role": "assistant", "content": response_text})