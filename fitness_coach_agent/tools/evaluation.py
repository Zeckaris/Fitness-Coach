"""Persistence for evaluation-round ticks, baselines and fulfilment.

Ticks are read from plan documents (the same ``completed`` /
``completed_quantity`` fields the workout screen writes). This module turns
those ticks into immediate baseline updates and, once the round is fulfilled,
re-scores the experience level and refreshes only an unconfirmed staged goal.
"""

import copy
import logging
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from auth.context import get_current_user_id
from db.mongo_client import (
    get_month_plans_collection,
    get_plans_collection,
    get_user_profile_collection,
)
from tools.baseline_assessment import (
    BASELINE_ASSESSMENT_DATA_VERSION,
    get_latest_baseline_assessment,
    record_baseline_assessment,
)
from tools.user_profile import get_user_profile
from utils.baseline_level import calculate_overall_level, compute_bmi
from utils.evaluation_round import (
    MOVEMENT_BASELINE_SPECS,
    apply_baseline_update,
    baseline_value_from_raw_markers,
    build_evaluation_round,
    evaluate_round,
    resolve_movement_baseline,
)

logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")
UTC = ZoneInfo("UTC")


def _now() -> datetime:
    return datetime.now(UTC)


def _month_id_for_date(date_str: str) -> str:
    return date_str[:7]


def _next_month_id(month_id: str) -> str:
    year, month = map(int, month_id.split("-"))
    month += 1
    if month > 12:
        month = 1
        year += 1
    return f"{year}-{month:02d}"


def _latest_raw_markers(user_id: str) -> dict:
    assessment = get_latest_baseline_assessment(user_id)
    return copy.deepcopy(((assessment or {}).get("answers") or {}).get("raw_markers") or {})


def _seeded_baseline_record(movement: str, raw_markers: dict) -> dict:
    value = baseline_value_from_raw_markers(movement, raw_markers)
    if value is None:
        return {}
    spec = MOVEMENT_BASELINE_SPECS.get(movement) or {}
    return {
        "value": value,
        "unit": spec.get("unit"),
        "source_exercise": None,
        "updated_at": None,
        "history": [],
    }


def _apply_round_baselines(
    round_obj: Optional[dict],
    *,
    raw_markers: dict,
    baselines: dict,
    now: datetime,
) -> list[dict]:
    """Recompute each movement's running baseline from all ticks so far.

    Cardio is intentionally skipped. A movement whose result has not changed
    does not get another history entry.
    """
    if not round_obj:
        return []

    pair_movements = [
        m for m in (round_obj.get("movements") or [])
        if m.get("kind") == "pair" and m.get("movement")
    ]
    movement_keys = []
    for movement in pair_movements:
        key = movement.get("movement")
        if key not in movement_keys:
            movement_keys.append(key)

    updates = []
    for movement_key in movement_keys:
        spec = MOVEMENT_BASELINE_SPECS.get(movement_key)
        if not spec:
            continue

        result = resolve_movement_baseline(pair_movements, movement_key)
        if result.get("value") is None:
            continue

        existing = baselines.get(movement_key)
        if existing and existing.get("value") == result["value"]:
            continue

        record = dict(existing or {})
        if not record:
            record = _seeded_baseline_record(movement_key, raw_markers)

        baselines[movement_key] = apply_baseline_update(
            record,
            result["value"],
            unit=spec.get("unit"),
            source_exercise=result.get("source_exercise"),
            updated_at=now,
            signature="{}|{}".format(result.get("role"), result["value"]),
        )
        updates.append({
            "movement": movement_key,
            "value": result["value"],
            "unit": spec.get("unit"),
            "source_exercise": result.get("source_exercise"),
            "history": baselines[movement_key]["history"],
        })
    return updates


def _raw_to_score_markers(raw_markers: dict) -> dict:
    """Convert onboarding raw marker names to baseline_level score markers."""
    mapping = {
        "push_ups": "push_ups",
        "squats": "squats",
        "run_minutes": "run",
        "run": "run",
        "frequency": "frequency",
        "plank_seconds": "plank",
        "plank": "plank",
        "pull_ups_reps": "pull_ups",
        "pull_ups": "pull_ups",
        "push_lift_kg": "push_lift",
        "push_lift": "push_lift",
        "pull_lift_kg": "pull_lift",
        "pull_lift": "pull_lift",
        "lower_lift_kg": "lower_lift",
        "lower_lift": "lower_lift",
        "weighted_lift": "weighted_lift",
        "strength_marker": "strength_marker",
    }
    markers = {}
    for raw_key, score_key in mapping.items():
        value = raw_markers.get(raw_key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            markers[score_key] = number
    return markers


def _age_from_dob(dob_text: str) -> Optional[float]:
    try:
        dob = date.fromisoformat(str(dob_text))
    except (TypeError, ValueError):
        return None
    today = datetime.now(LOCAL_TZ).date()
    return float(today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day)))


