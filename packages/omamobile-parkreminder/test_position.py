# SPDX-License-Identifier: MIT
"""parkreminder: Position aus ModemManager (position_modemmanager, locate).
Phone-frei: modemlocation wird durch Fakes ersetzt; keine Datenbank, kein mmcli."""
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("OMAMOBILE_MMCLI", "/nonexistent-mmcli")

import parkreminder as pr  # noqa: E402
from modemlocation import Fix, GpsUnavailable  # noqa: E402


class PositionModemmanager(unittest.TestCase):
    def setUp(self):
        self._orig = pr.collect_fixes

    def tearDown(self):
        pr.collect_fixes = self._orig

    def test_one_fix_becomes_the_park_point(self):
        seen = {}

        def fake(**kw):
            seen.update(kw)
            return [Fix(datetime(2026, 11, 7, 10, 0, 0), 48.0, 9.0, 0.8),
                    Fix(datetime(2026, 11, 7, 10, 0, 2), 48.0001, 9.0001, 0.7)]

        pr.collect_fixes = fake
        point = pr.position_modemmanager()
        self.assertEqual(seen["window_s"], 0)              # nur ein Fix, kein Sammeln
        self.assertEqual(seen["timeout_s"], pr.PARK_FIX_TIMEOUT_S)
        self.assertEqual(point["source"], "modemmanager")
        self.assertAlmostEqual(point["lat"], 48.0001)      # der letzte Fix zaehlt
        self.assertIn("2026-11-07T10:00:02", point["note"])

    def test_no_modem_or_no_fix_is_none(self):
        def fake(**kw):
            raise GpsUnavailable("kein Modem bei ModemManager")

        pr.collect_fixes = fake
        self.assertIsNone(pr.position_modemmanager())

    def test_locate_falls_back_to_lasttrip_when_modem_has_no_fix(self):
        def fake(**kw):
            raise GpsUnavailable("kein GPS-Fix")

        pr.collect_fixes = fake
        orig_lasttrip = pr.SOURCES["lasttrip"]
        pr.SOURCES["lasttrip"] = lambda: {"lat": 48.039, "lon": 9.0, "source": "lasttrip", "note": "x"}
        try:
            point = pr.locate("modemmanager:lasttrip")
        finally:
            pr.SOURCES["lasttrip"] = orig_lasttrip
        self.assertEqual(point["source"], "lasttrip")

    def test_locate_prefers_modem_fix_over_lasttrip(self):
        pr.collect_fixes = lambda **kw: [Fix(datetime(2026, 11, 7, 10, 0, 0), 48.5, 9.5)]
        orig_lasttrip = pr.SOURCES["lasttrip"]
        pr.SOURCES["lasttrip"] = lambda: {"lat": 48.039, "lon": 9.0, "source": "lasttrip", "note": "x"}
        try:
            point = pr.locate("modemmanager:lasttrip")
        finally:
            pr.SOURCES["lasttrip"] = orig_lasttrip
        self.assertEqual(point["source"], "modemmanager")


if __name__ == "__main__":
    unittest.main()
