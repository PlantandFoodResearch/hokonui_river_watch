"""
Tests for river_check.py threshold-crossing detection logic.

Each test patches HTTP, file I/O, and xmltodict so the pure crossing
logic can be exercised with controlled data sequences.
"""
import unittest
from unittest.mock import patch, MagicMock, call
from datetime import datetime

import river_check

TIMESTAMP_FORMAT = river_check.TIMESTAMP_FORMAT


def make_api_response(readings):
    """
    Build the dict structure that xmltodict.parse returns from the Hilltop API.

    readings: list of (timestamp_str, flow_str) tuples, e.g.
              [("2026-06-26T10:00:00", "195.0"), ...]
    """
    entries = [{"T": ts, "I1": flow} for ts, flow in readings]
    # xmltodict returns a plain dict (not a list) for a single <E> element
    e_value = entries[0] if len(entries) == 1 else entries
    return {
        "Hilltop": {
            "Measurement": {
                "Data": {
                    "E": e_value
                }
            }
        }
    }


def run_check(readings, last_run_time_str):
    """
    Run check_river_flow() with mocked dependencies.

    Returns the list of send_alert call args so tests can assert on them.
    """
    parsed = make_api_response(readings)
    mock_response = MagicMock()
    mock_response.content = b""

    last_run_dt = datetime.strptime(last_run_time_str, TIMESTAMP_FORMAT)

    with patch("requests.get", return_value=mock_response), \
         patch("xmltodict.parse", return_value=parsed), \
         patch.object(river_check, "read_last_run_time", return_value=last_run_dt), \
         patch.object(river_check, "save_last_run_time"), \
         patch.object(river_check, "send_alert") as mock_alert:
        river_check.check_river_flow()
        return mock_alert.call_args_list


class TestThresholdCrossing(unittest.TestCase):

    # ------------------------------------------------------------------
    # No-alert cases
    # ------------------------------------------------------------------

    def test_all_new_readings_below_threshold(self):
        """All readings in the window are below threshold — no alert."""
        readings = [
            ("2026-06-26T10:00:00", "150.0"),
            ("2026-06-26T10:05:00", "175.0"),
            ("2026-06-26T10:10:00", "199.9"),
        ]
        alerts = run_check(readings, "2026-06-26T09:55:00")
        self.assertEqual(alerts, [])

    def test_already_above_before_window_no_crossing(self):
        """Was already above threshold before last run — no new crossing."""
        readings = [
            ("2026-06-26T09:50:00", "210.0"),  # old reading, above
            ("2026-06-26T10:00:00", "215.0"),  # new, still above
            ("2026-06-26T10:05:00", "220.0"),  # new, still above
        ]
        alerts = run_check(readings, "2026-06-26T09:55:00")
        self.assertEqual(alerts, [])

    def test_no_new_readings_in_window(self):
        """All readings are older than last_run_time — early return, no alert."""
        readings = [
            ("2026-06-26T09:00:00", "210.0"),
            ("2026-06-26T09:30:00", "220.0"),
        ]
        alerts = run_check(readings, "2026-06-26T09:55:00")
        self.assertEqual(alerts, [])

    def test_rises_to_exactly_threshold_not_above(self):
        """Flow reaches exactly FLOW_THRESHOLD — not strictly above, no alert."""
        readings = [
            ("2026-06-26T10:00:00", "190.0"),
            ("2026-06-26T10:05:00", str(river_check.FLOW_THRESHOLD)),
        ]
        alerts = run_check(readings, "2026-06-26T09:55:00")
        self.assertEqual(alerts, [])

    # ------------------------------------------------------------------
    # Single-alert cases
    # ------------------------------------------------------------------

    def test_crossing_within_new_readings(self):
        """Flow crosses threshold upward inside the new window — one alert."""
        readings = [
            ("2026-06-26T10:00:00", "180.0"),
            ("2026-06-26T10:05:00", "195.0"),
            ("2026-06-26T10:10:00", "210.0"),   # <-- crossing here
            ("2026-06-26T10:15:00", "215.0"),
        ]
        alerts = run_check(readings, "2026-06-26T09:55:00")
        self.assertEqual(len(alerts), 1)
        self.assertAlmostEqual(alerts[0].args[0], 210.0)
        self.assertEqual(alerts[0].args[1], "2026-06-26T10:10:00")

    def test_gap_crossing_between_old_and_new(self):
        """Last old reading below, first new reading above — gap crossing detected."""
        readings = [
            ("2026-06-26T09:50:00", "190.0"),   # old, below
            ("2026-06-26T10:00:00", "205.0"),   # new, above — gap crossing
            ("2026-06-26T10:05:00", "210.0"),
        ]
        alerts = run_check(readings, "2026-06-26T09:55:00")
        self.assertEqual(len(alerts), 1)
        self.assertAlmostEqual(alerts[0].args[0], 205.0)
        self.assertEqual(alerts[0].args[1], "2026-06-26T10:00:00")

    def test_crosses_then_drops_back_below(self):
        """Crosses threshold then falls back — alert still fires at the crossing."""
        readings = [
            ("2026-06-26T10:00:00", "180.0"),
            ("2026-06-26T10:05:00", "210.0"),   # crossing
            ("2026-06-26T10:10:00", "195.0"),   # drops back below
        ]
        alerts = run_check(readings, "2026-06-26T09:55:00")
        self.assertEqual(len(alerts), 1)

    def test_single_new_reading_above_old_below(self):
        """Single-element E (dict, not list) — gap crossing still detected."""
        readings = [
            ("2026-06-26T09:50:00", "190.0"),   # old, below
            ("2026-06-26T10:00:00", "205.0"),   # only new reading, above
        ]
        alerts = run_check(readings, "2026-06-26T09:55:00")
        self.assertEqual(len(alerts), 1)

    # ------------------------------------------------------------------
    # Multiple-alert cases
    # ------------------------------------------------------------------

    def test_two_separate_crossings(self):
        """Flow crosses the threshold twice within the window — two alerts."""
        readings = [
            ("2026-06-26T10:00:00", "180.0"),
            ("2026-06-26T10:05:00", "210.0"),   # 1st crossing
            ("2026-06-26T10:10:00", "190.0"),   # drops below
            ("2026-06-26T10:15:00", "205.0"),   # 2nd crossing
        ]
        alerts = run_check(readings, "2026-06-26T09:55:00")
        self.assertEqual(len(alerts), 2)
        self.assertAlmostEqual(alerts[0].args[0], 210.0)
        self.assertAlmostEqual(alerts[1].args[0], 205.0)

    def test_gap_crossing_plus_second_crossing_in_window(self):
        """Gap crossing AND a second crossing later — two alerts total."""
        readings = [
            ("2026-06-26T09:50:00", "190.0"),   # old, below
            ("2026-06-26T10:00:00", "205.0"),   # new: gap crossing (1st alert)
            ("2026-06-26T10:05:00", "195.0"),   # drops below
            ("2026-06-26T10:10:00", "210.0"),   # 2nd crossing (2nd alert)
        ]
        alerts = run_check(readings, "2026-06-26T09:55:00")
        self.assertEqual(len(alerts), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
