"""
Deterministic (non-LLM) baseline experience level calculator per v1.8.0.
Calculates individual marker scores and equal-weighted overall experience level
(beginner, intermediate, advanced) from onboarding baseline questionnaire inputs.

Pure calculator functions — no database connections, no LLM calls.
"""

from typing import Literal, Optional

Gender = Literal["male", "female"]
ExperienceLevel = Literal["beginner", "intermediate", "advanced"]

# Reference weights (kg) for baseline calibration at age <= 40
REFERENCE_WEIGHT_KG: dict[Gender, float] = {
    "male": 70.0,
    "female": 60.0,
}

# Base threshold values: (intermediate_threshold, advanced_threshold)
# For push_ups and squats: rep counts at reference bodyweight and age <= 40.
# For run: continuous minutes (unisex, unscaled).
# For frequency: weekly workouts (unisex, unscaled).
# For plank: front plank hold in seconds (unisex, scaled by age and BMI).
BASE_THRESHOLDS: dict[str, dict[str, tuple[float, float]]] = {
    "push_ups": {
        "male": (10.0, 25.0),
        "female": (6.0, 18.0),
    },
    "squats": {
        "male": (15.0, 40.0),
        "female": (11.0, 28.0),
    },
    "run": {
        "unisex": (15.0, 30.0),
    },
    "frequency": {
        "unisex": (2.0, 4.0),
    },
    "plank": {
        "unisex": (30.0, 60.0),
    },
}

# Base ratio of bodyweight for weighted lift: 8-rep goblet squat or DB press (kg)
# e.g., Male Intermediate starts at 0.17 * BW, Advanced starts at 0.47 * BW
WEIGHTED_LIFT_BW_RATIOS: dict[Gender, tuple[float, float]] = {
    "male": (0.17, 0.47),
    "female": (0.13, 0.34),
}

MARKER_ALIASES: dict[str, str] = {
    "push_ups": "push_ups",
    "pushups": "push_ups",
    "push-ups": "push_ups",
    "squats": "squats",
    "bodyweight_squats": "squats",
    "weighted_lift": "weighted_lift",
    "weighted": "weighted_lift",
    "goblet_squat": "weighted_lift",
    "db_press": "weighted_lift",
    "run": "run",
    "continuous_run": "run",
    "longest_run": "run",
    "frequency": "frequency",
    "workout_frequency": "frequency",
    "weekly_frequency": "frequency",
    "plank": "plank",
    "plank_hold": "plank",
    "plank_seconds": "plank",
    "front_plank": "plank",
}

SCORE_TO_LEVEL: dict[int, ExperienceLevel] = {
    1: "beginner",
    2: "intermediate",
    3: "advanced",
}

LEVEL_TO_SCORE: dict[ExperienceLevel, int] = {
    "beginner": 1,
    "intermediate": 2,
    "advanced": 3,
}

# Reference BMI (kg/m^2) for neutral isometric plank threshold calibration.
# Midpoint of normal healthy adult BMI (18.5 - 24.9).
REFERENCE_BMI: float = 22.0


def compute_bmi(
    weight: float,
    height: float,
    weight_unit: str = "kg",
    height_unit: str = "cm",
) -> float:
    """
    Computes Body Mass Index (BMI in kg/m^2) with unit normalization.
    Supports weight in 'kg' or 'lb', height in 'cm', 'in', or 'm'.

    Used strictly for mechanical leverage adjustment on isometric holds (plank).
    Never displayed as a health label or used to restrict exercises.
    """
    try:
        w = float(weight)
        h = float(height)
    except (TypeError, ValueError):
        raise ValueError("Weight and height must be numeric.")

    if w <= 0 or h <= 0:
        raise ValueError("Weight and height must be positive numbers.")

    w_u = weight_unit.strip().lower()
    if w_u in ("lb", "lbs"):
        w_kg = w * 0.45359237
    elif w_u == "kg":
        w_kg = w
    else:
        raise ValueError(f"Unsupported weight unit: {weight_unit!r}. Must be 'kg' or 'lb'.")

    h_u = height_unit.strip().lower()
    if h_u == "cm":
        h_m = h / 100.0
    elif h_u in ("in", "inch", "inches"):
        h_m = h * 0.0254
    elif h_u in ("m", "meter", "meters"):
        h_m = h
    else:
        raise ValueError(f"Unsupported height unit: {height_unit!r}. Must be 'cm', 'in', or 'm'.")

    if h_m <= 0:
        raise ValueError("Height in meters must be positive.")

    return w_kg / (h_m ** 2)


