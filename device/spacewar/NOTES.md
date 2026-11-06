# Nothing Phone (1) (spacewar) bring-up notes

Findings from real hardware, newest first. The device descriptor is
[device.conf](device.conf), the firmware flow is [FIRMWARE.md](FIRMWARE.md).
Nothing here identifies this particular phone: no IMEI, serial, MAC or
partition dump — those live in `~/android/reference/` off-repo.

## 2026-11-06: P0 preparation done, phone not yet attached

Everything that needs the phone is still open; this entry is the state the
phone session starts from.

**Stock image situation (Nothing OS V3.2 is the EOL line, Android 15).**
Builds since Nothing OS 1.5.2 are one build for GLO and EEA (the fingerprint
differs only in the product segment). Chain and fingerprints:

- Final build: `Spacewar_V3.2-260618-1045`
  (`Nothing/Spacewar/Spacewar:15/AQ3A.240929.001/2606181045:user/release-keys`,
  same date stamp for SpacewarEEA).
- The final build's full-OTA zip is not published; it ships as incrementals
  from `android.googleapis.com/packages/ota-api/…` (links in the
  spike0en/nothing_archive release `Spacewar_V3.2-260618-1045`).
- Last published **full** OTA: `Spacewar_V3.2-250926-1631` (complete V3.2
  image, `payload.bin` with every partition — works with
  [tools/extract-spacewar-firmware](../../tools/extract-spacewar-firmware)).
- Staged off-repo in `~/android/reference/spacewar/stock/`: the full OTA zip,
  plus the final build's split `image-firmware.7z`, `image-boot.7z` and
  `hash.sha256` (partition images extracted from the final payload by the
  archive). If the attached phone runs something older, download that build's
  full OTA the same way before any rollback plan.

**Descriptor.** `NV_PARTS`/`NV_BACKUP_EXTRA` added (same set as the Pixels:
`efs efs_backup modem_userdata persist modem_a modem_b` + `devinfo`), so
`tools/nv-backup -d spacewar` knows the calibration set. On the Mac mini run
it with Homebrew bash (`/opt/homebrew/bin/bash`): the script needs `declare
-A`, macOS `/bin/bash` is 3.2.

**Bootloader state: unknown until the phone is attached.** The stock-capture
and backup paths depend on it (read-only either way):

1. Android boots, USB debugging on → `bin/omamobile capture spacewar-stock`
   (getprop, by-name partitions, cmdline, vendor modules; the root-only
   extras — live DTB, dmesg, iomem — need a rooted phone).
2. Calibration backup, in order of preference:
   - rooted adb: `dd` each `NV_PARTS` partition from `/dev/block/by-name/`
     read-only over `adb pull`, SHA256 both sides (the `tools/nv-backup`
     logic, `PHONE_SSH` pointed at adb);
   - unlocked bootloader, no root: `fastboot boot` a recovery image
     (boot-in-RAM, **nothing flashed**) with adb root, then the same `dd`;
     `devinfo` from fastboot (`fastboot getvar all`) records the unlock and
     slot state either way;
   - locked bootloader: those partitions are unreadable; say so and stop —
     unlocking wipes userdata, which is the owner's call, not ours.

**Safety (unchanged, from AGENTS.md §1):** never flash bootloader, radio,
persist or any calibration partition; slot A stays stock as the way back;
backups hold the IMEI and never leave `~/android/reference/`.

## Boot facts from postmarketOS (checked into device.conf, kept for reference)

pmOS `device-nothing-spacewar` deviceinfo (branch `pmos/main` in
`pmaports-ref`, read with `git show` — the checkout itself is on another
device's branch): header v2, pagesize 4096, base 0x0, kernel 0x8000, ramdisk
0x1000000, dtb 0x1f00000, tags 0x100, `append_dtb`, rootfs sparse on
`/dev/sda6` (sector 4096), `flash_method=fastboot`. pmOS's firmware package
follows mainlining/firmware-nothing-spacewar (NothingOS 3.2 EOL); we extract
from the owner's own image instead (FIRMWARE.md).
