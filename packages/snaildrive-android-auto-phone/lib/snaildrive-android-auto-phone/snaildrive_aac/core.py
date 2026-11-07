# SPDX-License-Identifier: GPL-3.0-or-later
"""Phone-seitiger Kern der SnailDrive-AA-Navi-Bruecke (phone-frei testbar).

Eingang: NDJSON-Frames des Offline-Navi-Dienstes (Navi-Datenkanal v1,
navi_tbt.py, Unix-Socket /run/snaildrive/navi.sock, Navi = Server).
Ausgang: je ein Nav-Status-Datensatz (aap-nav-status v1) auf dem lokalen
AAC-Socket. Der spaetere libaasdk-Encoder setzt diese Datensaetze in
AAP-Protobufs (NavigationStatus, Location, RouteInfo) um; das ist NICHT Teil
dieses Schritts (siehe README.md, "Nicht enthalten").

Regeln (docs/spacewar-aa-phone-hud-concept.md Kap. 4.2):
  * 1 Hz Push waehrend navigating/arriving,
  * Burst (hoechstens 2 Hz) bei Status-/Aktionswechsel (departure, turn, arrival),
  * bei status == error: kein Push, Status-Datei schreiben, Log.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Callable, Optional

import navi_tbt

RECORD_TYPE = "aap-nav-status"
RECORD_VERSION = 1

PUSH_INTERVAL_S = 1.0       # Dauer-Push im Navigationsbetrieb (1 Hz)
BURST_MIN_GAP_S = 0.5       # Ereignis-Burst hoechstens 2 Hz
STALE_AFTER_S = 10.0        # kein gueltiger Frame so lange -> stale
PUSHING_STATES = ("navigating", "arriving")


@dataclass(frozen=True)
class NavState:
    status: str
    action: str
    instruction: str
    distance_m: int
    turn_degree: int
    lat: float
    lon: float
    accuracy: float
    speed_kmh: float
    bearing: float
    satellites: int
    alt: float
    distance_to_dest_m: int
    eta_s: int


def state_from_frame(msg: dict[str, Any]) -> NavState:
    """Gueltige (von navi_tbt geprueft) Nachricht -> NavState."""
    loc = msg["loc"]
    nxt = msg["next"]
    route = msg["route"]
    return NavState(
        status=msg["status"],
        action=nxt["action"],
        instruction=nxt["instruction"],
        distance_m=nxt["distance"],
        turn_degree=_first_turn_degree(route),
        lat=loc["lat"],
        lon=loc["lon"],
        accuracy=loc["accuracy"],
        speed_kmh=loc["speed"],
        bearing=loc["bearing"],
        satellites=loc["satellites"],
        alt=loc["alt"],
        distance_to_dest_m=route["distance_to_dest"],
        eta_s=route["eta_s"],
    )


def _first_turn_degree(route: dict[str, Any]) -> int:
    legs = route.get("legs") or []
    return int(legs[0]["turn_degree"]) if legs else 0


def should_push(prev: Optional[NavState], cur: NavState, now: float,
                last_push: Optional[float]) -> bool:
    """Entscheidet, ob ein Datensatz jetzt rausgeht (siehe Modul-Doku)."""
    if last_push is not None and now - last_push < BURST_MIN_GAP_S:
        return False
    if prev is None:
        return cur.status in PUSHING_STATES
    if cur.status not in PUSHING_STATES:
        return False  # paused/idle/error: kein Push
    if (prev.status, prev.action, prev.instruction) != (cur.status, cur.action, cur.instruction):
        return True
    return last_push is None or now - last_push >= PUSH_INTERVAL_S


def to_record(state: NavState, ts: float) -> dict[str, Any]:
    """Datensatz aap-nav-status v1 (JSON, ein Objekt pro Zeile)."""
    return {
        "v": RECORD_VERSION,
        "type": RECORD_TYPE,
        "ts": ts,
        "status": state.status,
        "location": {
            "lat": state.lat, "lon": state.lon, "accuracy": state.accuracy,
            "speed_kmh": state.speed_kmh, "bearing": state.bearing,
            "satellites": state.satellites, "alt": state.alt,
        },
        "next": {
            "action": state.action,
            "instruction": state.instruction,
            "distance_m": state.distance_m,
            "turn_degree": state.turn_degree,
        },
        "route": {
            "distance_to_dest_m": state.distance_to_dest_m,
            "eta_s": state.eta_s,
        },
    }


def encode_record(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def write_status(path: str, state: str, ts: float, detail: str = "") -> None:
    """navidatenkanal.status atomar schreiben (Zustand: ok, error, stale, waiting)."""
    if state not in ("ok", "error", "stale", "waiting"):
        raise ValueError(f"unbekannter Status: {state!r}")
    body = {"v": RECORD_VERSION, "state": state, "ts": ts, "detail": detail[:200]}
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".navidatenkanal.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(body, fh, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Pipeline:
    """Zustandsbehaftete Verarbeitung einer Frame-Folge (ohne Sockets, testbar)."""

    def __init__(self, publish: Callable[[bytes], None],
                 status: Callable[[str, float, str], None]) -> None:
        self._publish = publish
        self._status = status
        self.prev: Optional[NavState] = None
        self.last_push: Optional[float] = None
        self.last_frame: Optional[float] = None
        self._stale = False

    def on_line(self, raw: bytes, now: float) -> bool:
        """Verarbeitet eine NDJSON-Zeile. True, wenn ein Datensatz rausging."""
        try:
            msg = navi_tbt.decode_line(raw)
        except navi_tbt.NaviTbtError as exc:
            self._status("error", now, "; ".join(exc.errors)[:200])
            return False
        if msg["status"] == "error":
            self._status("error", now, str(msg.get("error", "navi: error")))
            self.prev = None  # neuer Lauf nach dem Fehler beginnt ohne Vergleich
            return False
        self.last_frame = now
        self._stale = False
        state = state_from_frame(msg)
        pushed = False
        if should_push(self.prev, state, now, self.last_push):
            self._publish(encode_record(to_record(state, now)))
            self.last_push = now
            pushed = True
        self.prev = state
        self._status("ok", now, "")
        return pushed

    def on_tick(self, now: float) -> None:
        """Ohne Frames: nach STALE_AFTER_S einmalig 'stale' melden."""
        if self.last_frame is None or self._stale:
            return
        if now - self.last_frame >= STALE_AFTER_S:
            self._stale = True
            self._status("stale", now, "keine Navi-Daten")
