"""
Diagnostic script: verify whether Gemini implicit caching is hitting.

Purpose
-------
Implicit caching is supposed to be on by default for Gemini 2.5+ models
(including gemini-3.5-flash) once input exceeds ~2,048 tokens. Our baseline
system prompt + tool schemas is ~5,100 tokens, so it should qualify - but
"should qualify" isn't the same as "is hitting". This script calls the real,
tool-bound LLM (agent.graph.get_llm()) three times in a row with the exact
same system prompt and inspects whatever cache-related field the installed
SDK version returns, so we know for certain before designing anything
further.

This does NOT touch Mongo, Langfuse, or the graph/checkpointer - it's an
isolated LLM-only check so a bad result can't be blamed on unrelated state.

Usage
-----
    python tests/check_caching.py

Requires GEMINI_API_KEY to be set in your environment (same as normal).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import SystemMessage, HumanMessage

from agent.prompts import SYSTEM_PROMPT
from agent.graph import get_llm  # reuses the real, tools-bound LLM


def inspect_usage(label, response):
    print(f"\n--- {label} ---")

    usage = getattr(response, "usage_metadata", None)
    print("response.usage_metadata:", usage)

    meta = getattr(response, "response_metadata", None)
    if meta:
        raw_usage = meta.get("usage_metadata")
        print("response.response_metadata['usage_metadata']:", raw_usage)


def main():
    llm = get_llm()

    print("Call 1 (priming) ...")
    r1 = llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content="Hello")])
    inspect_usage("Call 1", r1)

    print("\nCall 2 (identical system prompt + tools, sent immediately after) ...")
    r2 = llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content="How are you?")])
    inspect_usage("Call 2", r2)

    print("\nCall 3 (same again) ...")
    r3 = llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content="What's up?")])
    inspect_usage("Call 3", r3)

    print(
        "\n----------------------------------------------------------------\n"
        "What to look for above, in calls 2 and 3 specifically:\n"
        "  - a field like 'cache_read', 'cached_content_token_count', or\n"
        "    'total_cached_tokens' with a NONZERO value\n"
        "      -> implicit caching is working; note the exact field name\n"
        "         and value so we can pull it into Langfuse for tracking.\n"
        "  - that field missing entirely, or present but always 0\n"
        "      -> caching isn't hitting; paste this full output back so we\n"
        "         can diagnose why (SDK version, request shape, etc.)\n"
        "----------------------------------------------------------------"
    )


if __name__ == "__main__":
    main()