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
from agent.week_setup import run_week_setup
from app.components.auth_ui import ensure_logged_in, render_logout_button
from app.components.onboarding_ui import ensure_onboarded, render_baseline_upgrade_prompt
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
ensure_onboarded()

if "graph" not in st.session_state:
    st.session_state.graph = build_graph()
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "history" not in st.session_state:
    st.session_state.history = []
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "Chat"
if "week_setup_running" not in st.session_state:
    st.session_state.week_setup_running = False


with st.sidebar:
    st.button("🆕 New Conversation", on_click=start_new_conversation)
    render_logout_button()
    st.caption(f"Session: {st.session_state.thread_id[:8]}")
    st.divider()
    st.markdown("**Review Pipelines**")

    setup_result = st.session_state.pop("week_setup_result", None)
    if setup_result:
        if setup_result["ok"]:
            st.success(setup_result["message"])
        elif setup_result["themes_saved"]:
            st.warning(setup_result["message"])
        else:
            st.error(setup_result["message"])

    if st.button(
        "📅 Set Week Themes",
        help="Save this month's week themes and prepare any missing daily plans for this week",
        disabled=st.session_state.week_setup_running,
    ):
        st.session_state.week_setup_running = True
        try:
            with st.status("Setting week themes...", expanded=True) as status:
                def _on_progress(label: str):
                    status.update(label=label)
                    status.write(label)

                setup_result = run_week_setup(on_progress=_on_progress)
                status.update(
                    label=setup_result["message"],
                    state="complete" if setup_result["plans_complete"] else "error",
                )
            st.session_state.week_setup_result = setup_result
        except Exception:
            st.session_state.week_setup_result = {
                "ok": False,
                "themes_saved": False,
                "plans_complete": False,
                "message": (
                    "Weekly setup couldn't be completed right now — please try again "
                    "in a moment."
                ),
            }
        finally:
            st.session_state.week_setup_running = False
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

# Optional upgrade prompt for existing users with older baseline data
render_baseline_upgrade_prompt()

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