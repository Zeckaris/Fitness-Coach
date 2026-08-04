"""
Throwaway fixture seeder for manually testing V9.4's goal-performance /
goal-progression branch (case 3: confirmed goal history exists, none for
the current month). NOT part of the shipped app — same spirit as V9.3's
tests/debug_backfill_schema.py.

Creates a dedicated test user (does not touch real user data), seeds a
confirmed goal + completion data for the PREVIOUS month at a controlled
performance level, and leaves the CURRENT month goal-less. Running
`python -m scripts.scheduled_plan_generation` afterward will route this
user through case 3.

NOTE: assumes the metrics collection shape implied by
db/mongo_client.py's get_metrics_collection docstring — one doc per
(user_id, metric_name, date) with a "value" field. Adjust
_seed_metric_reading() if tools/metrics.py's actual shape differs.

Usage:
    python -m tests.seed_goal_progression_fixtures increase
    python -m tests.seed_goal_progression_fixtures hold
    python -m tests.seed_goal_progression_fixtures decrease
    python -m tests.seed_goal_progression_fixtures decrease --no-metric
    python -m tests.seed_goal_progression_fixtures cleanup
"""

import argparse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from db.mongo_client import (
    get_users_collection,
    get_month_plans_collection,
    get_plans_collection,
    get_metrics_collection,
    get_backlog_collection,
)

LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")
TEST_EMAIL = "v94.fixture.test@example.local"

# (exercise, balance_area, unit) — reused from real workouts.json entries
# confirmed valid during actual scheduler test runs.
EXERCISES = [
    ("Push-Ups", "upper_body", "reps"),
    ("Bodyweight Squats", "lower_body", "reps"),
    ("Sit-Up", "core", "reps"),
    ("High Knees", "cardio", "minutes"),
]

MONTH_TARGET = 200.0

SCENARIOS = {
    "increase": {"completion_pct": 0.95, "backlog_count": 0, "metric_progress_pct": 0.90},
    "hold":     {"completion_pct": 0.65, "backlog_count": 2, "metric_progress_pct": 0.60},
    "decrease": {"completion_pct": 0.20, "backlog_count": 6, "metric_progress_pct": 0.15},
}


def _previous_month_id() -> str:
    today = datetime.now(LOCAL_TZ).date()
    first_day = today.replace(day=1)
    prev_month = first_day - timedelta(days=1)
    return prev_month.strftime("%Y-%m")


def _get_or_create_test_user() -> str:
    users = get_users_collection()
    existing = users.find_one({"email": TEST_EMAIL})
    if existing:
        return str(existing["_id"])
    result = users.insert_one({
        "email": TEST_EMAIL,
        "password_hash": b"fixture-only-not-a-real-login",
        "created_at": datetime.now(timezone.utc),
    })
    return str(result.inserted_id)


def _seed_confirmed_goal(user_id: str, month_id: str, include_metric: bool) -> None:
    volume_targets = [
        {"exercise": name, "unit": unit, "month_target": MONTH_TARGET, "balance_area": area}
        for name, area, unit in EXERCISES
    ]
    now = datetime.now(timezone.utc)
    goal = {
        "description": "V9.4 fixture goal — full body strength",
        "metric_name": "body_weight" if include_metric else None,
        "target_value": 75.0 if include_metric else None,
        "unit": "kg" if include_metric else None,
        "baseline_value": 80.0 if include_metric else None,
        "volume_targets": volume_targets,
        "status": "confirmed",
        "set_at": now,
        "confirmed_at": now,
    }
    get_month_plans_collection().update_one(
        {"user_id": user_id, "month_id": month_id},
        {"$set": {"goal": goal, "updated_at": now},
         "$setOnInsert": {"created_at": now, "week_plan_path": []}},
        upsert=True,
    )


