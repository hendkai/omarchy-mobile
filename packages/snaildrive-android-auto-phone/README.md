# snaildrive-android-auto-phone

Phone-seitiger Kern der SnailDrive-Android-Auto-Brücke für spacewar (Nothing Phone 1).
**Stand: Lab.** Kein Phone-I/O, kein Fahrzeugtest. Konzept:
`docs/spacewar-aa-phone-hud-concept.md` (Kap. 2–4), Navi-Quelle:
`docs/spacewar-offline-navi-concept.md`.

## Was drin ist

| Teil | Zweck |
|---|---|
| `snaildrive-aac-phoneside` | Dienst: Navi-Socket (Client) → AAC-Socket (Server). Verarbeitet NDJSON-Frames des Navi-Datenkanals v1 (`navi_tbt.py`, Kopie aus `car-apps/navi`). |
| `snaildrive-aac-phoneside convert FILE` | Offline-Umwandlung einer NDJSON-Datei in `aap-nav-status`-Zeilen (Lab). |
| `snaildrive-aoa-gadget` | USB-Gadget-Vorbereitung für den AA-Accessory-Pfad. Bindet nur an einen freien UDC und bricht ab, wenn der SSH-Gadget der initramfs ihn hält. |
| `snaildrive-android-auto-phone.service` | Systemd-Unit, **nicht aktiviert**. Start über das Auto-Profil (`carprofile`, Aktion `navi-aa`). |
| `snaildrive-aoa-gadget.service` | Systemd-Unit, nur mit Schalterdatei `/etc/snaildrive-android-auto-phone/aoa-gadget.enable`. |

## Datensatz `aap-nav-status` v1

```json
{"v":1,"type":"aap-nav-status","ts":…,"status":"navigating",
 "location":{"lat":…,"lon":…,"accuracy":…,"speed_kmh":…,"bearing":…,"satellites":…,"alt":…},
 "next":{"action":"turn-right","instruction":"…","distance_m":800,"turn_degree":45},
 "route":{"distance_to_dest_m":12400,"eta_s":42}}
```

Regeln: 1 Hz im Navigationsbetrieb, Burst (höchstens 2 Hz) bei Status- oder
Aktionswechsel, kein Push bei `paused`/`idle`/`error`. Die Status-Datei
`/run/snaildrive/navidatenkanal.status` trägt `ok`, `error`, `stale` oder `waiting`.

## Nicht enthalten (offen, bewusst)

- **AAP-Protobuf-Encoding und AA-Handshake.** Die AAP-Feldnummern werden aus den
  `.proto`-Dateien von `libaasdk` (GPLv3) übernommen, nicht aus Snippets geraten.
  Der Encoder ist eine Folgekarte. Dieses Paket liefert nur die Datensätze, die er
  konsumieren wird.
- **AOA-Handshake** (Vendor-Requests auf ep0 über FunctionFS). Der Gadget legt nur den
  FunctionFS-Endpunkt `ffs.aoa` an.
- **Lab-Harness `openauto-headless`.** Nicht installiert, nicht gelaufen. Die Loopback-Tests
  nutzen den Navi-Datenkanal, nicht die AA-Session.
- **Qt-HUD-Snaplet.** Noch keine sichtbare Oberfläche in diesem Schritt.
- **Vendor-IDs.** Der Gadget nutzt Lab-IDs `1d6b:0a5a` (Linux-Foundation-Testkennung,
  nicht registriert). Echte IDs sind offen.
- **Kernel-Fragment.** Siehe Kap. Kernel unten.

## Kernel (USB) — Befund

Die Annahme aus dem Konzept „functionfs fehlt“ stimmt nicht für die aktuelle Konfiguration:
`CONFIG_USB_CONFIGFS_F_FS=y`, `CONFIG_USB_CONFIGFS_ACM=y`, `CONFIG_USB_CONFIGFS=m`,
`CONFIG_USB_CONFIGFS_NCM=y`, `CONFIG_USB_CONFIGFS_MASS_STORAGE=y`,
`CONFIG_USB_DWC3_DUAL_ROLE=y` (der DWC3-Gadget ist im Dual-Role-Modus enthalten).
`CONFIG_USB_FUNCTIONFS` (Legacy-Modul) ist nicht gesetzt, wird für den configfs-Pfad nicht gebraucht.
Ein Kernel-Fragment ist deshalb in diesem Schritt nicht nötig. Offen bleibt, ob
`CONFIG_USB_CONFIGFS` als Modul für den AOA-Pfad nach `switch_root` geladen wird.

## Tests (phone-frei)

```sh
cd omarchy-mobile/packages/snaildrive-android-auto-phone
./test/all
```

`test/all` prüft Skripte, Python-Kompilierung, die Schema-Version von `navi_tbt.py`,
dass keine Unit sich selbst aktiviert, und führt die Python-Tests aus:
Regeln und Pipeline (`test_core.py`), Loopback mit echten Unix-Sockets
(`test_lab_loopback.py`) und die Gadget-Schutzlogik gegen ein temporäres configfs-Abbild
(`test_gadget.py`). Die Gadget-Tests schreiben nie nach `/sys`.

## Lizenz

GPL-3.0-or-later (Konzept Kap. 2.3: die eigene App wird mit `libaasdk` (GPLv3)
zusammen ausgeliefert). Die Lizenztexte liegen noch nicht im Paket, das folgt mit
dem libaasdk-Schritt.
