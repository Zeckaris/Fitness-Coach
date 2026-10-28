"""Shared exercise prescription finalisation and display formatting.

All plan save paths call ``finalize_exercises`` so a stored exercise never has
an empty set count or amount. Read paths call ``format_exercise_prescription``
so old plans with missing fields still display using library defaults.
"""

import re
from typing import Mapping, Optional

from tools.workout_library import get_exercise_metadata
from utils.baseline_targets import get_theme_dosing_structure

_EMPTY_VALUES = {"", "none", "null", "n/a", "na", "?", "-", "—"}
_DEFAULT_SETS = 1
_DEFAULT_REPS = "10"
_DEFAULT_UNIT = "reps"

_UNIT_ALIASES = {
    "rep": "reps",
    "reps": "reps",
    "repetition": "reps",
    "repetitions": "reps",
    "second": "seconds",
    "seconds": "seconds",
    "sec": "seconds",
    "secs": "seconds",
    "s": "seconds",
    "minute": "minutes",
    "minutes": "minutes",
    "min": "minutes",
    "mins": "minutes",
    "reps_per_side": "reps_per_side",
    "rep_per_side": "reps_per_side",
    "per_side": "reps_per_side",
    "each_side": "reps_per_side",
    "km": "km",
    "kilometre": "km",
    "kilometres": "km",
    "kilometer": "km",
    "kilometers": "km",
    "mi": "mi",
    "mile": "mi",
    "miles": "mi",
    "m": "m",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
}

_UNIT_LABELS = {
    "reps": "reps",
    "seconds": "sec",
    "minutes": "min",
    "reps_per_side": "reps per side",
    "km": "km",
    "mi": "mi",
    "m": "m",
}

_DISTANCE_UNITS = {"km", "mi", "m"}
_TIMED_UNITS = {"seconds", "minutes"}
_PER_SIDE_UNITS = {"reps_per_side"}
_REPS_UNIT_PATTERN = re.compile(
    r"\b(reps?|repetitions?|secs?|seconds?|mins?|minutes?|km|mi|m|per\s+side|each\s+side)\b",
    re.IGNORECASE,
)


def _clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in _EMPTY_VALUES else text


