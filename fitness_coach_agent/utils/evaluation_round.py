"""Evaluation rounds: scheduled evaluation movements, tick results and baselines.

An evaluation round is a general container: a start date plus every scheduled
evaluation movement in the remaining days of a month. Nothing here assumes the
round belongs to a new user's first days; later triggers (long gap, level raise)
can build a round from any start date.

This module is pure: it only inspects plan/exercise dicts and baseline records.
Persistence lives in ``tools.evaluation``.
"""

from datetime import date, datetime
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Exercise -> evaluation movement mapping.
#
# Keep all aliases here so a new substitution is a one-line change. "squat" and
# "plank" deliberately include replaceable substitutes because the current
# library has no plain Squat / Plank entry. If a real one appears, the preferred
# names below win during plan selection (see evaluation_plan.py).
# ---------------------------------------------------------------------------
MOVEMENT_PUSH = "push"
MOVEMENT_SQUAT = "squat"
MOVEMENT_PLANK = "plank"
MOVEMENT_PULL_UP = "pull_up"
MOVEMENT_CARDIO = "cardio"

EXERCISE_TO_MOVEMENT: dict[str, str] = {
    # Push
    "push-up": MOVEMENT_PUSH,
    "incline push-up": MOVEMENT_PUSH,
    "kneeling push-up": MOVEMENT_PUSH,
    "close-grip push-up": MOVEMENT_PUSH,
    "decline push-up": MOVEMENT_PUSH,
    "side push-up": MOVEMENT_PUSH,
    # Squat and its current substitutes
    "squat": MOVEMENT_SQUAT,
    "bodyweight squat": MOVEMENT_SQUAT,
    "air squat": MOVEMENT_SQUAT,
    "bodyweight air squat": MOVEMENT_SQUAT,
    "squat to overhead reach": MOVEMENT_SQUAT,
    # Plank and its current substitutes
    "plank": MOVEMENT_PLANK,
    "front plank": MOVEMENT_PLANK,
    "forearm plank": MOVEMENT_PLANK,
    "high plank": MOVEMENT_PLANK,
    "plank hold": MOVEMENT_PLANK,
    "bodyweight incline side plank": MOVEMENT_PLANK,
    # Pull
    "pull-up": MOVEMENT_PULL_UP,
    "band assisted pull-up": MOVEMENT_PULL_UP,
    "biceps pull-up": MOVEMENT_PULL_UP,
    # Cardio (never updates a baseline, but does count toward fulfilment)
    "jump rope": MOVEMENT_CARDIO,
    "mountain climber": MOVEMENT_CARDIO,
    "star jump (male)": MOVEMENT_CARDIO,
    "skater hops": MOVEMENT_CARDIO,
    "astride jumps (male)": MOVEMENT_CARDIO,
    "burpee": MOVEMENT_CARDIO,
    "jack burpee": MOVEMENT_CARDIO,
}

# Real movements that should be used ahead of substitutes when the library has
# them. Ordered by preference.
PREFERRED_EXERCISE_NAMES: dict[str, tuple[str, ...]] = {
    MOVEMENT_SQUAT: ("Squat", "Bodyweight Squat", "Air Squat", "Bodyweight Air Squat"),
    MOVEMENT_PLANK: ("Plank", "Front Plank", "Forearm Plank", "High Plank", "Plank Hold"),
}

# Baseline metadata per tracked movement. Cardio is absent on purpose.
MOVEMENT_BASELINE_SPECS: dict[str, dict] = {
    MOVEMENT_PUSH: {
        "assessment_key": "push_ups",
        "score_marker": "push_ups",
        "unit": "reps",
    },
    MOVEMENT_SQUAT: {
        "assessment_key": "squats",
        "score_marker": "squats",
        "unit": "reps",
    },
    MOVEMENT_PLANK: {
        "assessment_key": "plank_seconds",
        "score_marker": "plank",
        "unit": "seconds",
    },
    MOVEMENT_PULL_UP: {
        "assessment_key": "pull_ups_reps",
        "score_marker": "pull_ups",
        "unit": "reps",
    },
}


