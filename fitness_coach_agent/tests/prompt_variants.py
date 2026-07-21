"""
Diagnostic script: measure token breakdown across system prompt sections + tool schemas.

Purpose
-------
The baseline "tax" per LLM call is ~5,100 input tokens (see docs/V6.1 token audit
findings.md). This script decomposes that number into:

  1. GENERAL   — philosophy, disruption handling, plans-vs-today boundary,
                 exercise presentation rules, version notes (everything in
                 SYSTEM_PROMPT that is NOT tool descriptions).
  2. TOOLS_TEXT — the "Tools: 1. search_workout_library … 7. update_three_day_plan"
                 block inside SYSTEM_PROMPT (human-readable docs the prompt gives
                 the model about each tool).
  3. TOOL_SCHEMAS — the JSON schemas LangChain generates from each tool's Pydantic
                    args_schema when you call llm.bind_tools(TOOLS).  These are
                    machine-readable and separate from the prose above.

Together they make up the full static prefix sent on every LLM call.

This does NOT touch Mongo, Langfuse, or the graph/checkpointer — it's an
isolated LLM-only check.

Usage
-----
    python tests/prompt_variants.py

Requires GEMINI_API_KEY in your environment (same as normal).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from agent.prompts import SYSTEM_PROMPT
from agent.graph import TOOLS

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# ---------------------------------------------------------------------------
# System prompt is split at the "Tools:" / "You can call any combination" seam.
# ---------------------------------------------------------------------------

_TOOLS_SECTION_MARKER = "\nTools:\n"
_USAGE_GUIDELINES_MARKER = "\nYou can call any combination of these tools"

_tools_start = SYSTEM_PROMPT.index(_TOOLS_SECTION_MARKER)
_usage_start = SYSTEM_PROMPT.index(_USAGE_GUIDELINES_MARKER)

GENERAL = (SYSTEM_PROMPT[:_tools_start] + SYSTEM_PROMPT[_usage_start:]).strip()
TOOLS_TEXT = SYSTEM_PROMPT[_tools_start:].split(_USAGE_GUIDELINES_MARKER)[0].strip()


def _make_llm(use_tools: bool = False):
    llm = ChatGoogleGenerativeAI(
        model="gemini-3.5-flash", temperature=0.0, api_key=GEMINI_API_KEY
    )
    if use_tools:
        llm = llm.bind_tools(TOOLS)
    return llm


def _extract_tokens(response, label: str) -> int:
    """Pull input token count from the response metadata and print it."""
    usage = getattr(response, "usage_metadata", None)
    input_tokens = None

    if usage is not None:
        input_tokens = getattr(usage, "input_tokens", None)
        if input_tokens is None:
            # Some SDK versions store it as a dict-like
            input_tokens = usage.get("input_tokens") if hasattr(usage, "get") else None

    # Fallback: check response_metadata
    if input_tokens is None:
        meta = getattr(response, "response_metadata", None)
        if meta:
            raw = meta.get("usage_metadata") or meta.get("usage")
            if raw:
                input_tokens = raw.get("input_tokens") if hasattr(raw, "get") else None

    input_tokens = input_tokens or 0
    print(f"  {label}: {input_tokens} input tokens")
    return input_tokens


def run_variant(label: str, llm, system_content: str):
    """Send a single LLM call and return its input token count."""
    print(f"\n--- {label} ---")
    resp = llm.invoke([SystemMessage(content=system_content), HumanMessage(content="Hello")])
    return _extract_tokens(resp, label)


def main():
    print("=" * 70)
    print("TOKEN BREAKDOWN — system prompt sections + tool schemas")
    print("=" * 70)

    results = {}

    # 1. Full system prompt + all tool schemas (normal operating mode)
    llm_tools = _make_llm(use_tools=True)
    results["full_prompt + tool_schemas"] = run_variant(
        "Full SYSTEM_PROMPT + bound tool schemas", llm_tools, SYSTEM_PROMPT
    )

    # 2. Full system prompt only (no tools bound)
    llm_plain = _make_llm(use_tools=False)
    results["full_prompt_only"] = run_variant(
        "Full SYSTEM_PROMPT (no tools)", llm_plain, SYSTEM_PROMPT
    )

    # 3. General instructions only (no tools in prompt, no tool schemas)
    results["general_only"] = run_variant(
        "GENERAL instructions only", llm_plain, GENERAL
    )

    # 4. Tool description text only (the "Tools: 1. … 7. …" block from the prompt)
    results["tools_text_only"] = run_variant(
        "TOOLS_TEXT (tool descriptions from prompt)", llm_plain, TOOLS_TEXT
    )

    # 5.昨天 context placeholder — measure the "Context from yesterday" injection
    yesterday_sample = (
        "Context from yesterday: Check-in for 2026-07-15: sickness: cold, "
        "fatigue: slept 4 hours"
    )
    results["yesterday_sample"] = run_variant(
        "Yesterday context sample (standalone)", llm_plain, yesterday_sample
    )

    # -----------------------------------------------------------------------
    # Derived breakdown
    # -----------------------------------------------------------------------
    tool_schema_tokens = results["full_prompt + tool_schemas"] - results["full_prompt_only"]

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Full prompt + tool schemas .... {results['full_prompt + tool_schemas']:>6} tokens  (baseline per LLM call)")
    print(f"  Full prompt only .............. {results['full_prompt_only']:>6} tokens")
    print(f"    └─ GENERAL instructions ..... {results['general_only']:>6} tokens")
    print(f"    └─ TOOLS_TEXT (prompt) ....... {results['tools_text_only']:>6} tokens")
    print(f"    └─ prompt overhead/other ..... {results['full_prompt_only'] - results['general_only'] - results['tools_text_only']:>6} tokens")
    print(f"  TOOL_SCHEMAS (bound) .......... {tool_schema_tokens:>6} tokens  (full - prompt only)")
    print(f"  Yesterday context sample ...... {results['yesterday_sample']:>6} tokens  (injected at runtime)")
    print()
    print("  Prompt sections word counts:")
    print(f"    GENERAL ........... {len(GENERAL.split()):>5} words, {len(GENERAL):>5} chars")
    print(f"    TOOLS_TEXT ........ {len(TOOLS_TEXT.split()):>5} words, {len(TOOLS_TEXT):>5} chars")
    print(f"    Full SYSTEM_PROMPT  {len(SYSTEM_PROMPT.split()):>5} words, {len(SYSTEM_PROMPT):>5} chars")


if __name__ == "__main__":
    main()
