"""
User account storage: registration and credential verification.

Email is the unique identifier (structurally validated, not
deliverability-verified in this version.
"""

import bcrypt
from email_validator import validate_email, EmailNotValidError
from datetime import datetime, timezone
from typing import List

from db.mongo_client import get_users_collection


class InvalidEmailError(Exception):
    """Raised when an email fails structural validation."""
    pass


class EmailAlreadyRegisteredError(Exception):
    """Raised when attempting to register an email that already exists."""
    pass


class InvalidCredentialsError(Exception):
    """Raised when login email/password don't match a valid account."""
    pass


def _normalize_email(email: str) -> str:
    """Validates email structure and returns it lowercased."""
    try:
        result = validate_email(email, check_deliverability=False)
    except EmailNotValidError as e:
        raise InvalidEmailError(str(e))
    return result.normalized.lower()


def create_user(email: str, password: str) -> dict:
    """
    Registers a new user.

    Raises:
        InvalidEmailError: if email fails structural validation.
        EmailAlreadyRegisteredError: if email is already registered.

    Returns:
        The created user document (including its _id).
    """
    normalized_email = _normalize_email(email)
    collection = get_users_collection()

    if collection.find_one({"email": normalized_email}):
        raise EmailAlreadyRegisteredError(
            f"An account with email '{normalized_email}' already exists."
        )

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())

    doc = {
        "email": normalized_email,
        "password_hash": password_hash,
        "created_at": datetime.now(timezone.utc),
    }
    result = collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


def get_user_by_email(email: str) -> dict | None:
    """Looks up a user document by email (case-insensitive)."""
    normalized_email = email.strip().lower()
    collection = get_users_collection()
    return collection.find_one({"email": normalized_email})


def verify_password(user_doc: dict, password: str) -> bool:
    """Checks a plaintext password against a user document's stored hash."""
    return bcrypt.checkpw(
        password.encode("utf-8"), user_doc["password_hash"]
    )


def authenticate(email: str, password: str) -> dict:
    """
    Full login check: looks up the user by email and verifies password.

    Raises:
        InvalidCredentialsError: if no matching user or password is wrong.
            Deliberately the same error for both cases, so failed login
            attempts don't reveal whether an email is registered.

    Returns:
        The authenticated user document.
    """
    user_doc = get_user_by_email(email)
    if user_doc is None or not verify_password(user_doc, password):
        raise InvalidCredentialsError("Invalid email or password.")
    return user_doc


def list_all_user_ids() -> List[str]:
    """
    All registered user IDs (str-cast ObjectIds), for scheduled jobs
    that must loop over every user. Not used by any per-request path —
    per-request code gets its single user_id via auth/context.py.
    """
    collection = get_users_collection()
    return [str(doc["_id"]) for doc in collection.find({}, {"_id": 1})]