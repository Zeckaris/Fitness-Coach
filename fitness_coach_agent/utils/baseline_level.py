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
    actual_weight_kg: float,
) -> tuple[float, float]:
    """
    Compute adjusted (intermediate_threshold, advanced_threshold) for a marker.

    - Push-ups & squats: scaled by bodyweight factor and age factor.
    - Weighted lift: base threshold is ratio * actual_weight_kg, scaled by age factor.
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
) -> ExperienceLevel:
    """Convenience helper returning the level string for a single marker."""
    score = score_marker(marker_name, value, gender, age, actual_weight_kg)
    return SCORE_TO_LEVEL[score]


def calculate_overall_level(
    markers: dict[str, float] | list[int | float],
    gender: Optional[str] = None,
    age: Optional[float] = None,
    actual_weight_kg: Optional[float] = None,
) -> ExperienceLevel:
    """
    Calculates the overall experience level using equal-weight averaging across all present markers.
    Accepts 4 or 5 markers (5 when weighted lift is present).

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

    Returns:
        'beginner' | 'intermediate' | 'advanced'
    """
    if not markers:
        raise ValueError("At least one marker must be provided to calculate overall level.")

    if isinstance(markers, (list, tuple)):
        scores = [float(s) for s in markers]
    elif isinstance(markers, dict):
        # Filter out None values (e.g. if weighted_lift is None when user has no weights)
        present_markers = {k: v for k, v in markers.items() if v is not None}
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
                float(score_marker(m_name, m_val, gender, age, weight))
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
) -> dict:
    """
    Full diagnostic evaluation of the baseline questionnaire.
    Returns individual marker scores, thresholds used, and the overall experience level.
    """
    g = normalize_gender(gender)
    present_markers = {k: v for k, v in markers.items() if v is not None}
    if not present_markers:
        raise ValueError("At least one marker must be provided.")

    marker_details = {}
    scores = []

    for name, val in present_markers.items():
        score = score_marker(name, val, g, age, actual_weight_kg)
        scores.append(score)
        inter_th, adv_th = compute_marker_thresholds(name, g, age, actual_weight_kg)
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

    return {
        "experience_level": overall_level,
        "average_score": round(avg_score, 3),
        "marker_scores": {k: v["score"] for k, v in marker_details.items()},
        "marker_details": marker_details,
        "adjustment_factors": {
            "bodyweight_factor": round(compute_bodyweight_factor(actual_weight_kg, g), 4),
            "age_factor": round(compute_age_factor(age), 4),
        },
    }