def _normalise_name(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


def movement_for_exercise(name: str) -> Optional[str]:
    """Return the evaluation movement an exercise measures, or None.

    Falls back to the workout library's movement family so cardio variants are
    recognised even when they are not listed above. Cardio never has a baseline.
    """
    normalised = _normalise_name(name)
    if normalised in EXERCISE_TO_MOVEMENT:
        return EXERCISE_TO_MOVEMENT[normalised]

    try:
        from utils.evaluation_plan import movement_family
    except Exception:
        return None

    family = movement_family(name)
    if family == "cardio":
        return MOVEMENT_CARDIO
    if family == "push":
        return MOVEMENT_PUSH
    if family == "pull_up":
        return MOVEMENT_PULL_UP
    return None


def preferred_exercise_for_movement(
    movement: str,
    available_names: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Return the best real exercise name for a movement, if it exists.

    Substitutes remain in EXERCISE_TO_MOVEMENT; this only decides which name to
    pick first when a real Squat or Plank is available.
    """
    preferred = PREFERRED_EXERCISE_NAMES.get(movement)
    if not preferred:
        return None

    if available_names is None:
        try:
            from tools.workout_library import _WORKOUTS
        except Exception:
            return None
        available_names = [w.get("name") for w in _WORKOUTS if w.get("name")]

    by_normalised = {_normalise_name(name): name for name in available_names}
    for candidate in preferred:
        match = by_normalised.get(_normalise_name(candidate))
        if match:
            return match
    return None


def is_exercise_ticked(exercise: dict) -> bool:
    """A tick is either the boolean flag or completed quantity reaching target."""
    if exercise.get("completed"):
        return True
    target = exercise.get("target_quantity")
    if target is None:
        return False
    try:
        return float(exercise.get("completed_quantity") or 0) >= float(target)
    except (TypeError, ValueError):
        return False


def _entry_amount(entry: dict) -> Optional[float]:
    target = entry.get("target_quantity")
    try:
        if target is not None:
            return float(target)
    except (TypeError, ValueError):
        pass

    completed = entry.get("completed_quantity")
    try:
        if completed is not None:
            return float(completed)
    except (TypeError, ValueError):
        pass
    return None


def _pair_claim_and_easier(entries: list[dict]) -> tuple[Optional[dict], Optional[dict]]:
    claim = next((e for e in entries if e.get("pair_role") == "claim"), None)
    easier = next((e for e in entries if e.get("pair_role") == "easier"), None)

    if claim is None or easier is None:
        with_amount = [(e, _entry_amount(e)) for e in entries]
        with_amount = [(e, v) for e, v in with_amount if v is not None]
        if with_amount:
            highest = max(with_amount, key=lambda item: item[1])
            lowest = min(with_amount, key=lambda item: item[1])
            claim = claim or highest[0]
            easier = easier or lowest[0]
    return claim, easier


def _pair_tick_state(entries: list[dict]) -> tuple[Optional[dict], Optional[dict], bool, bool]:
    claim, easier = _pair_claim_and_easier(entries)
    claim_ticked = bool(claim and is_exercise_ticked(claim))
    easier_ticked = bool(easier and is_exercise_ticked(easier))
    return claim, easier, claim_ticked, easier_ticked


def pair_outcome(entries: list[dict]) -> dict:
    """Resolve one claimed/easier pair.

    - both ticked: the claimed (higher) amount wins
    - only the easier ticked: the easier amount wins
    - only the claim ticked: the claim amount wins
    - neither ticked: no result
    """
    claim, easier, claim_ticked, easier_ticked = _pair_tick_state(entries)

    if not claim_ticked and not easier_ticked:
        return {"ticked": False, "value": None, "role": None, "source_exercise": None}
    if claim_ticked:
        chosen, role = claim, "claim"
    else:
        chosen, role = easier, "easier"

    return {
        "ticked": True,
        "value": _entry_amount(chosen),
        "role": role,
        "source_exercise": (chosen or {}).get("name"),
        "unit": (chosen or {}).get("unit"),
    }


def resolve_movement_baseline(movements: list[dict], movement: Optional[str] = None) -> dict:
    """Running baseline result for one movement across every day it was shown.

    Claim wins when it was ticked on at least half of the shown days (exactly
    half is enough). Otherwise the lower amount wins if it was ticked at least
    once. No ticks -> no new baseline. A one-day round is decided by that day.
    """
    records = [
        m for m in (movements or [])
        if m.get("kind") == "pair"
        and (movement is None or m.get("movement") == movement)
    ]

    if not records:
        return {
            "movement": movement,
            "shown_days": 0,
            "claim_ticked_days": 0,
            "lower_ticked_days": 0,
            "ticked": False,
            "value": None,
            "role": None,
            "source_exercise": None,
            "unit": None,
        }

    by_day: dict = {}
    for record in records:
        by_day.setdefault(record.get("date") or id(record), []).append(record)

    claim_ticked_days = 0
    lower_ticked_days = 0
    claim_candidates = []
    lower_candidates = []

    for day_records in by_day.values():
        day_claim_ticked = False
        day_lower_ticked = False
        for record in day_records:
            claim, easier, claim_ticked, easier_ticked = _pair_tick_state(record.get("entries") or [])
            if claim_ticked:
                day_claim_ticked = True
                claim_candidates.append(claim)
            if easier_ticked:
                day_lower_ticked = True
                lower_candidates.append(easier)
        claim_ticked_days += int(day_claim_ticked)
        lower_ticked_days += int(day_lower_ticked)

    shown_days = len(by_day)
    needed = (shown_days + 1) // 2

    if claim_ticked_days >= needed:
        chosen, role = claim_candidates[-1], "claim"
    elif lower_ticked_days >= 1:
        chosen, role = lower_candidates[-1], "easier"
    else:
        chosen, role = None, None

    return {
        "movement": movement or records[0].get("movement"),
        "shown_days": shown_days,
        "claim_ticked_days": claim_ticked_days,
        "lower_ticked_days": lower_ticked_days,
        "ticked": chosen is not None,
        "value": _entry_amount(chosen) if chosen else None,
        "role": role,
        "source_exercise": (chosen or {}).get("name"),
        "unit": (chosen or {}).get("unit"),
    }


def _is_evaluation_day(day: dict) -> bool:
    if day.get("untracked"):
        return True
    return any(
        ex.get("pair_id")
        for ex in (day.get("exercises") or [])
    )


def _day_movements(day: dict) -> list[dict]:
    """Build the scheduled movements for one evaluation day.

    A pair_id is one movement (claim + easier are alternatives). A plain main
    entry counts only when it is cardio: later non-pair entries for tracked
    movements are ordinary session doses, not evaluation movements.
    """
    exercises = day.get("exercises") or []
    date_str = day.get("date") or ""

    pairs: dict[str, list[dict]] = {}
    ordered_pair_ids: list[str] = []
    for ex in exercises:
        pair_id = ex.get("pair_id")
        if not pair_id:
            continue
        if pair_id not in pairs:
            pairs[pair_id] = []
            ordered_pair_ids.append(pair_id)
        pairs[pair_id].append(ex)

    movements: list[dict] = []
    for pair_id in ordered_pair_ids:
        entries = pairs[pair_id]
        name = next((e.get("name") for e in entries if e.get("name")), "")
        movement = movement_for_exercise(name)
        outcome = pair_outcome(entries)
        movements.append({
            "movement": movement,
            "kind": "pair",
            "date": date_str,
            "pair_id": pair_id,
            "entries": entries,
            "ticked": outcome["ticked"],
            "outcome": outcome,
        })

    for ex in exercises:
        if ex.get("pair_id"):
            continue
        if (ex.get("category") or "main") != "main":
            continue
        movement = movement_for_exercise(ex.get("name"))
        if movement != MOVEMENT_CARDIO:
            continue
        ticked = is_exercise_ticked(ex)
        movements.append({
            "movement": MOVEMENT_CARDIO,
            "kind": "cardio",
            "date": date_str,
            "pair_id": None,
            "entries": [ex],
            "ticked": ticked,
            "outcome": {
                "ticked": ticked,
                "value": _entry_amount(ex),
                "role": None,
                "source_exercise": ex.get("name"),
                "unit": ex.get("unit"),
            },
        })

    return movements


def build_evaluation_round(
    plan_days: Iterable[dict],
    *,
    month_id: Optional[str] = None,
    start_date: Optional[str] = None,
) -> Optional[dict]:
    """Build a round from saved plan days.

    ``start_date`` lets a future trigger choose any start; when omitted the
    earliest evaluation day in the supplied docs is the start. ``month_id``
    filters the round to one month.
    """
    days = []
    for day in plan_days or []:
        date_str = day.get("date")
        if not date_str:
            continue
        if month_id and date_str[:7] != month_id:
            continue
        if start_date and date_str < start_date:
            continue
        if not _is_evaluation_day(day):
            continue
        movements = _day_movements(day)
        if movements:
            days.append((date_str, day, movements))

    if not days:
        return None

    days.sort(key=lambda item: item[0])
    all_movements = [m for _, _, movements in days for m in movements]
    dates = [date_str for date_str, _, _ in days]

    return {
        "month_id": month_id or dates[0][:7],
        "start_date": dates[0],
        "end_date": dates[-1],
        "dates": dates,
        "movements": all_movements,
    }


def _movement_is_ticked(movement: dict) -> bool:
    entries = movement.get("entries") or []
    if movement.get("kind") == "pair":
        return pair_outcome(entries)["ticked"]
    return any(is_exercise_ticked(ex) for ex in entries) or bool(movement.get("ticked"))


def evaluate_round(round_obj: Optional[dict]) -> dict:
    """Return scheduled/ticked counts and whether the round is fulfilled.

    Fulfilment is movement-based: about half or more of all scheduled
    evaluation movements must be ticked. A round with only one scheduled day is
    that day's whole round, so one tick fulfils it and the half rule cannot
    block it.
    """
    if not round_obj:
        return {
            "scheduled_movements": 0,
            "ticked_movements": 0,
            "scheduled_days": 0,
            "fulfilled": False,
        }

    movements = round_obj.get("movements") or []
    scheduled = len(movements)
    ticked = sum(1 for m in movements if _movement_is_ticked(m))
    scheduled_days = len(round_obj.get("dates") or round_obj.get("scheduled_dates") or [])

    if scheduled == 0:
        fulfilled = False
    elif scheduled_days <= 1:
        fulfilled = ticked >= 1
    else:
        fulfilled = ticked >= (scheduled + 1) // 2

    return {
        "scheduled_movements": scheduled,
        "ticked_movements": ticked,
        "scheduled_days": scheduled_days,
        "fulfilled": fulfilled,
    }


def is_round_fulfilled(round_obj: Optional[dict]) -> bool:
    return evaluate_round(round_obj)["fulfilled"]


def apply_baseline_update(
    current: Optional[dict],
    value: float,
    *,
    unit: Optional[str] = None,
    source_exercise: Optional[str] = None,
    updated_at: Optional[datetime] = None,
    signature: Optional[str] = None,
) -> dict:
    """Return the new baseline record, preserving the old one in history."""
    record = dict(current or {})
    old_value = record.get("value")
    history = list(record.get("history") or [])

    if old_value is not None and old_value != value:
        history.append({
            "value": old_value,
            "unit": record.get("unit"),
            "source_exercise": record.get("source_exercise"),
            "updated_at": record.get("updated_at"),
        })

    return {
        "value": value,
        "unit": unit or record.get("unit"),
        "source_exercise": source_exercise or record.get("source_exercise"),
        "updated_at": updated_at,
        "history": history,
        "last_signature": signature,
    }


def baseline_value_from_raw_markers(movement: str, raw_markers: Optional[dict]) -> Optional[float]:
    """Seed an updated baseline from the user's latest onboarding marker."""
    spec = MOVEMENT_BASELINE_SPECS.get(movement)
    if not spec or not raw_markers:
        return None

    keys = [spec["assessment_key"]]
    if movement == MOVEMENT_PLANK:
        keys.extend(("plank_seconds", "plank"))
    if movement == MOVEMENT_PULL_UP:
        keys.extend(("pull_ups_reps", "pull_ups"))

    for key in keys:
        value = raw_markers.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def month_end_date(month_id: str) -> str:
    """Return the last YYYY-MM-DD date of a month."""
    year, month = map(int, month_id.split("-"))
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    from datetime import timedelta
    return (next_first - timedelta(days=1)).strftime("%Y-%m-%d")
