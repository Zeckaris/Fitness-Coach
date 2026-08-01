"""
Non-LLM default theme path generation.

default_theme_path() is a plain, pure function: no I/O, no Mongo, no
LLM calls, no decorators. Both callers (generate_theme_path()'s except
branch, and generate_today_plan()'s backfill recovery step) get
identical, deterministic output for a given total_weeks.
"""

from typing import List

_FALLBACK_CHAIN = ["Volume", "Intensity", "Volume", "Peak", "Deload"]


def default_theme_path(total_weeks: int) -> List[dict]:
    """
    Build a deterministic week theme path of length total_weeks, cycling
    through _FALLBACK_CHAIN. 
    Returns a list of {"week_number": int, "theme": str} dicts,
    1-indexed, matching the shape update_month_plan/refresh_week_themes
    already write to week_plan_path.
    """
    if total_weeks < 1:
        raise ValueError(f"total_weeks must be >= 1, got {total_weeks}")

    themes = []
    for i in range(total_weeks):
        theme = _FALLBACK_CHAIN[i % len(_FALLBACK_CHAIN)]
        themes.append({"week_number": i + 1, "theme": theme})
    return themes