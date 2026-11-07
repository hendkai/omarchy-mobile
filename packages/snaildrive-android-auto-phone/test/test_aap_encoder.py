# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests fuer den AAP-Encoder (snaildrive_aac.aap_nav). Stdlib only, phone-frei.

Referenz-Bytes: protoc 35.0 (--encode) gegen die .proto-Dateien aus libaasdk
(opencardev/aasdk @ 5483c6481742d35bd9ed5923eb74a3562eeab84b, GPLv3). Eingaben
als Textformat, z.B.:

  protoc -I<root> --encode=aap_protobuf.service.navigationstatus.message.NavigationCurrentPosition \\
      aap_protobuf/service/navigationstatus/message/NavigationCurrentPosition.proto < in_pos.txt

Lauf:  cd packages/snaildrive-android-auto-phone && python3 -m unittest discover -s test -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parent / "lib" / "snaildrive-android-auto-phone"
sys.path.insert(0, str(LIB))

from snaildrive_aac import aap_nav  # noqa: E402

# Eingabe-Datensatz, entspricht den Textformat-Eingaben oben:
#   status: ACTIVE
#   step_distance { distance { meters: 800 display_units: METERS } }
#   destination_distances { distance { meters: 12400 display_units: METERS }
#                           time_to_arrival_seconds: 42 }
#   steps { maneuver { type: TURN_NORMAL_RIGHT } road { name: "A8 Richtung München" } }
RECORD = {
    "v": 1,
    "type": "aap-nav-status",
    "ts": 1730900800.0,
    "status": "navigating",
    "location": {"lat": 48.0, "lon": 9.0, "accuracy": 5.0, "speed_kmh": 50.0,
                 "bearing": 90.0, "satellites": 8, "alt": 500.0},
    "next": {"action": "turn-right", "instruction": "A8 Richtung München",
             "distance_m": 800, "turn_degree": 45},
    "route": {"distance_to_dest_m": 12400, "eta_s": 42},
}

# protoc-Referenz (hex)
REF_STATUS_ACTIVE = "0801"
REF_CURRENT_POSITION = "0a070a0508a006180112090a0508f0601801182a"
REF_STATE_TURN_RIGHT = "0a1c0a02080812160a144138205269636874756e67204dc3bc6e6368656e"


class Vectors(unittest.TestCase):
    def test_status_matches_protoc(self):
        self.assertEqual(aap_nav.navigation_status("navigating").hex(), REF_STATUS_ACTIVE)

    def test_current_position_matches_protoc(self):
        self.assertEqual(aap_nav.navigation_current_position(RECORD).hex(), REF_CURRENT_POSITION)

    def test_state_matches_protoc(self):
        self.assertEqual(aap_nav.navigation_state(RECORD).hex(), REF_STATE_TURN_RIGHT)

    def test_message_ids_from_proto(self):
        ids = [mid for mid, _ in aap_nav.messages(RECORD)]
        self.assertEqual(ids, [32771, 32774, 32775])  # NavigationStatusMessageId.proto


class Frames(unittest.TestCase):
    def test_frame_header_and_size_match_aasdk(self):
        # FrameHeader: channel 12 (NAVIGATION_STATUS), flags 0x03 = BULK | SPECIFIC | PLAIN.
        # SHORT-Groesse 2 Byte BE = Laenge von (Message-ID + Body) = 2 + 2 = 4.
        # Message-ID 32771 = 0x8003 (BE), Body = 08 01.
        frame = aap_nav.plain_frame(12, 32771, bytes.fromhex("0801"))
        self.assertEqual(frame.hex(), "0c" "03" "0004" "8003" "0801")

    def test_record_to_frames_channel_and_order(self):
        frames = aap_nav.record_to_frames(RECORD)
        self.assertEqual(len(frames), 3)
        self.assertTrue(all(f[0] == 12 and f[1] == 0x03 for f in frames))
        self.assertEqual([f[4:6] for f in frames], [b"\x80\x03", b"\x80\x06", b"\x80\x07"])

    def test_oversize_payload_is_refused(self):
        with self.assertRaises(ValueError):
            aap_nav.plain_frame(12, 32771, b"\x00" * 0x4000)

    def test_frame_length_matches_payload(self):
        for frame in aap_nav.record_to_frames(RECORD):
            size = int.from_bytes(frame[2:4], "big")
            self.assertEqual(len(frame), 4 + size)