def _demographics(user_id: str, answers: dict) -> dict:
    profile = get_user_profile(user_id) or {}
    gender = answers.get("gender") or profile.get("gender")
    age = answers.get("age_at_assessment")
    if age is None:
        age = _age_from_dob(answers.get("date_of_birth") or profile.get("date_of_birth") or "")
    if age is None:
        age = 30.0

    weight_kg = ((answers.get("weight") or {}).get("weight_kg"))
    if weight_kg is None:
        weight_kg = profile.get("body_weight_kg")
    if weight_kg is None:
        weight_kg = 70.0

    height = answers.get("height") or profile.get("height") or {}
    height_value = height.get("value") if isinstance(height, dict) else height
    height_unit = (height.get("unit") if isinstance(height, dict) else "cm") or "cm"
    height_cm = None
    try:
        height_cm = float(height_value)
        if height_unit.lower() in ("in", "inch", "inches"):
            height_cm *= 2.54
    except (TypeError, ValueError):
        height_cm = None

    bmi = None
    if height_cm and height_cm > 0:
        try:
            bmi = compute_bmi(weight_kg, height_cm, "kg", "cm")
        except (TypeError, ValueError):
            bmi = None

    return {
        "gender": gender,
        "age": float(age),
        "actual_weight_kg": float(weight_kg),
        "height_cm": height_cm,
        "bmi": bmi,
    }


def _rescore_experience_from_baselines(
    *,
    user_id: str,
    baselines: dict,
    now: datetime,
) -> Optional[str]:
    assessment = get_latest_baseline_assessment(user_id)
    answers = copy.deepcopy(((assessment or {}).get("answers") or {}))
    raw_markers = copy.deepcopy(answers.get("raw_markers") or {})

    for movement, record in (baselines or {}).items():
        spec = MOVEMENT_BASELINE_SPECS.get(movement)
        if not spec or record.get("value") is None:
            continue
        raw_markers[spec["assessment_key"]] = record["value"]

    score_markers = _raw_to_score_markers(raw_markers)
    demo = _demographics(user_id, answers)
    if not score_markers or not demo.get("gender"):
        logger.info("evaluation rescore skipped: insufficient markers or demographics")
        return None

    level = calculate_overall_level(
        markers=score_markers,
        gender=demo["gender"],
        age=demo["age"],
        actual_weight_kg=demo["actual_weight_kg"],
        height_cm=demo.get("height_cm"),
        bmi=demo.get("bmi"),
    )

    answers["raw_markers"] = raw_markers
    answers["evaluation_rescore_at"] = now
    record_baseline_assessment(
        experience_level=level,
        answers=answers,
        user_id=user_id,
        data_version=BASELINE_ASSESSMENT_DATA_VERSION,
    )
    logger.info("evaluation rescore: experience_level=%s", level)
    return level


def should_recompute_staged_goal(goal: Optional[dict]) -> bool:
    """Only an unconfirmed staged goal may be recomputed; confirmed is locked."""
    return bool(goal) and goal.get("status") != "confirmed"


def recompute_pending_goal_for_next_month(
    month_id: str,
    *,
    user_id: Optional[str] = None,
    collection=None,
    stage=None,
) -> dict:
    """Recompute next month's staged goal, never touching a confirmed one."""
    uid = user_id or get_current_user_id()
    target_month = _next_month_id(month_id)
    col = collection or get_month_plans_collection()
    doc = col.find_one({"user_id": uid, "month_id": target_month})
    goal = (doc or {}).get("goal") or {}

    if not should_recompute_staged_goal(goal):
        return {"recomputed": False, "target_month": target_month, "reason": "confirmed_or_missing"}

    if stage is None:
        from tools.month_plans import stage_month_goal
        stage = getattr(stage_month_goal, "func", stage_month_goal)

    result = stage(
        description=goal.get("description") or "Monthly fitness goal",
        focus=goal.get("focus"),
        metric_name=goal.get("metric_name"),
        target_value=goal.get("target_value"),
        unit=goal.get("unit"),
        baseline_value=goal.get("baseline_value"),
        month_id=target_month,
    )
    logger.info("evaluation round recomputed staged goal for %s", target_month)
    return {
        "recomputed": True,
        "target_month": target_month,
        "result": result,
    }


