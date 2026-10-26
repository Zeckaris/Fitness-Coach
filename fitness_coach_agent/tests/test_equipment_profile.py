"""
Unit tests for equipment profile, equipment overrides, precedence resolution,
and automated search_workout_library equipment filtering.
"""

import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tools.user_profile import (
    create_user_profile,
    update_user_profile,
    resolve_user_equipment,
    set_equipment_override,
)
from tools.workout_library import (
    EQUIPMENT_VOCABULARY,
    WorkoutQuery,
    search_workout_library,
    _matches,
)


class TestEquipmentProfileAndOverrides(unittest.TestCase):
    def setUp(self):
        self.user_id = "test_user_eq_123"

    @patch("tools.user_profile.get_user_profile_collection")
    def test_create_user_profile_with_equipment(self, mock_get_col):
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col

        doc = create_user_profile(
            date_of_birth="1995-05-15",
            gender="male",
            height_value=180.0,
            equipment=["dumbbells", "bench", "none"],
            user_id=self.user_id,
        )

        mock_col.update_one.assert_called_once()
        filter_arg, update_arg = mock_col.update_one.call_args[0]
        self.assertEqual(filter_arg, {"user_id": self.user_id})
        # 'none' bodyweight token should be filtered out from explicit list
        self.assertEqual(update_arg["$set"]["equipment"], ["dumbbells", "bench"])

    @patch("tools.user_profile.get_user_profile_collection")
    def test_update_user_profile_equipment(self, mock_get_col):
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col

        update_user_profile(
            equipment=["kettlebell", "pull_up_bar"],
            user_id=self.user_id,
        )

        mock_col.update_one.assert_called_once()
        filter_arg, update_arg = mock_col.update_one.call_args[0]
        self.assertEqual(filter_arg, {"user_id": self.user_id})
        self.assertEqual(update_arg["$set"]["equipment"], ["kettlebell", "pull_up_bar"])

    @patch("tools.user_profile.get_user_profile")
    def test_resolve_user_equipment_permanent_fallback(self, mock_get_profile):
        # Case 1: No profile -> defaults to [] (bodyweight only)
        mock_get_profile.return_value = None
        eq, meta = resolve_user_equipment(self.user_id)
        self.assertEqual(eq, [])
        self.assertFalse(meta["active_override"])

        # Case 2: Permanent profile with dumbbells & bench
        mock_get_profile.return_value = {
            "user_id": self.user_id,
            "equipment": ["dumbbells", "bench"],
        }
        eq, meta = resolve_user_equipment(self.user_id)
        self.assertEqual(eq, ["dumbbells", "bench"])
        self.assertFalse(meta["active_override"])

    @patch("tools.user_profile.get_user_profile")
    def test_resolve_user_equipment_precedence(self, mock_get_profile):
        now = datetime.now(ZoneInfo("UTC"))

        # Case 1: Active custom override ("dumbbells") over permanent ("kettlebell")
        mock_get_profile.return_value = {
            "user_id": self.user_id,
            "equipment": ["kettlebell"],
            "equipment_override": {
                "mode": "custom",
                "equipment": ["dumbbells"],
                "expires_at": now + timedelta(days=5),
            },
        }
        eq, meta = resolve_user_equipment(self.user_id)
        self.assertEqual(eq, ["dumbbells"])
        self.assertTrue(meta["active_override"])
        self.assertEqual(meta["mode"], "override_custom")

        # Case 2: Active "all" override -> returns None (unrestricted)
        mock_get_profile.return_value = {
            "user_id": self.user_id,
            "equipment": [],
            "equipment_override": {
                "mode": "all",
                "equipment": None,
                "expires_at": now + timedelta(days=2),
            },
        }
        eq, meta = resolve_user_equipment(self.user_id)
        self.assertIsNone(eq)
        self.assertTrue(meta["active_override"])
        self.assertEqual(meta["mode"], "override_all")

        # Case 3: Expired override -> falls back to permanent profile
        mock_get_profile.return_value = {
            "user_id": self.user_id,
            "equipment": ["resistance_band"],
            "equipment_override": {
                "mode": "custom",
                "equipment": [],
                "expires_at": now - timedelta(days=1),  # Expired yesterday
            },
        }
        eq, meta = resolve_user_equipment(self.user_id)
        self.assertEqual(eq, ["resistance_band"])
        self.assertFalse(meta["active_override"])

    @patch("tools.user_profile.get_current_user_id")
    @patch("tools.user_profile.get_user_profile_collection")
    def test_set_equipment_override_tool(self, mock_get_col, mock_get_user_id):
        mock_get_user_id.return_value = self.user_id
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col

        # 1. Custom override
        res = set_equipment_override.invoke({
            "mode": "custom",
            "equipment": ["dumbbells"],
            "duration_days": 3.0,
            "reason": "hotel gym",
        })
        self.assertIn("custom list [dumbbells] for 3 day(s)", res)
        mock_col.update_one.assert_called_once()
        filter_arg, update_arg = mock_col.update_one.call_args[0]
        self.assertEqual(filter_arg, {"user_id": self.user_id})
        self.assertEqual(update_arg["$set"]["equipment_override"]["mode"], "custom")
        self.assertEqual(update_arg["$set"]["equipment_override"]["equipment"], ["dumbbells"])

        # 2. Clear override
        mock_col.reset_mock()
        res_clear = set_equipment_override.invoke({"clear": True})
        self.assertIn("cleared", res_clear)
        filter_arg, update_arg = mock_col.update_one.call_args[0]
        self.assertIn("$unset", update_arg)
        self.assertIn("equipment_override", update_arg["$unset"])

    def test_workout_query_has_no_equipment_field(self):
        # Verify equipment was completely removed from WorkoutQuery schema
        self.assertNotIn("equipment", WorkoutQuery.model_fields)

    def test_matches_filtering_logic(self):
        dumbbell_exercise = {
            "name": "Dumbbell Bench Press",
            "equipment": ["dumbbells", "bench"],
        }
        bodyweight_exercise = {
            "name": "Push-ups",
            "equipment": ["none"],
        }
        query = WorkoutQuery()

        # Bodyweight only -> accepts Push-ups, rejects Dumbbell Bench Press
        self.assertTrue(_matches(bodyweight_exercise, query, resolved_equipment=[]))
        self.assertFalse(_matches(dumbbell_exercise, query, resolved_equipment=[]))

        # Dumbbells only (missing bench) -> rejects Dumbbell Bench Press
        self.assertFalse(_matches(dumbbell_exercise, query, resolved_equipment=["dumbbells"]))

        # Dumbbells + bench -> accepts Dumbbell Bench Press
        self.assertTrue(_matches(dumbbell_exercise, query, resolved_equipment=["dumbbells", "bench"]))

        # None (unrestricted "all" mode) -> accepts everything
        self.assertTrue(_matches(dumbbell_exercise, query, resolved_equipment=None))
        self.assertTrue(_matches(bodyweight_exercise, query, resolved_equipment=None))

    @patch("tools.user_profile.resolve_user_equipment")
    def test_search_workout_library_uses_resolve_user_equipment(self, mock_resolve):
        # Set resolved equipment to bodyweight only ([])
        mock_resolve.return_value = ([], {"mode": "permanent", "active_override": False})

        res = search_workout_library.invoke({"target_area": ["chest"]})
        # Result should only contain exercises that require no weighted gear
        self.assertNotIn("Dumbbell", res)

        # Change resolved equipment to ["dumbbells", "bench"]
        mock_resolve.return_value = (["dumbbells", "bench"], {"mode": "permanent", "active_override": False})
        res_weighted = search_workout_library.invoke({"target_area": ["chest"]})
        self.assertIsInstance(res_weighted, str)


if __name__ == "__main__":
    unittest.main()
