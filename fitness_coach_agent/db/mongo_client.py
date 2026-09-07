"""
MongoDB connection for check-in persistence.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv
load_dotenv()

from pymongo import MongoClient
from pymongo.collection import Collection

MONGO_URI = os.environ.get("MONGO_URI")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "fitnesscoach")
MONGO_CHECKINS_COLLECTION = os.environ.get("MONGO_CHECKINS_COLLECTION", "checkins")


@lru_cache(maxsize=1)
def get_mongo_client() -> MongoClient:
    if not MONGO_URI:
        raise ValueError(
            "MONGO_URI not found in environment. Check your .env file."
        )
    return MongoClient(MONGO_URI)


def get_checkins_collection() -> Collection:
    """
    Returns the MongoDB collection used to store daily check-ins.
    One document per (user_id, date), upserted as new info comes in.
    """
    client = get_mongo_client()
    db = client[MONGO_DB_NAME]
    return db[MONGO_CHECKINS_COLLECTION]


MONGO_PLANS_COLLECTION = os.environ.get("MONGO_PLANS_COLLECTION", "plans")


def get_plans_collection() -> Collection:
    """
    Returns the MongoDB collection used to store the 3-day plan.
    One document per (user_id, date), where date is the day the plan
    entry is FOR (always tomorrow or later - never today, see
    tools/plans.py). Upserted/patched as the plan is generated or
    adjusted.
    """
    client = get_mongo_client()
    db = client[MONGO_DB_NAME]
    return db[MONGO_PLANS_COLLECTION]


MONGO_WEEK_PLANS_COLLECTION = os.environ.get("MONGO_WEEK_PLANS_COLLECTION", "week_plans")


def get_week_plans_collection() -> Collection:
    """
    Returns the MongoDB collection used to store weekly structure plans.
    One document per (user_id, week_id), where week_id is the date of the
    Sunday review that generated it. Each doc holds two 3-day blocks with
    their own focus and (optionally) volume targets. Written only by
    agent/weekly_review.py.
    """
    client = get_mongo_client()
    db = client[MONGO_DB_NAME]
    return db[MONGO_WEEK_PLANS_COLLECTION]


MONGO_MONTH_PLANS_COLLECTION = os.environ.get("MONGO_MONTH_PLANS_COLLECTION", "month_plans")


def get_month_plans_collection() -> Collection:
    """
    Returns the MongoDB collection used to store the monthly goal and
    week-by-week theme path. One document per (user_id, month_id). The
    goal sub-document has a status of "pending" or "confirmed" - pending
    docs are freely editable via stage_month_goal, confirmed docs are
    immutable for that month_id. Theme path is refreshed by
    agent/monthly_review.py via update_month_plan, which never touches
    the goal sub-document.
    """
    client = get_mongo_client()
    db = client[MONGO_DB_NAME]
    return db[MONGO_MONTH_PLANS_COLLECTION]


MONGO_BACKLOG_COLLECTION = os.environ.get("MONGO_BACKLOG_COLLECTION", "backlog")


def get_backlog_collection() -> Collection:
    """
    Returns the MongoDB collection used to track missed exercises.
    One document per missed exercise (not per day, not per user) - see
    tools/backlog.py for the sync/read logic. Populated by
    sync_backlog(), consulted by update_three_day_plan's generation
    sequence via get_backlog().
    """
    client = get_mongo_client()
    db = client[MONGO_DB_NAME]
    return db[MONGO_BACKLOG_COLLECTION]


MONGO_METRICS_COLLECTION = os.environ.get("MONGO_METRICS_COLLECTION", "metrics")

def get_metrics_collection() -> Collection:
    """
    Returns the MongoDB collection used to store tracked progress
    metrics (e.g. body_weight, run_distance). One document per
    (user_id, metric_name, date), upserted as new readings come in.
    Separate from checkins - metrics are logged when relevant, not on
    every daily check-in.
    """
    client = get_mongo_client()
    db = client[MONGO_DB_NAME]
    return db[MONGO_METRICS_COLLECTION]


MONGO_USERS_COLLECTION = os.environ.get("MONGO_USERS_COLLECTION", "users")
def get_users_collection() -> Collection:
    """
    Returns the MongoDB collection used to store user accounts.
    One document per user: {email, password_hash, created_at}.
    _id (ObjectId) is used as the canonical user_id throughout the app,
    cast to str at the auth boundary. email has a unique index, enforced
    on first connection via ensure_email_index(). Email is structurally
    validated at registration but not deliverability-verified in this
    version.
    """
    client = get_mongo_client()
    db = client[MONGO_DB_NAME]
    return db[MONGO_USERS_COLLECTION]


MONGO_USER_PROFILE_COLLECTION = os.environ.get("MONGO_USER_PROFILE_COLLECTION", "user_profile")
def get_user_profile_collection() -> Collection:
    """
    Returns the MongoDB collection used to store user profiles.
    One document per user: {DOB, gender, height, unit_preferences, experience_level}.
    experience_level is a denormalized mirror of the latest baseline assessment.
    """
    client = get_mongo_client()
    db = client[MONGO_DB_NAME]
    return db[MONGO_USER_PROFILE_COLLECTION]


MONGO_BASELINE_ASSESSMENT_COLLECTION = os.environ.get("MONGO_BASELINE_ASSESSMENT_COLLECTION", "baseline_assessment")
def get_baseline_assessment_collection() -> Collection:
    """
    Returns the MongoDB collection used to store baseline assessments.
    Append-only, one doc per assessment event.
    Source of truth for experience_level.
    """
    client = get_mongo_client()
    db = client[MONGO_DB_NAME]
    return db[MONGO_BASELINE_ASSESSMENT_COLLECTION]

def ensure_email_index() -> None:
    """
    Creates a unique index on email if it doesn't already exist.
    Idempotent - safe to call on every app startup.
    """
    collection = get_users_collection()
    collection.create_index("email", unique=True)

# Confirm the connection works
if __name__ == "__main__":
    checkins = get_checkins_collection()
    print(f"Connected to MongoDB collection '{checkins.name}'")
    print(f"Existing check-in count: {checkins.count_documents({})}")

    plans = get_plans_collection()
    print(f"Connected to MongoDB collection '{plans.name}'")
    print(f"Existing plan count: {plans.count_documents({})}")
    
    week_plans = get_week_plans_collection()
    print(f"Connected to MongoDB collection '{week_plans.name}'")
    print(f"Existing week plan count: {week_plans.count_documents({})}")

    month_plans = get_month_plans_collection()
    print(f"Connected to MongoDB collection '{month_plans.name}'")
    print(f"Existing month plan count: {month_plans.count_documents({})}")

    backlog = get_backlog_collection()
    print(f"Connected to MongoDB collection '{backlog.name}'")
    print(f"Existing backlog count: {backlog.count_documents({})}")

    metrics = get_metrics_collection()
    print(f"Connected to MongoDB collection '{metrics.name}'")
    print(f"Existing metrics count: {metrics.count_documents({})}")
    
    users = get_users_collection()
    print(f"Connected to MongoDB collection '{users.name}'")
    print(f"Existing user count: {users.count_documents({})}")

    user_profile = get_user_profile_collection()
    print(f"Connected to MongoDB collection '{user_profile.name}'")
    print(f"Existing user profile count: {user_profile.count_documents({})}")

    baseline_assessment = get_baseline_assessment_collection()
    print(f"Connected to MongoDB collection '{baseline_assessment.name}'")
    print(f"Existing baseline assessment count: {baseline_assessment.count_documents({})}")