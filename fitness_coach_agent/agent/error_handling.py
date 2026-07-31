"""
Shared, call-site-agnostic error-handling helpers for LLM calls (V9.2).

Handles two of the three failure types from the V9.2 design doc:

  - Type A (Gemini call failure): SDK-level error or timeout, before any
    output parsing. -> call_llm_with_retry.
  - Type B (structured-output validation failure): the LLM responded,
    but .with_structured_output() parsing/Pydantic validation failed.
    -> call_structured_llm_with_reprompt.

Retry behavior, with key rotation:
  - If the failure is "this key can't serve this request at all" (rate
    limit / HTTP 429, or the requested model isn't available on this
    key's tier / HTTP 404) -> rotate to the next key in the pool and
    retry. This continues for at most one full cycle through the
    remaining configured keys, then gives up — it does NOT keep
    rotating indefinitely, since a rate limit typically doesn't clear
    for ~24h and spinning through the same keys again would be pointless.
  - Any other failure -> a single same-key retry, as before.

Type C (Mongo/AstraDB dependency failure) is handled separately via
@mongo_guarded / @astradb_guarded — not in this module.

Fallback behavior is deliberately NOT included here. Each call site
decides its own fallback, since the right fallback differs by context.
"""

import logging
import os
import re

from pydantic import ValidationError
from langchain_core.exceptions import OutputParserException

logger = logging.getLogger(__name__)


class LLMCallFailed(Exception):
    """Raised when a plain LLM call fails: either both the initial
    attempt and same-key retry fail, or a full cycle through all
    configured keys fails for a rate-limit/model-unavailable reason."""


class StructuredOutputFailed(Exception):
    """Raised when a structured-output LLM call ultimately fails —
    validation failure surviving a re-prompt, a same-key retry failing,
    or a full key-rotation cycle failing."""


def _should_rotate_key(exc: Exception) -> bool:
    """
    True if this failure means the CURRENT key cannot serve this
    request at all — rate limit exhausted (429), or this key's tier
    doesn't have access to the requested model (404) — so retrying the
    same key is pointless.

    langchain_google_genai wraps the real error (google.genai.errors.
    ClientError, which does carry status_code) inside its own
    ChatGoogleGenerativeAIError, which does not expose status_code
    directly. So we check: the exception itself, its __cause__ (the
    original wrapped error), and finally fall back to matching the
    status code in the string message — since that's the one thing
    that's reliably present across every wrapping layer we've observed.
    """
    for candidate in (exc, getattr(exc, "__cause__", None)):
        if candidate is None:
            continue
        status_code = getattr(candidate, "status_code", None) or getattr(candidate, "code", None)
        if status_code in (429, 404):
            return True

    message = str(exc)
    if re.search(r"\b429\b", message) or "RESOURCE_EXHAUSTED" in message:
        return True
    if re.search(r"\b404\b", message) or "NOT_FOUND" in message:
        return True

    return False


class ApiKeyPool:
    """
    Round-robins across Gemini API keys. Module-level and shared across
    all requests/sessions, since rate limits (and model availability)
    are tied to the key itself, not to any one user.
    """

    def __init__(self, keys: list[str]):
        if not keys:
            raise ValueError("No Gemini API keys configured.")
        self._keys = keys
        self._idx = 0

    def current(self) -> str:
        return self._keys[self._idx]

    def rotate(self) -> str:
        old_idx = self._idx
        self._idx = (self._idx + 1) % len(self._keys)
        logger.warning(
            "Gemini key #%d unusable (rate limit or model unavailable), rotating to key #%d",
            old_idx, self._idx,
        )
        return self._keys[self._idx]

    def __len__(self) -> int:
        return len(self._keys)


def _load_keys() -> list[str]:
    numbered = []
    i = 1
    while True:
        key = os.environ.get(f"GEMINI_API_KEY_{i}")
        if not key:
            break
        numbered.append(key)
        i += 1
    if numbered:
        return numbered
    single = os.environ.get("GEMINI_API_KEY")
    return [single] if single else []


gemini_key_pool = ApiKeyPool(_load_keys())


