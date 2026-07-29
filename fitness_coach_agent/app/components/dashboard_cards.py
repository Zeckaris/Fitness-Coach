"""
Read-only dashboard display components: progress, monthly goal, week plan,
and upcoming (forward) plans. Pulls directly from Mongo, no write paths.
"""

from datetime import datetime, timedelta

import streamlit as st
import streamlit_antd_components as sac

from db.mongo_client import get_plans_collection, get_month_plans_collection
from tools.plans import LOCAL_TZ
from tools.progress import calculate_progress
from tools.month_plans import _current_month_id
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
    doc = collection.find_one({"user_id": get_current_user_id(), "month_id": _current_month_id()})

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

        current_block = None
        next_block = None
        for block in blocks:
            dates = block.get("dates", [])
            if today_str in dates:
                current_block = block
            elif dates and dates[0] > today_str:
                next_block = block

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

        volume_targets = current_block.get("block_volume_targets") or []
        if volume_targets:
            for vt in volume_targets:
                exercise = vt.get("exercise", "?")
                target = vt.get("block_target", "?")
                unit = vt.get("unit", "")
                st.caption(f"• {exercise}: {target} {unit}")

        if next_block:
            next_dates = next_block.get("dates", [])
            next_focus = next_block.get("focus", "").replace("_", " ").title()
            next_start = next_dates[0] if next_dates else ""
            st.caption(f"Next: Block {next_block.get('block_number', '?')} starts {next_start} — {next_focus}")


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