"""
Streamlit UI for V2.
 
V2 scope: a single chat box, same as V1. The agent behind it now has tool
access (search_workout_library) and Langfuse tracing, but the UI layer
itself is unchanged. Still no memory across turns/sessions.
"""

import sys
import os

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


st.set_page_config(page_title="AI Fitness Coach - V2", page_icon="🏋️")
st.title("🏋️ AI Fitness Coach (V2)")
st.caption("Version 2: tool-calling agent (search_workout_library) with Langfuse tracing. Still no memory across sessions.")


if "graph" not in st.session_state:
    st.session_state.graph = build_graph()


if "history" not in st.session_state:
    st.session_state.history = []

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
            result = st.session_state.graph.invoke(
                {"messages": [{"role": "user", "content": user_input}]}
            )
            response_text = extract_text(result["messages"][-1].content)
            st.markdown(response_text)

    st.session_state.history.append({"role": "assistant", "content": response_text})