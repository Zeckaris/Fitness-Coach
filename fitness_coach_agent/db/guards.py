# db/guards.py
"""
Type C (dependency failure) guards — V9.2.

@mongo_guarded: for functions that make direct pymongo Collection calls.
Catches connection/timeout-level failures specifically — NOT
OperationFailure/WriteError/DuplicateKeyError, which are real
application-level outcomes (e.g. a genuine constraint violation), not
"the dependency is unreachable." Swallowing those under a generic
"couldn't reach your data" message would hide real bugs.

@astradb_guarded: scoped to search_fitness_knowledge_base only, per the
V9.2 design doc. On failure, returns a degraded (empty) result rather
than an error string, so the agent proceeds without retrieved context.
"""

import functools
import logging

from pymongo.errors import (
    ConnectionFailure,
    ServerSelectionTimeoutError,
    NetworkTimeout,
    AutoReconnect,
    NotPrimaryError,
)

logger = logging.getLogger(__name__)

_MONGO_DEPENDENCY_ERRORS = (
    ConnectionFailure,
    ServerSelectionTimeoutError,
    NetworkTimeout,
    AutoReconnect,
    NotPrimaryError,
)

MONGO_FALLBACK_MESSAGE = (
    "I couldn't reach your saved data just now — please try again in a moment."
)


def mongo_guarded(func):
    """Wrap a function that makes direct Mongo calls. On a
    connection/timeout-level failure, logs and returns
    MONGO_FALLBACK_MESSAGE instead of raising. Any other exception
    (including pymongo OperationFailure/WriteError/DuplicateKeyError)
    propagates unchanged."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except _MONGO_DEPENDENCY_ERRORS:
            logger.exception("Mongo dependency failure in %s", func.__name__)
            return MONGO_FALLBACK_MESSAGE

    return wrapper



def astradb_guarded(func):
    """Wrap search_fitness_knowledge_base. On any AstraDB/vectorstore
    failure, logs and returns a degraded result — the same "no results"
    string the function already returns for a genuine empty match, per
    the V9.2 design doc's "no error string" instruction. This is
    deliberately broad (plain Exception) rather than a narrow set of
    named exception types: unlike @mongo_guarded, there's no stable,
    well-documented exception hierarchy to target here (astrapy/
    langchain_astradb), and the fallback is low-stakes (agent proceeds
    without retrieved context) rather than a write-path decision — so
    over-catching is an acceptable tradeoff in this one case."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            logger.exception("AstraDB dependency failure in %s", func.__name__)
            query = kwargs.get("query") or (args[0] if args else "")
            return f"No relevant information found in the knowledge base for '{query}'."

    return wrapper