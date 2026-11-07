# omamobile-audiobookshelf

Audiobookshelf auf dem Phone: der Web-Client als eigenes Fenster (`audiobookshelf-open`), der Hörbuch-Sync von Unraid aufs Phone und der Bibliotheks-Rescan (`abs-sync`).

Der Hauptweg ist die offizielle Audiobookshelf-App (iOS/Android) gegen die Unraid-Instanz. Dieses Paket braucht man nur für den Sync und den Web-Client auf dem Phone.

| Befehl | Was er tut |
| --- | --- |
| `abs-sync` | Sync von der Quelle auf dem Unraid, danach Rescan. Das tut der Timer `abs-sync.timer` (alle 15 Minuten). |
| `abs-sync setup` | Einrichtung am Gerät (interaktiv, siehe unten). |
| `abs-sync check` | Prüft Schlüssel, SSH, Quelle, Token und Bibliothek. Nur lesend. |
| `audiobookshelf-open` | Öffnet die Audiobookshelf-Weboberfläche. |

## Einrichtung am Gerät

Host, Schlüssel, Bibliotheks-ID und Token gehören dem Besitzer des Geräts. Sie werden nur am Phone eingetragen, nie ins Repo, nie in Chat oder Logs.

1. Schlüssel für das Phone anlegen (einmalig, als root):

   ```sh
   sudo mkdir -p /etc/audiobookshelf-sync
   sudo ssh-keygen -t ed25519 -N '' -f /etc/audiobookshelf-sync/id_ed25519 -C omamobile-abs-sync
   ```

   Den öffentlichen Teil (`/etc/audiobookshelf-sync/id_ed25519.pub`) in die `authorized_keys` des Sync-Benutzers auf dem Unraid eintragen.
2. In Audiobookshelf einen API-Token erzeugen (Benutzer-Einstellungen) und die ID der Bibliothek notieren (Einstellungen > Bibliotheken).
3. Einrichtung starten:

   ```sh
   sudo abs-sync setup
   ```

   Das Skript fragt nach SSH-Ziel, Schlüsselpfad, Quelle (Symlink-Farm auf dem Unraid), Audiobookshelf-URL, Bibliotheks-ID und Token. Der Token wird nicht angezeigt; Enter bei der Token-Frage behält den bisherigen. Geschrieben werden:
   - `/etc/audiobookshelf-sync.conf` (0644): Pfade, Host, URL, Bibliotheks-ID, kein Token.
   - `/etc/audiobookshelf-sync.env` (0600): nur `ABS_TOKEN`.
4. Danach läuft `check` automatisch. Nur bei Erfolg fragt `setup`, ob der Timer aktiviert werden soll. Ohne ein ausdrückliches `j` bleibt er aus.
5. Nachprüfen:

   ```sh
   abs-sync check
   systemctl status abs-sync.timer
   ```

Den Timer später von Hand aktivieren oder abschalten:

```sh
sudo systemctl enable --now abs-sync.timer
sudo systemctl disable --now abs-sync.timer
```

## Dateien

| Pfad | Inhalt |
| --- | --- |
| `/etc/audiobookshelf-sync.conf` | KEY=VALUE, keine Shell (Vorlage im Paket) |
| `/etc/audiobookshelf-sync.env` | `ABS_TOKEN`, Modus 0600 |
| `/etc/audiobookshelf-sync/id_ed25519` | privater SSH-Schlüssel des Phones für den Unraid |
| `/var/lib/audiobookshelf-sync/` | Zielordner der Kopie und Stand-Datei des letzten Laufs |

`check` liest nur: ein `GET` auf die Bibliothek und ein Verzeichnis-Zählen auf dem Unraid. Der Sync-Lauf bricht still ab, wenn die Quelle nicht erreichbar ist (Heim-WLAN aus, kein Tailscale).

## Tests

```sh
packages/omamobile-audiobookshelf/test/all
```

Ohne Telefon und ohne echten Host: ssh, rsync und systemctl sind Stubs, `check` und `setup` sprechen mit einem Fake-Audiobookshelf auf 127.0.0.1.
