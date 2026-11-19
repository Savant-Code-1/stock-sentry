"""Tests for statistical and rule-based anomaly detection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.anomaly_agent import AnomalyAgent
from backend import anomaly_detection
from database.seed import seed_database_if_empty


class TestAnomalyDetection(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_anom.db"
        seed_database_if_empty(db_path=self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_detects_seeded_anomaly_categories(self) -> None:
        anomalies = anomaly_detection.detect_anomalies(db_path=self.db_path)
        self.assertGreater(len(anomalies), 0)

        events = {a["detected_event"] for a in anomalies}
        self.assertIn("Unusually High Sales Spike", events)
        self.assertIn("Unexpected Stock Reduction", events)
        self.assertIn("Repeated Downward Adjustment Pattern", events)

    def test_anomaly_required_fields_and_non_accusatory_disclaimer(self) -> None:
        agent = AnomalyAgent(db_path=self.db_path)
        scan = agent.scan_anomalies()
        required_keys = {
            "product_id", "product_name", "detected_event", "expected_value",
            "actual_value", "severity", "detection_time", "explanation",
            "suggested_action", "disclaimer",
        }
        for anom in scan["anomalies"]:
            self.assertTrue(required_keys.issubset(anom.keys()))
            self.assertIn("not proof of theft", anom["disclaimer"].lower())


if __name__ == "__main__":
    unittest.main()
