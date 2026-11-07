# SPDX-License-Identifier: GPL-3.0-or-later
"""AAP-Encoder (Lab): aap-nav-status-Datensatz -> AAP-NavigationStatus-Frames.

Quelle der Feldnummern, Enum-Werte, Message-IDs und des Framings ist libaasdk
(opencardev/aasdk, Commit 5483c6481742d35bd9ed5923eb74a3562eeab84b, GPLv3):

  protobuf/aap_protobuf/service/navigationstatus/NavigationStatusMessageId.proto
  protobuf/aap_protobuf/service/navigationstatus/message/
      NavigationStatus, NavigationState, NavigationCurrentPosition, NavigationStep,
      NavigationManeuver, NavigationRoad, NavigationStepDistance,
      NavigationDestinationDistance, NavigationDistance .proto
  include/aasdk/Messenger/{ChannelId,FrameHeader,FrameType,MessageType,EncryptionType}.hpp
  src/Messenger/{FrameHeader,MessageId,FrameSize,MessageOutStream}.cpp
  src/Channel/NavigationStatus/NavigationStatusService.cpp (insertPayload: Message-ID zuerst)

Die Protobuf-Bytes sind gegen protoc 35 geprueft (tests/test_aap_encoder.py).

Was dieser Schritt NICHT tut (siehe README.md, "Nicht enthalten"):
  * keine Verschluesselung (nach dem AA-Handshake ist jeder Frame verschluesselt;
    hier entstehen nur Klartext-BULK-Frames, die ein Lab-Harness braucht),
  * keine Fragmentierung (Nutzlast >= 0x4000 wird abgelehnt),
  * keinen Handshake, keinen Channel-Open, keine Start/Stop-Nachrichten,
  * keine Location-Nachricht (Position geht ueber den Sensor-Kanal, nicht hier).

Feldzuordnung (bewusst, keine Werte geraten):
  next.distance_m        -> NavigationCurrentPosition.step_distance.distance.meters
  route.distance_to_dest -> NavigationCurrentPosition.destination_distances[0].distance.meters
  route.eta_s            -> destination_distances[0].time_to_arrival_seconds
  next.action            -> NavigationState.steps[0].maneuver.type (Tabelle MANEUVER)
  next.instruction       -> NavigationState.steps[0].road.name (Text, keine Adresse)
  status                 -> NavigationStatus.status (ACTIVE bei navigating/arriving)
"""

from __future__ import annotations

import struct
from typing import Any

# --- Frame-Kopf (FrameHeader.cpp, ChannelId.hpp, FrameType.hpp, MessageType.hpp,
#     EncryptionType.hpp, FrameSize.cpp, MessageOutStream.cpp) ---
NAVIGATION_STATUS_CHANNEL = 12          # ChannelId::NAVIGATION_STATUS (0-basiert, Zaehlung in ChannelId.hpp)
FRAME_BULK = 0x03                       # FrameType::BULK = FIRST | LAST
MESSAGE_TYPE_SPECIFIC = 0x00            # MessageType::SPECIFIC (Nicht-Control)
ENCRYPTION_PLAIN = 0x00                 # EncryptionType::PLAIN
MAX_FRAME_PAYLOAD = 0x4000              # MessageOutStream::cMaxFramePayloadSize

# --- Message-IDs (NavigationStatusMessageId.proto) ---
MSG_NAVIGATION_STATUS = 32771           # INSTRUMENT_CLUSTER_NAVIGATION_STATUS
MSG_NAVIGATION_STATE = 32774            # INSTRUMENT_CLUSTER_NAVIGATION_STATE
MSG_NAVIGATION_CURRENT_POSITION = 32775  # INSTRUMENT_CLUSTER_NAVIGATION_CURRENT_POSITION

# --- Enum-Werte (NavigationStatus.NavigationStatusEnum, NavigationDistance.DistanceUnits,
#     NavigationManeuver.NavigationType) ---
STATUS_UNAVAILABLE = 0
STATUS_ACTIVE = 1
STATUS_INACTIVE = 2
UNIT_METERS = 1

