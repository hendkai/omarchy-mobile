#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""parkreminder: Parkpunkt beim Aussteigen merken (Omarchy Mobile).

Umsetzung von car-apps/parkreminder/DESIGN.md:
  udev (USB remove = Zuendung aus) -> `parkreminder trigger`
  1. Position holen: Quellen in config-Reihenfolge (source=...)
       lasttrip      Endpunkt der letzten Fahrt aus dem Fahrtenbuch (heute der
                     einzige Weg ohne Modem; das Fahrtenbuch laeuft mit)
       modemmanager  ModemManager-Location. Noch nicht angebunden (P4): liefert
                     bis dahin keinen Fix.
  2. Parkpunkt nach $PARKREMINDER_STATE/last_park.json (Standard:
     ~/.local/state/parkreminder/)
  3. Optional notify=signal: Hermes-One-shot (ist standardmaessig aus)

Ohne Fix: Exit 1 und keine Datei (der alte Parkpunkt bleibt gueltig).
Nie per Timer ausfuehren: waehrend der Fahrt wuerde der Parkpunkt ueberschrieben.
"""
from __future__ import annotations

import configparser
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = "last_park.json"


def config_path() -> Path:
    env = os.environ.get("PARKREMINDER_CONFIG")
    return Path(env) if env else Path("~/.config/parkreminder.conf").expanduser()


def state_dir() -> Path:
    env = os.environ.get("PARKREMINDER_STATE")
    if env:
        return Path(env)
    return Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "parkreminder"


def fahrtenbuch_db() -> Path:
    env = os.environ.get("FAHRTENBUCH_DB")
    if env:
        return Path(env)
    return Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "fahrtenbuch" / "fahrtenbuch.sqlite"


def load_config(path: Path) -> dict:
    cfg = configparser.ConfigParser()
    if path.exists():
        cfg.read(path, encoding="utf-8")
    sect = cfg["parkreminder"] if cfg.has_section("parkreminder") else {}
    notify = str(sect.get("notify", "none")).strip()
    if notify not in ("none", "signal"):
        notify = "none"
    source = str(sect.get("source", "lasttrip")).strip() or "lasttrip"
    return {"notify": notify, "source": source}


def position_lasttrip(db: Path) -> dict | None:
    """Endpunkt der juengsten Fahrt aus dem Fahrtenbuch (read-only)."""
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT end_lat, end_lon, ended_at FROM trips "
                              "WHERE end_lat IS NOT NULL ORDER BY ended_at DESC LIMIT 1").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    lat, lon, ended_at = row
    return {"lat": lat, "lon": lon, "source": "lasttrip",
            "note": f"Ende der letzten Fahrt {ended_at}"}


def position_modemmanager() -> dict | None:
    """ModemManager-Location. Noch nicht angebunden (P4): kein Fix."""
    return None


SOURCES = {"lasttrip": lambda: position_lasttrip(fahrtenbuch_db()),
           "modemmanager": position_modemmanager}


def locate(source_list: str) -> dict | None:
    for name in source_list.split(":"):
        fn = SOURCES.get(name.strip())
        if fn is None:
            continue
        point = fn()
        if point:
            return point
    return None


def save_park(point: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    data = dict(point)
    data["saved_at"] = datetime.now(timezone.utc).isoformat()
    path = directory / STATE_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def notify_hermes(text: str) -> None:
    """Notify=signal: Hermes-One-shot. Fehler bleiben still (Log genuegt)."""
    try:
        subprocess.run(["hermes", "chat", "-q", text], check=False, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def trigger() -> int:
    cfg = load_config(config_path())
    point = locate(cfg["source"])
    if point is None:
        print("parkreminder: kein Standort (Quellen: " + cfg["source"] + "); kein Parkpunkt gespeichert",
              file=sys.stderr)
        return 1
    path = save_park(point, state_dir())
    if cfg["notify"] == "signal":
        notify_hermes(f"Parkpunkt gespeichert ({point.get('source')}).")
    print(f"parkreminder: Parkpunkt -> {path}")
    return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="parkreminder")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("trigger", help="Standort jetzt fixieren (udev, USB remove)")
    sub.add_parser("show", help="letzten Parkpunkt anzeigen")
    a = ap.parse_args(argv)
    if a.cmd == "trigger":
        return trigger()
    path = state_dir() / STATE_FILE
    if not path.exists():
        print("parkreminder: noch kein Parkpunkt", file=sys.stderr)
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