def _round_already_fulfilled(month_doc: Optional[dict], start_date: str) -> bool:
    for summary in ((month_doc or {}).get("evaluation_rounds") or []):
        if summary.get("start_date") == start_date and summary.get("fulfilled_at"):
            return True
    return False


def capture_evaluation_ticks_for_date(
    date_str: Optional[str] = None,
    *,
    plan_doc: Optional[dict] = None,
    user_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Capture ticks on one evaluation day and process the current round.

    Baseline updates happen immediately, regardless of fulfilment. Once the
    round is fulfilled, the experience level is re-scored and next month's
    pending goal is recomputed exactly once for that round.
    """
    uid = user_id or get_current_user_id()
    if not uid:
        return {"status": "no_user"}

    now = now or _now()
    plans = get_plans_collection()
    if plan_doc is None:
        date_str = date_str or datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
        plan_doc = plans.find_one({"user_id": uid, "date": date_str})
    if not plan_doc or not plan_doc.get("untracked"):
        return {"status": "no_evaluation_plan"}

    date_str = plan_doc.get("date") or date_str
    month_id = _month_id_for_date(date_str)

    raw_markers = _latest_raw_markers(uid)
    profile = get_user_profile(uid) or {}
    baselines = copy.deepcopy(profile.get("evaluation_baselines") or {})

    round_docs = list(plans.find({
        "user_id": uid,
        "date": {"$regex": f"^{month_id}"},
        "untracked": True,
    }))
    round_obj = build_evaluation_round(round_docs, month_id=month_id)

    updates = _apply_round_baselines(
        round_obj,
        raw_markers=raw_markers,
        baselines=baselines,
        now=now,
    )

    if updates:
        get_user_profile_collection().update_one(
            {"user_id": uid},
            {
                "$set": {"evaluation_baselines": baselines, "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    evaluation = evaluate_round(round_obj)

    month_col = get_month_plans_collection()
    month_doc = month_col.find_one({"user_id": uid, "month_id": month_id})
    start_date = (round_obj or {}).get("start_date")
    already = bool(start_date) and _round_already_fulfilled(month_doc, start_date)

    if evaluation["fulfilled"] and start_date and not already:
        rescore_level = _rescore_experience_from_baselines(
            user_id=uid,
            baselines=baselines,
            now=now,
        )
        goal_result = recompute_pending_goal_for_next_month(month_id, user_id=uid)

        month_col.update_one(
            {"user_id": uid, "month_id": month_id},
            {
                "$push": {"evaluation_rounds": {
                    "start_date": start_date,
                    "end_date": round_obj.get("end_date"),
                    "dates": round_obj.get("dates"),
                    "scheduled_movements": evaluation["scheduled_movements"],
                    "ticked_movements": evaluation["ticked_movements"],
                    "fulfilled_at": now,
                    "experience_level": rescore_level,
                    "next_month_goal_recomputed": goal_result.get("recomputed", False),
                }},
                "$set": {"updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    return {
        "status": "captured",
        "date": date_str,
        "month_id": month_id,
        "baseline_updates": updates,
        "round": evaluation,
        "round_start": start_date,
        "already_fulfilled": already,
    }


def capture_evaluation_round_ticks(
    month_id: Optional[str] = None,
    *,
    user_id: Optional[str] = None,
) -> dict:
    """Process every evaluation day in a month's current round."""
    uid = user_id or get_current_user_id()
    month_id = month_id or datetime.now(LOCAL_TZ).strftime("%Y-%m")
    plans = get_plans_collection()
    docs = list(plans.find({
        "user_id": uid,
        "date": {"$regex": f"^{month_id}"},
        "untracked": True,
    }))
    results = []
    for doc in sorted(docs, key=lambda d: d.get("date", "")):
        results.append(capture_evaluation_ticks_for_date(
            doc.get("date"),
            plan_doc=doc,
            user_id=uid,
        ))
    return {"month_id": month_id, "days": results}
