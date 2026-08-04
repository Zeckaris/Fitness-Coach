"""Streamlit UI"""
import sys
import os
import uuid

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["STREAMLIT_WATCHER_TYPE"] = "none"

from dotenv import load_dotenv
load_dotenv()

import streamlit as st

from agent.graph import build_graph
from db.mongo_client import get_plans_collection
from agent.monthly_review import run_monthly_review
from app.components.auth_ui import ensure_logged_in, render_logout_button
from app.components.dashboard_cards import (
    render_progress_dashboard,
    render_month_goal,
    render_week_plan,
    render_upcoming_plans,
)
from app.components.workout_session import render_workout_session, get_today_str
from app.components.chat import render_chat
from auth.context import get_current_user_id


def start_new_conversation():
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.history = []


#  Main App 

st.set_page_config(page_title="AI Fitness Coach", page_icon="🏋️", layout="wide")
ensure_logged_in()

if "graph" not in st.session_state:
    st.session_state.graph = build_graph()
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "history" not in st.session_state:
    st.session_state.history = []
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "Chat"


with st.sidebar:
    st.button("🆕 New Conversation", on_click=start_new_conversation)
    render_logout_button()
    st.caption(f"Session: {st.session_state.thread_id[:8]}")
    st.divider()
    st.markdown("**Review Pipelines**")

    if st.button("📅 Set Week Themes", help="Run the monthly review pipeline to set week themes for the current month"):
            try:
                result = run_monthly_review()
                st.success(result)
            except Exception:
                st.error(
                    "Monthly review couldn't be completed right now — please try again in a moment."
                )
            st.rerun()
            
    st.divider()
    st.markdown("**Quick Actions**")
    if st.button("🗑️ Clear Today's Plan", use_container_width=True):
        collection = get_plans_collection()
        collection.delete_one({"user_id": get_current_user_id(), "date": get_today_str()})
        st.session_state.workout_state = {
            "status": "idle", "current_exercise_idx": 0,
            "current_set": 1, "timer_end": None, "rest_seconds": 30,
        }
        st.success("Today's plan cleared!")
        st.rerun()

# Dashboard row
dash_col1, dash_col2, dash_col3 = st.columns([1, 1, 1])
with dash_col1:
    render_progress_dashboard()
with dash_col2:
    render_month_goal()
with dash_col3:
    render_week_plan()

st.divider()

# Main tabs
tabs = st.tabs(["💬 Chat", "🏋️ Today's Workout", "📅 Upcoming Plans"])

with tabs[0]:
    render_chat()

with tabs[1]:
    render_workout_session()

with tabs[2]:
    render_upcoming_plans()