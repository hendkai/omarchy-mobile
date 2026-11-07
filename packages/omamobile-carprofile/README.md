# omamobile-carprofile

Auto-Profil `car` für Omarchy Mobile (EQB / Nothing Phone (1) spacewar).

AOA-Erkennung (Google-Open-Accessory `18d1:2d00` / `18d1:2d01`), Anstecken
(startet das car-Profil) und Abstecken (beendet die Sitzung). Eine udev-Regel
(AOA nur, kein VBUS) leitet nach dem Muster auf das Dispatcher-Skript,
das den Trigger im User-Manager des Fahrers startet.

## Dateien

- `carprofile/` – Python-Paket (`__init__`, `detect`, `profile`, `trigger`)
- `carprofile.example.json` – Konfigurationsvorlage (Default = Dry-run)
- `carprofile-dispatch`, `carprofile-trigger` – Dispatcher/Trigger (binär)
- `udev/99-carprofile.rules` – Regel, **ausgeliefert, nicht aktiv** (Symlink in
  `/etc/udev/rules.d/` nach Hendriks OK am Geraet)
- `tests/` – 25 phone-freie unittest (udev-Events simuliert)
- `install`, `test/all`, `PKGBUILD`, `version`

## Konfiguration (Default = Dry-run)

`~/.config/carprofile/carprofile.json`. `apply: false` führt keinen Befehl aus.
Aktionen ohne konfigurierten Befehl werden übersprungen.

## Tests (phone-frei)

```sh
cd packages/omamobile-carprofile
bash test/all
```

## Install

```sh
cd packages/omamobile-carprofile
./install /tmp/stage
```

## Offen
- **Hardware-Verifikation**: AOA-Re-Enumeration am Phone/EQB (Messplan in
  `car-apps/carprofile/MESSPLAN.md`, nicht ausgeführt; P4 + Hendriks OK).
- **EQB-Allowlist** (`extra_car_ids`): leer, bis echte IDs am Fahrzeug
  gemessen wurden.
- **Benachrichtigungen**: `notify-send` + Catppuccin Mocha (s.
  `car-apps/carprofile/docs/notifications.md`).
