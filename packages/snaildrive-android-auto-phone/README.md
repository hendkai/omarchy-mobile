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

## AAP-Encoder (Lab): `snaildrive_aac/aap_nav.py`

Setzt einen `aap-nav-status`-Datensatz in die Nachrichten des NavigationStatus-Dienstes
um: `NavigationStatus` (32771), `NavigationState` (32774) und `NavigationCurrentPosition`
(32775), dazu Klartext-BULK-Frames mit Kanal 12. Feldnummern, Enum-Werte, Message-IDs
und Framing stammen aus **libaasdk** (opencardev/aasdk, Commit
`5483c6481742d35bd9ed5923eb74a3562eeab84b`, GPLv3). Die `.proto`-Dateien selbst liegen
nicht im Paket, nur die daraus abgeleiteten Feldnummern im Code. Die Bytes sind gegen
`protoc 35.0` geprüft (`test/test_aap_encoder.py`, 18 Tests).

Feldzuordnung: `next.distance_m` → Schrittdistanz, `route.distance_to_dest_m` und
`route.eta_s` → Zieldistanz/Ankunftszeit, `next.action` → Maneuver-Typ (Tabelle
`MANEUVER`; `u-turn` und `ramp` bleiben `UNKNOWN`, weil der Datensatz keine Seite nennt),
`next.instruction` → Straßenname (Text). Position geht nicht über diese Nachrichten.

## Nicht enthalten (offen, bewusst)

- **AA-Handshake** (VERSION → TLS → AUTH → SERVICE_DISCOVERY → CHANNEL_OPEN). Der
  Encoder erzeugt keine Verbindung und keine Verschlüsselung. Nach dem Handshake ist
  jeder Frame verschlüsselt. Die Klartext-Frames dürfen nur gegen den Lab-Harness.
- **Fragmentierung** (Nutzlast ≥ 0x4000 wird abgelehnt), **Start/Stop-Nachrichten**.
- **Richtung und Auswahl der Nachrichten.** `aasdk` implementiert die Headunit-Seite,
  also den Empfänger. ID und Body sind dieselben Wire-Daten, ob ein Auto genau diese
  drei Nachrichten vom Telefon erwartet, ist im Lab zu prüfen.
- **AOA-Handshake** (Vendor-Requests auf ep0 über FunctionFS). Der Gadget legt nur den
  FunctionFS-Endpunkt `ffs.aoa` an.
- **Lab-Harness `openauto-headless`.** Nicht installiert, nicht gelaufen. Braucht
  Hendriks OK für den Lab-Container. Die Loopback-Tests nutzen den Navi-Datenkanal,
  nicht die AA-Session.
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

Gegenprobe (Linux-Container, `olddefconfig` auf der pmOS-Basis `config-postmarketos-qcom-sc7280.aarch64`
gegen den sc7280-Tree im Build-Volume): Die USB-Optionen stimmen mit `kernel-spacewar/out/.config`
überein, auch `CONFIG_USB_CONFIGFS_F_FS`, `_ACM`, `_MASS_STORAGE`, `_NCM` und
`CONFIG_USB_DWC3_DUAL_ROLE`, jeweils `=y`. Nicht identisch sind andere Symbole
(u. a. `CONFIG_SND_SOC_QDSP6_Q6VOICE`, `CONFIG_SLIM_QCOM_CTRL`, Toolchain-Probes): die
gespeicherte `.config` ist nicht reine Basis plus `olddefconfig`. Offen, nicht geklärt
(`kernel-spacewar/build.sh` setzt `olddefconfig` nur einmal per `.configured`-Marker).

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
