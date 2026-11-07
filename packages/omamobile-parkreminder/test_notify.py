# SPDX-License-Identifier: MIT
"""parkreminder: Hinweis "Standort gemerkt" (notify=desktop), phone-frei mit Fakes."""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("OMAMOBILE_MMCLI", "/nonexistent-mmcli")

import parkreminder as pr  # noqa: E402


class DesktopNotice(unittest.TestCase):
    def test_sends_fixed_text_without_coordinates(self):
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        status = pr.notify_desktop("Standort gemerkt", "Parkpunkt gespeichert",
                                   runner=runner, which=lambda n: "/usr/bin/notify-send")
        self.assertEqual(status, "sent")
        argv = runner.call_args[0][0]
        self.assertEqual(argv[0], "notify-send")
        self.assertEqual(argv[-3:], ["--", "Standort gemerkt", "Parkpunkt gespeichert"])
        self.assertNotIn("/", " ".join(argv))

    def test_missing_notify_send_is_skipped(self):
        runner = mock.Mock()
        self.assertEqual(pr.notify_desktop("a", "b", runner=runner, which=lambda n: None), "skipped")
        runner.assert_not_called()

    def test_failure_is_reported_not_raised(self):
        runner = mock.Mock(side_effect=OSError("kein Bus"))
        self.assertEqual(pr.notify_desktop("a", "b", runner=runner,
                                           which=lambda n: "/x"), "failed")

    def test_config_default_is_desktop_and_bad_value_falls_back(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "none.conf"
            self.assertEqual(pr.load_config(missing)["notify"], "desktop")
            bad = Path(d) / "bad.conf"
            bad.write_text("[parkreminder]\nnotify = blink\n", encoding="utf-8")
            self.assertEqual(pr.load_config(bad)["notify"], "desktop")
            off = Path(d) / "off.conf"
            off.write_text("[parkreminder]\nnotify = none\n", encoding="utf-8")
            self.assertEqual(pr.load_config(off)["notify"], "none")


if __name__ == "__main__":
    unittest.main()
