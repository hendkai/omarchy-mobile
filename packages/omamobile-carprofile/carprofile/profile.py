"""car-Profil: Zustand, Aktionen und Dispatch (Anstecken -> car, Abstecken -> zurueck).

Designregeln:
  * Dry-run ist der Default (``apply: false``). Echte Befehle laufen nur, wenn
    die Konfiguration ``apply: true`` setzt UND der Befehl konfiguriert ist.
    Kein Phone-I/O ohne Hendriks OK.
  * Eine Aktion ohne konfigurierten Befehl wird uebersprungen und geloggt; es
    werden keine Geraetebefehle erfunden.
  * Abstecken beendet nur eine Sitzung, die ein verifiziertes Anstecken
    (AOA) gestartet hat (Zustandsdatei). Ein beliebiges USB-Remove loest
    nichts aus -> keine Fehlalarme.
  * Reihenfolge beim Abstecken: park-reminder (braucht noch laufenden Trip
    fuer den letzten GPS-Fix) -> fahrtenbuch-end -> profile-revert.
  * park-reminder ist die geteilte Stelle mit der Park-Erinnerung
    (t_9eabf68f, DESIGN.md Punkt 3): es gibt genau EINEN udev-Trigger und
    EINE Disconnect-Kette; parkreminder wird ueber ``commands`` aufgerufen.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from . import notify
from .detect import DetectConfig, UsbId, classify, parse_extra_ids, usb_id_from_env

CONNECT_DEFAULT = ["display-minimal", "chargeplanner", "fahrtenbuch-start", "navi-aa"]
DISCONNECT_DEFAULT = ["park-reminder", "fahrtenbuch-end", "profile-revert"]
PARK_COMMAND_DEFAULT = "/usr/local/bin/parkreminder trigger"


@dataclass
class Config:
    apply: bool = False
    # Sichtbare Hinweise (notify-send) bei Profil an/aus. Unabhaengig von
    # ``apply``: ein Hinweis steuert kein Fahrzeug und kein Geraet.
    notify: bool = True
    extra_car_ids: List[str] = field(default_factory=list)
    connect: List[str] = field(default_factory=lambda: list(CONNECT_DEFAULT))
    disconnect: List[str] = field(default_factory=lambda: list(DISCONNECT_DEFAULT))
    commands: Dict[str, str] = field(default_factory=lambda: {"park-reminder": PARK_COMMAND_DEFAULT})
    timeout_s: int = 20

    @staticmethod
    def load(path: Optional[Path]) -> "Config":
        cfg = Config()
        if path is None or not path.exists():
            return cfg
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("apply", "notify", "extra_car_ids", "connect", "disconnect", "commands", "timeout_s"):
            if key in data:
                setattr(cfg, key, data[key])
        return cfg

    def detect_config(self) -> DetectConfig:
        return DetectConfig(extra_car_ids=parse_extra_ids(self.extra_car_ids))


def current_boot_id() -> str:
    """Boot-Id des Kernels (Linux). Leer auf Systemen ohne /proc (z.B. macOS-Tests)."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


@dataclass
class State:
    active: bool = False
    devpath: Optional[str] = None
    usb_id: Optional[str] = None
    since: Optional[str] = None
    reason: Optional[str] = None
    boot_id: Optional[str] = None

    @staticmethod
    def load(path: Path) -> "State":
        if not path.exists():
            return State()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Beschaedigter Zustand: sicher inaktiv, nichts erzwingen.
            return State()
        st = State(**{k: data.get(k) for k in ("active", "devpath", "usb_id", "since",
                                               "reason", "boot_id") if k in data})
        # Sitzung aus einem frueheren Boot (Phone neu gestartet, Abstecken nie
        # gesehen) gilt als beendet, sonst wuerde das naechste Anstecken blockiert.
        if st.active and (st.boot_id or "") != current_boot_id():
            return State()
        return st

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")
        os.replace(tmp, path)


Logger = Callable[[str], None]


def _stderr(msg: str) -> None:
    print(f"carprofile: {msg}", file=sys.stderr)


@dataclass
class ActionResult:
    name: str
    status: str  # "ran" | "dry-run" | "skipped" | "failed"
    detail: str = ""


