#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fahrtenbuch-Kern (phone-frei, nur Python-Standardbibliothek).

Ablauf: GPS-Trackpunkte (iso,lat,lon) -> Fahrten (Luecke > 5 min oder Sprung
> 150 km/h trennt) -> SQLite -> Zweck-Vorschlag per Regel -> Bestaetigung
(`assign --confirm` oder `set`) -> Monats-Export (CSV, HTML, PDF).

Datenschutz / Repo:
* Orte, Koordinaten und Arbeitgeber stehen NUR in der lokalen Regeldatei
  (Default ~/.fahrtenbuch/rules.json, Override $FAHRTENBUCH_RULES).
  Im Repo liegt nur rules.example.json mit Platzhaltern (radius_m 0 = trifft nie).
* Exporte landen standardmaessig in ~/.fahrtenbuch/exports (ausserhalb des Repos)
  und enthalten keine Koordinaten.
* Regel-Vorschlaege ohne Treffer bleiben Zweck "offen" - nichts wird still als
  geschaeftlich oder privat gebucht.

Live-Fahrt (start / end, vom car-Profil beim Anstecken/Abstecken gerufen):
* ``start`` merkt Zeitpunkt und (wenn verfuegbar) den ersten GNSS-Fix als
  offene Fahrt in der Tabelle ``live_trip``. ``end`` schliesst sie mit dem
  Ende-Fix ab und schreibt eine Zeile in ``trips``.
* Die GNSS-Quelle ist modemlocation.py aus omamobile-fahrtenbuch (mmcli). Ohne
  Modem oder ohne Fix bleiben Koordinaten leer, nichts wird geraten.
* Ein Live-Trip hat nur zwei Punkte, keinen Track: ``km`` bleibt leer
  ("keine Daten") und zaehlt nicht in die km-Summe. Eine Luftlinie waere keine
  gefahrene Strecke und wird deshalb nicht als km gebucht.
"""

from __future__ import annotations

import argparse
import csv
import html as htmllib
import json
import math
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional


def default_db() -> Path:
    """Ablageort ohne --db/$FAHRTENBUCH_DB: XDG-Datenverzeichnis.

    Bewusst nicht neben dem Skript: installiert liegt es unter /usr/bin (nicht
    beschreibbar). Gleicher Pfad wie omamobile-fahrtenbuch und parkreminder,
    damit alle drei dieselbe Datei sehen.
    """
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base).expanduser() / "fahrtenbuch" / "fahrtenbuch.sqlite"


GAP = timedelta(minutes=5)  # Standzeit ab der eine neue Fahrt beginnt
MAX_SPEED_KMH = 150.0  # GPS-Ausreisser-Cut
MIN_TRIP_M = 300  # kuerzer = Rangieren / GPS-Rauschen
ZWECKE = ("geschaeftlich", "privat")
OFFEN = "offen"
TRIP_COLS = (
    "id, started_at, ended_at, start_lat, start_lon, end_lat, end_lon, "
    "km, purpose, note, reviewed, soc_start, soc_end, aussen_c"
)
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
GNSS_LIB_DEFAULT = Path("/usr/lib/omamobile-fahrtenbuch")  # installiert: modemlocation.py
LIVE_FIX_TIMEOUT_S = 15.0  # carprofile gibt einer Aktion 20 s (timeout_s)
LIVE_NOTE = "keine Strecke: Live-Fahrt ohne Track"

# SoC-Mitschrift (nur lokal in der DB, nie im Export, nie im Repo).
# Quelle: omarchy-mercedes-Statuscache, read-only. Nur frische Werte (state ok,
# written_at jünger als SOC_MAX_AGE_S) werden übernommen; sonst bleibt das Feld leer.
SOC_STATUS_DEFAULT = Path.home() / ".local" / "state" / "omarchy-mercedes" / "status.json"
SOC_MAX_AGE_S = 600.0  # Cache-Eintrag älter als 10 min -> kein SoC
SOC_MATCH_WINDOW = timedelta(minutes=15)  # Probe muss so nah an Fahrtbeginn/-ende liegen
SOC_TRIP_COLS = ("soc_start", "soc_end", "aussen_c")


# ---------------------------------------------------------------------------
# Modell + Geometrie
# ---------------------------------------------------------------------------


@dataclass
class P:
    ts: datetime
    lat: float
    lon: float


@dataclass
class Trip:
    id: Optional[int]
    started_at: datetime
    ended_at: datetime
    start_lat: Optional[float]  # None = kein GNSS-Fix (Live-Fahrt)
    start_lon: Optional[float]
    end_lat: Optional[float]
    end_lon: Optional[float]
    km: Optional[float]  # None = keine Strecke erfasst, zaehlt nicht in die Summe
    purpose: Optional[str]
    note: str
    reviewed: bool
    soc_start: Optional[float] = None  # lokal, nie im Export
    soc_end: Optional[float] = None
    aussen_c: Optional[float] = None

    @classmethod
    def from_row(cls, row: tuple) -> "Trip":
        return cls(
            id=row[0],
            started_at=datetime.fromisoformat(row[1]),
            ended_at=datetime.fromisoformat(row[2]),
            start_lat=row[3],
            start_lon=row[4],
            end_lat=row[5],
            end_lon=row[6],
            km=row[7],
            purpose=row[8],
            note=row[9] or "",
            reviewed=bool(row[10]),
            soc_start=row[11],
            soc_end=row[12],
            aussen_c=row[13],
        )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def haversine(a: P, b: P) -> float:
    return haversine_km(a.lat, a.lon, b.lat, b.lon)


def path_len_m(pts: list[P]) -> float:
    return sum(haversine(a, b) for a, b in zip(pts, pts[1:])) * 1000


# ---------------------------------------------------------------------------
# Track -> Fahrten
# ---------------------------------------------------------------------------


def read_points(src: Path) -> list[P]:
    pts = []
    for line in src.read_text(encoding="utf-8").splitlines():
        parts = line.split(",")
        if len(parts) != 3:
            continue
        try:
            pts.append(P(datetime.fromisoformat(parts[0]), float(parts[1]), float(parts[2])))
        except ValueError:
            continue
    pts.sort(key=lambda p: p.ts)
    return pts


def split_trips(pts: list[P]) -> list[list[P]]:
    if len(pts) < 2:
        return []
    trips: list[list[P]] = []
    cur = [pts[0]]
    for prev, nxt in zip(pts, pts[1:]):
        dt_s = (nxt.ts - prev.ts).total_seconds()
        speed = haversine(prev, nxt) / (dt_s / 3600) if dt_s > 0 else float("inf")
        if nxt.ts - prev.ts > GAP or speed > MAX_SPEED_KMH:
            trips.append(cur)
            cur = [nxt]
        else:
            cur.append(nxt)
    trips.append(cur)
    return [t for t in trips if len(t) > 1 and path_len_m(t) >= MIN_TRIP_M]


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def init_db(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS trips(
            id INTEGER PRIMARY KEY,
            started_at TEXT, ended_at TEXT,
            start_lat REAL, start_lon REAL, end_lat REAL, end_lon REAL,
            km REAL, purpose TEXT, note TEXT DEFAULT '', reviewed INTEGER DEFAULT 0)"""
    )
    cols = {r[1] for r in db.execute("PRAGMA table_info(trips)")}
    if "reviewed" not in cols:  # Migration aus der Erstversion
        db.execute("ALTER TABLE trips ADD COLUMN reviewed INTEGER DEFAULT 0")
    for col in SOC_TRIP_COLS:  # SoC-Mitschrift: nullable, alte Zeilen bleiben NULL
        if col not in cols:
            db.execute(f"ALTER TABLE trips ADD COLUMN {col} REAL")
    db.execute(
        """CREATE TABLE IF NOT EXISTS soc_samples(
            id INTEGER PRIMARY KEY,
            sampled_at TEXT UNIQUE NOT NULL,
            soc_percent REAL NOT NULL,
            range_km REAL,
            outside_c REAL)"""
    )
    # Offene Live-Fahrt (start -> end). Hoechstens eine Zeile (id=1).
    db.execute(
        """CREATE TABLE IF NOT EXISTS live_trip(
            id INTEGER PRIMARY KEY CHECK (id = 1),
            started_at TEXT NOT NULL,
            start_lat REAL, start_lon REAL)"""
    )
    db.commit()


