"""
Onboarding UI component for first-time users and users inactive for 3+ months.
Gates dashboard access behind a blocking Streamlit screen per v1.8.0.
"""

from datetime import date, datetime
import streamlit as st

from tools.user_profile import (
    has_user_profile,
    is_user_inactive,
    create_user_profile,
)
from tools.baseline_assessment import (
    record_baseline_assessment,
    get_latest_baseline_assessment,
    BASELINE_ASSESSMENT_DATA_VERSION,
)
from tools.user_profile import (
    has_user_profile,
    is_user_inactive,
    create_user_profile,
    get_user_profile,
    get_user_profile_collection,
)
from tools.metrics import log_metric
from auth.context import get_current_user_id
from utils.baseline_level import (
    calculate_overall_level,
    evaluate_overall_assessment,
    compute_bmi,
)
from app.components.auth_ui import render_logout_button


FREE_WEIGHT_EQUIPMENT = {"dumbbells", "kettlebell"}
PULL_UP_EQUIPMENT = {"pull_up_bar"}
WEIGHTED_LIFT_DEFAULT = 0.0


def calculate_age_from_dob(dob: date) -> int:
    """Calculate age in whole years from date of birth."""
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def normalize_bodyweight_kg(weight_value: float, weight_unit: str) -> float:
    """Convert the entered bodyweight to kilograms."""
    value = float(weight_value)
    if weight_unit == "lb":
        value *= 0.45359237
    return value


def has_free_weight_equipment(equipment: list[str] | None) -> bool:
    """True only when the user has dumbbells or a kettlebell."""
    return any(
        str(item).strip().lower() in FREE_WEIGHT_EQUIPMENT
        for item in (equipment or [])
    )


def has_pull_up_equipment(equipment: list[str] | None) -> bool:
    """True only when the user has a pull-up bar."""
    return any(
        str(item).strip().lower() in PULL_UP_EQUIPMENT
        for item in (equipment or [])
    )