def call_llm_with_retry(llm_factory, messages, **invoke_kwargs):
    """
    Type A handling. `llm_factory` is callable(api_key: str) -> llm.
    `invoke_kwargs` is forwarded as-is to `.invoke()` (e.g.
    config={"callbacks": [...]}).

    Returns the LLM response on success.

    Raises:
        LLMCallFailed: same-key retry failed (non-rotation error), or
            a full cycle through all configured keys failed (rotation
            error). The caller decides the fallback.
    """
    key = gemini_key_pool.current()
    try:
        return llm_factory(key).invoke(messages, **invoke_kwargs)
    except Exception as first_exc:
        if _should_rotate_key(first_exc):
            for _ in range(len(gemini_key_pool) - 1):
                key = gemini_key_pool.rotate()
                try:
                    return llm_factory(key).invoke(messages, **invoke_kwargs)
                except Exception as rotate_exc:
                    if not _should_rotate_key(rotate_exc):
                        logger.exception("LLM call failed on rotated key (non-rotation error)")
                        raise LLMCallFailed(str(rotate_exc)) from rotate_exc
                    continue
            logger.error("Exhausted full key cycle — all Gemini keys rate-limited or unavailable")
            raise LLMCallFailed("All configured Gemini API keys are rate-limited or unavailable.")
        else:
            logger.warning("LLM call failed, retrying once: %s", first_exc)
            try:
                return llm_factory(key).invoke(messages, **invoke_kwargs)
            except Exception as second_exc:
                logger.exception("LLM call failed on retry as well")
                raise LLMCallFailed(str(second_exc)) from second_exc


def call_structured_llm_with_reprompt(llm_factory, prompt, output_schema, **invoke_kwargs):
    """
    Type B handling. `llm_factory` is callable(api_key: str) -> base
    chat model (NOT yet wrapped with `.with_structured_output()` — this
    helper does that binding itself, per attempt/key).

    Validation failure -> re-prompt once on the SAME key (the key
    works; the output didn't validate).
    Rate-limit / model-not-found failure -> rotate keys (one full
    cycle max), retrying the original prompt on each. If a rotated
    key's attempt fails validation, it also gets a single re-prompt
    before moving on.
    Any other failure -> single same-key retry.

    Returns the validated Pydantic object on success.

    Raises:
        StructuredOutputFailed: all applicable attempts exhausted. The
            caller decides the fallback.
    """

    def _invoke(api_key, text):
        return llm_factory(api_key).with_structured_output(output_schema).invoke(
            text, **invoke_kwargs
        )

    def _reprompt_text(validation_exc):
        return (
            f"{prompt}\n\n"
            f"Your previous response did not match the required format. "
            f"Validation error: {validation_exc}\n"
            f"Please respond again, correcting this error."
        )

    key = gemini_key_pool.current()
    try:
        return _invoke(key, prompt)
    except (ValidationError, OutputParserException) as validation_exc:
        logger.warning("Structured output validation failed, re-prompting once: %s", validation_exc)
        try:
            return _invoke(key, _reprompt_text(validation_exc))
        except Exception as second_exc:
            logger.exception("Structured output failed on re-prompt as well")
            raise StructuredOutputFailed(str(second_exc)) from second_exc
    except Exception as call_exc:
        if _should_rotate_key(call_exc):
            for _ in range(len(gemini_key_pool) - 1):
                key = gemini_key_pool.rotate()
                try:
                    return _invoke(key, prompt)
                except (ValidationError, OutputParserException) as validation_exc:
                    logger.warning(
                        "Structured output validation failed on rotated key, re-prompting once: %s",
                        validation_exc,
                    )
                    try:
                        return _invoke(key, _reprompt_text(validation_exc))
                    except Exception as second_exc:
                        logger.exception("Structured output failed on re-prompt (rotated key) as well")
                        raise StructuredOutputFailed(str(second_exc)) from second_exc
                except Exception as rotate_exc:
                    if not _should_rotate_key(rotate_exc):
                        logger.exception("Structured output call failed on rotated key (non-rotation error)")
                        raise StructuredOutputFailed(str(rotate_exc)) from rotate_exc
                    continue
            logger.error("Exhausted full key cycle — all Gemini keys rate-limited or unavailable")
            raise StructuredOutputFailed("All configured Gemini API keys are rate-limited or unavailable.")
        else:
            logger.warning("LLM call failed during structured output attempt, retrying once: %s", call_exc)
            try:
                return _invoke(key, prompt)
            except Exception as second_exc:
                logger.exception("Structured output call failed on retry as well")
                raise StructuredOutputFailed(str(second_exc)) from second_exc