def _seed_completion(user_id: str, month_id: str, completion_pct: float) -> None:
    """One plan doc per exercise, with completed_quantity set to hit the
    target completion_pct against MONTH_TARGET."""
    plans = get_plans_collection()
    now = datetime.now(timezone.utc)
    for i, (name, area, unit) in enumerate(EXERCISES):
        date_str = f"{month_id}-{i + 1:02d}"
        completed = round(MONTH_TARGET * completion_pct, 1)
        plans.update_one(
            {"user_id": user_id, "date": date_str},
            {"$set": {
                "user_id": user_id,
                "date": date_str,
                "status": "completed",
                "focus_area": area,
                "duration_minutes": 30,
                "exercises": [{
                    "name": name, "focus": area, "category": "main",
                    "target_quantity": MONTH_TARGET, "unit": unit,
                    "completed_quantity": completed, "completed": True,
                }],
                "updated_at": now,
            }, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )


def _seed_metric_reading(user_id: str, month_id: str, progress_pct: float) -> None:
    """Latest body_weight reading implying progress_pct of the way from
    baseline (80.0) to target (75.0)."""
    baseline, target = 80.0, 75.0
    value = round(baseline + (target - baseline) * progress_pct, 1)
    date_str = f"{month_id}-28"
    get_metrics_collection().update_one(
        {"user_id": user_id, "metric_name": "body_weight", "date": date_str},
        {"$set": {"value": value, "created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


def _seed_backlog(user_id: str, month_id: str, count: int) -> None:
    backlog = get_backlog_collection()
    now = datetime.now(timezone.utc)
    for i in range(count):
        name, area, unit = EXERCISES[i % len(EXERCISES)]
        source_date = f"{month_id}-{(i % 28) + 1:02d}"
        backlog.insert_one({
            "user_id": user_id,
            "source_date": source_date,
            "exercise_name": f"{name} (fixture {i})",
            "focus": area,
            "day_focus_area": area,
            "target_quantity": 20,
            "unit": unit,
            "completed_quantity": 0,
            "deficit": 20,
            "status": "open",
            "attempts": 0,
            "reinserted_date": None,
            "created_at": now,
            "updated_at": now,
            "resolved_at": None,
        })


def seed(scenario: str, include_metric: bool = True) -> None:
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{scenario}'. Choose from: {list(SCENARIOS)}")

    cfg = SCENARIOS[scenario]
    user_id = _get_or_create_test_user()
    prev_month_id = _previous_month_id()

    _seed_confirmed_goal(user_id, prev_month_id, include_metric)
    _seed_completion(user_id, prev_month_id, cfg["completion_pct"])
    _seed_backlog(user_id, prev_month_id, cfg["backlog_count"])
    if include_metric:
        _seed_metric_reading(user_id, prev_month_id, cfg["metric_progress_pct"])

    # Ensure current month has NO goal doc at all, so this user routes
    # through case 3 rather than being skipped as "pending in progress".
    get_month_plans_collection().delete_many({
        "user_id": user_id,
        "month_id": datetime.now(LOCAL_TZ).date().strftime("%Y-%m"),
    })

    print(f"Seeded scenario='{scenario}' (metric={include_metric}) for test user_id={user_id}")
    print(f"Previous month ({prev_month_id}): completion~{cfg['completion_pct']*100:.0f}%, "
          f"backlog={cfg['backlog_count']}")
    print("Run: python -m scripts.scheduled_plan_generation")


def cleanup() -> None:
    users = get_users_collection()
    existing = users.find_one({"email": TEST_EMAIL})
    if not existing:
        print("No fixture test user found — nothing to clean up.")
        return
    user_id = str(existing["_id"])

    get_month_plans_collection().delete_many({"user_id": user_id})
    get_plans_collection().delete_many({"user_id": user_id})
    get_metrics_collection().delete_many({"user_id": user_id})
    get_backlog_collection().delete_many({"user_id": user_id})
    users.delete_one({"_id": existing["_id"]})
    print(f"Cleaned up all fixture data for test user_id={user_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["increase", "hold", "decrease", "cleanup"])
    parser.add_argument("--no-metric", action="store_true", help="Omit the metric goal (volume-only)")
    args = parser.parse_args()

    if args.action == "cleanup":
        cleanup()
    else:
        seed(args.action, include_metric=not args.no_metric)