def resolve_weighted_lift_kg(
    weighted_lift_value: float | None,
    weight_unit: str,
    has_free_weights: bool,
) -> float | None:
    """Return a scorable lift in kg, or None if it must not be scored."""
    if not has_free_weights:
        return None
    try:
        value = float(weighted_lift_value or 0.0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if weight_unit == "lb":
        value *= 0.45359237
    return value


def resolve_lift_scorable(
    value: float | None,
    weight_unit: str,
    has_equipment: bool,
    is_reps: bool = False,
) -> float | None:
    """
    Return a scorable lift value (converted to kg if weight, or raw reps if pull-up),
    or None if skipped, non-positive, or missing required equipment.
    """
    if not has_equipment:
        return None
    try:
        val = float(value or 0.0)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    if not is_reps and weight_unit == "lb":
        val *= 0.45359237
    return val


def resolve_plank_seconds(plank_seconds_val: float | int | None) -> float | None:
    """Return a scorable plank hold in seconds, or None if skipped (0, blank, non-positive)."""
    try:
        value = float(plank_seconds_val or 0.0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value


def build_baseline_markers(
    push_ups: float,
    squats: float,
    run_minutes: float,
    frequency: float,
    weighted_lift_kg: float | None = None,
    plank_seconds: float | None = None,
    push_lift_kg: float | None = None,
    pull_lift_kg: float | None = None,
    lower_lift_kg: float | None = None,
    pull_ups_reps: float | None = None,
    strength_marker: float | None = None,
) -> dict:
    """Build the marker payload, excluding any non-scorable lifts or plank."""
    markers = {
        "push_ups": float(push_ups),
        "squats": float(squats),
        "run": float(run_minutes),
        "frequency": float(frequency),
    }
    if weighted_lift_kg is not None and weighted_lift_kg > 0:
        markers["weighted_lift"] = float(weighted_lift_kg)
    if plank_seconds is not None and plank_seconds > 0:
        markers["plank"] = float(plank_seconds)
    if push_lift_kg is not None and push_lift_kg > 0:
        markers["push_lift"] = float(push_lift_kg)
    if pull_lift_kg is not None and pull_lift_kg > 0:
        markers["pull_lift"] = float(pull_lift_kg)
    if lower_lift_kg is not None and lower_lift_kg > 0:
        markers["lower_lift"] = float(lower_lift_kg)
    if pull_ups_reps is not None and pull_ups_reps > 0:
        markers["pull_ups"] = float(pull_ups_reps)
    if strength_marker is not None and strength_marker > 0:
        markers["strength_marker"] = float(strength_marker)
    return markers


def render_onboarding_screen(is_reassessment: bool = False):
    """
    Renders the blocking onboarding form.
    Collects personal attributes (DOB, gender, height, weight preferences)
    and scores baseline performance markers before persisting profile and assessment.
    """
    if is_reassessment:
        st.title("🔄 Baseline Re-Assessment")
        st.info(
            "It has been over 3 months since your last completed workout. "
            "Let's re-calibrate your fitness baseline so your upcoming training plans "
            "are properly dosed."
        )
    else:
        st.title("🏋️ Welcome to AI Fitness Coach!")
        st.markdown(
            "Before generating your workouts, let's establish your baseline capacity. "
            "This takes just 2 minutes and ensures your targets are safe and achievable."
        )

    with st.form("onboarding_form"):
        st.subheader("1. Personal Details")
        col_dob, col_gender = st.columns(2)
        with col_dob:
            default_dob = date(date.today().year - 30, 1, 1)
            dob = st.date_input(
                "Date of Birth",
                value=default_dob,
                min_value=date(1900, 1, 1),
                max_value=date.today(),
                help="Your date of birth is used to compute age for threshold calibration.",
            )
        with col_gender:
            gender_option = st.selectbox(
                "Gender",
                options=["Male", "Female"],
                help="Calibrates strength standards against verified population data.",
            )

        col_h_val, col_h_unit = st.columns([2, 1])
        with col_h_val:
            height_val = st.number_input(
                "Height",
                min_value=50.0,
                max_value=260.0,
                value=175.0,
                step=0.5,
            )
        with col_h_unit:
            height_unit = st.selectbox("Height Unit", ["cm", "in"])

        col_w_val, col_w_unit = st.columns([2, 1])
        with col_w_val:
            weight_val = st.number_input(
                "Current Bodyweight",
                min_value=25.0,
                max_value=350.0,
                value=70.0,
                step=0.5,
                help="Used for bodyweight scaling on push-ups and squats.",
            )
        with col_w_unit:
            weight_unit = st.selectbox(
                "Weight Unit Preference",
                ["kg", "lb"],
                help="Preferred unit for weight logs and displays.",
            )

        st.divider()
        st.subheader("2. Baseline Performance Markers")
        st.caption(
            "Enter your realistic single-effort numbers. Be honest — conservative answers "
            "help prevent early overtraining and injury."
        )

        col_m1, col_m2 = st.columns(2)
        with col_m1:
            push_ups = st.number_input(
                "Max Consecutive Push-ups (reps)",
                min_value=0,
                max_value=250,
                value=12,
                step=1,
            )
        with col_m2:
            squats = st.number_input(
                "Max Consecutive Bodyweight Squats (reps)",
                min_value=0,
                max_value=300,
                value=20,
                step=1,
            )

        col_m3, col_m4 = st.columns(2)
        with col_m3:
            run_minutes = st.number_input(
                "Longest Continuous Run without stopping (minutes)",
                min_value=0.0,
                max_value=300.0,
                value=15.0,
                step=1.0,
                help="Minutes you can run continuously at a comfortable pace.",
            )
        with col_m4:
            frequency = st.number_input(
                "Current Weekly Workout Frequency (times/week)",
                min_value=0,
                max_value=14,
                value=2,
                step=1,
                help="How many workouts you currently do in a typical week.",
            )

        # Distance follow-up on the SAME run captured above. Duration feeds the
        # experience-level score; distance is stored separately and used as the
        # cardio volume baseline (duration and distance are not the same unit).
        col_rd_val, col_rd_unit = st.columns([2, 1])
        with col_rd_val:
            run_distance_val = st.number_input(
                "…and roughly how far does that run cover?",
                min_value=0.0,
                max_value=100.0,
                value=2.0,
                step=0.1,
                help="Approximate distance for the continuous run you just entered. "
                "Used to set your cardio volume target.",
            )
        with col_rd_unit:
            run_distance_unit = st.selectbox(
                "Distance Unit",
                ["km", "mi"],
                help="Unit for the run distance above.",
            )

        plank_seconds = st.number_input(
            "Optional: Longest Front Plank Hold with good form (seconds)",
            min_value=0,
            max_value=600,
            value=0,
            step=5,
            help=(
                "The longest continuous front plank you can hold with good form, in seconds. "
                "Enter 0 or leave blank to skip."
            ),
        )

        st.divider()
        st.subheader("3. Equipment & Strength Capacity")
        from tools.workout_library import EQUIPMENT_VOCABULARY

        EQUIPMENT_LABELS = {
            "dumbbells": "Dumbbells",
            "kettlebell": "Kettlebell",
            "bench": "Bench",
            "pull_up_bar": "Pull-up Bar",
            "resistance_band": "Resistance Band",
            "ab_wheel": "Ab Wheel",
            "rope": "Jump Rope",
            "stability_ball": "Stability Ball",
        }

        selected_equipment = st.multiselect(
            "Select all equipment you have access to (leave empty for bodyweight only):",
            options=EQUIPMENT_VOCABULARY,
            format_func=lambda x: EQUIPMENT_LABELS.get(x, x),
            help="Your workout plans will only include exercises matching your available equipment.",
        )

        st.markdown("#### Optional Strength Lifts")
        st.caption(
            "Enter the working weight you can lift for 8 clean reps with 1–2 reps in reserve. "
            "Leave at 0 to skip any lift. Skipped lifts are never scored. "
            "Push and lower-body lifts require dumbbells or a kettlebell (a bench alone does not count). "
            "Pull lift can be a weighted row (dumbbells or kettlebell) or max strict pull-ups (pull-up bar)."
        )

        push_lift_val = st.number_input(
            f"Optional Push Lift: 8-rep weight ({weight_unit}) — Dumbbell Bench Press or Floor Press (per hand)",
            min_value=0.0,
            max_value=200.0,
            value=0.0,
            step=0.5,
            help="Weight of ONE dumbbell per hand for 8 clean reps with 1-2 in reserve. Enter 0 to skip. Only scored if you have dumbbells or a kettlebell. A bench alone does not count.",
        )

        col_pt, col_pv = st.columns(2)
        with col_pt:
            pull_type = st.selectbox(
                "Optional Pull Lift Movement",
                options=["Dumbbell Bent-Over Row (per hand)", "Strict Pull-Ups (max reps)"],
                help="Choose weighted row (requires dumbbells/kettlebell) or max strict pull-ups (requires pull-up bar).",
            )
        with col_pv:
            if "Row" in pull_type:
                pull_lift_val = st.number_input(
                    f"Pull Lift: 8-rep weight ({weight_unit}) (per hand)",
                    min_value=0.0,
                    max_value=200.0,
                    value=0.0,
                    step=0.5,
                    help="Weight of ONE dumbbell per hand for 8 clean reps with 1-2 in reserve. Enter 0 to skip. Only scored if you have dumbbells or a kettlebell.",
                )
                pull_reps_val = 0
            else:
                pull_reps_val = st.number_input(
                    "Pull Lift: Max Strict Pull-Ups (reps)",
                    min_value=0,
                    max_value=100,
                    value=0,
                    step=1,
                    help="Max consecutive strict pull-ups with good form. Enter 0 to skip. Only scored if you have a pull-up bar.",
                )
                pull_lift_val = 0.0

        lower_lift_val = st.number_input(
            f"Optional Lower-Body Lift: 8-rep weight ({weight_unit}) — Goblet Squat (total weight)",
            min_value=0.0,
            max_value=300.0,
            value=0.0,
            step=0.5,
            help="Total weight of single dumbbell or kettlebell for 8 clean reps with 1-2 in reserve. Enter 0 to skip. Only scored if you have dumbbells or a kettlebell.",
        )

        submitted = st.form_submit_button(
            "Complete Onboarding & Calibrate Baseline",
            use_container_width=True,
        )

        if submitted:
            age = calculate_age_from_dob(dob)
            if age < 13 or age > 120:
                st.error("Please enter a valid date of birth (age must be at least 13).")
                return

            gender_clean = gender_option.lower()

            body_weight_kg = normalize_bodyweight_kg(weight_val, weight_unit)
            has_free_weights = has_free_weight_equipment(selected_equipment)
            has_pull_up_bar = has_pull_up_equipment(selected_equipment)

            push_lift_kg = resolve_lift_scorable(
                push_lift_val, weight_unit, has_equipment=has_free_weights, is_reps=False
            )
            if "Row" in pull_type:
                pull_lift_kg = resolve_lift_scorable(
                    pull_lift_val, weight_unit, has_equipment=has_free_weights, is_reps=False
                )
                pull_ups_reps = None
            else:
                pull_lift_kg = None
                pull_ups_reps = resolve_lift_scorable(
                    pull_reps_val, weight_unit, has_equipment=has_pull_up_bar, is_reps=True
                )

            lower_lift_kg = resolve_lift_scorable(
                lower_lift_val, weight_unit, has_equipment=has_free_weights, is_reps=False
            )
            plank_seconds_val = resolve_plank_seconds(plank_seconds)

            # Normalize run distance to km (stored alongside the raw value/unit)
            if run_distance_val and run_distance_unit == "mi":
                run_distance_km = run_distance_val * 1.609344
            elif run_distance_val:
                run_distance_km = float(run_distance_val)
            else:
                run_distance_km = None

            # Compute BMI for mechanical leverage adjustment on isometric holds
            bmi = compute_bmi(
                weight=body_weight_kg,
                height=float(height_val),
                weight_unit="kg",
                height_unit=height_unit,
            )

            # Build raw_lifts list for structured storage
            raw_lifts = []
            if push_lift_val and push_lift_val > 0:
                push_kg_all = normalize_bodyweight_kg(push_lift_val, weight_unit)
                raw_lifts.append({
                    "lift_type": "push",
                    "exercise": "Dumbbell Bench Press",
                    "weight_kg": round(push_kg_all, 2),
                    "reps": 8,
                    "per_hand": True,
                    "scored": push_lift_kg is not None,
                })
            if "Row" in pull_type and pull_lift_val and pull_lift_val > 0:
                pull_kg_all = normalize_bodyweight_kg(pull_lift_val, weight_unit)
                raw_lifts.append({
                    "lift_type": "pull",
                    "exercise": "Dumbbell Bent Over Row",
                    "weight_kg": round(pull_kg_all, 2),
                    "reps": 8,
                    "per_hand": True,
                    "scored": pull_lift_kg is not None,
                })
            elif "Pull-Up" in pull_type and pull_reps_val and pull_reps_val > 0:
                raw_lifts.append({
                    "lift_type": "pull",
                    "exercise": "Pull-Up",
                    "weight_kg": 0.0,
                    "reps": int(pull_reps_val),
                    "per_hand": False,
                    "scored": pull_ups_reps is not None,
                })
            if lower_lift_val and lower_lift_val > 0:
                lower_kg_all = normalize_bodyweight_kg(lower_lift_val, weight_unit)
                raw_lifts.append({
                    "lift_type": "lower",
                    "exercise": "Dumbbell Goblet Squat",
                    "weight_kg": round(lower_kg_all, 2),
                    "reps": 8,
                    "per_hand": False,
                    "scored": lower_lift_kg is not None,
                })

            markers = build_baseline_markers(
                push_ups=push_ups,
                squats=squats,
                run_minutes=run_minutes,
                frequency=frequency,
                plank_seconds=plank_seconds_val,
                push_lift_kg=push_lift_kg,
                pull_lift_kg=pull_lift_kg,
                lower_lift_kg=lower_lift_kg,
                pull_ups_reps=pull_ups_reps,
            )

            # Score using Phase 2 calculator
            level = calculate_overall_level(
                markers=markers,
                gender=gender_clean,
                age=age,
                actual_weight_kg=body_weight_kg,
                bmi=bmi,
            )

            # Full diagnostic evaluation to store in assessment doc
            evaluation = evaluate_overall_assessment(
                markers=markers,
                gender=gender_clean,
                age=age,
                actual_weight_kg=body_weight_kg,
                bmi=bmi,
            )

            # 1. Create/update profile document
            create_user_profile(
                date_of_birth=dob.isoformat(),
                gender=gender_clean,
                height_value=float(height_val),
                height_unit=height_unit,
                weight_unit_preference=weight_unit,
                equipment=selected_equipment,
                body_weight_kg=body_weight_kg,
            )

            # 2. Record append-only baseline assessment (mirrors experience_level)
            answers_payload = {
                "date_of_birth": dob.isoformat(),
                "age_at_assessment": age,
                "gender": gender_clean,
                "height": {"value": float(height_val), "unit": height_unit},
                "weight": {
                    "value": float(weight_val),
                    "unit": weight_unit,
                    "weight_kg": round(body_weight_kg, 2),
                },
                "equipment": selected_equipment,
                "raw_markers": {
                    "push_ups": push_ups,
                    "squats": squats,
                    "run_minutes": run_minutes,
                    "run_distance_val": float(run_distance_val) if run_distance_val else None,
                    "run_distance_unit": run_distance_unit if run_distance_val else None,
                    "run_distance_km": round(run_distance_km, 2) if run_distance_km else None,
                    "frequency": frequency,
                    "plank_seconds": round(plank_seconds_val, 1) if plank_seconds_val is not None else None,
                    "push_lift_kg": round(push_lift_kg, 2) if push_lift_kg is not None else None,
                    "pull_lift_kg": round(pull_lift_kg, 2) if pull_lift_kg is not None else None,
                    "pull_ups_reps": pull_ups_reps if pull_ups_reps is not None else None,
                    "lower_lift_kg": round(lower_lift_kg, 2) if lower_lift_kg is not None else None,
                },
                "raw_lifts": raw_lifts,
                "lifts": {
                    lift["lift_type"]: {
                        "exercise": lift["exercise"],
                        "weight_kg": lift["weight_kg"],
                        "reps": lift["reps"],
                        "per_hand": lift["per_hand"],
                    }
                    for lift in raw_lifts
                },
                "evaluation": evaluation,
            }

            record_baseline_assessment(
                experience_level=level,
                answers=answers_payload,
                data_version=BASELINE_ASSESSMENT_DATA_VERSION,
            )

            # 3. Log initial metric readings for weight and height
            try:
                log_metric.invoke({
                    "metric_name": "body_weight",
                    "value": float(weight_val),
                    "unit": weight_unit,
                })
                log_metric.invoke({
                    "metric_name": "height",
                    "value": float(height_val),
                    "unit": height_unit,
                })
            except Exception:
                pass  # Non-fatal if initial metric logging fails

            st.success(
                f"Assessment complete! Your baseline experience level is: **{level.title()}**."
            )
            st.rerun()

    st.markdown("---")
    st.caption("Need to switch accounts?")
    render_logout_button()


def render_baseline_upgrade_prompt():
    """
    Renders a non-blocking, dismissible prompt on the dashboard for existing users
    inviting them to add a plank hold and lift numbers, or skip.
    - Dismissible: persists dismissal in MongoDB so it does not nag every session.
    - Prefills existing answers from profile and latest assessment.
    - Submitting creates a new assessment doc (data_version=3) and updates profile mirror.
    - Never changes a confirmed current-month goal.
    """
    user_id = get_current_user_id()
    if not user_id:
        return

    profile = get_user_profile(user_id=user_id)
    if not profile:
        return

    # Check if already dismissed
    if profile.get("dismissed_baseline_upgrade_prompt"):
        return

    # Check if user already has data_version >= 3
    latest = get_latest_baseline_assessment(user_id=user_id)
    if latest and latest.get("data_version", 1) >= BASELINE_ASSESSMENT_DATA_VERSION:
        return

    # Extract existing answers to prefill
    existing_answers = (latest or {}).get("answers", {})
    existing_raw = existing_answers.get("raw_markers", {})
    weight_unit = profile.get("weight_unit_preference", "kg")
    body_weight_kg = profile.get("body_weight_kg", 70.0)
    display_weight = round(body_weight_kg / 0.45359237, 1) if weight_unit == "lb" else round(body_weight_kg, 1)
    height_val = float(profile.get("height_value", 175.0))
    height_unit = profile.get("height_unit", "cm")
    dob_str = profile.get("date_of_birth", "1995-01-01")
    try:
        dob_val = date.fromisoformat(dob_str)
    except Exception:
        dob_val = date(1995, 1, 1)
    gender_clean = profile.get("gender", "male").lower()
    equipment = profile.get("equipment", [])

    st.info(
        "💡 **New Strength & Core Baselines Available**\n\n"
        "We've added an optional front plank hold and three optional strength lifts (push, pull, lower-body) "
        "to give you more personalized workout targets. Would you like to add yours now, or skip?"
    )

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("➕ Add Plank & Strength Lifts", key="open_upgrade_form_btn"):
            st.session_state.show_upgrade_form = True
    with col2:
        if st.button("✕ Dismiss / Skip for Now", key="dismiss_upgrade_prompt_btn"):
            col = get_user_profile_collection()
            col.update_one(
                {"user_id": user_id},
                {"$set": {"dismissed_baseline_upgrade_prompt": True}}
            )
            st.session_state.show_upgrade_form = False
            st.rerun()

    if st.session_state.get("show_upgrade_form"):
        with st.expander("Update Baseline Capacity", expanded=True):
            with st.form("baseline_upgrade_form"):
                st.caption(
                    "Your previous answers have been prefilled. Add your plank hold or any strength "
                    "lifts you do, then submit. Skipped lifts are never scored."
                )

                col_u1, col_u2 = st.columns(2)
                with col_u1:
                    push_ups = st.number_input(
                        "Push-ups (reps)",
                        min_value=0, max_value=250,
                        value=int(existing_raw.get("push_ups", 12)),
                    )
                with col_u2:
                    squats = st.number_input(
                        "Bodyweight Squats (reps)",
                        min_value=0, max_value=300,
                        value=int(existing_raw.get("squats", 20)),
                    )

                col_u3, col_u4 = st.columns(2)
                with col_u3:
                    run_minutes = st.number_input(
                        "Longest Run (minutes)",
                        min_value=0.0, max_value=300.0,
                        value=float(existing_raw.get("run_minutes", 15.0)),
                    )
                with col_u4:
                    frequency = st.number_input(
                        "Weekly Frequency",
                        min_value=0, max_value=14,
                        value=int(existing_raw.get("frequency", 2)),
                    )

                plank_seconds = st.number_input(
                    "Front Plank Hold (seconds, 0 to skip)",
                    min_value=0, max_value=600,
                    value=int(existing_raw.get("plank_seconds") or 0),
                    help="Enter 0 or leave blank to skip.",
                )

                st.markdown("#### Equipment Access")
                from tools.workout_library import EQUIPMENT_VOCABULARY
                EQUIPMENT_LABELS = {
                    "dumbbells": "Dumbbells",
                    "kettlebell": "Kettlebell",
                    "bench": "Bench",
                    "pull_up_bar": "Pull-up Bar",
                    "resistance_band": "Resistance Band",
                    "ab_wheel": "Ab Wheel",
                    "rope": "Jump Rope",
                    "stability_ball": "Stability Ball",
                }
                selected_equipment = st.multiselect(
                    "Equipment you have access to:",
                    options=EQUIPMENT_VOCABULARY,
                    default=equipment,
                    format_func=lambda x: EQUIPMENT_LABELS.get(x, x),
                )

                st.markdown("#### Optional Strength Lifts")
                st.caption(
                    "8-rep working weight (1–2 reps in reserve). Leave at 0 to skip. "
                    "Push & lower-body lifts require dumbbells or kettlebell (bench alone does not count). "
                    "Pull lift can be dumbbell row (per hand) or strict pull-ups (pull-up bar)."
                )

                push_lift_val = st.number_input(
                    f"Optional Push Lift: 8-rep weight ({weight_unit}) — Dumbbell Bench Press / Floor Press (per hand)",
                    min_value=0.0, max_value=200.0, value=0.0, step=0.5,
                )

                col_pt, col_pv = st.columns(2)
                with col_pt:
                    pull_type = st.selectbox(
                        "Pull Movement",
                        options=["Dumbbell Bent-Over Row (per hand)", "Strict Pull-Ups (max reps)"],
                    )
                with col_pv:
                    if "Row" in pull_type:
                        pull_lift_val = st.number_input(
                            f"Pull Lift: 8-rep weight ({weight_unit}) (per hand)",
                            min_value=0.0, max_value=200.0, value=0.0, step=0.5,
                        )
                        pull_reps_val = 0
                    else:
                        pull_reps_val = st.number_input(
                            "Pull Lift: Max Strict Pull-Ups (reps)",
                            min_value=0, max_value=100, value=0, step=1,
                        )
                        pull_lift_val = 0.0

                lower_lift_val = st.number_input(
                    f"Optional Lower-Body Lift: 8-rep weight ({weight_unit}) — Goblet Squat (total weight)",
                    min_value=0.0, max_value=300.0, value=0.0, step=0.5,
                )

                upgrade_submitted = st.form_submit_button(
                    "Save Baseline & Update Assessment",
                    use_container_width=True,
                )

                if upgrade_submitted:
                    age = calculate_age_from_dob(dob_val)
                    has_free_weights = has_free_weight_equipment(selected_equipment)
                    has_pull_up_bar = has_pull_up_equipment(selected_equipment)

                    push_lift_kg = resolve_lift_scorable(
                        push_lift_val, weight_unit, has_equipment=has_free_weights, is_reps=False
                    )
                    if "Row" in pull_type:
                        pull_lift_kg = resolve_lift_scorable(
                            pull_lift_val, weight_unit, has_equipment=has_free_weights, is_reps=False
                        )
                        pull_ups_reps = None
                    else:
                        pull_lift_kg = None
                        pull_ups_reps = resolve_lift_scorable(
                            pull_reps_val, weight_unit, has_equipment=has_pull_up_bar, is_reps=True
                        )

                    lower_lift_kg = resolve_lift_scorable(
                        lower_lift_val, weight_unit, has_equipment=has_free_weights, is_reps=False
                    )
                    plank_seconds_val = resolve_plank_seconds(plank_seconds)

                    raw_lifts = []
                    if push_lift_val and push_lift_val > 0:
                        raw_lifts.append({
                            "lift_type": "push",
                            "exercise": "Dumbbell Bench Press",
                            "weight_kg": round(normalize_bodyweight_kg(push_lift_val, weight_unit), 2),
                            "reps": 8,
                            "per_hand": True,
                            "scored": push_lift_kg is not None,
                        })
                    if "Row" in pull_type and pull_lift_val and pull_lift_val > 0:
                        raw_lifts.append({
                            "lift_type": "pull",
                            "exercise": "Dumbbell Bent Over Row",
                            "weight_kg": round(normalize_bodyweight_kg(pull_lift_val, weight_unit), 2),
                            "reps": 8,
                            "per_hand": True,
                            "scored": pull_lift_kg is not None,
                        })
                    elif "Pull-Up" in pull_type and pull_reps_val and pull_reps_val > 0:
                        raw_lifts.append({
                            "lift_type": "pull",
                            "exercise": "Pull-Up",
                            "weight_kg": 0.0,
                            "reps": int(pull_reps_val),
                            "per_hand": False,
                            "scored": pull_ups_reps is not None,
                        })
                    if lower_lift_val and lower_lift_val > 0:
                        raw_lifts.append({
                            "lift_type": "lower",
                            "exercise": "Dumbbell Goblet Squat",
                            "weight_kg": round(normalize_bodyweight_kg(lower_lift_val, weight_unit), 2),
                            "reps": 8,
                            "per_hand": False,
                            "scored": lower_lift_kg is not None,
                        })

                    bmi = compute_bmi(
                        weight=body_weight_kg,
                        height=height_val,
                        weight_unit="kg",
                        height_unit=height_unit,
                    )

                    markers = build_baseline_markers(
                        push_ups=push_ups,
                        squats=squats,
                        run_minutes=run_minutes,
                        frequency=frequency,
                        plank_seconds=plank_seconds_val,
                        push_lift_kg=push_lift_kg,
                        pull_lift_kg=pull_lift_kg,
                        lower_lift_kg=lower_lift_kg,
                        pull_ups_reps=pull_ups_reps,
                    )

                    level = calculate_overall_level(
                        markers=markers,
                        gender=gender_clean,
                        age=age,
                        actual_weight_kg=body_weight_kg,
                        bmi=bmi,
                    )

                    evaluation = evaluate_overall_assessment(
                        markers=markers,
                        gender=gender_clean,
                        age=age,
                        actual_weight_kg=body_weight_kg,
                        bmi=bmi,
                    )

                    # Update profile equipment if changed
                    create_user_profile(
                        date_of_birth=dob_str,
                        gender=gender_clean,
                        height_value=height_val,
                        height_unit=height_unit,
                        weight_unit_preference=weight_unit,
                        equipment=selected_equipment,
                        body_weight_kg=body_weight_kg,
                        user_id=user_id,
                    )

                    answers_payload = {
                        "date_of_birth": dob_str,
                        "age_at_assessment": age,
                        "gender": gender_clean,
                        "height": {"value": height_val, "unit": height_unit},
                        "weight": {
                            "value": display_weight,
                            "unit": weight_unit,
                            "weight_kg": round(body_weight_kg, 2),
                        },
                        "equipment": selected_equipment,
                        "raw_markers": {
                            "push_ups": push_ups,
                            "squats": squats,
                            "run_minutes": run_minutes,
                            "run_distance_val": existing_raw.get("run_distance_val"),
                            "run_distance_unit": existing_raw.get("run_distance_unit"),
                            "run_distance_km": existing_raw.get("run_distance_km"),
                            "frequency": frequency,
                            "plank_seconds": round(plank_seconds_val, 1) if plank_seconds_val is not None else None,
                            "push_lift_kg": round(push_lift_kg, 2) if push_lift_kg is not None else None,
                            "pull_lift_kg": round(pull_lift_kg, 2) if pull_lift_kg is not None else None,
                            "pull_ups_reps": pull_ups_reps if pull_ups_reps is not None else None,
                            "lower_lift_kg": round(lower_lift_kg, 2) if lower_lift_kg is not None else None,
                        },
                        "raw_lifts": raw_lifts,
                        "lifts": {
                            lift["lift_type"]: {
                                "exercise": lift["exercise"],
                                "weight_kg": lift["weight_kg"],
                                "reps": lift["reps"],
                                "per_hand": lift["per_hand"],
                            }
                            for lift in raw_lifts
                        },
                        "evaluation": evaluation,
                    }

                    record_baseline_assessment(
                        experience_level=level,
                        answers=answers_payload,
                        user_id=user_id,
                        data_version=BASELINE_ASSESSMENT_DATA_VERSION,
                    )

                    col = get_user_profile_collection()
                    col.update_one(
                        {"user_id": user_id},
                        {"$set": {"dismissed_baseline_upgrade_prompt": True}}
                    )

                    st.session_state.show_upgrade_form = False
                    st.success(f"Assessment updated! Your experience level is now **{level.title()}**.")
                    st.rerun()


def ensure_onboarded():
    """
    Gates access to the dashboard.
    If the user has no profile or is inactive for 3+ months,
    renders the onboarding questionnaire and halts execution via st.stop().
    """
    if not has_user_profile():
        render_onboarding_screen(is_reassessment=False)
        st.stop()

    if is_user_inactive():
        render_onboarding_screen(is_reassessment=True)
        st.stop()
