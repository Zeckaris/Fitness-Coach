"""
Unit tests for onboarding front plank hold and data version bump.
Tests skipped vs answered plank resolution, marker payload construction,
and baseline assessment data version 2 persistence.
"""

import unittest
from unittest.mock import MagicMock, patch

from app.components.onboarding_ui import (
    resolve_plank_seconds,
    build_baseline_markers,
)
from tools.baseline_assessment import (
    BASELINE_ASSESSMENT_DATA_VERSION,
    record_baseline_assessment,
)
from utils.baseline_level import (
    compute_bmi,
    calculate_overall_level,
)


class TestOnboardingPlank(unittest.TestCase):
    def test_resolve_plank_seconds_skipped(self):
        # Blank / None means skipped
        self.assertIsNone(resolve_plank_seconds(None))
        # 0 or 0.0 means skipped
        self.assertIsNone(resolve_plank_seconds(0))
        self.assertIsNone(resolve_plank_seconds(0.0))
        # Negative means skipped
        self.assertIsNone(resolve_plank_seconds(-15))
        # Invalid strings
        self.assertIsNone(resolve_plank_seconds("invalid"))

    def test_resolve_plank_seconds_valid(self):
        self.assertEqual(resolve_plank_seconds(45), 45.0)
        self.assertEqual(resolve_plank_seconds(60.5), 60.5)
        self.assertEqual(resolve_plank_seconds("30"), 30.0)

    def test_build_baseline_markers_with_and_without_plank(self):
        # Skipped plank (None or 0) must never be in markers
        markers_none = build_baseline_markers(12, 20, 15, 2, plank_seconds=None)
        self.assertNotIn("plank", markers_none)

        markers_zero = build_baseline_markers(12, 20, 15, 2, plank_seconds=0)
        self.assertNotIn("plank", markers_zero)

        # Answered plank is added
        markers_answered = build_baseline_markers(12, 20, 15, 2, plank_seconds=45.0)
        self.assertIn("plank", markers_answered)
        self.assertEqual(markers_answered["plank"], 45.0)

    def test_baseline_assessment_data_version_bumped_to_2(self):
        self.assertGreaterEqual(BASELINE_ASSESSMENT_DATA_VERSION, 2)

    @patch("tools.user_profile.get_user_profile_collection")
    @patch("tools.baseline_assessment.get_baseline_assessment_collection")
    def test_record_assessment_stores_data_version_2_and_plank(
        self,
        mock_get_assess_col,
        mock_get_profile_col,
    ):
        mock_get_assess_col.return_value = MagicMock()
        mock_get_profile_col.return_value = MagicMock()

        record_baseline_assessment(
            experience_level="intermediate",
            answers={
                "raw_markers": {
                    "push_ups": 15,
                    "squats": 25,
                    "run_minutes": 20,
                    "frequency": 3,
                    "plank_seconds": 45.0,
                }
            },
            user_id="user_plank_test",
        )

        inserted_doc = mock_get_assess_col.return_value.insert_one.call_args[0][0]
        self.assertEqual(inserted_doc["data_version"], BASELINE_ASSESSMENT_DATA_VERSION)
        self.assertEqual(
            inserted_doc["answers"]["raw_markers"]["plank_seconds"], 45.0
        )


if __name__ == "__main__":
    unittest.main()
