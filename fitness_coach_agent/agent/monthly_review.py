"""Monthly review pipeline — V8."""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI
import logging
from langfuse.langchain import CallbackHandler

from db.mongo_client import get_month_plans_collection
from tools.backlog import sync_backlog
from tools.month_plans import _current_month_id
from tools.week_plans import _weeks_in_month
from tools.progress import calculate_progress
from agent.prompts import MONTHLY_REVIEW_PROMPT, THEME_PATH_PROMPT

DEFAULT_USER_ID = "default_user"
LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

langfuse_handler = CallbackHandler()


def _previous_month_id() -> str:
    today = datetime.now(LOCAL_TZ).date()
    first_day = today.replace(day=1)
    prev_month = first_day - timedelta(days=1)
    return prev_month.strftime("%Y-%m")


def _review_llm():
    return ChatGoogleGenerativeAI(model="gemini-3.5-flash", temperature=0.3, api_key=GEMINI_API_KEY)


class MonthlyReviewOutput(BaseModel):
    narrative: str = Field(description="2-4 sentence internal record of what happened last month and why.")
    coaching_context: str = Field(
        description="Concrete notes for next month's goal-setting: intensity, reps, rounds, or "
        "exercise adjustments based on this month's adherence and progress. Written for another coach."
    )


def _generate_review(summary: dict) -> dict:
    if summary.get("goal_status") != "confirmed":
        return {"narrative": "No confirmed goal last month; nothing to review.", "coaching_context": ""}

    try:
        llm = _review_llm().with_structured_output(MonthlyReviewOutput)
        prompt = MONTHLY_REVIEW_PROMPT.format(
            goal_description=summary.get("goal_description") or "unspecified",
            adherence=summary.get("adherence"),
            volume_progress=summary.get("volume_progress"),
            metric_progress=summary.get("metric_progress"),
        )
        result = llm.invoke(prompt, config={"callbacks": [langfuse_handler]})
        return {"narrative": result.narrative, "coaching_context": result.coaching_context}
    except Exception:
        logger.exception("Monthly review narrative generation failed")
        raise


def close_out_month(month_id: str) -> dict:
    """Compute final adherence/metric delta, then LLM-generate narrative + coaching context."""
    month_plans = get_month_plans_collection()
    doc = month_plans.find_one({"user_id": DEFAULT_USER_ID, "month_id": month_id})
    if not doc:
        return {"status": "no_doc"}

    goal = doc.get("goal") or {}
    progress = calculate_progress(month_id=month_id) if (goal and goal.get("status") == "confirmed") else None

    summary = {
        "month_id": month_id,
        "goal_description": goal.get("description") if goal else None,
        "goal_status": goal.get("status") if goal else None,
        "adherence": progress["adherence"] if progress else None,
        "volume_progress": progress["volume_progress"] if progress else [],
        "metric_progress": progress["metric_progress"] if progress else None,
        "closed_at": datetime.now(ZoneInfo("UTC")),
    }

    review = _generate_review(summary)
    summary["narrative"] = review["narrative"]
    summary["coaching_context"] = review["coaching_context"]

    month_plans.update_one(
        {"user_id": DEFAULT_USER_ID, "month_id": month_id},
        {"$set": {"close_out_summary": summary, "updated_at": datetime.now(ZoneInfo("UTC"))}},
    )
    return summary


class WeekThemeOutput(BaseModel):
    week_number: int
    theme: str


class ThemePathOutput(BaseModel):
    week_plan_path: List[WeekThemeOutput]


def generate_theme_path(prev_close_out: dict, current_goal: dict, total_weeks: int) -> List[dict]:
    try:
        llm = _review_llm().with_structured_output(ThemePathOutput)
        prompt = THEME_PATH_PROMPT.format(
            total_weeks=total_weeks,
            goal_description=current_goal.get("description", "unspecified"),
            last_month_narrative=prev_close_out.get("narrative", "No prior review available."),
            last_month_adherence=prev_close_out.get("adherence"),
        )
        result = llm.invoke(prompt, config={"callbacks": [langfuse_handler]})
        themes = sorted(result.week_plan_path, key=lambda t: t.week_number)
    except Exception:
        logger.exception("Theme path generation failed (total_weeks=%s), using fallback", total_weeks)
        themes = []

    # Validate and pad if needed
    if len(themes) != total_weeks:
        themes = themes[:total_weeks]
        fallback_chain = ["Volume", "Intensity", "Volume", "Peak", "Deload"]
        while len(themes) < total_weeks:
            fallback_theme = themes[-1].theme if themes else fallback_chain[len(themes) % len(fallback_chain)]
            themes.append(WeekThemeOutput(week_number=len(themes) + 1, theme=fallback_theme))

    return [{"week_number": i + 1, "theme": t.theme} for i, t in enumerate(themes)]

def refresh_week_themes() -> str:
    """Set this month's theme path, length = actual calendar weeks in the month."""
    month_plans = get_month_plans_collection()
    current_month = _current_month_id()
    doc = month_plans.find_one({"user_id": DEFAULT_USER_ID, "month_id": current_month})

    if not doc or not doc.get("goal") or doc["goal"].get("status") != "confirmed":
        return "No confirmed goal for this month. Set a goal first."

    total_weeks = _weeks_in_month(current_month)
    prev_doc = month_plans.find_one({"user_id": DEFAULT_USER_ID, "month_id": _previous_month_id()})
    prev_close_out = (prev_doc or {}).get("close_out_summary") or {}

    week_plan_path = generate_theme_path(prev_close_out, doc["goal"], total_weeks)

    month_plans.update_one(
        {"user_id": DEFAULT_USER_ID, "month_id": current_month},
        {"$set": {
            "week_plan_path": week_plan_path,
            "updated_at": datetime.now(ZoneInfo("UTC"))
        }}
    )
    theme_str = " → ".join(t["theme"] for t in week_plan_path)
    return f"Week themes set for {current_month} ({total_weeks} weeks): {theme_str}"


def run_monthly_review() -> str:
    try:
        sync_backlog()
        prev_month = _previous_month_id()
        summary = close_out_month(prev_month)
        result = refresh_week_themes()
        return f"Closed out {prev_month}.\n{result}"
    except Exception:
        logger.exception("run_monthly_review failed")
        raise


if __name__ == "__main__":
    print(run_monthly_review())