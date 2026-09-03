"""
Read-only dashboard display components: progress, monthly goal, week plan,
and upcoming (forward) plans. Pulls directly from Mongo, no write paths.
"""

from datetime import datetime, timedelta

import streamlit as st
import streamlit_antd_components as sac

from db.mongo_client import get_plans_collection, get_month_plans_collection
from utils.calendar_weeks import LOCAL_TZ, current_month_id
from tools.progress import calculate_progress
from tools.week_plans import _find_week_doc_for_date
from auth.context import get_current_user_id


def get_forward_plan_docs() -> list:
    collection = get_plans_collection()
    today = datetime.now(LOCAL_TZ).date()
    docs = []
    for offset in (1, 2, 3):
        date = (today + timedelta(days=offset)).strftime("%Y-%m-%d")
        doc = collection.find_one({"user_id": get_current_user_id(), "date": date})
        docs.append((date, doc))
    return docs


def render_progress_dashboard():
    data = calculate_progress()
    adherence = data["adherence"]

    with st.container(border=True):
        st.markdown("### 📊 Progress")

        if adherence["adherence_pct"] is None:
            st.caption("No exercises tracked in the last 7 days.")
        else:
            st.caption(f"Last {adherence['window_days']} days adherence")
            st.progress(
                adherence["adherence_pct"] / 100,
                text=f"{adherence['completed_count']}/{adherence['planned_count']} exercises ({adherence['adherence_pct']:.0f}%)",
            )

        if data["has_confirmed_goal"] and data["volume_progress"]:
            st.markdown("**Monthly Volume**")
            for vp in data["volume_progress"]:
                pct = vp["pct"] or 0
                st.progress(
                    min(pct / 100, 1.0),
                    text=f"{vp['exercise']}: {vp['completed_so_far']}/{vp['month_target']} {vp['unit']} ({pct:.0f}%)",
                )

        mp = data.get("metric_progress")
        if mp and mp.get("pct") is not None:
            st.markdown("**Metric**")
            st.caption(
                f"{mp['metric_name'].replace('_', ' ').title()}: "
                f"{mp['latest_value']} {mp.get('unit', '')} ({mp['pct']:.0f}% to target)"
            )

        if not data["has_confirmed_goal"] and adherence["adherence_pct"] is None:
            st.caption("No confirmed goal this month.")


def render_month_goal():
    collection = get_month_plans_collection()
    doc = collection.find_one({"user_id": get_current_user_id(), "month_id": current_month_id()})

    with st.container(border=True):
        st.markdown("### 🎯 Monthly Goal")

        goal = doc.get("goal") if doc else None
        if not goal:
            st.info("No goal set this month. Ask the coach to set one.")
            return

        status = goal.get("status", "unknown")
        description = goal.get("description", "Unspecified")

        if status == "confirmed":
            st.markdown(f"**{description}**")
        elif status == "pending":
            st.markdown(f"**{description}**")
            st.warning("Pending confirmation")
        else:
            st.info(description)

        metric_name = goal.get("metric_name")
        if metric_name and goal.get("target_value") is not None:
            baseline = goal.get("baseline_value")
            target = goal.get("target_value")
            unit = goal.get("unit", "")
            st.caption(f"📏 {metric_name.replace('_', ' ').title()}: {baseline} → {target} {unit}")

        volume_targets = goal.get("volume_targets") or []
        if volume_targets:
            st.markdown("**Volume Targets**")
            for vt in volume_targets:
                exercise = vt.get("exercise", "?")
                area = vt.get("balance_area", "").replace("_", " ").title()
                month_target = vt.get("month_target", 0)
                unit = vt.get("unit", "")
                st.caption(f"• {exercise} ({area}): {month_target} {unit}")

        themes = doc.get("week_plan_path") or []
        if themes:
            st.markdown("**Week Themes**")
            theme_text = " → ".join(
                f"W{t['week_number']}: {t['theme']}" for t in themes
            )
            st.caption(theme_text)

            source = doc.get("theme_path_source")
            if source and source != "llm":
                st.warning("⚠️ Week themes were set by default (LLM failed). Click **Set Week Themes** to regenerate.")


def render_week_plan():
    today_str = datetime.now(LOCAL_TZ).date().strftime("%Y-%m-%d")
    doc = _find_week_doc_for_date(today_str)

    with st.container(border=True):
        st.markdown("### 📆 Week Plan")

        if not doc:
            st.caption("No week plan yet. Ask the coach to generate one.")
            return

        focus = doc.get("focus", "Unspecified").replace("_", " ").title()
        st.markdown(f"**Focus: {focus}**")

        week_targets = doc.get("week_volume_targets") or []
        if week_targets:
            st.markdown("**Weekly Volume Targets**")
            for vt in week_targets:
                exercise = vt.get("exercise", "?")
                target = vt.get("week_target", "?")
                unit = vt.get("unit", "")
                st.caption(f"• {exercise}: {target} {unit}")
        else:
            daily_volume_targets = doc.get("daily_volume_targets") or []
            if daily_volume_targets and daily_volume_targets[0].get("targets"):
                st.markdown("**Weekly Volume Targets**")
                first_day_targets = daily_volume_targets[0].get("targets") or []
                sessions_approx = max(1, round(4 * len(daily_volume_targets) / 7))
                for vt in first_day_targets:
                    exercise = vt.get("exercise", "?")
                    unit = vt.get("unit", "")
                    daily_t = vt.get("daily_target", 0)
                    st.caption(f"• {exercise}: {daily_t * sessions_approx} {unit} (approx)")
            else:
                st.caption("No weekly targets defined.")


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