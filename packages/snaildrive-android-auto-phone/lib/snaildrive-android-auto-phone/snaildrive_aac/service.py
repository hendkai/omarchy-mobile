# SPDX-License-Identifier: GPL-3.0-or-later
"""Socket-Schicht des AAC-Phone-Dienstes: Navi-Client, AAC-Hub, Lauf-Schleife."""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import Callable, List, Optional

from .core import STALE_AFTER_S, Pipeline, write_status


class Hub:
    """Unix-Socket-Server; jeder Verbindung werden die Datensaetze zugestellt."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._subs: List[socket.socket] = []
        self._lock = threading.Lock()
        self._srv: Optional[socket.socket] = None
        self._stop = threading.Event()

    def start(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        if os.path.exists(self.path):
            os.unlink(self.path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.path)
        os.chmod(self.path, 0o660)
        srv.listen(4)
        srv.settimeout(0.5)
        self._srv = srv
        threading.Thread(target=self._accept, name="aac-hub", daemon=True).start()

    def _accept(self) -> None:
        srv = self._srv
        if srv is None:
            return
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._lock:
                self._subs.append(conn)

    def publish(self, line: bytes) -> int:
        """Schickt eine Zeile an alle Abonnenten; liefert die Zahl der Zustellungen."""
        sent = 0
        with self._lock:
            alive = []
            for conn in self._subs:
                try:
                    conn.sendall(line)
                    alive.append(conn)
                    sent += 1
                except OSError:
                    conn.close()
            self._subs = alive
        return sent

    def stop(self) -> None:
        self._stop.set()
        if self._srv is not None:
            self._srv.close()
        with self._lock:
            for conn in self._subs:
                conn.close()
            self._subs = []
        if os.path.exists(self.path):
            os.unlink(self.path)


def run_navi_client(navi_path: str, pipeline: Pipeline, stop: threading.Event,
                    clock: Callable[[], float] = time.time,
                    retry_s: float = 2.0) -> None:
    """Verbindet sich als Client mit dem Navi-Socket und verarbeitet Zeilen.

    Bei Verbindungsverlust wird nach retry_s neu verbunden. Ohne Frames meldet
    die Pipeline nach STALE_AFTER_S 'stale'.
    """
    while not stop.is_set():
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        try:
            sock.connect(navi_path)
        except OSError:
            sock.close()
            pipeline._status("waiting", clock(), f"kein Navi-Socket: {navi_path}")
            stop.wait(retry_s)
            continue
        buf = b""
        try:
            while not stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    pipeline.on_tick(clock())
                    continue
                if not chunk:
                    break  # Navi-Dienst hat geschlossen
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        pipeline.on_line(line, clock())
                if len(buf) > 8192:
                    buf = b""  # Schutz: uebergrosse Zeile verwerfen
                    pipeline._status("error", clock(), "Zeile zu lang, verworfen")
        finally:
            sock.close()
        stop.wait(retry_s)


def serve(navi_path: str, out_path: str, status_path: str,
          clock: Callable[[], float] = time.time) -> None:
    """Lauf bis SIGINT/SIGTERM: Navi-Client -> Pipeline -> Hub."""
    hub = Hub(out_path)
    hub.start()
    stop = threading.Event()
    def publish(line: bytes) -> None:
        hub.publish(line)

    pipe = Pipeline(publish=publish,
                    status=lambda st, ts, det: write_status(status_path, st, ts, det))
    try:
        run_navi_client(navi_path, pipe, stop, clock=clock)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        hub.stop()
