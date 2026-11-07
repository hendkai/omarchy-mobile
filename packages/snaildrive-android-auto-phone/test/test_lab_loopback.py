# SPDX-License-Identifier: GPL-3.0-or-later
"""Lab-Loopback ohne Phone und ohne Fahrzeug: echte Unix-Sockets, kein Navi-Dienst.

Ein Fake-Navi-Dienst (Server) schickt NDJSON-Frames; der Phone-Dienst verbindet
sich als Client, verarbeitet sie, und ein AAC-Abonnent (Stellvertreter fuer den
spaeteren libaasdk-Encoder) bekommt die Nav-Status-Datensaetze.

Erwartung: 4 Frames -> 2 Datensaetze (navigating/turn-right, Burst auf turn-left),
der Fehler-Frame erzeugt keinen Datensatz, die Status-Datei endet auf 'error'.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parent / "lib" / "snaildrive-android-auto-phone"
sys.path.insert(0, str(LIB))

from snaildrive_aac import core  # noqa: E402
from snaildrive_aac.service import Hub, run_navi_client  # noqa: E402
from test_core import frame, line  # noqa: E402


def _sock_dir() -> str:
    # AF_UNIX-Pfade sind kurz (macOS: 104 Zeichen): eigenes Kurz-Verzeichnis.
    return tempfile.mkdtemp(prefix="sdaac-", dir="/tmp")


class Loopback(unittest.TestCase):
    def setUp(self):
        self.dir = _sock_dir()
        self.navi = os.path.join(self.dir, "navi.sock")
        self.out = os.path.join(self.dir, "aac.sock")
        self.status = os.path.join(self.dir, "navidatenkanal.status")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _fake_navi(self, frames):
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.navi)
        srv.listen(1)

        def serve():
            conn, _ = srv.accept()
            with conn:
                try:
                    for f in frames:
                        conn.sendall(line(f))
                        time.sleep(0.02)
                    # Verbindung offen lassen, bis der Test fertig ist.
                    time.sleep(3)
                except OSError:
                    pass  # Client hat schon geschlossen (Testende)
            srv.close()

        threading.Thread(target=serve, daemon=True).start()

    def test_frames_to_subscriber_with_rate_rules(self):
        frames = [
            frame(action="turn-right"),                 # erster Frame -> Push
            frame(action="turn-right"),                 # gleich, < 1 s -> still
            frame(action="turn-left"),                  # Aktionswechsel -> Burst-Push
            frame(status="error"),                      # Navi-Fehler -> kein Push
        ]
        self._fake_navi(frames)

        hub = Hub(self.out)
        hub.start()
        sub = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sub.settimeout(5)
        sub.connect(self.out)
        deadline = time.time() + 5
        while not hub._subs and time.time() < deadline:
            time.sleep(0.01)  # Hub hat den Abonnenten angenommen

        # Deterministische Uhr: jede Zeile 0.25 s weiter.
        ticks = iter(0.25 * i for i in range(1, 100))
        clock = lambda: next(ticks)  # noqa: E731

        stop = threading.Event()
        pipe = core.Pipeline(
            publish=lambda b: hub.publish(b),
            status=lambda st, ts, det: core.write_status(self.status, st, ts, det),
        )
        client = threading.Thread(
            target=run_navi_client, args=(self.navi, pipe, stop),
            kwargs={"clock": clock, "retry_s": 0.2}, daemon=True)
        client.start()

        got = b""
        records = []
        body = {}
        try:
            while len(records) < 2:
                chunk = sub.recv(65536)
                if not chunk:
                    break
                got += chunk
                records = [json.loads(x) for x in got.decode("utf-8").splitlines() if x]
            # Fehler-Frame abwarten (Status-Datei 'error'), bevor der Dienst endet.
            deadline = time.time() + 5
            while time.time() < deadline:
                if os.path.exists(self.status):
                    body = json.loads(Path(self.status).read_text(encoding="utf-8"))
                    if body.get("state") == "error":
                        break
                time.sleep(0.05)
        finally:
            stop.set()
            sub.close()
            hub.stop()
            client.join(timeout=5)

        self.assertEqual(len(records), 2, f"erwartet 2 Datensaetze, bekam {len(records)}")
        self.assertEqual(records[0]["next"]["action"], "turn-right")
        self.assertEqual(records[1]["next"]["action"], "turn-left")
        self.assertTrue(all(r["type"] == "aap-nav-status" and r["v"] == 1 for r in records))
        # Fehler-Frame: Status 'error', kein weiterer Push (nur die 2 oben).
        self.assertEqual(body.get("state"), "error")
        self.assertEqual(body.get("detail"), "gnss: kein Fix")


if __name__ == "__main__":
    unittest.main()
