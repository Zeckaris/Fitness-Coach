"""
Non-LLM default theme path generation.

default_theme_path() is a plain, pure function: no I/O, no Mongo, no
LLM calls, no decorators. Called only when the LLM theme-path generation
fails; the caller is responsible for recording theme_path_source so the
fallback is visible in the month document and on the dashboard.

Option B chain: Foundation first, following the V1.6.2 periodization
baseline. For months with fewer than 5 weeks the chain is truncated from
the right (e.g. 4 weeks -> Foundation, Volume, Intensity, Peak — no
Deload). For months with more than 5 weeks (rare) the chain cycles.
"""

from typing import List

# Option B (v1.7): Foundation-first, all five canonical themes in
# periodization order. Truncate from the right for short months.
_FALLBACK_CHAIN = ["Foundation", "Volume", "Intensity", "Peak", "Deload"]


def default_theme_path(total_weeks: int) -> List[dict]:
    """
    Build a deterministic week theme path of length total_weeks.

    Returns a list of {"week_number": int, "theme": str} dicts,
    1-indexed, matching the shape refresh_week_themes writes to
    week_plan_path.

    For total_weeks <= 5 the chain is used front-to-back (truncated).
    For total_weeks > 5 it cycles. The caller must separately record
    theme_path_source on the month document.
    """
    if total_weeks < 1:
        raise ValueError(f"total_weeks must be >= 1, got {total_weeks}")

    themes = []
    for i in range(total_weeks):
        theme = _FALLBACK_CHAIN[i % len(_FALLBACK_CHAIN)]
        themes.append({"week_number": i + 1, "theme": theme})
    return themes