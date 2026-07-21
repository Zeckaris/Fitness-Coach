"""Streamlit UI"""
import sys
import os
import uuid
import subprocess
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["STREAMLIT_WATCHER_TYPE"] = "none"

from dotenv import load_dotenv
load_dotenv()

import streamlit as st
import streamlit_antd_components as sac
from streamlit.components.v1 import html

from agent.graph import build_graph
from db.mongo_client import get_plans_collection, get_month_plans_collection
from tools.plans import LOCAL_TZ, DEFAULT_USER_ID
from tools.progress import calculate_progress
from tools.month_plans import format_month_plan, _current_month_id
from tools.week_plans import format_week_plan, _find_week_doc_for_date
from agent.monthly_review import run_monthly_review



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


def start_new_conversation():
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.history = []


def get_today_str() -> str:
    return datetime.now(LOCAL_TZ).date().strftime("%Y-%m-%d")


def get_forward_plan_docs() -> list:
    collection = get_plans_collection()
    today = datetime.now(LOCAL_TZ).date()
    docs = []
    for offset in (1, 2, 3):
        date = (today + timedelta(days=offset)).strftime("%Y-%m-%d")
        doc = collection.find_one({"user_id": DEFAULT_USER_ID, "date": date})
        docs.append((date, doc))
    return docs


def get_today_plan() -> dict | None:
    """Fetch today's plan if it exists."""
    collection = get_plans_collection()
    return collection.find_one({"user_id": DEFAULT_USER_ID, "date": get_today_str()})


def create_today_plan(exercises: list, focus_area: str = "full_body", duration_minutes: int = 45) -> dict:
    """Create or overwrite today's plan directly in the UI."""
    collection = get_plans_collection()
    today = get_today_str()
    doc = {
        "user_id": DEFAULT_USER_ID,
        "date": today,
        "focus_area": focus_area,
        "status": "planned",
        "duration_minutes": duration_minutes,
        "exercises": exercises,
        "notes": "Generated from workout session UI",
        "created_at": datetime.now(ZoneInfo("UTC")),
        "updated_at": datetime.now(ZoneInfo("UTC")),
    }
    collection.update_one(
        {"user_id": DEFAULT_USER_ID, "date": today},
        {"$set": doc},
        upsert=True,
    )
    return doc


def update_today_exercise_completion(exercise_idx: int, field: str, value):
    collection = get_plans_collection()
    today = get_today_str()
    collection.update_one(
        {"user_id": DEFAULT_USER_ID, "date": today},
        {"$set": {f"exercises.{exercise_idx}.{field}": value}},
    )


def finish_today_plan():
    """Mark today's plan as completed."""
    collection = get_plans_collection()
    today = get_today_str()
    collection.update_one(
        {"user_id": DEFAULT_USER_ID, "date": today},
        {"$set": {"status": "completed", "completed_at": datetime.now(ZoneInfo("UTC"))}},
    )



def render_progress_dashboard():
    data = calculate_progress()
    adherence = data["adherence"]

    with st.container(border=True):
        st.markdown("### 📊 Progress")

        # Adherence section
        if adherence["adherence_pct"] is None:
            st.caption("No exercises tracked in the last 7 days.")
        else:
            st.caption(f"Last {adherence['window_days']} days adherence")
            st.progress(
                adherence["adherence_pct"] / 100,
                text=f"{adherence['completed_count']}/{adherence['planned_count']} exercises ({adherence['adherence_pct']:.0f}%)",
            )

        # Volume progress (only if confirmed goal exists)
        if data["has_confirmed_goal"] and data["volume_progress"]:
            st.markdown("**Monthly Volume**")
            for vp in data["volume_progress"]:
                pct = vp["pct"] or 0
                st.progress(
                    min(pct / 100, 1.0),
                    text=f"{vp['exercise']}: {vp['completed_so_far']}/{vp['month_target']} {vp['unit']} ({pct:.0f}%)",
                )

        # Metric progress
        mp = data.get("metric_progress")
        if mp and mp.get("pct") is not None:
            st.markdown("**Metric**")
            st.caption(
                f"{mp['metric_name'].replace('_', ' ').title()}: "
                f"{mp['latest_value']} {mp.get('unit', '')} ({mp['pct']:.0f}% to target)"
            )

        # Fallback if nothing to show
        if not data["has_confirmed_goal"] and adherence["adherence_pct"] is None:
            st.caption("No confirmed goal this month.")


