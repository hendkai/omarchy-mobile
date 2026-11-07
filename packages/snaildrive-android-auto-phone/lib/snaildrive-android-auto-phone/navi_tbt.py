#!/usr/bin/env python3
"""SnailDrive Navi - Turn-by-Turn-Schnittstelle v1 (Referenz, stdlib-only).

Definiert das Nachrichtenformat zwischen dem Offline-Navi-Dienst und der
AA-Phone-Seite (snaildrive-android-auto-phone). Ausfuehrliche Spezifikation:
docs/spacewar-offline-navi-concept.md, Kap. 3. Das Format folgt dem
JSON-v1-Schema in docs/spacewar-aa-phone-hud-concept.md Kap. 4.2.

Transport: Unix-Socket /run/snaildrive/navi.sock, NDJSON (eine JSON-Nachricht
pro Zeile, UTF-8). Der Navi-Dienst ist Server, die AA-Seite Client.

Enthalten ist nur das Format (validieren, erzeugen, serialisieren). Keine
Positionsquelle, keine Karte, keine Routing-Logik, kein Socket-Server.

CLI:
    python3 navi_tbt.py sample        # gueltige Beispielnachricht ausgeben
    python3 navi_tbt.py validate FILE  # NDJSON-Datei pruefen (Exit 1 bei Fehlern)
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any

SCHEMA_VERSION = 1

STATUS_VALUES = ("idle", "navigating", "arriving", "paused", "error")

# Aktionen fuer leg.action / next.action (Kap. 4.2 Konzept, erweitert um
# die Abbiegearten, die fuer HUD-Pfeile gebraucht werden).
ACTION_VALUES = (
    "none",
    "departure",
    "arrival",
    "straight",
    "slight-left",
    "slight-right",
    "turn-left",
    "turn-right",
    "sharp-left",
    "sharp-right",
    "u-turn",
    "keep-left",
    "keep-right",
    "merge",
    "ramp",
    "roundabout-enter",
    "roundabout-exit",
    "ferry",
)

LEG_KEYS = ("instruction", "action", "turn_degree", "distance")
NEXT_KEYS = ("action", "instruction", "distance")
LOC_KEYS = ("lat", "lon", "accuracy", "speed", "bearing", "satellites", "alt")
ROUTE_KEYS = ("distance_to_dest", "eta_s", "legs")
TOP_KEYS = ("v", "ts", "loc", "route", "next", "status")
OPTIONAL_TOP_KEYS = ("error",)

MAX_LINE_BYTES = 8192  # Obergrenze pro NDJSON-Zeile; Schutz vor Fehlkonfiguration


class NaviTbtError(ValueError):
    """Eine Nachricht verletzt das Schema. Die Liste steht in .errors."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


# ---------------------------------------------------------------------------
# Pruefung
# ---------------------------------------------------------------------------

def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _check_exact_keys(obj: dict, required: tuple, path: str, errs: list[str],
                      optional: tuple = ()) -> None:
    missing = [k for k in required if k not in obj]
    extra = [k for k in obj if k not in required and k not in optional]
    for k in missing:
        errs.append(f"{path}: Pflichtfeld fehlt: {k}")
    for k in extra:
        errs.append(f"{path}: unbekanntes Feld: {k}")


def _check_leg(leg: Any, path: str, errs: list[str]) -> None:
    if not isinstance(leg, dict):
        errs.append(f"{path}: muss Objekt sein")
        return
    _check_exact_keys(leg, LEG_KEYS, path, errs)
    if "instruction" in leg and not isinstance(leg["instruction"], str):
        errs.append(f"{path}.instruction: muss String sein")
    if "action" in leg and leg["action"] not in ACTION_VALUES:
        errs.append(f"{path}.action: unbekannt: {leg['action']!r}")
    if "turn_degree" in leg and (not _is_int(leg["turn_degree"])
                                 or not -180 <= leg["turn_degree"] <= 180):
        errs.append(f"{path}.turn_degree: ganze Zahl -180..180 erwartet")
    if "distance" in leg and (not _is_int(leg["distance"]) or leg["distance"] < 0):
        errs.append(f"{path}.distance: ganze Zahl >= 0 erwartet (Meter)")