class Mapping(unittest.TestCase):
    def test_maneuver_values_from_proto(self):
        # NavigationManeuver.NavigationType (protobuf/.../NavigationManeuver.proto)
        self.assertEqual(aap_nav.MANEUVER["departure"], 1)
        self.assertEqual(aap_nav.MANEUVER["turn-left"], 7)
        self.assertEqual(aap_nav.MANEUVER["turn-right"], 8)
        self.assertEqual(aap_nav.MANEUVER["sharp-left"], 9)
        self.assertEqual(aap_nav.MANEUVER["straight"], 36)
        self.assertEqual(aap_nav.MANEUVER["arrival"], 39)

    def test_ambiguous_actions_stay_unknown(self):
        # Ohne Seitenangabe im Datensatz wird nichts geraten.
        self.assertEqual(aap_nav.MANEUVER["u-turn"], 0)
        self.assertEqual(aap_nav.MANEUVER["ramp"], 0)

    def test_every_navi_tbt_action_is_mapped(self):
        import navi_tbt
        self.assertEqual(set(navi_tbt.ACTION_VALUES) - set(aap_nav.MANEUVER), set())

    def test_unknown_action_is_an_error(self):
        bad = dict(RECORD, next=dict(RECORD["next"], action="teleport"))
        with self.assertRaises(ValueError):
            aap_nav.navigation_state(bad)

    def test_paused_is_inactive_and_not_active(self):
        self.assertEqual(aap_nav.navigation_status("paused").hex(), "0802")

    def test_wrong_record_type_is_refused(self):
        with self.assertRaises(ValueError):
            aap_nav.messages(dict(RECORD, type="other"))

    def test_location_is_not_in_the_aa_messages(self):
        # Position geht ueber den Sensor-Kanal, nicht ueber die Navi-Nachrichten.
        blob = b"".join(body for _, body in aap_nav.messages(RECORD))
        for key in ("48.0", "9.0", "bearing"):
            self.assertNotIn(key.encode(), blob)


class EndToEnd(unittest.TestCase):
    def test_navi_frame_through_pipeline_to_aap_frames(self):
        # Navi-Frame (navi_tbt) -> Pipeline -> aap-nav-status -> AAP-Frames.
        from snaildrive_aac import core
        from test_core import frame, line

        out = []
        pipe = core.Pipeline(publish=out.append, status=lambda *a: None)
        self.assertTrue(pipe.on_line(line(frame(action="turn-left", distance=300)), 10.0))
        record = __import__("json").loads(out[0].decode("utf-8"))
        frames = aap_nav.record_to_frames(record)
        self.assertEqual(len(frames), 3)
        # steps (Feld 1, Laenge n) -> NavigationStep.maneuver (Feld 1, Laenge 2) mit type = 7
        body = aap_nav.navigation_state(record)
        self.assertEqual(body[0], 0x0a)
        self.assertEqual(body[2:6], b"\x0a\x02\x08\x07")  # TURN_NORMAL_LEFT


class Varint(unittest.TestCase):
    def test_small_and_multibyte(self):
        self.assertEqual(aap_nav._varint(0), b"\x00")
        self.assertEqual(aap_nav._varint(300), b"\xac\x02")   # protobuf-Doku-Beispiel
        self.assertEqual(aap_nav._varint(12400), b"\xf0\x60")  # wie protoc oben

    def test_negative_int32_is_ten_bytes(self):
        self.assertEqual(aap_nav._varint(-1), b"\xff" * 9 + b"\x01")


if __name__ == "__main__":
    unittest.main()