def render_month_goal():
    collection = get_month_plans_collection()
    doc = collection.find_one({"user_id": DEFAULT_USER_ID, "month_id": _current_month_id()})

    with st.container(border=True):
        st.markdown("### 🎯 Monthly Goal")

        goal = doc.get("goal") if doc else None
        if not goal:
            st.info("No goal set this month. Ask the coach to set one.")
            return

        status = goal.get("status", "unknown")
        description = goal.get("description", "Unspecified")

        # Status badge
        if status == "confirmed":
            st.markdown(f"**{description}**")
        elif status == "pending":
            st.markdown(f"**{description}**")
            st.warning("Pending confirmation")
        else:
            st.info(description)

        # Metric target (if set)
        metric_name = goal.get("metric_name")
        if metric_name and goal.get("target_value") is not None:
            baseline = goal.get("baseline_value")
            target = goal.get("target_value")
            unit = goal.get("unit", "")
            st.caption(f"📏 {metric_name.replace('_', ' ').title()}: {baseline} → {target} {unit}")

        # Volume targets
        volume_targets = goal.get("volume_targets") or []
        if volume_targets:
            st.markdown("**Volume Targets**")
            for vt in volume_targets:
                exercise = vt.get("exercise", "?")
                area = vt.get("balance_area", "").replace("_", " ").title()
                month_target = vt.get("month_target", 0)
                unit = vt.get("unit", "")
                st.caption(f"• {exercise} ({area}): {month_target} {unit}")

        # Week themes
        themes = doc.get("week_plan_path") or []
        if themes:
            st.markdown("**Week Themes**")
            theme_text = " → ".join(
                f"W{t['week_number']}: {t['theme']}" for t in themes
            )
            st.caption(theme_text)


def render_week_plan():
    today_str = datetime.now(LOCAL_TZ).date().strftime("%Y-%m-%d")
    doc = _find_week_doc_for_date(today_str)

    with st.container(border=True):
        st.markdown("### 📆 Week Plan")

        if not doc:
            st.caption("No week plan yet. Ask the coach to generate one.")
            return

        blocks = doc.get("blocks") or []
        if not blocks:
            st.caption("No blocks defined.")
            return

        # Find the current block (the one containing today)
        current_block = None
        next_block = None
        for block in blocks:
            dates = block.get("dates", [])
            if today_str in dates:
                current_block = block
            elif dates and dates[0] > today_str:
                next_block = block

        # If today isn't in any block, show the most recent one
        if not current_block:
            current_block = blocks[-1]

        block_num = current_block.get("block_number", "?")
        total_blocks = len(blocks)
        focus = current_block.get("focus", "Unspecified").replace("_", " ").title()
        dates = current_block.get("dates", [])
        date_range = f"{dates[0]} — {dates[-1]}" if dates else ""

        st.markdown(f"**Block {block_num}/{total_blocks}: {focus}**")
        if date_range:
            st.caption(date_range)

        # Volume targets for this block
        volume_targets = current_block.get("block_volume_targets") or []
        if volume_targets:
            for vt in volume_targets:
                exercise = vt.get("exercise", "?")
                target = vt.get("block_target", "?")
                unit = vt.get("unit", "")
                st.caption(f"• {exercise}: {target} {unit}")

        # Hint about next block
        if next_block:
            next_dates = next_block.get("dates", [])
            next_focus = next_block.get("focus", "").replace("_", " ").title()
            next_start = next_dates[0] if next_dates else ""
            st.caption(f"Next: Block {next_block.get('block_number', '?')} starts {next_start} — {next_focus}")