def save_trips(db: sqlite3.Connection, trips: list[list[P]]) -> int:
    n = 0
    for t in trips:
        if db.execute("SELECT 1 FROM trips WHERE started_at=?", (t[0].ts.isoformat(),)).fetchone():
            continue
        km = path_len_m(t) / 1000
        db.execute(
            "INSERT INTO trips(started_at, ended_at, start_lat, start_lon, end_lat, end_lon,"
            " km, purpose, note, reviewed) VALUES(?,?,?,?,?,?,?,NULL,'',0)",
            (
                t[0].ts.isoformat(),
                t[-1].ts.isoformat(),
                t[0].lat,
                t[0].lon,
                t[-1].lat,
                t[-1].lon,
                round(km, 2),
            ),
        )
        n += 1
    db.commit()
    return n


def _select_trips(db: sqlite3.Connection, month: Optional[str], unreviewed_only: bool = False) -> list[Trip]:
    where, params = [], []
    if month:
        where.append("substr(started_at,1,7)=?")
        params.append(month)
    if unreviewed_only:
        where.append("reviewed=0")
    sql = f"SELECT {TRIP_COLS} FROM trips"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY started_at"
    return [Trip.from_row(r) for r in db.execute(sql, params).fetchall()]


def set_trip(db: sqlite3.Connection, trip_id: int, purpose: Optional[str] = None, note: Optional[str] = None) -> None:
    """Manuelle Bestaetigung: setzt Zweck und/oder Notiz und markiert die Fahrt als geprueft."""
    if purpose is not None and purpose not in ZWECKE:
        raise ValueError(f"Zweck muss einer von {ZWECKE} sein, nicht {purpose!r}")
    if db.execute("SELECT 1 FROM trips WHERE id=?", (trip_id,)).fetchone() is None:
        raise LookupError(f"Fahrt {trip_id} nicht gefunden")
    sets, params = ["reviewed=1"], []
    if purpose is not None:
        sets.append("purpose=?")
        params.append(purpose)
    if note is not None:
        sets.append("note=?")
        params.append(note)
    db.execute(f"UPDATE trips SET {', '.join(sets)} WHERE id=?", (*params, trip_id))
    db.commit()


# ---------------------------------------------------------------------------
# SoC-Mitschrift (read-only Statuscache -> lokale DB)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SocReading:
    soc_percent: float
    range_km: Optional[float]
    outside_c: Optional[float]
    written_at: datetime  # lokale Naivzeit, wie die Trackzeitstempel


