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
from tools.baseline_assessment import record_baseline_assessment
from tools.metrics import log_metric
from utils.baseline_level import (
    calculate_overall_level,
    evaluate_overall_assessment,
)
from app.components.auth_ui import render_logout_button


def calculate_age_from_dob(dob: date) -> int:
    """Calculate age in whole years from date of birth."""
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


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

        st.divider()
        st.subheader("3. Equipment Access")
        has_weights = st.checkbox(
            "I have access to weights (dumbbells, kettlebells, or gym equipment)",
            value=False,
            help="If checked, you can provide an optional weighted lift benchmark.",
        )

        weighted_lift_val = 0.0
        if has_weights:
            weighted_lift_val = st.number_input(
                f"Heaviest 8-rep lift ({weight_unit}) — goblet squat or DB press",
                min_value=0.0,
                max_value=200.0,
                value=10.0 if weight_unit == "kg" else 25.0,
                step=0.5,
                help="Weight of the dumbbell or kettlebell you can comfortably move for 8 reps.",
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

            # Normalize weight and lift to kg for the calculator
            if weight_unit == "lb":
                weight_kg = weight_val * 0.45359237
                lift_kg = weighted_lift_val * 0.45359237 if has_weights else None
            else:
                weight_kg = weight_val
                lift_kg = weighted_lift_val if has_weights else None

            # Normalize run distance to km (stored alongside the raw value/unit)
            if run_distance_val and run_distance_unit == "mi":
                run_distance_km = run_distance_val * 1.609344
            elif run_distance_val:
                run_distance_km = float(run_distance_val)
            else:
                run_distance_km = None

            markers = {
                "push_ups": float(push_ups),
                "squats": float(squats),
                "run": float(run_minutes),
                "frequency": float(frequency),
            }
            if has_weights and lift_kg is not None and lift_kg > 0:
                markers["weighted_lift"] = float(lift_kg)

            # Score using Phase 2 calculator
            level = calculate_overall_level(
                markers=markers,
                gender=gender_clean,
                age=age,
                actual_weight_kg=weight_kg,
            )

            # Full diagnostic evaluation to store in assessment doc
            evaluation = evaluate_overall_assessment(
                markers=markers,
                gender=gender_clean,
                age=age,
                actual_weight_kg=weight_kg,
            )

            # 1. Create/update profile document
            create_user_profile(
                date_of_birth=dob.isoformat(),
                gender=gender_clean,
                height_value=float(height_val),
                height_unit=height_unit,
                weight_unit_preference=weight_unit,
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
                    "weight_kg": round(weight_kg, 2),
                },
                "has_weights": has_weights,
                "raw_markers": {
                    "push_ups": push_ups,
                    "squats": squats,
                    "run_minutes": run_minutes,
                    "run_distance_val": float(run_distance_val) if run_distance_val else None,
                    "run_distance_unit": run_distance_unit if run_distance_val else None,
                    "run_distance_km": round(run_distance_km, 2) if run_distance_km else None,
                    "frequency": frequency,
                    "weighted_lift_val": weighted_lift_val if has_weights else None,
                    "weighted_lift_unit": weight_unit if has_weights else None,
                    "weighted_lift_kg": round(lift_kg, 2) if (has_weights and lift_kg) else None,
                },
                "evaluation": evaluation,
            }

            record_baseline_assessment(
                experience_level=level,
                answers=answers_payload,
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
