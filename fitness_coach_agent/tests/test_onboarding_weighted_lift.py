import unittest
from unittest.mock import MagicMock, patch

from app.components.onboarding_ui import (
    WEIGHTED_LIFT_DEFAULT,
    build_baseline_markers,
    has_free_weight_equipment,
    normalize_bodyweight_kg,
    resolve_weighted_lift_kg,
)
from tools.baseline_assessment import (
    BASELINE_ASSESSMENT_DATA_VERSION,
    record_baseline_assessment,
)
from tools.user_profile import create_user_profile


class TestWeightedLiftOnboarding(unittest.TestCase):
    def test_skipped_lift_is_not_scored(self):
        self.assertIsNone(resolve_weighted_lift_kg(None, "kg", True))
        markers = build_baseline_markers(20, 30, 15, 3, weighted_lift_kg=None)
        self.assertNotIn("weighted_lift", markers)

    def test_untouched_default_is_not_scored(self):
        self.assertEqual(WEIGHTED_LIFT_DEFAULT, 0.0)
        self.assertIsNone(
            resolve_weighted_lift_kg(WEIGHTED_LIFT_DEFAULT, "kg", True)
        )
        markers = build_baseline_markers(
            20,
            30,
            15,
            3,
            weighted_lift_kg=resolve_weighted_lift_kg(
                WEIGHTED_LIFT_DEFAULT, "kg", True
            ),
        )
        self.assertNotIn("weighted_lift", markers)

    def test_bench_alone_does_not_enable_weighted_scoring(self):
        equipment = ["bench", "resistance_band"]
        self.assertFalse(has_free_weight_equipment(equipment))
        lift_kg = resolve_weighted_lift_kg(
            20.0,
            "kg",
            has_free_weight_equipment(equipment),
        )
        self.assertIsNone(lift_kg)
        markers = build_baseline_markers(20, 30, 15, 3, weighted_lift_kg=lift_kg)
        self.assertNotIn("weighted_lift", markers)

    def test_real_lift_is_scored(self):
        for equipment in (["dumbbells"], ["kettlebell"]):
            with self.subTest(equipment=equipment):
                self.assertTrue(has_free_weight_equipment(equipment))
                lift_kg = resolve_weighted_lift_kg(
                    20.0,
                    "kg",
                    has_free_weight_equipment(equipment),
                )
                self.assertEqual(lift_kg, 20.0)
                markers = build_baseline_markers(
                    20,
                    30,
                    15,
                    3,
                    weighted_lift_kg=lift_kg,
                )
                self.assertEqual(markers["weighted_lift"], 20.0)

    @patch("tools.user_profile.get_user_profile_collection")
    def test_bodyweight_is_saved_to_profile_in_kg(self, mock_get_col):
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col

        create_user_profile(
            date_of_birth="1995-05-15",
            gender="male",
            height_value=180.0,
            height_unit="cm",
            weight_unit_preference="lb",
            user_id="test_user_123",
            body_weight_kg=normalize_bodyweight_kg(180.0, "lb"),
        )

        _, update_arg = mock_col.update_one.call_args[0]
        self.assertEqual(update_arg["$set"]["body_weight_kg"], 81.65)

    @patch("tools.user_profile.get_user_profile_collection")
    @patch("tools.baseline_assessment.get_baseline_assessment_collection")
    def test_new_baseline_assessment_has_data_version(
        self,
        mock_get_assess_col,
        mock_get_profile_col,
    ):
        mock_get_assess_col.return_value = MagicMock()
        mock_get_profile_col.return_value = MagicMock()

        record_baseline_assessment(
            experience_level="beginner",
            answers={"push_ups": 10},
            user_id="test_user_123",
        )

        inserted_doc = mock_get_assess_col.return_value.insert_one.call_args[0][0]
        self.assertEqual(
            inserted_doc["data_version"],
            BASELINE_ASSESSMENT_DATA_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