def _num(v: object) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def read_fresh_soc(path: Path, now: Optional[float] = None, max_age_s: float = SOC_MAX_AGE_S) -> Optional[SocReading]:
    """Liest den omarchy-mercedes-Statuscache, nur lesend.

    None bei fehlender, kaputter, nicht-ok, veralteter oder unplausibler Datei.
    Nie raten, nie 0 % annehmen. `outside_temp_c` ist optional und wird nur
    übernommen, wenn der Connector es liefert.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("state") != "ok":
        return None
    soc = _num(raw.get("soc_percent"))
    written_ms = _num(raw.get("written_at_ms"))
    if soc is None or not 0.0 <= soc <= 100.0 or written_ms is None:
        return None
    now_s = time.time() if now is None else now
    age = now_s - written_ms / 1000.0
    if age < -60 or age > max_age_s:  # Zukunft (Uhrenfehler) oder veraltet
        return None
    return SocReading(
        soc_percent=soc,
        range_km=_num(raw.get("range_km")),
        outside_c=_num(raw.get("outside_temp_c")),
        written_at=datetime.fromtimestamp(written_ms / 1000.0),
    )


def record_soc_sample(db: sqlite3.Connection, reading: SocReading, sampled_at: Optional[datetime] = None) -> bool:
    """Schreibt eine Probe. True = neu gespeichert, False = Duplikat/keine Änderung."""
    ts = (sampled_at or datetime.now()).replace(microsecond=0)
    cur = db.execute(
        "INSERT OR IGNORE INTO soc_samples(sampled_at, soc_percent, range_km, outside_c) VALUES(?,?,?,?)",
        (ts.isoformat(), reading.soc_percent, reading.range_km, reading.outside_c),
    )
    db.commit()
    return cur.rowcount == 1


def _nearest_sample(db: sqlite3.Connection, when: datetime, before: bool) -> Optional[tuple]:
    if before:
        sql = ("SELECT sampled_at, soc_percent, outside_c FROM soc_samples "
               "WHERE sampled_at <= ? ORDER BY sampled_at DESC LIMIT 1")
    else:
        sql = ("SELECT sampled_at, soc_percent, outside_c FROM soc_samples "
               "WHERE sampled_at >= ? ORDER BY sampled_at ASC LIMIT 1")
    row = db.execute(sql, (when.isoformat(),)).fetchone()
    if row is None:
        return None
    if abs(datetime.fromisoformat(row[0]) - when) > SOC_MATCH_WINDOW:
        return None
    return row


def attach_soc(db: sqlite3.Connection) -> dict:
    """Füllt soc_start/soc_end/aussen_c aus Proben im Zeitfenster.

    Überschreibt nie einen vorhandenen Wert. Ohne passende Probe bleibt NULL.
    """
    stats = {"start": 0, "end": 0, "unberuehrt": 0}
    rows = db.execute(
        "SELECT id, started_at, ended_at, soc_start, soc_end, aussen_c FROM trips"
    ).fetchall()
    for tid, started, ended, s_soc, e_soc, aussen in rows:
        sets, params = [], []
        if s_soc is None:
            probe = _nearest_sample(db, datetime.fromisoformat(started), before=True)
            if probe:
                sets.append("soc_start=?")
                params.append(probe[1])
                if aussen is None and probe[2] is not None:
                    sets.append("aussen_c=?")
                    params.append(probe[2])
                stats["start"] += 1
        if e_soc is None:
            probe = _nearest_sample(db, datetime.fromisoformat(ended), before=False)
            if probe:
                sets.append("soc_end=?")
                params.append(probe[1])
                stats["end"] += 1
        if sets:
            db.execute(f"UPDATE trips SET {', '.join(sets)} WHERE id=?", (*params, tid))
        else:
            stats["unberuehrt"] += 1
    db.commit()
    return stats


def soc_complete_count(db: sqlite3.Connection) -> int:
    """Fahrten mit Start- UND End-SoC: Grundlage für die Kalibrierungs-Sperre."""
    return db.execute("SELECT COUNT(*) FROM trips WHERE soc_start IS NOT NULL AND soc_end IS NOT NULL").fetchone()[0]


# ---------------------------------------------------------------------------
# Zweck-Regeln (lokal; Repo enthaelt nur Platzhalter)
# ---------------------------------------------------------------------------

GEO_KEYS = {"lat", "lon", "radius_m", "name"}


def _validate_geo(geo: Optional[dict], where: str) -> Optional[dict]:
    if geo is None:
        return None
    if not isinstance(geo, dict):
        raise ValueError(f"{where}: geo muss ein Objekt sein")
    unknown = set(geo) - GEO_KEYS
    if unknown or not {"lat", "lon", "radius_m"} <= set(geo):
        raise ValueError(f"{where}: braucht lat, lon, radius_m (optional name); unbekannt: {sorted(unknown)}")
    return {
        "lat": float(geo["lat"]),
        "lon": float(geo["lon"]),
        "radius_m": float(geo["radius_m"]),
        "name": str(geo.get("name", "")),
    }


@dataclass
class PurposeRule:
    """Alle gesetzten Bedingungen muessen zutreffen; erste passende Regel gewinnt.

    to_geo / from_geo: Geofence {lat, lon, radius_m, name} am Ziel bzw. Start.
    weekdays: 0=Mo .. 6=So. start_hour / end_hour: Dezimalstunden (7.5 = 07:30).
    keywords: Teilstrings in der Notiz (case-insensitive).
    """

    purpose: str
    label: str = ""
    to_geo: Optional[dict] = None
    from_geo: Optional[dict] = None
    weekdays: tuple = ()
    start_hour: Optional[float] = None
    end_hour: Optional[float] = None
    keywords: tuple = ()

    def __post_init__(self):
        self.purpose = (self.purpose or "").lower()
        if self.purpose not in ZWECKE:
            raise ValueError(f"purpose muss einer von {ZWECKE} sein, nicht {self.purpose!r}")
        self.weekdays = tuple(int(d) for d in self.weekdays)
        if any(d < 0 or d > 6 for d in self.weekdays):
            raise ValueError("weekdays: nur 0..6")
        self.keywords = tuple(str(k).lower() for k in self.keywords)
        self.to_geo = _validate_geo(self.to_geo, "to_geo")
        self.from_geo = _validate_geo(self.from_geo, "from_geo")
        if not (
            self.to_geo or self.from_geo or self.weekdays or self.keywords
            or self.start_hour is not None or self.end_hour is not None
        ):
            raise ValueError("Regel ohne Bedingung wuerde alles treffen - nicht erlaubt")


def _inside(geo: dict, lat: Optional[float], lon: Optional[float]) -> bool:
    if lat is None or lon is None:  # ohne GNSS-Fix kein Geofence-Treffer
        return False
    return haversine_km(geo["lat"], geo["lon"], lat, lon) * 1000 <= geo["radius_m"]


def rule_matches(rule: PurposeRule, trip: Trip) -> bool:
    st = trip.started_at
    if rule.weekdays and st.weekday() not in rule.weekdays:
        return False
    if rule.start_hour is not None or rule.end_hour is not None:
        h = st.hour + st.minute / 60
        if rule.start_hour is not None and h < rule.start_hour:
            return False
        if rule.end_hour is not None and h > rule.end_hour:
            return False
    if rule.to_geo and not _inside(rule.to_geo, trip.end_lat, trip.end_lon):
        return False
    if rule.from_geo and not _inside(rule.from_geo, trip.start_lat, trip.start_lon):
        return False
    if rule.keywords:
        text = trip.note.lower()
        if not any(k in text for k in rule.keywords):
            return False
    return True


@dataclass
class Suggestion:
    purpose: Optional[str]  # None = keine Regel -> offen
    label: str
    ziel: str  # Name des Ziel-Geofence, falls vorhanden


def suggest(trip: Trip, store: "PurposeStore") -> Suggestion:
    for rule in store.rules:
        if rule_matches(rule, trip):
            ziel = rule.to_geo.get("name", "") if rule.to_geo else ""
            return Suggestion(rule.purpose, rule.label or rule.purpose, ziel)
    return Suggestion(None, "keine Regel", "")


def _default_rules() -> list[PurposeRule]:
    # Bewusst ohne Zeit- oder Ortsbedingung: Eingebaut wird nur Notiz-Text ausgewertet.
    return [
        PurposeRule(purpose="geschaeftlich", keywords=("geschaeftlich", "kunde", "termin"), label="Notiz: geschaeftlich"),
        PurposeRule(purpose="privat", keywords=("privat", "einkauf", "freizeit"), label="Notiz: privat"),
    ]


def local_rules_path() -> Path:
    env = os.environ.get("FAHRTENBUCH_RULES")
    return Path(env) if env else Path.home() / ".fahrtenbuch" / "rules.json"


class PurposeStore:
    def __init__(self, rules: list[PurposeRule]):
        self.rules = rules

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "PurposeStore":
        """Benutzer-Regeln zuerst, eingebaute Notiz-Regeln danach.
        Fehlerhafte Datei -> ValueError (kein stiller Fallback)."""
        p = path or local_rules_path()
        user: list[PurposeRule] = []
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise ValueError(f"{p}: kein gueltiges JSON ({exc})") from exc
            if not isinstance(data, list):
                raise ValueError(f"{p}: Wurzel muss eine Liste von Regeln sein")
            for i, item in enumerate(data):
                try:
                    user.append(PurposeRule(**item))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{p}: Regel #{i}: {exc}") from exc
        return cls(user + _default_rules())


def confirm_purpose(prompt: str) -> bool:
    try:
        ans = input(f"{prompt} [j/N] ")
    except (EOFError, KeyboardInterrupt):
        return False
    return ans.strip().lower() in ("j", "ja", "y", "yes")


def assign_purpose(
    db: sqlite3.Connection,
    store: PurposeStore,
    month: Optional[str] = None,
    confirm: bool = False,
    ask: Callable[[str], bool] = confirm_purpose,
) -> dict:
    """Schreibt Vorschlaege fuer unbestaetigte Fahrten.

    Ohne Regel-Treffer: bleibt Zweck NULL (= offen). Mit confirm=True wird jede
    Fahrt einzeln zur Bestaetigung vorgelegt; Ablehnung schreibt nichts.
    """
    stats = {"vorgeschlagen": 0, "bestaetigt": 0, "verworfen": 0, "offen": 0}
    for t in _select_trips(db, month, unreviewed_only=True):
        s = suggest(t, store)
        if s.purpose is None:
            stats["offen"] += 1
            continue
        if confirm:
            prompt = (
                f"{t.started_at:%d.%m.%Y %H:%M}-{t.ended_at:%H:%M}, {t.km:.1f} km "
                f"-> {s.purpose} ({s.label}){' ' + s.ziel if s.ziel else ''}?"
            )
            if not ask(prompt):
                stats["verworfen"] += 1
                continue
            db.execute(
                "UPDATE trips SET purpose=?, note=CASE WHEN note='' THEN ? ELSE note END, reviewed=1 WHERE id=?",
                (s.purpose, s.ziel, t.id),
            )
            stats["bestaetigt"] += 1
        else:
            db.execute(
                "UPDATE trips SET purpose=?, note=CASE WHEN note='' THEN ? ELSE note END WHERE id=?",
                (s.purpose, s.ziel, t.id),
            )
            stats["vorgeschlagen"] += 1
    db.commit()
    return stats


# ---------------------------------------------------------------------------
# Export (Finanzamt-tauglich: Datum, Zeit, km, Zweck, Ziel/Notiz)
# ---------------------------------------------------------------------------


def _de(x: Optional[float]) -> str:
    if x is None:
        return "keine Daten"
    return f"{x:.2f}".replace(".", ",")


def _status(t: Trip) -> str:
    if t.purpose is None:
        return OFFEN
    return "bestaetigt" if t.reviewed else "Vorschlag"


def _purpose_label(t: Trip) -> str:
    return t.purpose or OFFEN


def _check_month(month: str) -> None:
    if not MONTH_RE.match(month):
        raise ValueError(f"Monat muss YYYY-MM sein, nicht {month!r}")


def _summary(trips: list[Trip]) -> tuple[dict, float, int]:
    per: dict = {}
    for t in trips:
        km, n = per.get(_purpose_label(t), (0.0, 0))
        per[_purpose_label(t)] = (km + (t.km or 0.0), n + 1)  # ohne Strecke: nicht in km
    total = sum(t.km or 0.0 for t in trips)
    open_count = sum(1 for t in trips if t.purpose is None)
    return per, total, open_count


def _no_km_count(trips: list[Trip]) -> int:
    return sum(1 for t in trips if t.km is None)


def export_dir_default() -> Path:
    env = os.environ.get("FAHRTENBUCH_EXPORT")
    return Path(env) if env else Path.home() / ".fahrtenbuch" / "exports"


def export_finance(db: sqlite3.Connection, month: str, out_dir: Path) -> list[Path]:
    _check_month(month)
    trips = _select_trips(db, month)
    out_dir.mkdir(parents=True, exist_ok=True)
    detail = out_dir / f"Fahrtenbuch_{month}_detail.csv"
    summary = out_dir / f"Fahrtenbuch_{month}_summary.csv"
    with open(detail, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Datum", "Beginn", "Ende", "km", "Zweck", "Ziel/Notiz", "Status"])
        for t in trips:
            w.writerow([
                f"{t.started_at:%d.%m.%Y}",
                f"{t.started_at:%H:%M}",
                f"{t.ended_at:%H:%M}",
                _de(t.km),
                _purpose_label(t),
                t.note,
                _status(t),
            ])
    per, total, open_count = _summary(trips)
    with open(summary, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Zweck", "Anzahl", "km"])
        for label in sorted(per):
            km, n = per[label]
            w.writerow([label, n, _de(km)])
        w.writerow(["GESAMT", len(trips), _de(total)])
        if open_count:
            w.writerow([f"WARNUNG: {open_count} Fahrt(en) ohne Zweck - vor Abgabe zuordnen"])
        no_km = _no_km_count(trips)
        if no_km:
            w.writerow([f"HINWEIS: {no_km} Fahrt(en) ohne Strecke (nur Start/Ziel) - nicht in der km-Summe"])
    return [detail, summary]


OMARCHY_CSS = """
:root{--crust:#11111b;--mantle:#181825;--base:#1e1e2e;--surface0:#313244;--surface1:#45475a;
--text:#cdd6f4;--subtext:#a6adc8;--overlay:#6c7086;--mauve:#cba6f7;--blue:#89b4fa;
--green:#a6e3a1;--yellow:#f9e2af;--red:#f38ba8;}
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a12;color:var(--text);font-family:'JetBrainsMono Nerd Font','JetBrains Mono',ui-monospace,Menlo,monospace;padding:40px;font-size:16px}
.frame{background:var(--base);border:2px solid var(--surface1);border-radius:14px;overflow:hidden;max-width:1100px;margin:0 auto}
.titlebar{background:var(--mantle);padding:14px 22px;border-bottom:1px solid var(--surface0);display:flex;justify-content:space-between;align-items:center}
.titlebar .t{font-weight:700;font-size:18px;display:flex;gap:12px;align-items:center}
.dot{width:12px;height:12px;border-radius:50%;background:var(--mauve);display:inline-block}
.body{padding:22px 26px;display:flex;flex-direction:column;gap:18px}
.card{background:var(--mantle);border:1.5px solid var(--surface0);border-radius:12px;padding:16px 20px}
.card h4{color:var(--subtext);font-size:13px;letter-spacing:1px;text-transform:uppercase;margin-bottom:10px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:7px 8px;border-bottom:1px solid var(--surface0);text-align:left}
th{color:var(--subtext);font-weight:600}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.tag-b{color:var(--blue)} .tag-p{color:var(--green)} .tag-o{color:var(--red);font-weight:700}
.warn{color:var(--yellow)} .muted{color:var(--overlay);font-size:12px}
@media print{
 @page{size:A4;margin:14mm}
 body{background:#fff;color:#000;padding:0;font-size:11pt}
 .frame{border:none;border-radius:0;background:#fff;max-width:none}
 .titlebar{background:#fff;border-bottom:2px solid #000;padding:0 0 6px}
 .card{background:#fff;border:1px solid #999;border-radius:0}
 .card h4,th{color:#000}
 th,td{border-bottom:1px solid #bbb;padding:4px 6px}
 .tag-b,.tag-p,.tag-o,.warn,.muted{color:#000}
 .noprint{display:none}
 tr{page-break-inside:avoid}
}
"""


def _html_doc(month: str, trips: list[Trip]) -> str:
    esc = htmllib.escape
    per, total, open_count = _summary(trips)
    rows = []
    for t in trips:
        cls = {"geschaeftlich": "tag-b", "privat": "tag-p"}.get(t.purpose or "", "tag-o")
        rows.append(
            "<tr>"
            f"<td>{t.started_at:%d.%m.%Y}</td>"
            f"<td>{t.started_at:%H:%M}&ndash;{t.ended_at:%H:%M}</td>"
            f"<td class=\"num\">{_de(t.km)}</td>"
            f"<td class=\"{cls}\">{esc(_purpose_label(t))}</td>"
            f"<td>{esc(t.note)}</td>"
            f"<td>{esc(_status(t))}</td>"
            "</tr>"
        )
    sums = "".join(
        f"<tr><td>{esc(label)}</td><td class=\"num\">{n}</td><td class=\"num\">{_de(km)} km</td></tr>"
        for label, (km, n) in sorted(per.items())
    )
    warn = (
        f"<p class=\"warn\">WARNUNG: {open_count} Fahrt(en) ohne Zweck - vor Abgabe zuordnen.</p>"
        if open_count else ""
    )
    if _no_km_count(trips):
        warn += (
            f"<p class=\"muted\">HINWEIS: {_no_km_count(trips)} Fahrt(en) ohne Strecke "
            "(nur Start/Ziel) - nicht in der km-Summe.</p>"
        )
    empty = '<tr><td colspan="6">keine Fahrten im Monat</td></tr>'
    return f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<title>Fahrtenbuch {esc(month)}</title>
<style>{OMARCHY_CSS}</style></head>
<body><div class="frame">
<div class="titlebar"><div class="t"><span class="dot"></span>Fahrtenbuch {esc(month)}</div>
<span class="muted noprint">Druck: Strg/Cmd+P &rarr; PDF</span></div>
<div class="body">
<div class="card"><h4>Zusammenfassung</h4>
<table><tr><th>Zweck</th><th class="num">Fahrten</th><th class="num">km</th></tr>
{sums}
<tr><th>GESAMT</th><th class="num">{len(trips)}</th><th class="num">{_de(total)} km</th></tr></table>
{warn}</div>
<div class="card"><h4>Fahrten</h4>
<table><tr><th>Datum</th><th>Zeit</th><th class="num">km</th><th>Zweck</th><th>Ziel/Notiz</th><th>Status</th></tr>
{''.join(rows) if rows else empty}
</table></div>
<p class="muted">Erstellt {datetime.now():%d.%m.%Y %H:%M} &middot; Keine Koordinaten im Export.</p>
</div></div></body></html>
"""


def export_html(db: sqlite3.Connection, month: str, out_dir: Path) -> Path:
    _check_month(month)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"Fahrtenbuch_{month}.html"
    out.write_text(_html_doc(month, _select_trips(db, month)), encoding="utf-8")
    return out


# --- PDF (stdlib, korrekte xref-Offsets) -----------------------------------

PAGE_TOP = 800
PAGE_BOTTOM = 60


def _pdf_str(s: str) -> bytes:
    b = s.encode("cp1252", errors="replace")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _paginate(items: list[tuple[str, float, str]]) -> list[list[tuple[str, float, str, float]]]:
    pages: list[list[tuple[str, float, str, float]]] = [[]]
    y = PAGE_TOP
    for font, size, text in items:
        lh = size * 1.45
        if y - lh < PAGE_BOTTOM:
            pages.append([])
            y = PAGE_TOP
        pages[-1].append((font, size, text, y))
        y -= lh
    return pages


def _page_stream(lines: list[tuple[str, float, str, float]], page_no: int, n_pages: int) -> bytes:
    parts = [b"BT"]
    for font, size, text, y in lines:
        parts.append(f"/{font} {size} Tf 1 0 0 1 50 {y:.1f} Tm ".encode() + b"(" + _pdf_str(text) + b") Tj")
    footer = f"Seite {page_no}/{n_pages} - Fahrtenbuch"
    parts.append(f"/F1 8 Tf 1 0 0 1 50 30 Tm ({footer}) Tj".encode("ascii"))
    parts.append(b"ET")
    return b"\n".join(parts)


def _pdf_bytes(streams: list[bytes]) -> bytes:
    n = len(streams)
    page_ids = [5 + 2 * i for i in range(n)]
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(f"{p} 0 R" for p in page_ids), n)).encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
    ]
    for i, stream in enumerate(streams):
        cid = page_ids[i] + 1
        objs.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                "/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents %d 0 R >>" % cid
            ).encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)


def _pdf_row(t: Trip) -> str:
    return (
        f"{t.started_at:%d.%m.%Y} {t.started_at:%H:%M}-{t.ended_at:%H:%M} "
        f"{_de(t.km):>7} {_purpose_label(t)[:15]:<15} {_status(t)[:11]:<11} {t.note[:30]}"
    )


def export_pdf(db: sqlite3.Connection, month: str, out_dir: Path) -> Path:
    _check_month(month)
    trips = _select_trips(db, month)
    per, total, open_count = _summary(trips)
    items: list[tuple[str, float, str]] = [
        ("F1", 14, f"Fahrtenbuch {month}"),
        ("F1", 9, f"Erstellt {datetime.now():%d.%m.%Y %H:%M} - Gesamt {_de(total)} km, {len(trips)} Fahrten"),
        ("F1", 9, "Zusammenfassung nach Zweck:"),
    ]
    for label in sorted(per):
        km, n = per[label]
        items.append(("F2", 9, f"  {label:<16} {n:>4} Fahrten  {_de(km):>10} km"))
    if open_count:
        items.append(("F1", 9, f"WARNUNG: {open_count} Fahrt(en) ohne Zweck - vor Abgabe zuordnen."))
    if _no_km_count(trips):
        items.append(("F1", 9, f"HINWEIS: {_no_km_count(trips)} Fahrt(en) ohne Strecke (nur Start/Ziel)."))
    items.append(("F1", 9, ""))
    items.append(("F2", 8.5, f"{'Datum':10} {'Zeit':11} {'km':>7} {'Zweck':<15} {'Status':<11} Ziel/Notiz"))
    for t in trips:
        items.append(("F2", 8.5, _pdf_row(t)))
    if not trips:
        items.append(("F1", 9, "keine Fahrten im Monat"))
    pages = _paginate(items)
    streams = [_page_stream(p, i + 1, len(pages)) for i, p in enumerate(pages)]
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"Fahrtenbuch_{month}.pdf"
    out.write_bytes(_pdf_bytes(streams))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _db_path(args) -> Path:
    if getattr(args, "db", None):
        return Path(args.db)
    env = os.environ.get("FAHRTENBUCH_DB")
    return Path(env) if env else default_db()


def _open(args) -> sqlite3.Connection:
    db_file = _db_path(args)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_file)
    init_db(db)
    return db


# ---------------------------------------------------------------------------
# Live-Fahrt (car-Profil: start beim Anstecken, end beim Abstecken)
# ---------------------------------------------------------------------------


def gnss_first_fix() -> Optional[P]:
    """Erster GNSS-Fix vom Modem (modemlocation.py aus omamobile-fahrtenbuch).

    None, wenn das Modul fehlt, kein Modem oben ist oder kein Fix kommt.
    Lib-Pfad: $FAHRTENBUCH_GNSS_LIB oder /usr/lib/omamobile-fahrtenbuch.
    Nie ein erfundener Wert.
    """
    lib = Path(os.environ.get("FAHRTENBUCH_GNSS_LIB") or GNSS_LIB_DEFAULT)
    if not (lib / "modemlocation.py").exists():
        return None
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    try:
        import modemlocation  # pyright: ignore[reportMissingImports]  # Pfad oben gesetzt
    except ImportError:
        return None
    try:
        fixes = modemlocation.collect_fixes(window_s=0.0, timeout_s=LIVE_FIX_TIMEOUT_S)
    except modemlocation.GpsUnavailable:
        return None
    if not fixes:
        return None
    return P(fixes[0].ts, fixes[0].lat, fixes[0].lon)


def live_start(db: sqlite3.Connection, now: datetime, fix: Optional[P]) -> Optional[str]:
    """Oeffnet die Live-Fahrt. Gibt den Beginn einer verworfenen offenen Fahrt zurueck.

    carprofile ruft start nur bei einer neuen Sitzung; eine noch offene Marke
    bedeutet also, dass das end nie kam (z.B. Neustart mitten in der Fahrt).
    """
    row = db.execute("SELECT started_at FROM live_trip WHERE id=1").fetchone()
    stale = row[0] if row else None
    at = fix.ts if fix else now
    db.execute("DELETE FROM live_trip WHERE id=1")
    db.execute(
        "INSERT INTO live_trip(id, started_at, start_lat, start_lon) VALUES(1,?,?,?)",
        (at.isoformat(), fix.lat if fix else None, fix.lon if fix else None),
    )
    db.commit()
    return stale


def live_end(db: sqlite3.Connection, now: datetime, fix: Optional[P]) -> Optional[int]:
    """Schliesst die offene Fahrt als Trip. None, wenn keine Fahrt offen ist.

    Ohne Track gibt es nur zwei Punkte: km bleibt leer (keine Luftlinie als
    gefahrene Strecke), die Notiz sagt das.
    """
    row = db.execute("SELECT started_at, start_lat, start_lon FROM live_trip WHERE id=1").fetchone()
    if row is None:
        return None
    started = datetime.fromisoformat(row[0])
    ended = max(fix.ts if fix else now, started)
    cur = db.execute(
        "INSERT INTO trips(started_at, ended_at, start_lat, start_lon, end_lat, end_lon,"
        " km, purpose, note, reviewed) VALUES(?,?,?,?,?,?,NULL,NULL,?,0)",
        (
            row[0],
            ended.isoformat(),
            row[1],
            row[2],
            fix.lat if fix else None,
            fix.lon if fix else None,
            LIVE_NOTE,
        ),
    )
    db.execute("DELETE FROM live_trip WHERE id=1")
    db.commit()
    return cur.lastrowid


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Fahrtenbuch: Zweck-Zuordnung + Finanzamt-Export.")
    ap.add_argument("--db", help="SQLite-Datei (Default: $FAHRTENBUCH_DB, sonst $XDG_DATA_HOME/fahrtenbuch/fahrtenbuch.sqlite)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import", help="GPS-Track (iso,lat,lon) einlesen")
    p.add_argument("path")
    p.set_defaults(func=_cmd_import)

    p = sub.add_parser("assign", help="Zweck-Vorschlaege per Regel schreiben")
    p.add_argument("--month", help="YYYY-MM")
    p.add_argument("--confirm", action="store_true", help="jede Fahrt einzeln bestaetigen")
    p.add_argument("--rules", help="Regeldatei (Default ~/.fahrtenbuch/rules.json oder $FAHRTENBUCH_RULES)")
    p.set_defaults(func=_cmd_assign)

    p = sub.add_parser("set", help="Fahrt manuell bestaetigen (Zweck und/oder Notiz)")
    p.add_argument("id", type=int)
    p.add_argument("--purpose", choices=ZWECKE)
    p.add_argument("--note")
    p.set_defaults(func=_cmd_set)

    for name, fn, hlp in (
        ("start", _cmd_start, "Live-Fahrt beginnen (car-Profil: Anstecken)"),
        ("end", _cmd_end, "Live-Fahrt beenden (car-Profil: Abstecken)"),
    ):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("--gps-source", choices=["modemmanager", "none"], default="modemmanager")
        p.set_defaults(func=fn)

    p = sub.add_parser("list", help="Fahrten anzeigen")
    p.add_argument("--month")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("soc-sample", help="frischen SoC aus dem Statuscache lesend in die DB schreiben")
    p.add_argument("--cache", help=f"Statuscache (Default {SOC_STATUS_DEFAULT})")
    p.set_defaults(func=_cmd_soc_sample)

    p = sub.add_parser("export", help="Monatsexport (Standard: ~/.fahrtenbuch/exports)")
    p.add_argument("month", help="YYYY-MM")
    p.add_argument("--format", choices=["csv", "html", "pdf", "all"], default="all")
    p.add_argument("--out", help="Ausgabeverzeichnis")
    p.set_defaults(func=_cmd_export)
    return ap


def _cmd_import(args) -> int:
    db = _open(args)
    trips = split_trips(read_points(Path(args.path)))
    n = save_trips(db, trips)
    soc = attach_soc(db)
    print(f"{n} neue Fahrten gespeichert ({len(trips)} erkannt)")
    print(f"SoC zugeordnet: Start {soc['start']}, Ende {soc['end']}")
    return 0


def _cmd_soc_sample(args) -> int:
    db = _open(args)
    cache = Path(args.cache).expanduser() if args.cache else SOC_STATUS_DEFAULT
    reading = read_fresh_soc(cache)
    if reading is None:
        print("kein frischer SoC (Cache fehlt, veraltet, nicht ok oder ohne Wert) - nichts gespeichert")
        return 0
    neu = record_soc_sample(db, reading)
    print(f"SoC {reading.soc_percent:.0f} % {'gespeichert' if neu else 'bereits vorhanden'}")
    return 0


def _cmd_assign(args) -> int:
    db = _open(args)
    store = PurposeStore.load(Path(args.rules) if args.rules else None)
    stats = assign_purpose(db, store, month=args.month, confirm=args.confirm)
    print(
        f"vorgeschlagen {stats['vorgeschlagen']}, bestaetigt {stats['bestaetigt']}, "
        f"verworfen {stats['verworfen']}, offen (keine Regel) {stats['offen']}"
    )
    return 0


def _cmd_set(args) -> int:
    db = _open(args)
    set_trip(db, args.id, purpose=args.purpose, note=args.note)
    print(f"Fahrt {args.id} aktualisiert")
    return 0


def _gps_fix(args) -> Optional[P]:
    return None if args.gps_source == "none" else gnss_first_fix()


def _cmd_start(args) -> int:
    db = _open(args)
    fix = _gps_fix(args)
    stale = live_start(db, datetime.now(), fix)
    if stale:
        print(f"vorherige offene Fahrt ab {stale} verworfen (kein end gesehen)")
    print("Fahrt gestartet" + (" (GPS-Startpunkt)" if fix else " (ohne GPS-Fix: keine Koordinaten)"))
    return 0


def _cmd_end(args) -> int:
    db = _open(args)
    fix = _gps_fix(args)
    trip_id = live_end(db, datetime.now(), fix)
    if trip_id is None:
        print("keine laufende Fahrt - nichts zu beenden")
        return 0
    attach_soc(db)
    print(
        f"Fahrt {trip_id} gespeichert" + (" (GPS-Endpunkt)" if fix else " (ohne GPS-Fix)")
        + ", Strecke keine Daten, Zweck offen"
    )
    return 0


def _cmd_list(args) -> int:
    db = _open(args)
    for t in _select_trips(db, args.month):
        soc = ""
        if t.soc_start is not None or t.soc_end is not None:
            s = "?" if t.soc_start is None else f"{t.soc_start:.0f}"
            e = "?" if t.soc_end is None else f"{t.soc_end:.0f}"
            soc = f"  SoC {s}->{e} %"
        print(
            f"{t.id:>4}  {t.started_at:%d.%m.%Y %H:%M}-{t.ended_at:%H:%M}  "
            f"{_de(t.km):>7} km  {_purpose_label(t):<14} {_status(t):<11} {t.note}{soc}"
        )
    return 0


def _cmd_export(args) -> int:
    db = _open(args)
    out_dir = Path(args.out) if args.out else export_dir_default()
    written: list[Path] = []
    if args.format in ("csv", "all"):
        written += export_finance(db, args.month, out_dir)
    if args.format in ("html", "all"):
        written.append(export_html(db, args.month, out_dir))
    if args.format in ("pdf", "all"):
        written.append(export_pdf(db, args.month, out_dir))
    for p in written:
        print(p)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_argparser().parse_args(argv)
    try:
        return args.func(args)
    except LookupError as exc:
        print(f"fehler: {exc}", file=sys.stderr)
        return 3
    except (ValueError, OSError) as exc:
        print(f"fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
