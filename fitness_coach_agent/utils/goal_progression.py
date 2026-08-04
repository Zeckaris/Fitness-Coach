"""
Deterministic (non-LLM) goal progression, used by V9.4's scheduled plan
generation to build a new month's goal from a user's most recent
confirmed goal plus its performance tier. Mirrors utils/theme_defaults.py's
default_theme_path in staying pure, non-LLM logic — the caller writes
the result directly to Mongo with status='confirmed' (no pending/confirm
dance, since there's no user in the loop to confirm anything).
"""

from datetime import datetime, timezone
from typing import Optional


_TIER_MULTIPLIERS = {
    "increase": 1.10,
    "hold": 1.0,
    "decrease": 0.90,
}


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
        "metric_name": new_metric_name,
        "target_value": new_target_value,
        "unit": new_unit,
        "baseline_value": new_baseline_value,
        "volume_targets": new_volume_targets,
        "status": "confirmed",
        "set_at": now,
        "confirmed_at": now,
    }