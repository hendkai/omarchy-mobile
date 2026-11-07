#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""GNSS fixes from ModemManager (omamobile-fahrtenbuch installs this module to
/usr/lib/omamobile-fahrtenbuch/; parkreminder imports it from there).

Only the `mmcli` command of ModemManager is used, no D-Bus binding:

  mmcli -L -J                          which modems exist
  mmcli -m N -J                        their state
  mmcli -m N --location-enable-gps-nmea   GNSS on (only for a modem that is up)
  mmcli -m N --location-get            NMEA sentences of the current fixes
  mmcli -m N --location-disable-gps-nmea  GNSS off again (always, in finally)

The GNSS source is enabled only when the modem is in a state after its
bring-up (BRINGUP_STATES). Otherwise GpsUnavailable: the callers then fall
back (fahrtenbuch: CSV import, parkreminder: lasttrip). A receiver is never
left on: the disable runs after every collection, also after errors.

NMEA is parsed from the output of --location-get (any line that contains a
checksummed $xxGGA or $xxRMC sentence); a sentence with a bad checksum or
without a fix is ignored. Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

# Modem states in which GNSS may be switched on: the bring-up has finished.
# Anything else (locked, disabled, initializing, enabling, failed, ...) keeps GNSS off.
BRINGUP_STATES = ("enabled", "searching", "registered", "connected")

MMCLI_TIMEOUT_S = 20.0
# Test-Schalter: OMAMOBILE_MMCLI=/nonexistent zwingt "kein mmcli" (Suiten ohne Hardware).
MMCLI = os.environ.get("OMAMOBILE_MMCLI", "mmcli")

# (returncode, stdout, stderr)
Runner = Callable[[list, float], tuple]

_SENTENCE = re.compile(r"\$[A-Z]{2}(?:GGA|RMC),[^\r\n$*]*\*[0-9A-Fa-f]{2}")


class GpsUnavailable(Exception):
    """No GNSS fix can be had now (no modem, modem not up, no fix in time, ...)."""


@dataclass(frozen=True)
class Fix:
    ts: datetime        # naive, local time (like the CSV tracks of the Fahrtenbuch)
    lat: float
    lon: float
    hdop: Optional[float] = None


def _checksum_ok(sentence: str) -> bool:
    body, _, want = sentence[1:].rpartition("*")
    got = 0
    for ch in body:
        got ^= ord(ch)
    try:
        return got == int(want, 16)
    except ValueError:
        return False