def compute_bmi_factor(
    bmi: Optional[float],
    reference_bmi: float = REFERENCE_BMI,
) -> float:
    """
    Mild BMI adjustment factor for isometric plank hold:
    Torque across the lumbar spine and trunk increases with body mass and leverage.
    Higher BMI loosens thresholds mildly: (reference_bmi / actual_bmi) ** 0.3,
    floored at 0.75 so a minimum standard is always maintained.
    For BMI <= reference_bmi (lean or normal weight), returns 1.0 (no penalty/tightening).
    """
    if bmi is None or bmi <= 0:
        return 1.0
    if bmi <= reference_bmi:
        return 1.0
    factor = (reference_bmi / bmi) ** 0.3
    return max(0.75, factor)


def normalize_gender(gender: str) -> Gender:
    """Normalize gender string to 'male' or 'female'."""
    g = gender.strip().lower()
    if g in ("male", "m"):
        return "male"
    if g in ("female", "f"):
        return "female"
    raise ValueError(f"Unsupported gender: {gender!r}. Must be 'male' or 'female'.")


def compute_bodyweight_factor(actual_weight_kg: float, gender: Gender) -> float:
    """
    Bodyweight factor for push-ups and bodyweight squats:
    (reference_weight / actual_weight) ^ 0.5, clamped to [0.6, 1.5].
    Heavier bodies move more mass per rep, so rep threshold scales down moderately.
    """
    if actual_weight_kg <= 0:
        return 1.0
    ref_weight = REFERENCE_WEIGHT_KG[gender]
    factor = (ref_weight / actual_weight_kg) ** 0.5
    return max(0.6, min(1.5, factor))


def compute_age_factor(age: float) -> float:
    """
    Age factor: 1.0 for age <= 40, then -5% per decade past 40, floored at 0.5.
    Evaluated continuously as (age - 40) / 10 * 0.05 to avoid arbitrary cliff-edges
    on milestone birthdays.
    """
    if age <= 40.0:
        return 1.0
    reduction = 0.05 * ((age - 40.0) / 10.0)
    return max(0.5, 1.0 - reduction)


