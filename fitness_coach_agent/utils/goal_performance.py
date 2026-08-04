"""
Deterministic (non-LLM) performance evaluation used by V9.4's scheduled
plan generation to decide whether an auto-started monthly goal should
increase, hold, or decrease from the user's most recent confirmed goal.

Deliberately does NOT read close_out_summary or _calculate_adherence()
(tools/progress.py) — close-out may never have run for a given month,
and adherence is not month-scoped (it always reflects the last 7 days
from *today*, not the month being evaluated). Instead this reads data
that IS correctly month-scoped: volume/metric progress percentages, and
backlog items filtered to the month's own date range.
"""

import logging

from db.mongo_client import get_backlog_collection
from tools.progress import _calculate_volume_progress, _calculate_metric_progress
from auth.context import get_current_user_id

logger = logging.getLogger(__name__)

# Conservative thresholds per explicit design instruction: small nudges,
# never a cliff. Tunable without a design change.
INCREASE_PCT_THRESHOLD = 85
DECREASE_PCT_THRESHOLD = 50
HEAVY_BACKLOG_THRESHOLD = 5   # open backlog items sourced from this month
LOW_BACKLOG_THRESHOLD = 2


def _open_backlog_count(month_id: str) -> int:
    """Open backlog items whose source_date falls within month_id."""
    backlog = get_backlog_collection()
    return backlog.count_documents({
        "user_id": get_current_user_id(),
        "status": "open",
        "source_date": {"$regex": f"^{month_id}"},
    })


def evaluate_goal_performance(goal: dict, month_id: str) -> dict:
    """
    Returns: {"tier": "increase" | "hold" | "decrease",
              "avg_pct": float | None,
              "open_backlog_count": int}

    avg_pct is the mean of all available progress percentages: each
    volume_target's pct (via _calculate_volume_progress, correctly
    month-scoped) plus the metric goal's pct if present. Missing/None
    values are excluded from the average, not treated as zero.

    Backlog can pull the tier down even when avg_pct alone looks
    borderline-okay, but never independently pushes it up past what
    avg_pct supports.
    """
    pct_values = []

    for vp in _calculate_volume_progress(goal, month_id):
        if vp["pct"] is not None:
            pct_values.append(vp["pct"])

    metric_progress = _calculate_metric_progress(goal)
    latest_metric_value = metric_progress.get("latest_value") if metric_progress else None
    if metric_progress and metric_progress.get("pct") is not None:
        pct_values.append(metric_progress["pct"])

    avg_pct = sum(pct_values) / len(pct_values) if pct_values else None
    backlog_count = _open_backlog_count(month_id)

    if avg_pct is None:
        tier = "hold"
    elif avg_pct >= INCREASE_PCT_THRESHOLD and backlog_count <= LOW_BACKLOG_THRESHOLD:
        tier = "increase"
    elif avg_pct < DECREASE_PCT_THRESHOLD or backlog_count >= HEAVY_BACKLOG_THRESHOLD:
        tier = "decrease"
    else:
        tier = "hold"

    return {
        "tier": tier,
        "avg_pct": avg_pct,
        "open_backlog_count": backlog_count,
        "latest_metric_value": latest_metric_value,
    }