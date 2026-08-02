"""
Execution-context holder for the current authenticated user's ID.
"""

from contextvars import ContextVar

_current_user_id: ContextVar[str] = ContextVar("current_user_id", default=None)


def set_current_user_id(user_id: str) -> None:
    """Set the current user's ID for this execution context."""
    _current_user_id.set(user_id)


def get_current_user_id() -> str:
    """
    Get the current user's ID.

    Raises:
        RuntimeError: if no user_id has been set in this context yet
            (e.g. called before login, or context wasn't re-set after
            a Streamlit rerun).
    """
    user_id = _current_user_id.get()
    if user_id is None:
        raise RuntimeError(
            "No user_id set in context. Ensure set_current_user_id() is "
            "called after login and on every rerun while logged in."
        )
    return user_id


def clear_current_user_id() -> None:
    """Clear the current user's ID (used on logout)."""
    _current_user_id.set(None)