# Upcoming Plans (Read-Only)

def render_upcoming_plans():
    docs = get_forward_plan_docs()
    st.subheader("📅 Upcoming Plans")

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
                        name = ex.get("name", "Exercise")
                        sets = ex.get("sets", "?")
                        reps = ex.get("reps", "?")
                        target_qty = ex.get("target_quantity")
                        unit = ex.get("unit", "")
                        cat = ex.get("category", "main")
                        cat_emoji = {"warmup": "🔥", "main": "💪", "cooldown": "🧘"}.get(cat, "💪")

                        if target_qty is not None:
                            st.markdown(f"{cat_emoji} **{name}** — {sets}x{reps} (target: {target_qty} {unit})")
                        else:
                            st.markdown(f"{cat_emoji} **{name}** — {sets}x{reps}")

                if doc.get("avoid_body_parts"):
                    st.caption(f"Avoiding: {', '.join(doc['avoid_body_parts'])}")
                if doc.get("notes"):
                    st.info(doc["notes"])


#  Workout Session Flow 
def render_workout_session():
    """Guided workout session for today's plan."""

    # Initialize session state for workout flow
    if "workout_state" not in st.session_state:
        st.session_state.workout_state = {
            "status": "idle",
            "current_exercise_idx": 0,
            "current_set": 1,
            "timer_end": None,
            "rest_seconds": 30,
        }

    ws = st.session_state.workout_state
    today_plan = get_today_plan()

    st.subheader("🏋️ Today's Workout")

    # Workout already completed today
    if today_plan and today_plan.get("status") == "completed":
        completed_at = today_plan.get("completed_at")
        focus = today_plan.get("focus_area", "full body")
        exercises = today_plan.get("exercises", [])
        ex_count = len(exercises)
        time_str = ""
        if completed_at:
            if hasattr(completed_at, "strftime"):
                time_str = f" at {completed_at.strftime('%I:%M %p')}"
            else:
                time_str = ""
        st.success(f"You've completed your workout for today!{time_str}")
        st.caption(f"Focus: {focus.replace('_', ' ').title()} — {ex_count} exercises")
        return

    # Rest day
    if today_plan and today_plan.get("status") == "rest":
        st.info("Today is a rest day. Recover and come back strong!")
        return

    # No plan exists yet
    if not today_plan or today_plan.get("status") != "planned":
        st.info("No workout planned for today yet.")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("🤖 Ask Coach for Today's Workout", use_container_width=True):
                st.session_state.active_tab = "Chat"
                st.session_state.pending_message = "Generate my workout plan for today with warmup, main work, and cooldown exercises."
                st.rerun()

        with col2:
            if st.button("⚡ Quick Start (Template)", use_container_width=True):
                template_exercises = [
                    {"name": "Joint Mobility Warm-Up", "focus": "full_body", "category": "warmup", "sets": 1, "reps": "5-10 min", "duration_minutes": 5, "completed": False, "completed_quantity": 0},
                    {"name": "Arm Circles", "focus": "shoulders", "category": "warmup", "sets": 2, "reps": "30 sec", "duration_minutes": 2, "completed": False, "completed_quantity": 0},
                    {"name": "Push-Ups", "focus": "chest", "category": "main", "sets": 3, "reps": "10-12", "duration_minutes": 5, "completed": False, "completed_quantity": 0},
                    {"name": "Plank", "focus": "abs", "category": "main", "sets": 3, "reps": "30 sec", "duration_minutes": 3, "completed": False, "completed_quantity": 0},
                    {"name": "Bodyweight Squats", "focus": "quads", "category": "main", "sets": 3, "reps": "12-15", "duration_minutes": 5, "completed": False, "completed_quantity": 0},
                    {"name": "Bird Dog", "focus": "abs", "category": "main", "sets": 3, "reps": "10 per side", "duration_minutes": 4, "completed": False, "completed_quantity": 0},
                    {"name": "Static Stretch", "focus": "full_body", "category": "cooldown", "sets": 1, "reps": "5 min", "duration_minutes": 5, "completed": False, "completed_quantity": 0},
                ]
                create_today_plan(template_exercises, focus_area="full_body", duration_minutes=30)
                st.success("Template workout created!")
                st.rerun()

        return

    # Plan exists — show session flow
    exercises = today_plan.get("exercises", [])
    if not exercises:
        st.warning("Today's plan has no exercises.")
        return

    total_exercises = len(exercises)
    current_idx = ws["current_exercise_idx"]

    # Workout completed
    if ws["status"] == "completed" or current_idx >= total_exercises:
        st.balloons()
        st.success("🎉 Workout Complete! Great job!")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 Log to Check-In", use_container_width=True):
                finish_today_plan()
                st.success("Workout logged!")
                st.session_state.workout_state = {
                    "status": "idle", "current_exercise_idx": 0,
                    "current_set": 1, "timer_end": None, "rest_seconds": 30,
                }
                st.rerun()
        with col2:
            if st.button("🔄 Start New Workout", use_container_width=True):
                st.session_state.workout_state = {
                    "status": "idle", "current_exercise_idx": 0,
                    "current_set": 1, "timer_end": None, "rest_seconds": 30,
                }
                st.rerun()
        return

    # Progress bar
    progress = current_idx / total_exercises
    st.progress(progress, text=f"Exercise {current_idx + 1} of {total_exercises}")

    # Current exercise
    ex = exercises[current_idx]
    name = ex.get("name", "Exercise")
    sets = ex.get("sets", 1)
    reps = ex.get("reps", "?")
    duration = ex.get("duration_minutes")
    category = ex.get("category", "main")
    description = ex.get("description", "")

    cat_labels = {"warmup": "🔥 WARM-UP", "main": "💪 MAIN WORK", "cooldown": "🧘 COOL-DOWN"}
    cat_color = {"warmup": "#f97316", "main": "#3b82f6", "cooldown": "#22c55e"}

    # Category header
    st.markdown(f"### {cat_labels.get(category, '💪')} — {name}")

    # Exercise card
    with st.container(border=True):
        col_info, col_action = st.columns([2, 1])

        with col_info:
            st.markdown(f"**Sets:** {sets}  " + chr(10) + f"**Reps/Duration:** {reps}")
            if description:
                st.caption(description)

            if ws["status"] == "exercising" or ws["status"] == "idle":
                st.markdown(f"**Set {ws['current_set']} of {sets}**")

        with col_action:
            is_time_based = duration is not None and ("sec" in str(reps).lower() or "min" in str(reps).lower())

            if ws["status"] == "idle":
                if is_time_based:
                    if st.button("▶️ START TIMER", use_container_width=True, type="primary"):
                        ws["status"] = "exercising"
                        timer_duration = duration * 60 if "min" in str(reps).lower() else duration
                        ws["timer_end"] = time.time() + timer_duration
                        st.rerun()
                else:
                    if st.button("✅ COMPLETE SET", use_container_width=True, type="primary"):
                        ws["status"] = "resting"
                        ws["rest_seconds"] = 30
                        ws["timer_end"] = time.time() + ws["rest_seconds"]

                        if ws["current_set"] >= sets:
                            update_today_exercise_completion(current_idx, "completed", True)
                            update_today_exercise_completion(current_idx, "completed_quantity", ex.get("target_quantity", 0) or sets)

                        st.rerun()

            elif ws["status"] == "exercising" and is_time_based:
                remaining = max(0, ws["timer_end"] - time.time())
                mins, secs = divmod(int(remaining), 60)

                timer_html = f"""
                <div style="text-align: center; padding: 20px;">
                    <div style="font-size: 48px; font-weight: bold; color: {cat_color.get(category, '#3b82f6')}; font-family: monospace;">
                        {mins:02d}:{secs:02d}
                    </div>
                    <div style="font-size: 14px; color: #6b7280; margin-top: 8px;">
                        Keep going!
                    </div>
                </div>
                """
                html(timer_html, height=120)

                if remaining <= 0:
                    ws["status"] = "resting"
                    ws["rest_seconds"] = 30
                    ws["timer_end"] = time.time() + ws["rest_seconds"]
                    update_today_exercise_completion(current_idx, "completed", True)
                    st.rerun()
                else:
                    time.sleep(0.5)
                    st.rerun()

            elif ws["status"] == "resting":
                remaining = max(0, ws["timer_end"] - time.time())

                if remaining > 0:
                    st.markdown(f"**Rest: {int(remaining)}s**")
                    if st.button("⏭️ Skip Rest", use_container_width=True):
                        remaining = 0
                    else:
                        time.sleep(0.5)
                        st.rerun()

                if remaining <= 0:
                    if ws["current_set"] < sets:
                        ws["current_set"] += 1
                        ws["status"] = "idle"
                        ws["timer_end"] = None
                    else:
                        ws["current_exercise_idx"] += 1
                        ws["current_set"] = 1
                        ws["status"] = "idle"
                        ws["timer_end"] = None
                    st.rerun()

    # Upcoming exercises preview
    if current_idx + 1 < total_exercises:
        with st.expander("👀 Up Next"):
            for i in range(current_idx + 1, min(current_idx + 3, total_exercises)):
                next_ex = exercises[i]
                next_cat = next_ex.get("category", "main")
                emoji = {"warmup": "🔥", "main": "💪", "cooldown": "🧘"}.get(next_cat, "💪")
                st.markdown(f"{emoji} **{next_ex.get('name')}** — {next_ex.get('sets')}x{next_ex.get('reps')}")


