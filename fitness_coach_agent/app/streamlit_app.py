"""
Streamlit UI for V6.

V6 scope: adds a live view of the forward-looking 3-day plan (tomorrow,
day+2, day+3) alongside the chat, rendered as cards via
streamlit-antd-components instead of a plain table.

The plan is read DIRECTLY from MongoDB here, not through the agent/graph -
same principle as the chat history already being state Streamlit owns
itself.

Fix: Streamlit only re-executes the script top-to-bottom on the next
interaction, not automatically after invoke() returns within the same
run - so the plan section (rendered near the top) was showing stale data
fetched before that turn's tool calls ran. We now force a fresh rerun
right after the assistant's turn completes, so the top-of-script plan
fetch picks up whatever update_three_day_plan just wrote, without needing
a manual page refresh.
"""

import sys
import os
import uuid
from datetime import datetime, timedelta


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import streamlit as st
import streamlit_antd_components as sac

from agent.graph import build_graph
from db.mongo_client import get_plans_collection
from tools.plans import LOCAL_TZ, DEFAULT_USER_ID


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


def get_forward_plan_docs() -> list:
    """
    Fetches the raw plan documents for tomorrow, day+2, day+3 directly
    from MongoDB, as (date, doc) pairs. doc is None for a day with no
    entry yet.
    """
    collection = get_plans_collection()
    today = datetime.now(LOCAL_TZ).date()
    docs = []
    for offset in (1, 2, 3):
        date = (today + timedelta(days=offset)).strftime("%Y-%m-%d")
        doc = collection.find_one({"user_id": DEFAULT_USER_ID, "date": date})
        docs.append((date, doc))
    return docs


def render_plan(docs: list):
    """Renders the 3-day plan as a stepper header + one card per day."""
    st.subheader("📅 Your Upcoming Plan")

    step_items = []
    for date, doc in docs:
        if not doc:
            label = "Not planned"
        elif doc.get("status") == "rest":
            label = "Rest"
        else:
            label = doc.get("focus_area", "Planned").replace("_", " ").title()
        step_items.append(sac.StepsItem(title=date, description=label))

    sac.steps(items=step_items, format_func="title", size="sm", return_index=False)

    cols = st.columns(3)
    for col, (date, doc) in zip(cols, docs):
        with col:
            with st.container(border=True):
                if not doc:
                    st.markdown(f"**{date}**")
                    st.caption("Not planned yet.")
                    continue

                if doc.get("status") == "rest":
                    st.markdown("### 😌 Rest Day")
                    st.caption(date)
                else:
                    focus = doc.get("focus_area", "—").replace("_", " ").title()
                    duration = doc.get("duration_minutes")
                    st.markdown(f"### 💪 {focus}")
                    caption = date if duration is None else f"{date} • {duration} min"
                    st.caption(caption)

                    for ex in doc.get("exercises") or []:
                        sets = ex.get("sets", "?")
                        reps = ex.get("reps", "?")
                        st.markdown(f"- **{ex['name']}** — {sets}x{reps}")

                if doc.get("avoid_body_parts"):
                    st.caption(f"Avoiding: {', '.join(doc['avoid_body_parts'])}")
                if doc.get("notes"):
                    st.info(doc["notes"])


st.set_page_config(page_title="AI Fitness Coach - V6", page_icon="🏋️", layout="wide")
st.title("🏋️ AI Fitness Coach (V6)")
st.caption(
    "Version 6: real 3-day plan generation - the coach builds and patches "
    "your upcoming workouts (tomorrow, day+2, day+3), shown below."
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

render_plan(get_forward_plan_docs())
st.divider()

# Render existing history
for msg in st.session_state.history:
    role = "user" if msg["role"] == "user" else "assistant"
    with st.chat_message(role):
        st.markdown(msg["content"])


user_input = st.chat_input("How's your day going? Any updates that affect your fitness?")

if user_input:
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
    st.rerun()