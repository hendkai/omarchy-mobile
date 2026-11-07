#!/usr/bin/env python3
"""carprofile-trigger: einziger udev-Einstiegspunkt fuer das car-Profil.

Aufruf (aus udev/systemd-run):   carprofile-trigger add|remove
Liest die udev-Umgebung (DEVPATH, ID_VENDOR_ID, ID_MODEL_ID, PRODUCT) aus
os.environ. Fuer Tests ohne Phone: ``--env KEY=VAL`` mehrfach angeben.

Exit-Codes: 0 = ok/ignoriert/noop, 1 = Sitzung beendet, aber Aktionen fehlerhaft,
2 = Aufrufsfehler.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional

if __package__ in (None, ""):  # direkt als Skript gestartet
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from carprofile.profile import (  # noqa: E402
    Config, default_config_path, default_state_path, handle_add, handle_remove,
)

UDEV_KEYS = ("DEVPATH", "ID_VENDOR_ID", "ID_MODEL_ID", "PRODUCT")


def _parse_env_args(pairs: List[str]) -> dict:
    env = {}
    for p in pairs:
        if "=" not in p:
            raise ValueError(f"--env erwartet KEY=VAL, bekam {p!r}")
        k, v = p.split("=", 1)
        env[k] = v
    return env


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="carprofile-trigger")
    ap.add_argument("action", choices=["add", "remove"])
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--state", type=Path, default=None)
    ap.add_argument("--env", action="append", default=[],
                    help="Simulation: udev-Variable KEY=VAL (mehrfach)")
    ap.add_argument("--json", action="store_true", help="Ergebnis als JSON auf stdout")
    args = ap.parse_args(argv)

    try:
        extra = _parse_env_args(args.env)
    except ValueError as exc:
        print(f"carprofile: {exc}", file=sys.stderr)
        return 2

    env = {k: os.environ[k] for k in UDEV_KEYS if k in os.environ}
    env.update(extra)

    cfg = Config.load(args.config or default_config_path())
    state_path = args.state or default_state_path()

    if args.action == "add":
        result = handle_add(env, state_path, cfg)
    else:
        result = handle_remove(env, state_path, cfg)

    if args.json:
        import json
        print(json.dumps(result, ensure_ascii=False))
    if result.get("outcome") == "ended-with-errors":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
