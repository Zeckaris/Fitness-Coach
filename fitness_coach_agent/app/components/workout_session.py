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
from tools.workout_library import _WORKOUTS


# ── Exercise media lookup ───────────────────────────────────────────────────
# workout_library.json stores gif_url/image as paths relative to the
# exercises-dataset repo (e.g. "videos/0276-iny3m5y.gif"). Plan documents
# only persist name/sets/reps/etc — not media — so we resolve media by
# exercise name against the library at render time.

GITHUB_MEDIA_BASE = "https://raw.githubusercontent.com/hasaneyldrm/exercises-dataset/main/"

_MEDIA_BY_NAME = {
    w["name"].strip().lower(): w
    for w in _WORKOUTS
    if w.get("gif_url") or w.get("image")
}


def get_exercise_media(name: str) -> dict | None:
    """Look up gif/image URLs + attribution for an exercise by name.

    Returns None if the exercise isn't in the library or has no media.
    Prefers the animated gif; falls back to the static image.
    """
    entry = _MEDIA_BY_NAME.get((name or "").strip().lower())
    if not entry:
        return None

    gif_url = entry.get("gif_url")
    image_url = entry.get("image")
    if not gif_url and not image_url:
        return None

    return {
        "gif": f"{GITHUB_MEDIA_BASE}{gif_url}" if gif_url else None,
        "image": f"{GITHUB_MEDIA_BASE}{image_url}" if image_url else None,
        "attribution": entry.get("attribution"),
    }


def render_exercise_media(name: str, *, width: int | None = None) -> bool:
    """Render an exercise's demo gif (falling back to a static image).

    Returns True if something was rendered, False if no media was found
    (caller can decide whether to show a placeholder).
    """
    media = get_exercise_media(name)
    if not media:
        return False

    src = media["gif"] or media["image"]
    kwargs = {"width": width} if width else {"use_container_width": True}
    st.image(src, **kwargs)
    if media.get("attribution"):
        st.caption(media["attribution"])
    return True


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
                # NOTE: this template is deliberately limited to exercises
                # confirmed present in the rebuilt (V9.5) workout library
                # with equipment == ["none"] and a description that matches
                # that equipment claim. The rebuilt library has no true
                # stretch/mobility/cooldown-type entries yet (excluded during
                # curation for lacking media), so "Dead Bug" appears twice —
                # once at main-work intensity, once reduced for cooldown —
                # rather than inventing an exercise not in the library.
                template_exercises = [
                    {
                        "name": "Bear Crawl", "focus": "full_body", "category": "warmup",
                        "sets": 1, "reps": "8-10", "equipment": ["none"], "duration_minutes": 2,
                        "description": "Start on all fours with your hands directly under your shoulders and your knees directly under your hips. Lift your knees slightly off the ground, keeping your back flat and your core engaged. Move your right hand and left foot forward simultaneously, followed by your left hand and right foot.",
                        "completed": False, "completed_quantity": 0,
                    },
                    {
                        "name": "Star Jump (Male)", "focus": "full_body", "category": "warmup",
                        "sets": 2, "reps": "10", "equipment": ["none"], "duration_minutes": 2,
                        "description": "Stand with your feet shoulder-width apart and your arms by your sides. Bend your knees slightly and jump up explosively. As you jump, spread your legs and extend your arms out to the sides, forming a star shape with your body.",
                        "completed": False, "completed_quantity": 0,
                    },
                    {
                        "name": "Dead Bug", "focus": "abs", "category": "main",
                        "sets": 3, "reps": "10-12 per side", "equipment": ["none"], "duration_minutes": 3,
                        "description": "Lie flat on your back with your arms extended towards the ceiling. Bend your knees and lift your legs off the ground, creating a 90-degree angle at your hips and knees. Engage your core and lower back to press your lower back into the ground.",
                        "completed": False, "completed_quantity": 0,
                    },
                    {
                        "name": "Mountain Climber", "focus": "full_body", "category": "main",
                        "sets": 3, "reps": "20", "equipment": ["none"], "duration_minutes": 3,
                        "description": "Start in a high plank position with your hands directly under your shoulders and your body in a straight line. Engage your core and bring your right knee towards your chest, then quickly switch and bring your left knee towards your chest. Continue alternating legs in a running motion, keeping your hips low and your core engaged.",
                        "completed": False, "completed_quantity": 0,
                    },
                    {
                        "name": "Astride Jumps (Male)", "focus": "full_body", "category": "main",
                        "sets": 3, "reps": "10-12", "equipment": ["none"], "duration_minutes": 3,
                        "description": "Stand with your feet shoulder-width apart. Bend your knees and lower your body into a squat position. Jump explosively upwards, extending your legs and arms. While in the air, spread your legs apart and bring your arms out to the sides.",
                        "completed": False, "completed_quantity": 0,
                    },
                    {
                        "name": "Skater Hops", "focus": "full_body", "category": "main",
                        "sets": 3, "reps": "10-12", "equipment": ["none"], "duration_minutes": 3,
                        "description": "Stand with your feet shoulder-width apart. Bend your knees slightly and jump to the right, landing on your right foot. As you land, swing your left leg behind your right leg and tap the ground with your left toes.",
                        "completed": False, "completed_quantity": 0,
                    },
                    {
                        "name": "Dead Bug", "focus": "abs", "category": "cooldown",
                        "sets": 1, "reps": "8 per side", "equipment": ["none"], "duration_minutes": 2,
                        "description": "Lie flat on your back with your arms extended towards the ceiling. Bend your knees and lift your legs off the ground, creating a 90-degree angle at your hips and knees. Engage your core and lower back to press your lower back into the ground.",
                        "completed": False, "completed_quantity": 0,
                    },
                ]
                create_today_plan(template_exercises, focus_area="full_body", duration_minutes=18)
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
        # Demo media sits front-and-center above the set/rep info and
        # controls — this is what the person is meant to be looking at
        # while they're actually moving.
        media_col, _spacer = st.columns([1, 2])
        with media_col:
            img_left, img_center, img_right = st.columns([1, 3, 1])
            with img_center:
                if not render_exercise_media(name, width=280):
                    st.caption("No demo video available for this exercise.")

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
                next_name = next_ex.get("name")

                thumb_col, text_col = st.columns([1, 4])
                with thumb_col:
                    media = get_exercise_media(next_name)
                    if media and (media["image"] or media["gif"]):
                        st.image(media["image"] or media["gif"], width=64)
                with text_col:
                    st.markdown(f"{emoji} **{next_name}** — {next_ex.get('sets')}x{next_ex.get('reps')}")