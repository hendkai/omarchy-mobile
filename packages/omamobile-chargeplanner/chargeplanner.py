#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""EV-Ladeplaner (Omarchy Mobile: Paket omamobile-chargeplanner).

Datenquelle: Bundesnetzagentur-Ladesaeulenregister (CSV, ~50 MB). Das CSV ist
NICHT im Paket: es wird beim ersten Lauf bzw. per Timer (monatlich) aus
REGISTER_URL geladen. Kein API-Key, offline nutzbar, sobald es da ist.

Bewertet die Erreichbarkeit nach SoC und sortiert nach Nutzen (Leistung,
Distanz, Fahrtrichtung, CCS). SoC-Quelle: --soc (manuell). Die
Mercedes-Anbindung (omarchy-mercedes, read-only) ist eine Folgekarte.

Konfiguration (KEY=VALUE, keine Shell):
  /etc/chargeplanner.conf            (Paket-Default, Vorlage)
  ~/.config/chargeplanner.conf       (Override)
  REGISTER_URL=<CSV-Link der BNetzA>
  Umgebung: CHARGEPLANNER_CONFIG, CHARGEPLANNER_DATA_DIR, CHARGEPLANNER_REGISTER_URL

Datenort (erste Treffer): CHARGEPLANNER_DATA_DIR; /var/lib/chargeplanner
(wenn dort das Register liegt); ~/.local/share/chargeplanner.

EQB 300 4MATIC: 66,5 kWh netto, ~423 km WLTP, CCS bis 100 kW.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

WLTP_KM = 423.0
MAX_AGE_DAYS = 25  # der monatliche Timer holt das Register vor Ablauf neu
REGISTER_NAME = "ladesaeulenregister.csv"
SYSTEM_DATA_DIR = Path("/var/lib/chargeplanner")
CORRIDOR_DEFAULT_KM = 30.0


def read_config() -> dict:
    """KEY=VALUE-Dateien lesen (nie als Shell ausfuehren)."""
    env_path = os.environ.get("CHARGEPLANNER_CONFIG")
    paths = [Path(env_path)] if env_path else [
        Path("/etc/chargeplanner.conf"),
        Path("~/.config/chargeplanner.conf").expanduser(),
    ]
    cfg: dict = {}
    for p in paths:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            cfg[k.strip()] = v.strip().strip('"')
    return cfg


def data_dir() -> Path:
    env = os.environ.get("CHARGEPLANNER_DATA_DIR")
    if env:
        return Path(env)
    if (SYSTEM_DATA_DIR / REGISTER_NAME).exists():
        return SYSTEM_DATA_DIR
    return Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "chargeplanner"