# Aktion (navi_tbt.ACTION_VALUES) -> NavigationManeuver.NavigationType.
# Fehlt ein Wert im Datensatz (z.B. Richtung bei u-turn/ramp), wird UNKNOWN (0)
# gesendet statt eine Richtung zu raten.
MANEUVER = {
    "none": 0,                 # UNKNOWN
    "departure": 1,            # DEPART
    "keep-left": 3,            # KEEP_LEFT
    "keep-right": 4,           # KEEP_RIGHT
    "slight-left": 5,          # TURN_SLIGHT_LEFT
    "slight-right": 6,         # TURN_SLIGHT_RIGHT
    "turn-left": 7,            # TURN_NORMAL_LEFT
    "turn-right": 8,           # TURN_NORMAL_RIGHT
    "sharp-left": 9,           # TURN_SHARP_LEFT
    "sharp-right": 10,         # TURN_SHARP_RIGHT
    "u-turn": 0,               # UNKNOWN: Datensatz nennt keine Seite
    "merge": 29,               # MERGE_SIDE_UNSPECIFIED
    "ramp": 0,                 # UNKNOWN: Datensatz nennt keine Seite
    "roundabout-enter": 30,    # ROUNDABOUT_ENTER
    "roundabout-exit": 31,     # ROUNDABOUT_EXIT
    "straight": 36,            # STRAIGHT
    "ferry": 37,               # FERRY_BOAT
    "arrival": 39,             # DESTINATION
}

PUSHING_STATES = ("navigating", "arriving")


# --- Protobuf-Wire-Format (stdlib, nur was gebraucht wird) ---
# Wire-Typen: 0 = varint, 2 = length-delimited.

def _varint(value: int) -> bytes:
    if value < 0:  # int32/int64 negativ: 64-Bit-Zweierkomplement, 10 Byte
        value += 1 << 64
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _vfield(field: int, value: int) -> bytes:
    return _tag(field, 0) + _varint(int(value))


def _lfield(field: int, data: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(data)) + data


def _sfield(field: int, text: str) -> bytes:
    return _lfield(field, text.encode("utf-8"))


# --- Nachrichten (Felder in aufsteigender Nummer, wie protoc sie schreibt) ---

def _distance(meters: int) -> bytes:
    # NavigationDistance: meters = 1, display_units = 3
    return _vfield(1, meters) + _vfield(3, UNIT_METERS)


def navigation_status(status: str) -> bytes:
    # NavigationStatus: status = 1
    value = STATUS_ACTIVE if status in PUSHING_STATES else STATUS_INACTIVE
    return _vfield(1, value)


def navigation_current_position(record: dict[str, Any]) -> bytes:
    # NavigationCurrentPosition: step_distance = 1, destination_distances = 2
    # NavigationStepDistance: distance = 1
    # NavigationDestinationDistance: distance = 1, time_to_arrival_seconds = 3
    step = _lfield(1, _distance(record["next"]["distance_m"]))
    dest = _lfield(1, _distance(record["route"]["distance_to_dest_m"]))
    dest += _vfield(3, record["route"]["eta_s"])
    return _lfield(1, step) + _lfield(2, dest)


def navigation_state(record: dict[str, Any]) -> bytes:
    # NavigationState: steps = 1 (NavigationStep)
    # NavigationStep: maneuver = 1, road = 2
    # NavigationManeuver: type = 1; NavigationRoad: name = 1
    action = record["next"]["action"]
    if action not in MANEUVER:
        raise ValueError(f"keine Maneuver-Zuordnung fuer action {action!r}")
    maneuver = _vfield(1, MANEUVER[action])
    road = _sfield(1, record["next"]["instruction"])
    return _lfield(1, _lfield(1, maneuver) + _lfield(2, road))


def messages(record: dict[str, Any]) -> list[tuple[int, bytes]]:
    """Ein aap-nav-status-Datensatz -> [(Message-ID, Protobuf-Body), ...]."""
    if record.get("type") != "aap-nav-status" or record.get("v") != 1:
        raise ValueError("kein aap-nav-status v1")
    return [
        (MSG_NAVIGATION_STATUS, navigation_status(record["status"])),
        (MSG_NAVIGATION_STATE, navigation_state(record)),
        (MSG_NAVIGATION_CURRENT_POSITION, navigation_current_position(record)),
    ]


def plain_frame(channel_id: int, message_id: int, body: bytes) -> bytes:
    """BULK-Frame im Klartext: Kanal, Flags, Groesse (2 Byte BE), Message-ID (2 Byte BE), Body.

    Nur fuer den Lab-Harness ohne TLS. Auf einer echten AA-Session darf nichts
    unverschluesselt gehen.
    """
    payload = struct.pack(">H", message_id) + body
    if len(payload) >= MAX_FRAME_PAYLOAD:
        raise ValueError(f"Nutzlast {len(payload)} B >= {MAX_FRAME_PAYLOAD} B: Fragmentierung nicht umgesetzt")
    header = bytes([channel_id, FRAME_BULK | MESSAGE_TYPE_SPECIFIC | ENCRYPTION_PLAIN])
    return header + struct.pack(">H", len(payload)) + payload


def record_to_frames(record: dict[str, Any]) -> list[bytes]:
    """aap-nav-status v1 -> Klartext-Frames fuer den Navigationskanal (Lab)."""
    return [plain_frame(NAVIGATION_STATUS_CHANNEL, mid, body) for mid, body in messages(record)]
