"""carprofile-dispatch: Regel -> Dispatcher -> User-Manager (phone-frei, Fake systemd-run)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
# Quelle: bin/carprofile-dispatch; Paketkopie (omamobile-carapps): Wurzel.
DISPATCH = next(p for p in (HERE / "bin" / "carprofile-dispatch", HERE / "carprofile-dispatch")
                if p.exists())
RULES = HERE / "udev" / "99-carprofile.rules"


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.log = self.dir / "argv.txt"
        fake = self.dir / "systemd-run"
        fake.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$@\" > '{self.log}'\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        self.fake = fake
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CARPROFILE_SYSTEMD_RUN": str(fake),
            "CARPROFILE_TRIGGER": "/opt/fake/carprofile-trigger",
            "CARPROFILE_DEFAULTS": str(self.dir / "absent-defaults"),
            "CARPROFILE_USER": "hendrik",
        }

    def tearDown(self):
        self._tmp.cleanup()

    def run_dispatch(self, action, extra=None, drop=()):
        env = dict(self.env)
        env.update(extra or {})
        for key in drop:
            env.pop(key, None)
        return subprocess.run(["sh", str(DISPATCH), action], env=env,
                              capture_output=True, text=True, timeout=20)

    def argv(self):
        return self.log.read_text(encoding="utf-8").splitlines()

    def test_add_runs_trigger_in_user_manager_of_configured_user(self):
        r = self.run_dispatch("add", {"DEVPATH": "/devices/1-1", "ID_VENDOR_ID": "18d1",
                                      "ID_MODEL_ID": "2d00"})
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.argv()
        self.assertIn("--user", a)
        self.assertIn("--machine=hendrik@.host", a)
        self.assertIn("--setenv=DEVPATH=/devices/1-1", a)
        self.assertIn("--setenv=ID_VENDOR_ID=18d1", a)
        self.assertIn("--setenv=ID_MODEL_ID=2d00", a)
        self.assertEqual(a[-3:], ["/opt/fake/carprofile-trigger", "add", "--json"])

    def test_remove_forwards_product_and_devpath_only_when_set(self):
        r = self.run_dispatch("remove", {"DEVPATH": "/devices/1-1"}, drop=("PRODUCT",))
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.argv()
        self.assertIn("--setenv=DEVPATH=/devices/1-1", a)
        self.assertFalse(any(x.startswith("--setenv=PRODUCT=") for x in a))
        self.assertEqual(a[-3:], ["/opt/fake/carprofile-trigger", "remove", "--json"])

    def test_user_from_defaults_file_when_env_unset(self):
        defaults = self.dir / "carprofile.defaults"
        defaults.write_text("CARPROFILE_USER=alice\n", encoding="utf-8")
        r = self.run_dispatch("add", {"CARPROFILE_DEFAULTS": str(defaults)},
                              drop=("CARPROFILE_USER",))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--machine=alice@.host", self.argv())

    def test_missing_user_fails_without_calling_systemd_run(self):
        self.log.unlink(missing_ok=True)
        r = self.run_dispatch("add", drop=("CARPROFILE_USER",))
        self.assertEqual(r.returncode, 2)
        self.assertIn("CARPROFILE_USER", r.stderr)
        self.assertFalse(self.log.exists())

    def test_invalid_user_is_rejected(self):
        self.log.unlink(missing_ok=True)
        r = self.run_dispatch("add", {"CARPROFILE_USER": "bad user;rm"})
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.log.exists())

    def test_bad_action_is_usage_error(self):
        r = self.run_dispatch("bogus")
        self.assertEqual(r.returncode, 2)

    def test_rule_routes_both_directions_through_dispatcher(self):
        text = RULES.read_text(encoding="utf-8")
        rule_lines = [ln for ln in text.splitlines() if ln.startswith("ACTION==")]
        self.assertEqual(len(rule_lines), 2)
        for ln in rule_lines:
            self.assertIn("/usr/bin/carprofile-dispatch", ln)
            self.assertNotIn("carprofile-trigger", ln)  # kein direkter Trigger aus udev
            self.assertIn("--setenv=DEVPATH=%p", ln)
        self.assertIn("carprofile-dispatch add", rule_lines[0])
        self.assertIn("carprofile-dispatch remove", rule_lines[1])
        self.assertIn("18d1", rule_lines[0])  # Anstecken nur AOA, kein Google-Breitmatch

    def test_dispatcher_parses(self):
        r = subprocess.run(["sh", "-n", str(DISPATCH)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