def run_action(name: str, cfg: Config, log: Logger = _stderr) -> ActionResult:
    """Fuehrt eine benannte Aktion aus (oder simuliert sie)."""
    cmd = cfg.commands.get(name)
    if not cmd:
        log(f"aktion {name}: kein Befehl konfiguriert -> uebersprungen")
        return ActionResult(name, "skipped", "kein Befehl konfiguriert")
    argv = shlex.split(cmd)
    if not argv:
        return ActionResult(name, "skipped", "leerer Befehl")
    if not cfg.apply:
        log(f"aktion {name}: dry-run, wuerde ausfuehren: {argv[0]} ...")
        return ActionResult(name, "dry-run", argv[0])
    if shutil.which(argv[0]) is None and not Path(argv[0]).exists():
        log(f"aktion {name}: Programm fehlt ({argv[0]}) -> uebersprungen")
        return ActionResult(name, "skipped", f"{argv[0]} nicht gefunden")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=cfg.timeout_s)
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"aktion {name}: Fehler {exc.__class__.__name__}")
        return ActionResult(name, "failed", exc.__class__.__name__)
    if proc.returncode != 0:
        log(f"aktion {name}: rc={proc.returncode}")
        return ActionResult(name, "failed", f"rc={proc.returncode}")
    log(f"aktion {name}: ok")
    return ActionResult(name, "ran")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def handle_add(env: dict, state_path: Path, cfg: Config, log: Logger = _stderr) -> dict:
    """Anstecken: bei verifiziertem AOA-Kfz-Anschluss das car-Profil starten."""
    uid = usb_id_from_env(env)
    verdict = classify(uid, cfg.detect_config())
    if not verdict.is_car:
        log(f"add ignoriert ({verdict.reason})")
        return {"event": "add", "outcome": "ignored", "reason": verdict.reason}

    state = State.load(state_path)
    devpath = env.get("DEVPATH")
    if state.active:
        # Re-Enumeration derselben Sitzung: Devpath nachziehen, nichts neu starten.
        if devpath and devpath != state.devpath:
            state.devpath = devpath
            state.save(state_path)
        log("add: Sitzung laeuft bereits")
        return {"event": "add", "outcome": "already-active"}

    log(f"kfz-anschluss erkannt ({verdict.reason}, {verdict.usb_id}) -> car-Profil")
    results = [run_action(n, cfg, log) for n in cfg.connect]
    State(active=True, devpath=devpath, usb_id=str(verdict.usb_id),
          since=_now_iso(), reason=verdict.reason,
          boot_id=current_boot_id()).save(state_path)
    notice = notify.send("profile_on", cfg.notify, log)
    return {"event": "add", "outcome": "started",
            "actions": [r.__dict__ for r in results], "notice": notice}


def _remove_matches(env: dict, state: State) -> bool:
    devpath = env.get("DEVPATH")
    if devpath and state.devpath and devpath == state.devpath:
        return True
    uid = usb_id_from_env(env)
    return uid is not None and str(uid) == state.usb_id


def handle_remove(env: dict, state_path: Path, cfg: Config, log: Logger = _stderr) -> dict:
    """Abstecken: nur wenn eine per AOA gestartete Sitzung zu diesem Geraet gehoert."""
    state = State.load(state_path)
    if not state.active:
        return {"event": "remove", "outcome": "noop"}
    if not _remove_matches(env, state):
        log("remove ignoriert (nicht das Kfz-Geraet der laufenden Sitzung)")
        return {"event": "remove", "outcome": "ignored"}

    log("kfz-anschluss getrennt -> car-Profil beenden")
    results = [run_action(n, cfg, log) for n in cfg.disconnect]
    State(active=False, since=_now_iso()).save(state_path)
    notice = notify.send("profile_off", cfg.notify, log)
    failed = [r.name for r in results if r.status == "failed"]
    return {"event": "remove", "outcome": "ended" if not failed else "ended-with-errors",
            "failed": failed, "actions": [r.__dict__ for r in results], "notice": notice}


def default_state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "carprofile" / "state.json"


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "carprofile" / "carprofile.json"
