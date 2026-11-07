<!-- SPDX-License-Identifier: MIT -->
# Installing Omarchy Mobile on spacewar — gated runbook

`tools/install-phone`, `bin/omamobile flash` and `bin/omamobile push` take
`BOOT_METHOD=qcom-abl-v2`. Everything here writes only `boot_b`, `dtbo_b`
(erased) and `userdata`, and switches to slot B through ABL
(`fastboot --set-active=b`); slot A's stock Android stays untouched and is
the way back. **Nothing below runs without the owner's explicit go
(AGENTS.md §1); the dry run comes first, always.**

What each tool does on this device:

- `tools/install-phone --device spacewar` — the full install: `boot_b`,
  `fastboot erase dtbo_b` (this ABL applies stock's dtbo overlays onto the
  DTB our boot image carries, and they target stock's nodes), the ABL also
  needs `fastboot erase vendor_boot_b` at install or it falls straight back
  to fastboot on every reboot (spacewar-pmos Pflicht), `userdata` with the
  root image (erased), `--set-active=b`, `reboot`. It writes
  `/etc/omamobile/boot-update.conf` with `AUTO_WRITE=no`:
  `omamobile-boot-update` has no qcom-abl-v2 path yet.
- `bin/omamobile flash` (DEVICE=spacewar) — the same writes over fastboot,
  for first boot from a built tree or recovery (no userdata image).
- `bin/omamobile push` (DEVICE=spacewar) — day-2 kernel updates: modules
  first, then `boot_b` rewritten by `dd` on sda (found by `PARTNAME`,
  read-only check, size check), verified by SHA-256, then `reboot-to
  omamobile`. No dtbo erase: it stays erased from the install.
- `bin/omamobile fastboot` refuses qcom-abl-v2 (the panic path to the
  bootloader is uncharacterized on this ABL): reach fastboot with the
  buttons — from off, Volume Down + Power held ~20 s.

## Steps

Read-only first. `fastboot getvar all` is read-only; nothing else queries
the phone before the dry run.

| # | Risk | Step | Expect / Pass | Stop condition | Rollback |
|---|------|------|---------------|----------------|----------|
|| 1 | none | Build the artifacts: `DEVICE=spacewar bin/omamobile images` (kernel + initramfs first) and the spacewar root image (`tools/build-rootfs`) | `out*/images/boot.img` with `DEVICE`=spacewar; `boot.img` ≈ header v2, DTB appended (pmOS format) | any build failure | — |
|| 1b | none | Build the Omarchy early-boot splash for 1080×2400: run `device/spacewar/splash/make-splash.sh` (theme logo via ImageMagick, else supplied PNG, else built-in placeholder), then `DEVICE=spacewar SPLASH_BGRA_GZ=<abs> omarchy-mobile/bin/omamobile initramfs` (splash.bgra.gz must be 1080*2400*4 raw bytes, gzip-verified) | `initramfs` packs `splash.bgra.gz` → `stage/splash.bgra.gz` → `/splash.bgra.gz` in the image; `bin/omamobile initramfs` validates the byte size | splash generator fails, size mismatch, or gzip corrupt | rerun `make-splash.sh` and rebuild the initramfs |
|| 2 | none | Phone into fastboot with the buttons (Volume Down + Power ~20 s from off); `fastboot devices` shows exactly one | one serial, state `fastboot` | no device, or two (unplug the other) | — |
| 2 | none | Phone into fastboot with the buttons (Volume Down + Power ~20 s from off); `fastboot devices` shows exactly one | one serial, state `fastboot` | no device, or two (unplug the other) | — |
| 3 | none | `fastboot getvar all` sanity: `product: spacewar`, `unlocked: yes`, `current-slot: a`, both slots' `version-bootloader` equal; note `partition-size:boot_b` ≥ boot.img size | all five hold | any miss (wrong phone, locked, slot b active, sizes differ) | — |
| 4 | none | `tools/install-phone --device spacewar --dry-run` (answers piped or typed) | the plan lists exactly: `flash boot_b`, `erase dtbo_b`, `fastboot erase vendor_boot_b`, `flash userdata`, `--set-active=b`, `reboot` — and nothing else; nothing written | any other partition in the plan | — |
| 5 | **medium** (erases `userdata`) | `tools/install-phone --device spacewar`, type `spacewar` when asked | every flash `OKAY`; the phone reboots slot B; first boot creates the user (minutes); SSH answers on 172.16.42.1 | a `FAIL` mid-run: slot B is incomplete — rerun step 5; no SSH after ~2 min: hold Power ~30 s, then Volume Down + Power into fastboot and rerun | stock Android on slot A: `fastboot --set-active=a`, `fastboot reboot` — userdata was erased, so Android needs a factory reset |
| 6 | low | Day-2 kernel updates: `DEVICE=spacewar bin/omamobile push` from a new build | `boot_b (/dev/sdX) written and verified`, reboot-to omamobile, new `uname -v` | verify mismatch: rerun; phone still answers over SSH | slot A untouched; rerun push |

## Open gaps (do not cross without new steps here)

- NV_PARTS and NV_BACKUP_EXTRA are now set in the descriptor
  (`device/spacewar/device.conf`, `efs efs_backup modem_userdata persist
  modem_a modem_b` + `devinfo`), so `tools/nv-backup` knows the radio
  partition set. Take a read-only backup of the phone (from slot A / stock
  Android, or on first boot from slot B) and verify it with
  `tools/nv-backup --check` before any radio/modem/efs work. After any
  stock Android boot, take a fresh backup.

- The ABL zeroes `vendor_boot_<slot>` on every boot and refuses a non-empty
  vendor boot; it must be erased at install exactly once. Per
  `spacewar-pmos/README`, `vendor_boot_<slot>` must be erased or the ABL
  falls straight back to fastboot after every reboot. Both
  `install-phone` (slot B) and `install-phone-slota` (slot A) erase their
  slot's `vendor_boot_b/a` after the DTB erase. The erase is a storage-
  guard allowlisted partition now (UFS_RW in `device/spacewar/device.conf`).

- `bin/omamobile fastboot` (the no-buttons reboot helper) stays refused for
  this ABL; the panic path (`sysrq c` fallthrough) is uncharacterized. Use
  the buttons (Power ~30 s, then Volume Down + Power ~20 s) instead.

## Open items — 2026-11-07 (research + status)

- **Item 7 — ABL boot-success for Slot A (needs human decision).**
  The `qcom-abl-v2` ABL never marks a slot "successfully booted", so after
  the retry counter (7 boots) the slot is flagged unbootable and the phone
  stops trying. On android-v4/Pixel this is a GPT partition-attribute write
  exposed by `bootctl mark-boot-successful`; the qcom `bootctrl` HAL holds
  `BOOT_SUCCESSFUL` in the GPT partition-entry attributes field. For
  `qcom-abl-v2` the AMD/ABL has no exposed API to set that flag; EDK2/
  UEFI.lnx `FindBootableSlot()` consults `successful_boot`/`tries_remaining`
  in the slot metadata. There is no kernel-space way for us to set it on
  this ABL. The Android `boot_control` HAL performs the GPT write.
  **Decision required from Hendrik (AGENTS.md GPT-Ausnahme):** which slot-
  success marker to implement for slot A, or accept the 7-boot retry
  ceiling. Research: [boot_control_qcom.cpp][1], [mobile-nixos #111][2].

- **Item 8 — usb0 carrier loss (open bug, awaiting results).**
  `usb0` loses link carrier ~2 s after Arch start, simultaneous with the
  ADSP-APR / Audio-Probe. Bring-up blacklist lives in
  `/etc/modprobe.d/omamobile-spacewar-bringup.conf` (snd_soc_sm8250,
  snd_soc_wcd938x). Result awaited.

- **Item 9 — Root SSH + link-local in the initramfs.** The initramfs runs
  dropbear; root's `authorized_keys` is placed by `install-phone` (Hendrik's
  `id_ed25519` has a passphrase → Bring-up-Key at
  `~/.hermes/cache/scratch/phone_key`). The laptop has no sudo rights on
  172.16.42.2, so the init script additionally sets a link-local
  169.254.42.1 on `usb0` — documented here, not yet in the init.

## References

- Android boot_control HAL — qcom bootctrl: https://android.googlesource.com/platform/hardware/qcom/bootctrl
- mobile-nixos #111 (A/B slot boot — bootctl mark-boot-successful, GPT
  attributes): https://github.com/mobile-nixos/mobile-nixos/issues/111
- Android A/B "Implement OTA updates" (slot retry count, setActiveBootSlot): https://source.android.com/docs/core/architecture/bootloader/updating

