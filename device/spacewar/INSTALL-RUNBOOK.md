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
  DTB our boot image carries, and they target stock's nodes), `userdata`
  with the root image (erased), `--set-active=b`, `reboot`. Serial pinned,
  product must answer `spacewar`, bootloader unlocked, both bootloader
  slots on the same version, codename typed. It writes
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
| 1 | none | Build the artifacts: `DEVICE=spacewar bin/omamobile images` (kernel + initramfs first) and the spacewar root image (`tools/build-rootfs`) | `out*/images/boot.img` with `DEVICE`=spacewar; `boot.img` ≈ header v2, DTB appended (pmOS format) | any build failure | — |
| 2 | none | Phone into fastboot with the buttons (Volume Down + Power ~20 s from off); `fastboot devices` shows exactly one | one serial, state `fastboot` | no device, or two (unplug the other) | — |
| 3 | none | `fastboot getvar all` sanity: `product: spacewar`, `unlocked: yes`, `current-slot: a`, both slots' `version-bootloader` equal; note `partition-size:boot_b` ≥ boot.img size | all five hold | any miss (wrong phone, locked, slot b active, sizes differ) | — |
| 4 | none | `tools/install-phone --device spacewar --dry-run` (answers piped or typed) | the plan lists exactly: `flash boot_b`, `erase dtbo_b`, `flash userdata`, `--set-active=b`, `reboot` — and nothing else; nothing written | any other partition in the plan | — |
| 5 | **medium** (erases `userdata`) | `tools/install-phone --device spacewar`, type `spacewar` when asked | every flash `OKAY`; the phone reboots slot B; first boot creates the user (minutes); SSH answers on 172.16.42.1 | a `FAIL` mid-run: slot B is incomplete — rerun step 5; no SSH after ~2 min: hold Power ~30 s, then Volume Down + Power into fastboot and rerun | stock Android on slot A: `fastboot --set-active=a`, `fastboot reboot` — userdata was erased, so Android needs a factory reset |
| 6 | low | Day-2 kernel updates: `DEVICE=spacewar bin/omamobile push` from a new build | `boot_b (/dev/sdX) written and verified`, reboot-to omamobile, new `uname -v` | verify mismatch: rerun; phone still answers over SSH | slot A untouched; rerun push |

## Open gaps (do not cross without new steps here)

- The descriptor has no `NV_PARTS` yet, so `tools/nv-backup` refuses this
  device: **no radio, modem or efs/persist work on the phone** until the
  partition list exists and a read-only backup (and its `--check`) has been
  taken.
- `omamobile-boot-update` refuses qcom-abl-v2: kernel updates via `pacman`
  are held back by `omamobile-update`'s preflight gate (safe, by design);
  update from the laptop with `push` until that path lands.
- `bin/omamobile fastboot` (the no-buttons reboot helper) stays refused for
  this ABL; the panic path (`sysrq c` fallthrough) is uncharacterized.
