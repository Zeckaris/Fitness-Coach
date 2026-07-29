"""
Guided workout session flow for today's plan: idle -> exercising -> resting
-> next exercise, driven by st.session_state.workout_state.
"""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
from streamlit.components.v1 import html

from db.mongo_client import get_plans_collection
from tools.plans import LOCAL_TZ
from auth.context import get_current_user_id


def get_today_str() -> str:
    return datetime.now(LOCAL_TZ).date().strftime("%Y-%m-%d")


def get_today_plan() -> dict | None:
    """Fetch today's plan if it exists."""
    collection = get_plans_collection()
    return collection.find_one({"user_id": get_current_user_id(), "date": get_today_str()})


def create_today_plan(exercises: list, focus_area: str = "full_body", duration_minutes: int = 45) -> dict:
    """Create or overwrite today's plan directly in the UI."""
    collection = get_plans_collection()
    today = get_today_str()
    doc = {
        "user_id": get_current_user_id(),
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
        {"user_id": get_current_user_id(), "date": today},
        {"$set": doc},
        upsert=True,
    )
    return doc


def update_today_exercise_completion(exercise_idx: int, field: str, value):
    collection = get_plans_collection()
    today = get_today_str()
    collection.update_one(
        {"user_id": get_current_user_id(), "date": today},
        {"$set": {f"exercises.{exercise_idx}.{field}": value}},
    )


def finish_today_plan():
    """Mark today's plan as completed."""
    collection = get_plans_collection()
    today = get_today_str()
    collection.update_one(
        {"user_id": get_current_user_id(), "date": today},
        {"$set": {"status": "completed", "completed_at": datetime.now(ZoneInfo("UTC"))}},
    )


def render_workout_session():
    """Guided workout session for today's plan."""

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

    if today_plan and today_plan.get("status") == "rest":
        st.info("Today is a rest day. Recover and come back strong!")
        return

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

    exercises = today_plan.get("exercises", [])
    if not exercises:
        st.warning("Today's plan has no exercises.")
        return

    total_exercises = len(exercises)
    current_idx = ws["current_exercise_idx"]

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

    progress = current_idx / total_exercises
    st.progress(progress, text=f"Exercise {current_idx + 1} of {total_exercises}")

    ex = exercises[current_idx]
    name = ex.get("name", "Exercise")
    sets = ex.get("sets", 1)
    reps = ex.get("reps", "?")
    duration = ex.get("duration_minutes")
    category = ex.get("category", "main")
    description = ex.get("description", "")

    cat_labels = {"warmup": "🔥 WARM-UP", "main": "💪 MAIN WORK", "cooldown": "🧘 COOL-DOWN"}
    cat_color = {"warmup": "#f97316", "main": "#3b82f6", "cooldown": "#22c55e"}

    st.markdown(f"### {cat_labels.get(category, '💪')} — {name}")

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

    if current_idx + 1 < total_exercises:
        with st.expander("👀 Up Next"):
            for i in range(current_idx + 1, min(current_idx + 3, total_exercises)):
                next_ex = exercises[i]
                next_cat = next_ex.get("category", "main")
                emoji = {"warmup": "🔥", "main": "💪", "cooldown": "🧘"}.get(next_cat, "💪")
                st.markdown(f"{emoji} **{next_ex.get('name')}** — {next_ex.get('sets')}x{next_ex.get('reps')}")