#  Chat Interface

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
                config = {"configurable": {"thread_id": st.session_state.thread_id}}
                result = st.session_state.graph.invoke(
                    {"messages": [{"role": "user", "content": msg}]},
                    config=config,
                )
                response_text = extract_text(result["messages"][-1].content)
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
                config = {"configurable": {"thread_id": st.session_state.thread_id}}
                result = st.session_state.graph.invoke(
                    {"messages": [{"role": "user", "content": user_input}]},
                    config=config,
                )
                response_text = extract_text(result["messages"][-1].content)
                st.markdown(response_text)

        st.session_state.history.append({"role": "assistant", "content": response_text})
        st.rerun()


#  Main App 

st.set_page_config(page_title="AI Fitness Coach", page_icon="🏋️", layout="wide")

if "graph" not in st.session_state:
    st.session_state.graph = build_graph()
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "history" not in st.session_state:
    st.session_state.history = []
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "Chat"

st.title("🏋️ AI Fitness Coach")
st.caption("Workout sessions, guided flow, progress dashboard, week/month horizons.")

with st.sidebar:
    st.button("🆕 New Conversation", on_click=start_new_conversation)
    st.caption(f"Session: {st.session_state.thread_id[:8]}")
    st.divider()
    st.markdown("**Review Pipelines**")

    if st.button("📅 Set Week Themes", help="Run the monthly review pipeline to set week themes for the current month"):
        try:
            result = run_monthly_review() 
            if result.returncode == 0:
                st.success("Week themes set! You can now ask the coach to generate your weekly plan.")
                if result.stdout:
                    st.code(result.stdout)
            else:
                st.error("Monthly review failed:" + chr(10) + result.stderr)
        except Exception as e:
            st.error(f"Could not run monthly review: {e}")
        st.rerun()

    st.divider()
    st.markdown("**Quick Actions**")
    if st.button("🗑️ Clear Today's Plan", use_container_width=True):
        collection = get_plans_collection()
        collection.delete_one({"user_id": DEFAULT_USER_ID, "date": get_today_str()})
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