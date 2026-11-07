"""Tests fuer carprofile.notify: notify-send-Hinweise (phone-frei, Fake-Runner)."""

import subprocess
import unittest
from unittest import mock

from carprofile import notify
from carprofile.profile import Config, State, handle_add, handle_remove

AOA_ADD = {"DEVPATH": "/devices/platform/usb1/1-1", "ID_VENDOR_ID": "18d1", "ID_MODEL_ID": "2d00"}


def _ok(argv, **kw):
    return subprocess.CompletedProcess(argv, 0, "", "")


class Argv(unittest.TestCase):
    def test_three_states_exist(self):
        self.assertEqual(set(notify.NOTICES), {"profile_on", "profile_off", "location_saved"})

    def test_argv_is_notify_send_with_fixed_text_and_separator(self):
        a = notify.argv("profile_on")
        self.assertEqual(a[0], "notify-send")
        self.assertIn("--urgency=normal", a)
        self.assertIn("--expire-time=3000", a)
        self.assertEqual(a[-3:-1], ["--", "car-Profil an"])
        self.assertEqual(a[-1], "Kfz-Anschluss erkannt")

    def test_texts_carry_no_identifiers_or_places(self):
        import re
        for key in notify.NOTICES:
            text = " ".join(notify.argv(key))
            self.assertIsNone(re.search(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{4}", text), key)
            self.assertIsNone(re.search(r"\d+\.\d+", text), key)
            self.assertNotIn("/", text, key)


class Send(unittest.TestCase):
    def setUp(self):
        self.logs = []

    def _log(self, m):
        self.logs.append(m)

    def test_disabled_sends_nothing(self):
        runner = mock.Mock()
        r = notify.send("profile_on", False, self._log, runner=runner, which=lambda n: "/x")
        self.assertEqual(r["status"], "off")
        runner.assert_not_called()

    def test_missing_notify_send_is_skipped_not_fatal(self):
        runner = mock.Mock()
        r = notify.send("profile_off", True, self._log, runner=runner, which=lambda n: None)
        self.assertEqual(r["status"], "skipped")
        runner.assert_not_called()

    def test_sent_when_runner_succeeds(self):
        runner = mock.Mock(side_effect=_ok)
        r = notify.send("location_saved", True, self._log, runner=runner, which=lambda n: "/x")
        self.assertEqual(r, {"notice": "location_saved", "status": "sent"})
        runner.assert_called_once()
        self.assertEqual(runner.call_args[0][0], notify.argv("location_saved"))

    def test_nonzero_exit_is_failed(self):
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 1, "", "err"))
        r = notify.send("profile_on", True, self._log, runner=runner, which=lambda n: "/x")
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["detail"], "rc=1")

    def test_timeout_or_oserror_is_failed_not_raised(self):
        for exc in (subprocess.TimeoutExpired("notify-send", 5), OSError("bus weg")):
            runner = mock.Mock(side_effect=exc)
            r = notify.send("profile_on", True, self._log, runner=runner, which=lambda n: "/x")
            self.assertEqual(r["status"], "failed")

    def test_unknown_key_is_programming_error(self):
        with self.assertRaises(KeyError):
            notify.send("nope", True, self._log)


class SessionHooks(unittest.TestCase):
    """handle_add/handle_remove melden den Zustand, und nur wenn sie etwas getan haben."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name) / "state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _cfg(self, **kw):
        c = Config()
        c.apply = False
        for k, v in kw.items():
            setattr(c, k, v)
        return c

    def test_add_sends_profile_on_once(self):
        with mock.patch("carprofile.notify.send", wraps=notify.send) as spy:
            with mock.patch("carprofile.notify.subprocess.run", side_effect=_ok) as run, \
                 mock.patch("carprofile.notify.shutil.which", return_value="/usr/bin/notify-send"):
                r = handle_add(AOA_ADD, self.state, self._cfg(), lambda m: None)
                self.assertEqual(r["notice"]["status"], "sent")
                self.assertEqual(run.call_args[0][0], notify.argv("profile_on"))
                # Re-Enumeration derselben Sitzung: kein zweiter Hinweis.
                r2 = handle_add(AOA_ADD, self.state, self._cfg(), lambda m: None)
                self.assertEqual(r2["outcome"], "already-active")
                self.assertNotIn("notice", r2)
                self.assertEqual(spy.call_count, 1)

    def test_remove_sends_profile_off(self):
        with mock.patch("carprofile.notify.subprocess.run", side_effect=_ok) as run, \
             mock.patch("carprofile.notify.shutil.which", return_value="/usr/bin/notify-send"):
            handle_add(AOA_ADD, self.state, self._cfg(notify=False), lambda m: None)
            r = handle_remove({"DEVPATH": AOA_ADD["DEVPATH"]}, self.state, self._cfg(), lambda m: None)
            self.assertEqual(r["outcome"], "ended")
            self.assertEqual(r["notice"]["status"], "sent")
            self.assertEqual(run.call_args[0][0], notify.argv("profile_off"))

    def test_unrelated_remove_sends_nothing(self):
        with mock.patch("carprofile.notify.subprocess.run", side_effect=_ok) as run:
            handle_add(AOA_ADD, self.state, self._cfg(notify=False), lambda m: None)
            r = handle_remove({"DEVPATH": "/other"}, self.state, self._cfg(), lambda m: None)
            self.assertEqual(r["outcome"], "ignored")
            run.assert_not_called()

    def test_notify_off_in_config_is_respected(self):
        with mock.patch("carprofile.notify.subprocess.run", side_effect=_ok) as run:
            r = handle_add(AOA_ADD, self.state, self._cfg(notify=False), lambda m: None)
            self.assertEqual(r["notice"]["status"], "off")
            run.assert_not_called()
        self.assertTrue(State.load(self.state).active)

    def test_notify_failure_does_not_end_session(self):
        with mock.patch("carprofile.notify.subprocess.run", side_effect=OSError("kein Bus")), \
             mock.patch("carprofile.notify.shutil.which", return_value="/usr/bin/notify-send"):
            r = handle_add(AOA_ADD, self.state, self._cfg(), lambda m: None)
            self.assertEqual(r["outcome"], "started")
            self.assertEqual(r["notice"]["status"], "failed")
        self.assertTrue(State.load(self.state).active)

    def test_config_load_reads_notify(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "carprofile.json"
            p.write_text(json.dumps({"notify": False}), encoding="utf-8")
            self.assertFalse(Config.load(p).notify)
        self.assertTrue(Config().notify)


if __name__ == "__main__":
    unittest.main()
