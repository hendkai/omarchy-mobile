# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests fuer den phone-seitigen Kern (snaildrive_aac.core). Stdlib only.

Lauf:  cd packages/snaildrive-android-auto-phone && python3 -m unittest discover -s test -v
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parent / "lib" / "snaildrive-android-auto-phone"
sys.path.insert(0, str(LIB))

import navi_tbt  # noqa: E402
from snaildrive_aac import core  # noqa: E402


def frame(action="turn-right", status="navigating", distance=800, ts=1730900800.0):
    msg = copy.deepcopy(navi_tbt.sample_message(ts))
    msg["status"] = status
    msg["next"]["action"] = action
    msg["next"]["distance"] = distance
    msg["route"]["legs"][0]["action"] = action
    msg["route"]["legs"][0]["distance"] = distance
    if status == "error":
        msg["error"] = "gnss: kein Fix"
    return msg


def line(msg) -> bytes:
    return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")


def state(**kw) -> core.NavState:
    return core.state_from_frame(frame(**kw))


class ShouldPush(unittest.TestCase):
    def test_first_navigating_frame_pushes(self):
        self.assertTrue(core.should_push(None, state(), 10.0, None))

    def test_first_idle_frame_does_not_push(self):
        self.assertFalse(core.should_push(None, state(status="idle"), 10.0, None))

    def test_same_state_within_one_second_is_silent(self):
        s = state()
        self.assertFalse(core.should_push(s, s, 10.5, 10.0))

    def test_steady_state_pushes_every_second(self):
        s = state()
        self.assertTrue(core.should_push(s, s, 11.0, 10.0))

    def test_action_change_bursts_after_min_gap(self):
        a, b = state(action="turn-right"), state(action="turn-left")
        self.assertFalse(core.should_push(a, b, 10.2, 10.0))   # innerhalb 0.5 s
        self.assertTrue(core.should_push(a, b, 10.5, 10.0))    # Burst erlaubt

    def test_paused_and_error_do_not_push(self):
        a = state()
        self.assertFalse(core.should_push(a, state(status="paused"), 20.0, 10.0))
        self.assertFalse(core.should_push(a, state(status="error"), 20.0, 10.0))

    def test_arriving_is_a_pushing_state(self):
        a = state(status="navigating")
        b = state(status="arriving")
        self.assertTrue(core.should_push(a, b, 10.6, 10.0))


class Record(unittest.TestCase):
    def test_record_shape_and_values(self):
        rec = core.to_record(state(action="turn-left", distance=300), 42.0)
        self.assertEqual(rec["v"], 1)
        self.assertEqual(rec["type"], "aap-nav-status")
        self.assertEqual(rec["next"]["action"], "turn-left")
        self.assertEqual(rec["next"]["distance_m"], 300)
        self.assertEqual(rec["location"]["speed_kmh"], 48.5)
        self.assertEqual(rec["route"]["eta_s"], 42)

    def test_encode_is_one_utf8_line(self):
        rec = core.to_record(state(), 1.0)
        rec["next"]["instruction"] = "Ausfahrt Königstraße"
        raw = core.encode_record(rec)
        self.assertEqual(raw.count(b"\n"), 1)
        self.assertIn("Königstraße".encode("utf-8"), raw)
        self.assertEqual(json.loads(raw.decode("utf-8"))["next"]["instruction"], "Ausfahrt Königstraße")


class Pipe:
    def __init__(self):
        self.out = []
        self.status = []
        self.p = core.Pipeline(publish=self.out.append,
                               status=lambda st, ts, det: self.status.append((st, det)))


class PipelineRules(unittest.TestCase):
    def test_valid_frames_push_and_report_ok(self):
        t = Pipe()
        self.assertTrue(t.p.on_line(line(frame()), 10.0))
        self.assertEqual(len(t.out), 1)
        self.assertEqual(t.status[-1][0], "ok")

    def test_invalid_frame_is_error_and_never_pushed(self):
        t = Pipe()
        bad = frame()
        bad["extra"] = 1  # unbekanntes Feld -> navi_tbt lehnt ab
        self.assertFalse(t.p.on_line(line(bad), 10.0))
        self.assertEqual(t.out, [])
        self.assertEqual(t.status[-1][0], "error")

    def test_garbage_bytes_are_error(self):
        t = Pipe()
        self.assertFalse(t.p.on_line(b"{nope\n", 10.0))
        self.assertEqual(t.status[-1][0], "error")

    def test_navi_error_frame_stops_push_and_resets_comparison(self):
        t = Pipe()
        t.p.on_line(line(frame()), 10.0)
        self.assertFalse(t.p.on_line(line(frame(status="error")), 11.0))
        self.assertEqual(t.status[-1], ("error", "gnss: kein Fix"))
        self.assertIsNone(t.p.prev)

    def test_stale_after_silence_reported_once(self):
        t = Pipe()
        t.p.on_line(line(frame()), 10.0)
        t.p.on_tick(12.0)
        self.assertEqual(t.status[-1][0], "ok")
        t.p.on_tick(10.0 + core.STALE_AFTER_S)
        t.p.on_tick(10.0 + core.STALE_AFTER_S + 5)
        self.assertEqual([s for s, _ in t.status].count("stale"), 1)


class StatusFile(unittest.TestCase):
    def test_writes_atomically_and_round_trips(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "navidatenkanal.status")
            core.write_status(path, "ok", 5.0, "fine")
            body = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(body["state"], "ok")
            self.assertEqual(body["detail"], "fine")
            self.assertEqual([p for p in os.listdir(os.path.dirname(path))], ["navidatenkanal.status"])

    def test_rejects_unknown_state(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                core.write_status(os.path.join(d, "s"), "driving", 1.0)


class VendoredTbt(unittest.TestCase):
    def test_vendored_copy_matches_reference_when_present(self):
        # Die Paketkopie ist die Referenz aus car-apps/navi (Wrapper-Wurzel, 4 Ebenen hoch).
        ref = HERE.parents[3] / "car-apps" / "navi" / "navi_tbt.py"
        if not ref.exists():
            self.skipTest("car-apps/navi nicht im Checkout")
        self.assertEqual((LIB / "navi_tbt.py").read_bytes(), ref.read_bytes())


if __name__ == "__main__":
    unittest.main()