def compute_marker_thresholds(
    marker_name: str,
    gender: str,
    age: float,
    actual_weight_kg: float = 70.0,
    height_cm: Optional[float] = None,
    bmi: Optional[float] = None,
) -> tuple[float, float]:
    """
    Compute adjusted (intermediate_threshold, advanced_threshold) for a marker.

    - Push-ups & squats: scaled by bodyweight factor and age factor.
    - Weighted lift: base threshold is ratio * actual_weight_kg, scaled by age factor.
    - Plank: unisex, starting at (30s, 60s), scaled by age factor and BMI factor.
             Bodyweight is not double-counted since push-ups/squats already use it.
    - Run & frequency: unisex and unscaled (no bodyweight or age scaling).
    """
    canonical_marker = MARKER_ALIASES.get(marker_name.strip().lower())
    if not canonical_marker:
        raise ValueError(
            f"Unknown marker name: {marker_name!r}. "
            f"Valid options: {list(MARKER_ALIASES.keys())}"
        )

    g = normalize_gender(gender)
    age_f = compute_age_factor(age)
    bw_f = compute_bodyweight_factor(actual_weight_kg, g)

    if canonical_marker in ("push_ups", "squats"):
        base_inter, base_adv = BASE_THRESHOLDS[canonical_marker][g]
        return (base_inter * bw_f * age_f, base_adv * bw_f * age_f)

    if canonical_marker == "weighted_lift":
        ratio_inter, ratio_adv = WEIGHTED_LIFT_BW_RATIOS[g]
        return (
            ratio_inter * actual_weight_kg * age_f,
            ratio_adv * actual_weight_kg * age_f,
        )

    if canonical_marker == "plank":
        base_inter, base_adv = BASE_THRESHOLDS["plank"]["unisex"]
        if bmi is not None:
            bmi_f = compute_bmi_factor(bmi)
        elif height_cm is not None and height_cm > 0 and actual_weight_kg > 0:
            calc_bmi = compute_bmi(actual_weight_kg, height_cm, "kg", "cm")
            bmi_f = compute_bmi_factor(calc_bmi)
        else:
            bmi_f = 1.0
        return (base_inter * age_f * bmi_f, base_adv * age_f * bmi_f)

    if canonical_marker in ("run", "frequency"):
        # Unisex, unscaled
        return BASE_THRESHOLDS[canonical_marker]["unisex"]

    raise ValueError(f"Unhandled canonical marker: {canonical_marker}")


def score_marker(
    marker_name: str,
    value: float,
    gender: str,
    age: float,
    actual_weight_kg: float = 70.0,
    height_cm: Optional[float] = None,
    bmi: Optional[float] = None,
) -> int:
    """
    Scores a single marker against its beginner (1), intermediate (2), or advanced (3) band.

    Returns:
        1 for beginner
        2 for intermediate
        3 for advanced
    """
    intermediate_th, advanced_th = compute_marker_thresholds(
        marker_name=marker_name,
        gender=gender,
        age=age,
        actual_weight_kg=actual_weight_kg,
        height_cm=height_cm,
        bmi=bmi,
    )

    if value >= advanced_th:
        return 3
    if value >= intermediate_th:
        return 2
    return 1


def get_marker_level(
    marker_name: str,
    value: float,
    gender: str,
    age: float,
    actual_weight_kg: float = 70.0,
    height_cm: Optional[float] = None,
    bmi: Optional[float] = None,
) -> ExperienceLevel:
    """Convenience helper returning the level string for a single marker."""
    score = score_marker(
        marker_name,
        value,
        gender,
        age,
        actual_weight_kg,
        height_cm=height_cm,
        bmi=bmi,
    )
    return SCORE_TO_LEVEL[score]


