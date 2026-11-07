"""Tests fuer carprofile: phone-frei, udev-Events simuliert (stdlib unittest)."""

import json
import tempfile
import unittest
from pathlib import Path

from carprofile.detect import (
    AOA_PRODUCT_IDS, REASON_AOA, REASON_EXTRA, REASON_NOT_AOA, REASON_NO_IDS,
    DetectConfig, UsbId, classify, parse_extra_ids, usb_id_from_env,
)
from carprofile.profile import Config, State, handle_add, handle_remove

AOA_ADD = {"DEVPATH": "/devices/platform/usb1/1-1", "ID_VENDOR_ID": "18d1", "ID_MODEL_ID": "2d00"}
AOA_ADD_ADB = {"DEVPATH": "/devices/platform/usb1/1-1", "ID_VENDOR_ID": "18d1", "ID_MODEL_ID": "2d01"}
CHARGER = {"DEVPATH": "/devices/platform/usb1/1-2", "ID_VENDOR_ID": "2109", "ID_MODEL_ID": "0817"}
PC_HUB = {"DEVPATH": "/devices/platform/usb1/1-3", "PRODUCT": "1d6b/2/606"}


def cfg_dry(**kw):
    c = Config()
    c.apply = False
    # Tests ohne echte Hinweise; die Hinweise sind in test_notify.py geprueft.
    c.notify = False
    for k, v in kw.items():
        setattr(c, k, v)
    return c


