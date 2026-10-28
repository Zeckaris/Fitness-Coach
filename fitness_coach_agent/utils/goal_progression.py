"""
Deterministic (non-LLM) goal builders for the scheduled month rollover.

``build_goal_from_multipliers`` is the current path: the progression
multipliers persisted by close_out_month are the single source of truth.
``progress_goal`` is the older tier-scaling builder retained for
compatibility; the scheduler no longer calls it.

Both stay pure and non-LLM. The caller writes the result directly to Mongo
with status='confirmed' (no pending/confirm dance, since there is no user in
the loop to confirm anything).
"""

from datetime import datetime, timezone
from typing import Optional


_TIER_MULTIPLIERS = {
    "increase": 1.10,
    "hold": 1.0,
    "decrease": 0.90,
}

_CONTINUOUS_UNITS = {"km", "mi", "m"}


def _round_target(value: float, unit: Optional[str]) -> float:
    return round(value, 1) if unit in _CONTINUOUS_UNITS else round(value)


def _area_multiplier(multipliers: dict, area: Optional[str]) -> float:
    try:
        return float((multipliers or {}).get(area, 1.0))
    except (TypeError, ValueError):
        return 1.0


def progress_goal(previous_goal: dict, tier: str, latest_metric_value: Optional[float] = None) -> dict:
    """
    Builds a new goal dict shaped like stage_month_goal's output, ready
    for a direct pre-confirmed write.

    - description: carried forward unchanged (no LLM in this path,
      consistent with V9.3's precedent of minimizing LLM-call surface
      on unattended/backend paths).
    - volume_targets: each month_target scaled by the tier multiplier
      (small, capped nudges — never a cliff).
    - metric goal (if present): baseline_value rolled forward to the
      previous goal's target_value (assume last month's target was
      reached — that's this month's new starting point). target_value's
      DELTA from the new baseline is scaled by the same tier multiplier,
      preserving direction (loss stays loss, gain stays gain).
    - status: "confirmed" directly.
    """
    multiplier = _TIER_MULTIPLIERS.get(tier, 1.0)
    now = datetime.now(timezone.utc)

    new_volume_targets = None
    if previous_goal.get("volume_targets"):
        new_volume_targets = [
            {**vt, "month_target": round(vt["month_target"] * multiplier, 1)}
            for vt in previous_goal["volume_targets"]
        ]

    new_metric_name = previous_goal.get("metric_name")
    new_target_value = previous_goal.get("target_value")
    new_baseline_value = previous_goal.get("baseline_value")
    new_unit = previous_goal.get("unit")

    if new_metric_name:
        prev_baseline = previous_goal.get("baseline_value")
        prev_target = previous_goal.get("target_value")
        if prev_baseline is not None and prev_target is not None:
            prev_delta = prev_target - prev_baseline

            if latest_metric_value is not None:
                # Real logged data beats any assumption.
                new_baseline_value = latest_metric_value
            elif tier == "increase":
                # No metric readings logged, but volume performance was
                # strong — reasonable to assume real progress happened.
                new_baseline_value = prev_target
            else:
                # No readings logged and performance was mediocre/poor —
                # don't assume progress that was never recorded.
                new_baseline_value = prev_baseline

            new_target_value = round(new_baseline_value + prev_delta * multiplier, 2)

    return {
        "description": previous_goal.get("description"),
        "focus": previous_goal.get("focus", "balanced"),
        "metric_name": new_metric_name,
        "target_value": new_target_value,
        "unit": new_unit,
        "baseline_value": new_baseline_value,
        "volume_targets": new_volume_targets,
        "status": "confirmed",
        "set_at": now,
        "confirmed_at": now,
    }


def build_goal_from_multipliers(
    previous_goal: dict,
    multipliers: dict,
    latest_metric_value: Optional[float] = None,
) -> dict:
    """
    Build a new confirmed goal from the previous goal plus the progression
    multipliers stored on the user profile.

    The persisted multipliers are the single source of truth for volume
    progression. Each previous volume_target was created with the multiplier
    snapshot stored on its goal (``previous_goal["progression_multipliers"]``),
    so the new target applies only the *change* in multiplier:
        new_target = previous_target * (current_multiplier / previous_multiplier)

    This prevents re-applying the cumulative multiplier from baseline every
    month. Legacy goals without a snapshot are treated as having multiplier
    1.0, which keeps the first multiplier-based rollover correct.

    Metric goals have no per-area multiplier. They roll forward without any
    tier scaling: if a newer metric reading exists it becomes the new
    baseline and the previous target delta is preserved; otherwise the goal
    is carried forward unchanged.
    """
    now = datetime.now(timezone.utc)
    previous_snapshot = previous_goal.get("progression_multipliers") or {}

    new_volume_targets = None
    if previous_goal.get("volume_targets"):
        new_volume_targets = []
        for vt in previous_goal["volume_targets"]:
            area = vt.get("balance_area")
            current_multiplier = _area_multiplier(multipliers, area)
            previous_multiplier = _area_multiplier(previous_snapshot, area)
            ratio = (current_multiplier / previous_multiplier) if previous_multiplier else 1.0
            # Use full_month_target as the rollover base so that a mid-month
            # scaled goal never permanently shrinks future months. Legacy goals
            # without full_month_target fall back to month_target (= full target
            # for goals that were never scaled, so backward-compatible).
            base_target = float(vt.get("full_month_target") or vt["month_target"])
            new_target = _round_target(base_target * ratio, vt.get("unit"))
            # Strip mid-month fields from the new goal — next month is always full.
            clean_vt = {k: v for k, v in vt.items() if k not in ("full_month_target", "goal_start_date")}
            new_volume_targets.append({**clean_vt, "month_target": new_target})

    new_metric_name = previous_goal.get("metric_name")
    new_baseline_value = previous_goal.get("baseline_value")
    new_target_value = previous_goal.get("target_value")
    new_unit = previous_goal.get("unit")

    if new_metric_name:
        prev_baseline = previous_goal.get("baseline_value")
        prev_target = previous_goal.get("target_value")
        if latest_metric_value is not None:
            new_baseline_value = latest_metric_value
        if prev_baseline is not None and prev_target is not None and new_baseline_value is not None:
            new_target_value = round(new_baseline_value + (prev_target - prev_baseline), 2)

    return {
        "description": previous_goal.get("description"),
        "focus": previous_goal.get("focus", "balanced"),
        "metric_name": new_metric_name,
        "target_value": new_target_value,
        "unit": new_unit,
        "baseline_value": new_baseline_value,
        "volume_targets": new_volume_targets,
        "progression_multipliers": {
            area: _area_multiplier(multipliers, area)
            for area in ("upper_body", "lower_body", "core", "cardio")
        },
        "status": "confirmed",
        "set_at": now,
        "confirmed_at": now,
    }