# SPDX-License-Identifier: MIT
"""car-Profil-Hinweise: die drei sichtbaren Ereignisse, ueber notify-send.

Kein eigener Daemon und keine eigene UI (docs/notifications.md): notify-send
spricht den freedesktop-Benachrichtigungsdienst an, den die Phone-Shell bereits
betreibt. Die Darstellung (Toast, Verlauf in der Benachrichtigungsleiste) macht
die Shell, nicht dieses Modul.

Die Texte enthalten keine Standorte, keine Kennungen und keine Termine. Ein
Hinweis zeigt nur, dass etwas passiert ist.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

APP_NAME = "carprofile"
NOTIFY_SEND = "notify-send"
TIMEOUT_S = 5


@dataclass(frozen=True)
class Notice:
    title: str
    body: str
    urgency: str      # low | normal | critical (notify-send -u)
    expire_ms: int    # notify-send -t
    icon: str         # freedesktop-Iconname, Fallback übernimmt die Shell


# Die drei Zustaende aus docs/notifications.md. Texte bewusst ohne Daten.
NOTICES: Dict[str, Notice] = {
    "profile_on": Notice("car-Profil an", "Kfz-Anschluss erkannt",
                         "normal", 3000, "audio-card"),
    "profile_off": Notice("car-Profil aus", "Sitzung beendet",
                          "low", 2000, "audio-card"),
    "location_saved": Notice("Standort gemerkt", "Parkpunkt gespeichert",
                             "low", 4000, "find-location"),
}


def argv(key: str) -> List[str]:
    """Der notify-send-Aufruf fuer einen Zustand. ``--`` trennt Optionen vom Text."""
    n = NOTICES[key]
    return [NOTIFY_SEND,
            f"--app-name={APP_NAME}",
            f"--urgency={n.urgency}",
            f"--expire-time={n.expire_ms}",
            f"--icon={n.icon}",
            "--", n.title, n.body]


Runner = Callable[..., "subprocess.CompletedProcess[str]"]
Logger = Callable[[str], None]


def send(key: str, enabled: bool, log: Logger,
         runner: Optional[Runner] = None,
         which: Optional[Callable[[str], Optional[str]]] = None) -> dict:
    """Schickt den Hinweis. Fehler sind nie fatal: das Profil laeuft trotzdem.

    Ergebnis: ``status`` ist ``off`` (abgeschaltet), ``skipped`` (kein
    notify-send), ``sent`` oder ``failed``.
    """
    if key not in NOTICES:
        raise KeyError(key)
    if not enabled:
        return {"notice": key, "status": "off"}
    # Zur Laufzeit aufloesen (nicht als Default-Argument), damit Tests die
    # Funktionen patchen koennen.
    runner = runner or subprocess.run
    which = which or shutil.which
    if which(NOTIFY_SEND) is None:
        log(f"hinweis {key}: {NOTIFY_SEND} fehlt -> uebersprungen")
        return {"notice": key, "status": "skipped"}
    try:
        proc = runner(argv(key), capture_output=True, text=True, timeout=TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"hinweis {key}: Fehler {exc.__class__.__name__}")
        return {"notice": key, "status": "failed", "detail": exc.__class__.__name__}
    if proc.returncode != 0:
        log(f"hinweis {key}: rc={proc.returncode}")
        return {"notice": key, "status": "failed", "detail": f"rc={proc.returncode}"}
    return {"notice": key, "status": "sent"}
