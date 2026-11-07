#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fahrtenbuch (Omarchy Mobile: Paket omamobile-fahrtenbuch).

Trips aus GPS-Trackpunkten erkennen (Luecke > 5 min oder Sprung > 150 km/h =
neue Fahrt), in SQLite speichern, Monats-Export als CSV + HTML (druckbar -> PDF).
Nur die Python-Standardbibliothek.

Datenschutz: die Datenbank und alle Exporte liegen ausserhalb des Pakets, in
$FAHRTENBUCH_DB bzw. ~/.local/share/fahrtenbuch/. Nichts davon gehoert ins Repo.

GPS-Quelle (--gps-source):
  track         CSV-Import (iso-zeit,lat,lon je Zeile). Standard, offline.
  modemmanager  ModemManager-Location / omamobile-gnss. Die Anbindung haengt am
                Modem-Bring-up (P4). Bis dahin faellt sie mit Warnung auf den
                CSV-Import zurueck.

Nutzung:
  fahrtenbuch import track.csv
  fahrtenbuch export 2026-11 [--out Fahrtenbuch_2026-11]
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

GAP = timedelta(minutes=5)          # Standtpause ab der eine neue Fahrt beginnt
MAX_SPEED = 150.0 / 3.6             # km/h -> m/s; grober GPS-Ausreisser-Schnitt
MIN_TRIP_M = 300                    # kuerzere "Fahrt" = Rangieren/GPS-Rauschen


def db_path() -> Path:
    env = os.environ.get("FAHRTENBUCH_DB")
    if env:
        return Path(env)
    return Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "fahrtenbuch" / "fahrtenbuch.sqlite"


@dataclass
class P:
    ts: datetime
    lat: float
    lon: float


def haversine(a: P, b: P) -> float:
    la1, lo1, la2, lo2 = math.radians(a.lat), math.radians(a.lon), math.radians(b.lat), math.radians(b.lon)
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


class GpsUnavailable(Exception):
    """Keine GPS-Quelle liefert Punkte (Modem nicht oben, kein D-Bus, ...)."""


def read_track(src: Path) -> list[P]:
    pts = []
    with open(src, encoding="utf-8-sig") as f:
        for line in f.read().splitlines():
            parts = line.split(",")
            if len(parts) != 3:
                continue
            ts, lat, lon = parts
            try:
                pts.append(P(datetime.fromisoformat(ts), float(lat), float(lon)))
            except ValueError:
                continue
    pts.sort(key=lambda p: p.ts)
    return pts


def modemmanager_points() -> list[P]:
    """ModemManager-Location. Noch nicht verdrahtet (Modem-Bring-up P4)."""
    raise GpsUnavailable("ModemManager-Location ist noch nicht angebunden (P4)")


def read_points(source: str, path: Path) -> list[P]:
    if source == "track":
        return read_track(path)
    if source == "modemmanager":
        try:
            return modemmanager_points()
        except GpsUnavailable as exc:
            print(f"fahrtenbuch: gps-source=modemmanager nicht verfuegbar ({exc}); "
                  f"nehme den CSV-Import", file=sys.stderr)
            return read_track(path)
    raise ValueError(f"unbekannte gps-source {source!r}")


def split_trips(pts: list[P]) -> list[list[P]]:
    trips, cur = [], [pts[0]] if pts else []
    for prev, nxt in zip(pts, pts[1:]):
        dt = nxt.ts - prev.ts
        d = haversine(prev, nxt)
        v = d / dt.total_seconds() if dt.total_seconds() > 0 else 999.0
        if dt > GAP or v > MAX_SPEED:
            if len(cur) > 1:
                trips.append(cur)
            cur = [nxt]
        else:
            cur.append(nxt)
    if len(cur) > 1:
        trips.append(cur)
    return [t for t in trips if sum(haversine(a, b) for a, b in zip(t, t[1:])) * 1000 >= MIN_TRIP_M]


def init_db(db: sqlite3.Connection):
    db.execute("""CREATE TABLE IF NOT EXISTS trips(
        id INTEGER PRIMARY KEY,
        started_at TEXT, ended_at TEXT,
        start_lat REAL, start_lon REAL, end_lat REAL, end_lon REAL,
        km REAL, purpose TEXT DEFAULT 'privat', note TEXT DEFAULT '')""")
    db.commit()


def save_trips(db: sqlite3.Connection, trips: list[list[P]]) -> int:
    n = 0
    for t in trips:
        km = sum(haversine(a, b) for a, b in zip(t, t[1:]))
        if db.execute("SELECT 1 FROM trips WHERE started_at=?", (t[0].ts.isoformat(),)).fetchone():
            continue
        db.execute("INSERT INTO trips VALUES(NULL,?,?,?,?,?,?,?,'privat','')",
                   (t[0].ts.isoformat(), t[-1].ts.isoformat(), t[0].lat, t[0].lon,
                    t[-1].lat, t[-1].lon, round(km, 2)))
        n += 1
    db.commit()
    return n


def export(db: sqlite3.Connection, month: str, out: Path):
    rows = db.execute("""SELECT started_at, ended_at, start_lat, start_lon, end_lat, end_lon, km, purpose, note
                         FROM trips WHERE substr(started_at,1,7)=? ORDER BY started_at""", (month,)).fetchall()
    csv_p = out.with_suffix(".csv")
    with open(csv_p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Start", "Ende", "Start-Lat", "Start-Lon", "Ziel-Lat", "Ziel-Lon", "km", "Zweck", "Notiz"])
        w.writerows(rows)
    total = sum(r[6] or 0 for r in rows)
    html = ["<html><meta charset='utf-8'><body>",
            f"<h1>Fahrtenbuch {month}</h1>",
            f"<p>Gesamt: {total:.1f} km, {len(rows)} Fahrten</p><table border=1 cellpadding=4>",
            "<tr><th>Start</th><th>Ende</th><th>km</th><th>Zweck</th></tr>"]
    for r in rows:
        html.append(f"<tr><td>{r[0][:16].replace('T', ' ')}</td><td>{r[1][:16].replace('T', ' ')}</td>"
                    f"<td>{r[6]}</td><td>{r[7]}</td></tr>")
    html.append("</table></body></html>")
    out.with_suffix(".html").write_text("\n".join(html), encoding="utf-8")
    return csv_p, len(rows), total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fahrtenbuch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="Trips aus einer GPS-Quelle speichern")
    imp.add_argument("path", type=Path, help="CSV-Track (iso,lat,lon je Zeile)")
    imp.add_argument("--gps-source", choices=["track", "modemmanager"], default="track")
    exp = sub.add_parser("export", help="Monat exportieren (CSV + HTML)")
    exp.add_argument("month", help="YYYY-MM")
    exp.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)

    db_file = db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_file)
    init_db(db)
    if a.cmd == "import":
        trips = split_trips(read_points(a.gps_source, a.path))
        n = save_trips(db, trips)
        print(f"{n} neue Fahrten gespeichert ({len(trips)} erkannt)")
    else:
        out = a.out or Path(f"Fahrtenbuch_{a.month}")
        csv_p, count, total = export(db, a.month, out)
        print(f"{count} Fahrten, {total:.1f} km -> {csv_p} + {out.with_suffix('.html')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
