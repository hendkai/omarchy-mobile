# spacewar firmware: what runs what, and where it comes from

Nothing's and Qualcomm's firmware is not ours to redistribute (AGENTS.md
section 1), so nothing here is in the repository. Every file below is
extracted from a Nothing OS image the user already has, on their own
machine, with [tools/extract-spacewar-firmware](../../tools/extract-spacewar-firmware):

```sh
tools/extract-spacewar-firmware -o out/spacewar-firmware NothingOS-*.zip
tar -C out/spacewar-firmware -cf - usr | tools/phone-ssh 'tar -C / -xf -'
```

The tool takes a full OTA zip, a `payload.bin`, or a directory
`payload-dumper-go` already dumped. It stages into
`usr/lib/firmware/updates` (searched before linux-firmware, owned by no
package) and writes a checksum manifest beside the tree. Which partition
image carries which file is discovered (every FAT image is listed and
searched), so a Nothing OS rename degrades into a clear "missing" instead
of a silent wrong copy. Its functional test runs against synthetic
FAT/ext4 fixtures in the build container.

## What the device tree asks for

`sm7325-nothing-spacewar.dts` (sc7280-mainline v7.2.2-sc7280 + the 52
spacewar patches) names every remoteproc file under
`qcom/sm7325/nothing/spacewar/`:

| File | Partition (FAT, `image/`) | Loads |
|---|---|---|
| `modem.mbn` | modem | MPSS: the LTE/NR modem (ModemManager, qrtr) |
| `adsp.mbn` | adsp | ADSP: audio and the sensors DSP (hexagonrpcd) |
| `cdsp.mbn` | cdsp | CDSP (fastrpc compute) |
| `wpss.mbn` | wpss | WPSS: ath11k's WiFi subsystem |
| `vpu20_1v.mbn` | vpu | Iris video decoder |
| `ipa_fws.mbn` | ipa_fws | IPA (mobile data path) |
| `a660_zap.mbn` | — (`/vendor/firmware`) | Adreno 660L ZAP shader for GPU resets |

The `.jsn` sidecars (`adspr/adsps/adspua/battmgr/cdspr/modemr.jsn`) come
from `/vendor/firmware`; pd-mapper tolerates a missing one. The sensors
registry and socinfo under `/vendor/etc/` go to
`usr/share/qcom/sm7325/Nothing/spacewar/` for hexagonrpcd's sensors PD
(libssc). The touch blob (`focaltech_ts_novatek.bin`) is held in case an
`fts_tp` controller ships with a blank EEPROM.

## What linux-firmware already covers

ath11k's `amss.bin`/`m3.bin` (`ath11k/WCN6750/hw1.0/`), the WCN6750
Bluetooth firmware (`qca/`), and the a660 shader toolkit. No extraction
needed; the root image installs `linux-firmware`.

## The one manual step: ath11k board-2.bin

The per-board WiFi calibration (bdwlan/regdb from the modem partition) is
staged raw to `out/spacewar-firmware/wifi-board/`. Packing it needs
`ath11k-bdencoder` (qca-swiss-army-knife) and a JSON manifest, so the tool
prints the recipe instead of guessing the manifest format. Until it is
packed, ath11k uses linux-firmware's generic board-2.bin (the rb3gen2
data): WiFi works, radio calibration is generic — the same state as the
woodyst port (spacewar-pmos, docs/wifi-modem.es.md).

The board data is factory radio calibration: never commit it, never share
it, and never copy it between phones.

## Attribution

The extraction set follows mainlining's firmware-nothing-spacewar (the
files pmOS ships, NothingOS 3.2 EOL) and the woodyst spacewar-pmos notes;
the file list itself comes from the device tree's `firmware-name`
properties, which are the ground truth for what mainline requests.