def _positive_int(value) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _positive_number(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _normalise_unit(value) -> str:
    text = _clean_text(value).lower().replace("-", "_").replace(" ", "_")
    return _UNIT_ALIASES.get(text, "")


def _library_baseline(name: str) -> dict:
    metadata = get_exercise_metadata(name) or {}
    baseline = metadata.get("baseline")
    return baseline if isinstance(baseline, dict) else {}


def _unit_from_reps(reps: str) -> str:
    lowered = (reps or "").lower()
    if "per side" in lowered or "each side" in lowered:
        return "reps_per_side"
    match = _REPS_UNIT_PATTERN.search(lowered)
    if not match:
        return ""
    token = match.group(1).lower()
    if token.startswith("sec"):
        return "seconds"
    if token.startswith("min"):
        return "minutes"
    if token.startswith("rep"):
        return "reps"
    if token == "km":
        return "km"
    if token == "mi":
        return "mi"
    return "m"


def _resolve_unit(plan_unit, library_unit, reps: str) -> str:
    for candidate in (plan_unit, library_unit):
        normalised = _normalise_unit(candidate)
        if normalised:
            return normalised
    return _unit_from_reps(reps) or _DEFAULT_UNIT


def _easier_end(reps: str) -> str:
    """Use the first (easier) value from a range, keeping any suffix."""
    text = _clean_text(reps)
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if not match:
        return text
    return text[: match.start()] + match.group(1) + text[match.end() :]


def _format_number(value) -> str:
    number = _positive_number(value)
    if number is None:
        return str(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _dose_reps(target_quantity, unit: str, reps_per_set: int) -> str:
    if unit in _DISTANCE_UNITS:
        return f"{_format_number(target_quantity)} {unit}"
    if unit in _PER_SIDE_UNITS:
        return f"{reps_per_set} reps per side"
    if unit in _TIMED_UNITS:
        label = _UNIT_LABELS.get(unit, unit)
        return f"{reps_per_set} {label}"
    return f"{reps_per_set} reps"


def _target_map(daily_targets) -> dict:
    result = {}
    for target in daily_targets or []:
        if not isinstance(target, Mapping):
            continue
        exercise = _clean_text(target.get("exercise"))
        quantity = _positive_number(target.get("daily_target"))
        if exercise and quantity is not None:
            result[exercise] = {
                "daily_target": quantity,
                "unit": target.get("unit"),
            }
    return result


def resolve_exercise_prescription(
    exercise: Mapping,
    *,
    week_focus: Optional[str] = None,
    daily_targets=None,
    for_display: bool = False,
) -> dict:
    """Return positive sets plus a non-empty reps/duration amount.

    Resolution order for save paths: goal/target dose, valid plan amount,
    library default, then a safe generic default. Display paths skip target
    dosing and only fall back from stored values to the library.
    """
    item = dict(exercise or {})
    name = _clean_text(item.get("name"))
    category = _clean_text(item.get("category")).lower() or "main"
    baseline = _library_baseline(name)

    library_sets = _positive_int(baseline.get("sets"))
    library_reps = _clean_text(baseline.get("reps"))
    library_unit = _normalise_unit(baseline.get("unit"))

    plan_sets = _positive_int(item.get("sets"))
    plan_reps = _clean_text(item.get("reps"))
    plan_unit = _normalise_unit(item.get("unit"))

    target_quantity = _positive_number(item.get("target_quantity"))
    target_map = _target_map(daily_targets)
    target_from_map = False

    if target_quantity is None and category == "main" and name in target_map:
        target_quantity = target_map[name]["daily_target"]
        target_from_map = True
        if not plan_unit:
            plan_unit = _normalise_unit(target_map[name].get("unit"))

    set_group_id = _clean_text(item.get("set_group_id"))

    if (
        not for_display
        and category == "main"
        and target_quantity is not None
        and not set_group_id
    ):
        dose_unit = (
            plan_unit
            or _normalise_unit((target_map.get(name) or {}).get("unit"))
            or library_unit
            or _resolve_unit(plan_unit, library_unit, plan_reps)
            or _DEFAULT_UNIT
        )
        sets, reps_per_set = get_theme_dosing_structure(
            week_focus, int(round(target_quantity))
        )
        return {
            "sets": _positive_int(sets) or _DEFAULT_SETS,
            "reps": _dose_reps(target_quantity, dose_unit, reps_per_set),
            "unit": dose_unit,
            "target_quantity": target_quantity,
            "source": "target",
        }

    if not for_display and category in ("warmup", "cooldown"):
        base_sets = library_sets or plan_sets or _DEFAULT_SETS
        base_reps = library_reps or plan_reps or _DEFAULT_REPS
        unit = _resolve_unit(library_unit, plan_unit, base_reps)
        sets = max(1, min(base_sets, 2))
        reps = _easier_end(base_reps) or _DEFAULT_REPS
        source = "library_light"
    else:
        base_sets = plan_sets or library_sets or _DEFAULT_SETS
        base_reps = plan_reps or library_reps or _DEFAULT_REPS
        unit = _resolve_unit(plan_unit, library_unit, base_reps)
        sets = max(1, base_sets)
        reps = base_reps or _DEFAULT_REPS
        source = "target_map" if target_from_map else ("plan" if plan_reps or plan_sets else "library")

    return {
        "sets": sets,
        "reps": reps,
        "unit": unit,
        "target_quantity": target_quantity,
        "source": source,
    }


def finalize_exercise(
    exercise: Mapping,
    *,
    week_focus: Optional[str] = None,
    daily_targets=None,
) -> dict:
    """Return a copy of an exercise with non-empty sets and reps/duration."""
    result = dict(exercise or {})
    resolved = resolve_exercise_prescription(
        result,
        week_focus=week_focus,
        daily_targets=daily_targets,
        for_display=False,
    )
    result["sets"] = resolved["sets"]
    result["reps"] = resolved["reps"]
    result["unit"] = resolved["unit"]
    if _clean_text(result.get("category")).lower() in ("warmup", "cooldown"):
        result["target_quantity"] = None
        return result
    target_quantity = resolved.get("target_quantity")
    if target_quantity is not None:
        result["target_quantity"] = (
            int(target_quantity) if float(target_quantity).is_integer() else target_quantity
        )
    return result


def finalize_exercises(
    exercises,
    *,
    week_focus: Optional[str] = None,
    daily_targets=None,
) -> list[dict]:
    """Finalise every exercise before a plan is persisted."""
    return [
        finalize_exercise(exercise, week_focus=week_focus, daily_targets=daily_targets)
        for exercise in (exercises or [])
    ]


def _unit_suffix_for_reps(reps: str, unit: str) -> str:
    """Return the missing unit suffix, without duplicating an existing label."""
    lowered = (reps or "").lower()
    if unit == "reps":
        return "" if re.search(r"\breps?\b", lowered) else "reps"
    if unit == "seconds":
        return "" if re.search(r"\b(sec|secs|second|seconds)\b", lowered) else "sec"
    if unit == "minutes":
        return "" if re.search(r"\b(min|mins|minute|minutes)\b", lowered) else "min"
    if unit == "reps_per_side":
        if "per side" in lowered or "each side" in lowered:
            return ""
        return "per side" if re.search(r"\breps?\b", lowered) else "reps per side"
    if unit in _DISTANCE_UNITS:
        return "" if re.search(rf"\b{re.escape(unit)}\b", lowered) else unit
    return ""


def format_exercise_prescription(exercise: Mapping) -> str:
    """Format an exercise as e.g. ``2x10 reps``, ``2x30 sec``, ``2x10 reps per side``.

    Missing stored values fall back to the library, so old plans display
    correctly without being rewritten.
    """
    resolved = resolve_exercise_prescription(exercise, for_display=True)
    sets = resolved["sets"]
    reps = resolved["reps"]
    unit = resolved["unit"]

    suffix = _unit_suffix_for_reps(reps, unit)
    if suffix:
        reps = f"{reps} {suffix}"

    if unit in _DISTANCE_UNITS and sets == 1:
        return reps
    return f"{sets}x{reps}"
