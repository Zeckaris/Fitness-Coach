"""
Tests for Python pre-scheduling of goal-tracked exercises and post-generation force-insertion.
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("GEMINI_API_KEY", "dummy_key_for_testing")

from unittest.mock import patch, MagicMock
from agent.plan_generation import pre_schedule_goal_exercises, validate_and_enforce_pre_assigned_goals
from tools.plans import BackfillDayPlanInput, ExercisePlanItem


def test_pre_schedule_goal_exercises_basic():
    """Verify pre-scheduler assigns goal exercises to training days based on target frequency."""
    dates_to_plan = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-19", "2026-09-20"]
    goal_exercise_names = ["Push-Up", "Pull-Up", "Bodyweight Squat"]
    goal_workouts = [
        {"name": "Push-Up", "category": "Upper Body", "target_quantity": 40, "unit": "reps"},
        {"name": "Pull-Up", "category": "Upper Body", "target_quantity": 20, "unit": "reps"},
        {"name": "Bodyweight Squat", "category": "Lower Body", "target_quantity": 60, "unit": "reps"},
    ]

    mock_week_plan = {
        "day_focuses": {
            "2026-09-14": {"focus": "Upper Body Strength", "is_rest_day": False},
            "2026-09-15": {"focus": "Lower Body Strength", "is_rest_day": False},
            "2026-09-16": {"focus": "Rest & Recovery", "is_rest_day": True},
            "2026-09-17": {"focus": "Upper Body Strength", "is_rest_day": False},
            "2026-09-18": {"focus": "Lower Body Strength", "is_rest_day": False},
            "2026-09-19": {"focus": "Full Body Circuit", "is_rest_day": False},
            "2026-09-20": {"focus": "Rest & Recovery", "is_rest_day": True},
        }
    }

    with patch("tools.week_plans._find_week_doc_for_date", return_value=mock_week_plan), \
         patch("db.mongo_client.get_plans_collection") as mock_plans_coll:
        mock_plans_coll.return_value.find.return_value = []

        pre_scheduled_map, goal_by_day_str = pre_schedule_goal_exercises(
            dates_to_plan=dates_to_plan,
            goal_exercise_names=goal_exercise_names,
            goal_workouts=goal_workouts,
            user_id="test_user",
            week_id="2026-W38",
        )

        # Rest days (16th and 20th) should have 0 goal exercises assigned
        assert pre_scheduled_map["2026-09-16"] == []
        assert pre_scheduled_map["2026-09-20"] == []

        # For 7 days window, 4 sessions target per exercise
        for name in goal_exercise_names:
            assigned_count = sum(1 for d in dates_to_plan if name in pre_scheduled_map[d])
            assert assigned_count == 4, f"{name} was assigned {assigned_count} times, expected 4"


def test_pre_schedule_completed_session_counting():
    """Verify pre-scheduler counts completed past sessions and excludes incomplete ones."""
    dates_to_plan = ["2026-09-17", "2026-09-18", "2026-09-19", "2026-09-20"]  # 4-day window -> target frequency = max(1, round(4*4/7)) = 2
    goal_exercise_names = ["Push-Up"]
    goal_workouts = [
        {"name": "Push-Up", "category": "Upper Body", "target_quantity": 40, "unit": "reps"},
    ]

    mock_week_plan = {
        "day_focuses": {d: {"focus": "Strength", "is_rest_day": False} for d in dates_to_plan}
    }

    # Simulate past plans in week: 1 completed Push-Up session, 1 planned-but-incomplete session
    mock_past_plans = [
        {
            "date": "2026-09-14",
            "exercises": [
                {"name": "Push-Up", "category": "main", "status": "completed", "completed_quantity": 40, "target_quantity": 40}
            ]
        },
        {
            "date": "2026-09-15",
            "exercises": [
                {"name": "Push-Up", "category": "main", "status": "planned", "completed_quantity": 0, "target_quantity": 40}
            ]
        },
    ]

    with patch("tools.week_plans._find_week_doc_for_date", return_value=mock_week_plan), \
         patch("db.mongo_client.get_plans_collection") as mock_plans_coll:
        mock_plans_coll.return_value.find.return_value = mock_past_plans

        pre_scheduled_map, _ = pre_schedule_goal_exercises(
            dates_to_plan=dates_to_plan,
            goal_exercise_names=goal_exercise_names,
            goal_workouts=goal_workouts,
            user_id="test_user",
            week_id="2026-W38",
        )

        # Weekly target = 4. 1 completed session past in week -> 4 - 1 = 3 remaining assigned in 4-day window
        assigned_count = sum(1 for d in dates_to_plan if "Push-Up" in pre_scheduled_map[d])
        assert assigned_count == 3


def test_validate_and_enforce_pre_assigned_goals_force_insertion():
    """Verify force-insertion of missing pre-assigned goal exercise into parsed output."""
    pre_scheduled_map = {
        "2026-09-14": ["Push-Up", "Pull-Up"]
    }
    goal_workouts = [
        {"name": "Push-Up", "category": "Upper Body", "target_quantity": 40, "unit": "reps"},
        {"name": "Pull-Up", "category": "Upper Body", "target_quantity": 20, "unit": "reps"},
    ]

    # LLM output only included Push-Up, missed Pull-Up
    day_plan = BackfillDayPlanInput.model_construct(
        date="2026-09-14",
        focus_area="Upper Body",
        status="planned",
        duration_minutes=45,
        exercises=[
            ExercisePlanItem.model_construct(name="Bear Crawl", focus="warmup", category="warmup", sets=2, reps="30 sec"),
            ExercisePlanItem.model_construct(name="Push-Up", focus="chest", category="main", sets=4, reps="10 reps", target_quantity=40, unit="reps"),
            ExercisePlanItem.model_construct(name="Dumbbell Row", focus="back", category="main", sets=3, reps="12 reps"),
            ExercisePlanItem.model_construct(name="Child Pose", focus="cooldown", category="cooldown", sets=2, reps="30 sec"),
        ]
    )

    validate_and_enforce_pre_assigned_goals([day_plan], pre_scheduled_map, goal_workouts)

    # Pull-Up should now be in main phase exercises
    exercise_names = [e.name for e in day_plan.exercises]
    assert "Pull-Up" in exercise_names
    pullup_item = next(e for e in day_plan.exercises if e.name == "Pull-Up")
    assert pullup_item.category == "main"


def test_validate_and_enforce_pre_assigned_goals_trim_filler_if_exceed_max():
    """Verify force-insertion trims non-goal main exercise if main phase count exceeds 12."""
    pre_scheduled_map = {
        "2026-09-14": ["Push-Up"]
    }
    goal_workouts = [
        {"name": "Push-Up", "category": "Upper Body", "target_quantity": 40, "unit": "reps"},
    ]

    # Day plan already has 12 main exercises, all non-goal
    main_items = [
        ExercisePlanItem.model_construct(name="Dumbbell Row", focus="back", category="main", sets=3, reps="10 reps")
        for _ in range(12)
    ]
    # Give the last item a distinct name to check trimming
    main_items[-1] = ExercisePlanItem.model_construct(name="Bodyweight Squat", focus="quads", category="main", sets=3, reps="10 reps")

    day_plan = BackfillDayPlanInput.model_construct(
        date="2026-09-14",
        focus_area="Full Body",
        status="planned",
        duration_minutes=60,
        exercises=main_items
    )

    validate_and_enforce_pre_assigned_goals([day_plan], pre_scheduled_map, goal_workouts)

    main_phase_names = [e.name for e in day_plan.exercises if e.category == "main"]
    assert len(main_phase_names) == 12
    assert "Push-Up" in main_phase_names
    assert "Bodyweight Squat" not in main_phase_names  # Trimmed filler


if __name__ == "__main__":
    test_pre_schedule_goal_exercises_basic()
    test_pre_schedule_completed_session_counting()
    test_validate_and_enforce_pre_assigned_goals_force_insertion()
    test_validate_and_enforce_pre_assigned_goals_trim_filler_if_exceed_max()
    print("All pre-scheduler tests passed successfully!")