def validate(msg: Any) -> list[str]:
    """Gibt eine Liste von Fehlern zurueck. Leere Liste = gueltig."""
    errs: list[str] = []
    if not isinstance(msg, dict):
        return ["top: muss Objekt sein"]
    _check_exact_keys(msg, TOP_KEYS, "top", errs, OPTIONAL_TOP_KEYS)

    if "v" in msg and msg["v"] != SCHEMA_VERSION:
        errs.append(f"v: muss {SCHEMA_VERSION} sein, ist {msg['v']!r}")
    if "ts" in msg and (not _is_num(msg["ts"]) or msg["ts"] <= 0):
        errs.append("ts: Epoch-Sekunden > 0 erwartet")

    if "status" in msg and msg["status"] not in STATUS_VALUES:
        errs.append(f"status: unbekannt: {msg['status']!r}")
    if "error" in msg:
        if not isinstance(msg["error"], str):
            errs.append("error: muss String sein")
        elif msg.get("status") != "error" and msg["error"]:
            errs.append("error: nur gesetzt, wenn status == 'error'")

    loc = msg.get("loc")
    if "loc" in msg:
        if not isinstance(loc, dict):
            errs.append("loc: muss Objekt sein")
        else:
            _check_exact_keys(loc, LOC_KEYS, "loc", errs)
            if "lat" in loc and (not _is_num(loc["lat"]) or not -90 <= loc["lat"] <= 90):
                errs.append("loc.lat: -90..90 erwartet")
            if "lon" in loc and (not _is_num(loc["lon"]) or not -180 <= loc["lon"] <= 180):
                errs.append("loc.lon: -180..180 erwartet")
            if "accuracy" in loc and (not _is_num(loc["accuracy"]) or loc["accuracy"] < 0):
                errs.append("loc.accuracy: Meter >= 0 erwartet")
            if "speed" in loc and (not _is_num(loc["speed"]) or loc["speed"] < 0):
                errs.append("loc.speed: km/h >= 0 erwartet")
            if "bearing" in loc and (not _is_num(loc["bearing"])
                                     or not 0 <= loc["bearing"] < 360):
                errs.append("loc.bearing: Grad 0..<360 erwartet")
            if "satellites" in loc and (not _is_int(loc["satellites"]) or loc["satellites"] < 0):
                errs.append("loc.satellites: ganze Zahl >= 0 erwartet")
            if "alt" in loc and not _is_num(loc["alt"]):
                errs.append("loc.alt: Zahl erwartet (Meter)")

    route = msg.get("route")
    if "route" in msg:
        if not isinstance(route, dict):
            errs.append("route: muss Objekt sein")
        else:
            _check_exact_keys(route, ROUTE_KEYS, "route", errs)
            if "distance_to_dest" in route and (not _is_int(route["distance_to_dest"])
                                                or route["distance_to_dest"] < 0):
                errs.append("route.distance_to_dest: ganze Zahl >= 0 (Meter) erwartet")
            if "eta_s" in route and (not _is_int(route["eta_s"]) or route["eta_s"] < 0):
                errs.append("route.eta_s: ganze Zahl >= 0 (Sekunden) erwartet")
            legs = route.get("legs")
            if "legs" in route:
                if not isinstance(legs, list):
                    errs.append("route.legs: Liste erwartet")
                else:
                    for i, leg in enumerate(legs):
                        _check_leg(leg, f"route.legs[{i}]", errs)

    nxt = msg.get("next")
    if "next" in msg:
        if not isinstance(nxt, dict):
            errs.append("next: muss Objekt sein")
        else:
            _check_exact_keys(nxt, NEXT_KEYS, "next", errs)
            if "action" in nxt and nxt["action"] not in ACTION_VALUES:
                errs.append(f"next.action: unbekannt: {nxt['action']!r}")
            if "instruction" in nxt and not isinstance(nxt["instruction"], str):
                errs.append("next.instruction: muss String sein")
            if "distance" in nxt and (not _is_int(nxt["distance"]) or nxt["distance"] < 0):
                errs.append("next.distance: ganze Zahl >= 0 (Meter) erwartet")

    return errs


def check(msg: Any) -> dict:
    """Wie validate(), wirft aber NaviTbtError bei Fehlern. Gibt msg zurueck."""
    errs = validate(msg)
    if errs:
        raise NaviTbtError(errs)
    return msg


# ---------------------------------------------------------------------------
# Serialisierung (NDJSON)
# ---------------------------------------------------------------------------

def encode_line(msg: dict) -> bytes:
    """Eine gueltige Nachricht als eine NDJSON-Zeile (inkl. \\n)."""
    check(msg)
    line = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(line) > MAX_LINE_BYTES:
        raise NaviTbtError([f"Zeile zu lang: {len(line)} > {MAX_LINE_BYTES} Bytes"])
    return line


def decode_line(line: bytes) -> dict:
    """Eine NDJSON-Zeile lesen und pruefen. Wirft NaviTbtError."""
    if len(line) > MAX_LINE_BYTES:
        raise NaviTbtError([f"Zeile zu lang: {len(line)} > {MAX_LINE_BYTES} Bytes"])
    try:
        msg = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NaviTbtError([f"kein gueltiges JSON: {exc}"]) from exc
    return check(msg)


# ---------------------------------------------------------------------------
# Beispiel (Testdaten, keine echten Positionen)
# ---------------------------------------------------------------------------

def sample_message(ts: float = 1730900800.0) -> dict:
    """Gueltige Beispielnachricht mit generischen Testkoordinaten (Berlin-Mitte)."""
    return {
        "v": SCHEMA_VERSION,
        "ts": ts,
        "loc": {
            "lat": 52.5200,
            "lon": 13.4050,
            "accuracy": 12.0,
            "speed": 48.5,
            "bearing": 142.3,
            "satellites": 8,
            "alt": 34.0,
        },
        "route": {
            "distance_to_dest": 12400,
            "eta_s": 42,
            "legs": [
                {
                    "instruction": "Richtung Autobahn, Abfahrt 3",
                    "action": "turn-right",
                    "turn_degree": 45,
                    "distance": 800,
                }
            ],
        },
        "next": {
            "action": "turn-right",
            "instruction": "Richtung Autobahn, Abfahrt 3",
            "distance": 800,
        },
        "status": "navigating",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_validate(path: str) -> int:
    bad = 0
    n = 0
    with open(path, "rb") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.rstrip(b"\r\n")
            if not raw:
                continue
            n += 1
            try:
                decode_line(raw)
            except NaviTbtError as exc:
                bad += 1
                for e in exc.errors:
                    print(f"{path}:{lineno}: {e}", file=sys.stderr)
    print(f"{n - bad}/{n} Nachrichten gueltig")
    return 1 if bad or n == 0 else 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "sample":
        sys.stdout.write(json.dumps(sample_message(), ensure_ascii=False, indent=2) + "\n")
        return 0
    if len(argv) == 3 and argv[1] == "validate":
        return _cmd_validate(argv[2])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