def calculate_overall_level(
    markers: dict[str, Optional[float]] | list[int | float],
    gender: Optional[str] = None,
    age: Optional[float] = None,
    actual_weight_kg: Optional[float] = None,
    height_cm: Optional[float] = None,
    bmi: Optional[float] = None,
) -> ExperienceLevel:
    """
    Calculates the overall experience level using equal-weight averaging across all present markers.
    Accepts 4 to 6 markers (optional weighted lift and plank hold).

    Thresholds for overall level:
        average < 1.5     -> beginner
        1.5 <= average < 2.5 -> intermediate
        average >= 2.5    -> advanced

    Args:
        markers: Either a dict of {marker_name: raw_value} or a list of individual 1/2/3 scores.
                 If raw values are passed in a dict, gender, age, and actual_weight_kg must be provided.
        gender: 'male' or 'female' (required if markers is a dict of raw values).
        age: User age in years (required if markers is a dict of raw values).
        actual_weight_kg: User bodyweight in kg (required if markers is a dict of raw values).
        height_cm: User height in cm (optional, for plank BMI calculation).
        bmi: Precomputed BMI (optional, for plank BMI calculation).

    Returns:
        'beginner' | 'intermediate' | 'advanced'
    """
    if not markers:
        raise ValueError("At least one marker must be provided to calculate overall level.")

    if isinstance(markers, (list, tuple)):
        scores = [float(s) for s in markers]
    elif isinstance(markers, dict):
        # Filter out None values and non-positive optional markers (plank, weighted_lift)
        present_markers = {}
        for k, v in markers.items():
            if v is None:
                continue
            canonical = MARKER_ALIASES.get(str(k).strip().lower())
            if canonical in ("weighted_lift", "plank"):
                try:
                    if float(v) <= 0:
                        continue
                except (TypeError, ValueError):
                    continue
            present_markers[k] = v

        if not present_markers:
            raise ValueError("No valid marker values provided.")

        # Check if values are already scores (1, 2, 3) and demographic args omitted
        all_numeric_scores = all(
            isinstance(v, (int, float)) and v in (1, 2, 3) for v in present_markers.values()
        )
        if all_numeric_scores and (gender is None or age is None):
            scores = [float(v) for v in present_markers.values()]
        else:
            if gender is None or age is None:
                raise ValueError(
                    "gender and age must be provided when scoring raw marker values."
                )
            weight = actual_weight_kg if actual_weight_kg is not None else REFERENCE_WEIGHT_KG[normalize_gender(gender)]
            scores = [
                float(score_marker(m_name, m_val, gender, age, weight, height_cm=height_cm, bmi=bmi))
                for m_name, m_val in present_markers.items()
            ]
    else:
        raise TypeError(f"markers must be a dict or list, got {type(markers)}")

    avg_score = sum(scores) / len(scores)

    if avg_score < 1.5:
        return "beginner"
    if avg_score < 2.5:
        return "intermediate"
    return "advanced"


def evaluate_overall_assessment(
    markers: dict[str, Optional[float]],
    gender: str,
    age: float,
    actual_weight_kg: float,
    height_cm: Optional[float] = None,
    bmi: Optional[float] = None,
) -> dict:
    """
    Full diagnostic evaluation of the baseline questionnaire.
    Returns individual marker scores, thresholds used, and the overall experience level.
    """
    g = normalize_gender(gender)
    present_markers = {}
    for k, v in markers.items():
        if v is None:
            continue
        canonical = MARKER_ALIASES.get(str(k).strip().lower())
        if canonical in ("weighted_lift", "plank"):
            try:
                if float(v) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
        present_markers[k] = v

    if not present_markers:
        raise ValueError("At least one marker must be provided.")

    marker_details = {}
    scores = []

    for name, val in present_markers.items():
        score = score_marker(name, val, g, age, actual_weight_kg, height_cm=height_cm, bmi=bmi)
        scores.append(score)
        inter_th, adv_th = compute_marker_thresholds(
            name, g, age, actual_weight_kg, height_cm=height_cm, bmi=bmi
        )
        marker_details[name] = {
            "value": val,
            "score": score,
            "level": SCORE_TO_LEVEL[score],
            "intermediate_threshold": round(inter_th, 2),
            "advanced_threshold": round(adv_th, 2),
        }

    avg_score = sum(scores) / len(scores)
    if avg_score < 1.5:
        overall_level = "beginner"
    elif avg_score < 2.5:
        overall_level = "intermediate"
    else:
        overall_level = "advanced"

    if bmi is not None:
        bmi_f = compute_bmi_factor(bmi)
    elif height_cm is not None and height_cm > 0 and actual_weight_kg > 0:
        calc_bmi = compute_bmi(actual_weight_kg, height_cm, "kg", "cm")
        bmi_f = compute_bmi_factor(calc_bmi)
    else:
        bmi_f = 1.0

    return {
        "experience_level": overall_level,
        "average_score": round(avg_score, 3),
        "marker_scores": {k: v["score"] for k, v in marker_details.items()},
        "marker_details": marker_details,
        "adjustment_factors": {
            "bodyweight_factor": round(compute_bodyweight_factor(actual_weight_kg, g), 4),
            "age_factor": round(compute_age_factor(age), 4),
            "bmi_factor": round(bmi_f, 4),
        },
    }