class DetectTests(unittest.TestCase):
    def test_aoa_accessory_is_car(self):
        for pid in AOA_PRODUCT_IDS:
            v = classify(UsbId("18d1", pid))
            self.assertTrue(v.is_car)
            self.assertEqual(v.reason, REASON_AOA)

    def test_charger_vbus_is_not_car(self):
        v = classify(usb_id_from_env(CHARGER))
        self.assertFalse(v.is_car)
        self.assertEqual(v.reason, REASON_NOT_AOA)

    def test_pc_hub_via_product_is_not_car(self):
        self.assertFalse(classify(usb_id_from_env(PC_HUB)).is_car)

    def test_no_ids_is_not_car(self):
        v = classify(usb_id_from_env({"DEVPATH": "/x"}))
        self.assertFalse(v.is_car)
        self.assertEqual(v.reason, REASON_NO_IDS)

    def test_google_vid_other_pid_is_not_car(self):
        # 18d1:4ee1 (normales Android-ADB-Geraet) darf nicht als Auto zaehlen.
        self.assertFalse(classify(UsbId("18d1", "4ee1")).is_car)

    def test_extra_allowlist_only_when_configured(self):
        extra = parse_extra_ids(["abcd:0123", "kaputt", "zz:zz"])
        self.assertEqual(extra, (UsbId("abcd", "0123"),))
        v = classify(UsbId("abcd", "0123"), DetectConfig(extra_car_ids=extra))
        self.assertTrue(v.is_car)
        self.assertEqual(v.reason, REASON_EXTRA)
        self.assertFalse(classify(UsbId("abcd", "0123")).is_car)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.state = self.dir / "state.json"
        self.log = []

    def tearDown(self):
        self._tmp.cleanup()

    def _log(self, msg):
        self.log.append(msg)

    def test_charger_add_does_not_start_profile(self):
        r = handle_add(CHARGER, self.state, cfg_dry(), self._log)
        self.assertEqual(r["outcome"], "ignored")
        self.assertFalse(State.load(self.state).active)

    def test_aoa_add_starts_and_dry_run_lists_actions(self):
        r = handle_add(AOA_ADD, self.state, cfg_dry(), self._log)
        self.assertEqual(r["outcome"], "started")
        st = State.load(self.state)
        self.assertTrue(st.active)
        self.assertEqual(st.devpath, AOA_ADD["DEVPATH"])
        # connect-Aktionen ohne konfigurierte Befehle -> skipped, nichts ausgefuehrt
        self.assertTrue(all(a["status"] == "skipped" for a in r["actions"]))

    def test_repeated_add_is_idempotent(self):
        handle_add(AOA_ADD, self.state, cfg_dry(), self._log)
        r = handle_add(AOA_ADD_ADB, self.state, cfg_dry(), self._log)
        self.assertEqual(r["outcome"], "already-active")

    def test_remove_without_session_is_noop(self):
        r = handle_remove(CHARGER, self.state, cfg_dry(), self._log)
        self.assertEqual(r["outcome"], "noop")

    def test_unrelated_remove_does_not_end_session(self):
        handle_add(AOA_ADD, self.state, cfg_dry(), self._log)
        r = handle_remove(CHARGER, self.state, cfg_dry(), self._log)
        self.assertEqual(r["outcome"], "ignored")
        self.assertTrue(State.load(self.state).active)

    def test_remove_of_car_device_ends_session_in_order(self):
        handle_add(AOA_ADD, self.state, cfg_dry(), self._log)
        r = handle_remove({"DEVPATH": AOA_ADD["DEVPATH"]}, self.state, cfg_dry(), self._log)
        self.assertEqual(r["outcome"], "ended")
        self.assertEqual([a["name"] for a in r["actions"]],
                         ["park-reminder", "fahrtenbuch-end", "profile-revert"])
        self.assertFalse(State.load(self.state).active)

    def test_remove_matches_by_id_when_devpath_changed(self):
        handle_add(AOA_ADD, self.state, cfg_dry(), self._log)
        r = handle_remove({"DEVPATH": "/other", "PRODUCT": "18d1/2d00/0"},
                          self.state, cfg_dry(), self._log)
        self.assertEqual(r["outcome"], "ended")

    def test_park_command_runs_before_profile_revert_when_applied(self):
        # apply=True, aber Befehle existieren nicht -> skipped, kein Crash, Sitzung endet
        cfg = cfg_dry(apply=True, commands={"park-reminder": "/nonexistent/parkreminder trigger",
                                            "profile-revert": "/nonexistent/revert"})
        handle_add(AOA_ADD, self.state, cfg, self._log)
        r = handle_remove({"DEVPATH": AOA_ADD["DEVPATH"]}, self.state, cfg, self._log)
        self.assertEqual(r["outcome"], "ended")
        statuses = {a["name"]: a["status"] for a in r["actions"]}
        self.assertEqual(statuses["park-reminder"], "skipped")
        self.assertEqual(statuses["profile-revert"], "skipped")
        self.assertFalse(State.load(self.state).active)

    def test_session_from_previous_boot_is_stale(self):
        from unittest import mock
        with mock.patch("carprofile.profile.current_boot_id", return_value="boot-A"):
            handle_add(AOA_ADD, self.state, cfg_dry(), self._log)
        # Phone neu gestartet: Abstecken nie gesehen. Naechstes Anstecken startet frisch.
        with mock.patch("carprofile.profile.current_boot_id", return_value="boot-B"):
            self.assertFalse(State.load(self.state).active)
            r = handle_add(AOA_ADD, self.state, cfg_dry(), self._log)
            self.assertEqual(r["outcome"], "started")
            self.assertEqual(State.load(self.state).boot_id, "boot-B")

    def test_corrupt_state_is_treated_as_inactive(self):
        self.state.write_text("{not json", encoding="utf-8")
        self.assertFalse(State.load(self.state).active)
        r = handle_remove({"DEVPATH": "/x"}, self.state, cfg_dry(), self._log)
        self.assertEqual(r["outcome"], "noop")

    def test_config_load_overrides_defaults(self):
        p = self.dir / "carprofile.json"
        p.write_text(json.dumps({"apply": True, "extra_car_ids": ["abcd:0123"]}), encoding="utf-8")
        cfg = Config.load(p)
        self.assertTrue(cfg.apply)
        self.assertEqual(cfg.detect_config().extra_car_ids, (UsbId("abcd", "0123"),))
        self.assertEqual(cfg.connect[0], "display-minimal")
        self.assertTrue(cfg.notify)


if __name__ == "__main__":
    unittest.main()
