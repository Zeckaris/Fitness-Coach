# db/guards.py
"""
Type C (dependency failure) guards 

@mongo_guarded: for functions that make direct pymongo Collection calls.
Catches connection/timeout-level failures specifically.

@astradb_guarded: scoped to search_fitness_knowledge_base only
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
    failure, logs and returns a degraded result This is
    deliberately broad (plain Exception) rather than a narrow set of
    named exception types: unlike @mongo_guarded, there's no stable,
    well-documented exception hierarchy to target here (astrapy/
    langchain_astradb)"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            logger.exception("AstraDB dependency failure in %s", func.__name__)
            query = kwargs.get("query") or (args[0] if args else "")
            return f"No relevant information found in the knowledge base for '{query}'."

    return wrapper