def register_path() -> Path:
    return data_dir() / REGISTER_NAME


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def bearing(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def angle_diff(d1: float, d2: float) -> float:
    d = abs(d1 - d2) % 360
    return d if d <= 180 else 360 - d


def de_num(s: str) -> float:
    return float(s.replace(",", ".").strip() or 0)


def load_stations(register: Path) -> list[dict]:
    """Ladesaeulenregister parsen: nur in Betrieb, mit Koordinaten."""
    out = []
    with open(register, encoding="utf-8-sig", newline="") as f:
        header = None
        for row in csv.reader(f, delimiter=";"):
            if not header:
                if row and row[0].strip() == "Ladeeinrichtungs-ID":
                    header = row
                continue
            try:
                if len(row) < 28 or row[3].strip() != "In Betrieb":
                    continue
                lat, lon = de_num(row[15]), de_num(row[16])
                if not lat or not lon:
                    continue
                kw = de_num(row[6])
                connectors = []
                for i in range(24, min(len(row), 24 + 6 * 3), 3):
                    if i < len(row) and row[i].strip():
                        connectors.append(row[i].strip())
                        try:
                            kw = max(kw, de_num(row[i + 1]))
                        except (ValueError, IndexError):
                            pass
                ccs = any(("ccs" in c.lower() or "combo" in c.lower() or "css" in c.lower())
                          for c in connectors)
                out.append({
                    "id": row[0], "name": row[2] or row[1], "operator": row[1],
                    "lat": lat, "lon": lon, "kw": kw, "ccs": ccs,
                    "ort": f"{row[11]} {row[12]}".strip(),
                    "art": row[4], "anzahl_lp": row[5],
                })
            except (ValueError, IndexError):
                continue
    return out


def reachable_km(soc_pct: float, reserve_pct: float = 12.0) -> float:
    usable = max(0.0, soc_pct - reserve_pct)
    # konservative Autobahn-Winterreichweite: ~70 % von WLTP
    return WLTP_KM * (usable / 100.0) * 0.70


def plan(register: Path, center, soc, heading=None, radius_km=CORRIDOR_DEFAULT_KM, limit=10):
    sts = load_stations(register)
    reach = reachable_km(soc)
    scored = []
    for s in sts:
        d = haversine(center, (s["lat"], s["lon"]))
        if d > radius_km or d > reach:
            continue
        dev = angle_diff(bearing(center, (s["lat"], s["lon"])), heading) if heading is not None and d > 2 else 0
        if dev > 35:  # nicht gegen die Fahrtrichtung
            continue
        # Score: schnell + nah + in Fahrtrichtung + CCS bevorzugt
        score = (min(s["kw"], 100) / 100.0) * 40 + max(0.0, 1 - d / 50.0) * 30 \
            + (1 - dev / 35.0) * 10 + (20 if s["ccs"] else 0)
        detour_penalty = d * 0.3  # Umweg kostet
        scored.append({**s, "dist_km": round(d, 1), "score": round(score - detour_penalty, 1)})
    scored.sort(key=lambda x: -x["score"])
    return {"soc_pct": soc, "reachable_km": round(reach, 0),
            "stations_total": len(sts), "stations": scored[:limit]}


def ensure_register(register: Path, url: str, max_age_days: float = MAX_AGE_DAYS) -> Path:
    """Register laden, wenn es fehlt oder aelter als max_age_days ist.

    Ohne URL und ohne Datei: Abbruch mit Hinweis. Schlaegt der Download fehl,
    bleibt eine vorhandene Datei in Gebrauch (mit Warnung).
    """
    if register.exists():
        age_days = (time.time() - register.stat().st_mtime) / 86400
        if age_days < max_age_days:
            return register
    if not url:
        if register.exists():
            print("chargeplanner: REGISTER_URL fehlt, altes Register wird benutzt", file=sys.stderr)
            return register
        raise SystemExit("chargeplanner: kein Ladesaeulenregister und keine REGISTER_URL "
                         "(in /etc/chargeplanner.conf eintragen)")
    register.parent.mkdir(parents=True, exist_ok=True)
    tmp = register.with_name(register.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=180) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(register)
        print(f"chargeplanner: Register geladen ({register.stat().st_size} Bytes)", flush=True)
    except Exception as exc:  # Netz optional: Verfuegbarkeit ist kein Grund fuer Absturz
        tmp.unlink(missing_ok=True)
        if register.exists():
            print(f"chargeplanner: Download fehlgeschlagen ({exc}), altes Register wird benutzt",
                  file=sys.stderr)
            return register
        raise SystemExit(f"chargeplanner: Download fehlgeschlagen: {exc}")
    return register


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="chargeplanner")
    p.add_argument("lat", type=float, nargs="?")
    p.add_argument("lon", type=float, nargs="?")
    p.add_argument("--soc", type=float, help="Ladestand in Prozent")
    p.add_argument("--heading", type=float, default=None, help="Fahrtrichtung 0-360")
    p.add_argument("--radius", type=float, default=CORRIDOR_DEFAULT_KM)
    p.add_argument("--refresh-register", action="store_true",
                   help="Register laden, falls aelter als 25 Tage (fuer den Timer)")
    a = p.parse_args(argv)

    cfg = read_config()
    url = os.environ.get("CHARGEPLANNER_REGISTER_URL") or cfg.get("REGISTER_URL", "")
    reg = register_path()

    if a.refresh_register:
        ensure_register(reg, url)
        print(f"chargeplanner: Register {reg}")
        return 0
    if a.lat is None or a.lon is None or a.soc is None:
        p.error("lat lon --soc sind noetig")
    ensure_register(reg, url)
    res = plan(reg, (a.lat, a.lon), a.soc, a.heading, a.radius)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
