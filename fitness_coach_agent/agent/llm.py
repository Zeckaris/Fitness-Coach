"""
agent/llm.py — Shared Gemini client construction (V9.2).
"""

from langchain_google_genai import ChatGoogleGenerativeAI

COACH_MODEL = "gemini-3.5-flash-lite"
REVIEW_MODEL = "gemini-3.5-flash-lite"


def build_coach_llm(api_key: str) -> ChatGoogleGenerativeAI:
    """Base (untooled) client for the main coach conversation."""
    return ChatGoogleGenerativeAI(model=COACH_MODEL, temperature=0.4, api_key=api_key)


def build_review_llm(api_key: str) -> ChatGoogleGenerativeAI:
    """Base client for the monthly review pipeline (narrative + theme path)."""
    return ChatGoogleGenerativeAI(model=REVIEW_MODEL, temperature=0.3, api_key=api_key)