def _degrees(raw: str, hemi: str) -> Optional[float]:
    """NMEA ddmm.mmmm / dddmm.mmmm with N/S/E/W -> signed decimal degrees."""
    if not raw or not hemi:
        return None
    value = float(raw)
    deg = int(value // 100)
    dec = deg + (value - deg * 100) / 60.0
    if hemi in ("S", "W"):
        dec = -dec
    elif hemi not in ("N", "E"):
        return None
    return dec


def _utc_time(hhmmss: str) -> tuple:
    return int(hhmmss[0:2]), int(hhmmss[2:4]), int(hhmmss[4:6])


def _to_local_naive(utc: datetime) -> datetime:
    return datetime.fromtimestamp(utc.replace(tzinfo=timezone.utc).timestamp())


def parse_sentence(sentence: str, now_utc: Optional[datetime] = None) -> Optional[Fix]:
    """One $..GGA / $..RMC sentence -> Fix, or None (bad checksum, no fix, junk)."""
    sentence = sentence.strip()
    if not sentence.startswith("$") or "*" not in sentence or not _checksum_ok(sentence):
        return None
    f = sentence[1:sentence.rindex("*")].split(",")
    kind = f[0][2:]
    try:
        if kind == "RMC":
            # id, time, status(A/V), lat, N/S, lon, E/W, speed, course, date
            if len(f) < 10 or f[2] != "A" or not f[1] or not f[9]:
                return None
            h, m, s = _utc_time(f[1])
            day, mon, yr = int(f[9][0:2]), int(f[9][2:4]), 2000 + int(f[9][4:6])
            utc = datetime(yr, mon, day, h, m, s, tzinfo=timezone.utc)
            hdop = None
            lat, lon = _degrees(f[3], f[4]), _degrees(f[5], f[6])
        elif kind == "GGA":
            # id, time, lat, N/S, lon, E/W, quality (0 = no fix), sats, hdop, ...
            if len(f) < 9 or f[6] in ("", "0") or not f[1]:
                return None
            now = now_utc or datetime.now(timezone.utc)
            h, m, s = _utc_time(f[1])
            utc = now.replace(hour=h, minute=m, second=s, microsecond=0)
            if utc > now + timedelta(minutes=1):   # fix from just before midnight
                utc -= timedelta(days=1)
            hdop = float(f[8]) if f[8] else None
            lat, lon = _degrees(f[2], f[3]), _degrees(f[4], f[5])
        else:
            return None
    except (ValueError, IndexError):
        return None
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    return Fix(_to_local_naive(utc), round(lat, 6), round(lon, 6), hdop)


def parse_output(text: str, now_utc: Optional[datetime] = None) -> list:
    """All fixes in the text of `mmcli --location-get`, in order, no duplicates."""
    out, seen = [], set()
    for m in _SENTENCE.finditer(text):
        fix = parse_sentence(m.group(0), now_utc)
        if fix is None:
            continue
        key = (fix.ts, fix.lat, fix.lon)
        if key not in seen:
            seen.add(key)
            out.append(fix)
    return out


def subprocess_runner(argv: list, timeout: float) -> tuple:
    """The real runner. A missing mmcli or a hang is GpsUnavailable, not a crash."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        raise GpsUnavailable("mmcli fehlt (ModemManager nicht installiert)")
    except subprocess.TimeoutExpired:
        raise GpsUnavailable(f"{argv[0]} antwortet nicht ({timeout:.0f} s)")
    return p.returncode, p.stdout, p.stderr


def _mmcli(run: Runner, args: list, timeout: float = MMCLI_TIMEOUT_S) -> str:
    rc, out, err = run([MMCLI, *args], timeout)
    if rc != 0:
        detail = (err or "").strip() or f"Exit {rc}"
        raise GpsUnavailable(f"mmcli {' '.join(args)}: {detail}")
    return out


def find_modem(run: Runner) -> str:
    """Index of the one modem, when its bring-up has finished; else GpsUnavailable."""
    try:
        paths = json.loads(_mmcli(run, ["-L", "-J"])).get("modem-list", [])
    except ValueError:
        raise GpsUnavailable("mmcli -L: keine JSON-Ausgabe")
    if not paths:
        raise GpsUnavailable("kein Modem bei ModemManager (Bring-up nicht abgeschlossen?)")
    index = paths[0].rsplit("/", 1)[-1]
    if not index.isdigit():
        raise GpsUnavailable(f"unerwarteter Modem-Pfad {paths[0]!r}")
    try:
        info = json.loads(_mmcli(run, ["-m", index, "-J"]))
    except ValueError:
        raise GpsUnavailable("mmcli -m: keine JSON-Ausgabe")
    state = (info.get("modem", {}).get("generic", {}) or {}).get("state", "")
    if state not in BRINGUP_STATES:
        raise GpsUnavailable(f"Modem nicht oben (Zustand {state or 'unbekannt'}); GNSS bleibt aus")
    return index


def collect_fixes(window_s: float = 0.0, timeout_s: float = 30.0, interval_s: float = 2.0,
                  runner: Optional[Runner] = None,
                  clock: Callable[[], float] = time.monotonic,
                  sleep: Callable[[float], None] = time.sleep) -> list:
    """Enable GNSS, wait for the first fix (at most timeout_s), keep sampling for
    window_s after it, then disable GNSS again. Returns the fixes in order.

    window_s=0 returns as soon as there is one fix. Raises GpsUnavailable when
    there is no modem up or no fix within timeout_s.
    """
    run = runner or subprocess_runner
    index = find_modem(run)
    fixes, seen = [], set()
    last_err = None
    start = clock()
    first_fix_at = None
    try:
        _mmcli(run, ["-m", index, "--location-enable-gps-nmea"])
        while True:
            try:
                text = _mmcli(run, ["-m", index, "--location-get"])
            except GpsUnavailable as exc:
                last_err, text = exc, ""
            for fix in parse_output(text):
                key = (fix.ts, fix.lat, fix.lon)
                if key not in seen:
                    seen.add(key)
                    fixes.append(fix)
            now = clock()
            if fixes and first_fix_at is None:
                first_fix_at = now
            if first_fix_at is not None and now - first_fix_at >= window_s:
                return fixes
            if first_fix_at is None and now - start >= timeout_s:
                why = f"; letzter Fehler: {last_err}" if last_err else ""
                raise GpsUnavailable(f"kein GPS-Fix in {timeout_s:.0f} s{why}")
            sleep(interval_s)
    finally:
        try:
            _mmcli(run, ["-m", index, "--location-disable-gps-nmea"])
        except GpsUnavailable:
            pass   # a failing disable must not hide the result or the first error
