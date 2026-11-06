# spacewar P0 runbook: stock inventory, calibration backup, firmware source

Read-only against the phone, every step. Nothing here flashes, erases or
writes any partition; the only writes are to `~/android/reference/` on the
Mac mini. Preconditions checked at the top; if one fails, stop and record.
Working directory: the wrapper repo (`spacewar-linux`), `bin/omamobile` from
`omarchy-mobile/`. Bash on the Mac mini: `/opt/homebrew/bin/bash` (5.3) —
`/bin/bash` is 3.2 and too old for the tools.

## S0. Precondition answers (from the owner, before touching the phone)

- Phone attached per USB to the Mac mini (direct port, no unpowered hub).
- Bootloader unlocked already? (Decides the backup path in S3.)
- Second device — daily data may be lost? (Never assumed; unlock wipes.)
- If Android boots: USB debugging enabled, RSA prompt accepted.

## S1. Stock facts (safe in any state)

```sh
fastboot devices                       # in fastboot: serial must show
fastboot getvar all 2>&1 | tee /tmp/getvar.txt | grep -Ei "unlocked|current-slot|slot-(a|b)|(a|b):successful|kernel|version-bootloader"
#   → record: unlocked yes/no, current slot, slot success flags, bootloader version
```

If Android boots instead: `bin/omamobile capture spacewar-stock` (getprop,
by-name partition layout, cmdline, vendor module list; the root extras need
a rooted phone). The build fingerprint (`ro.build.fingerprint`) decides
whether the staged stock image matches — see NOTES.md's build chain; an
older on-phone build needs that build's full OTA downloaded before any
rollback plan is written.

## S2. Identify the calibration partitions (read-only, in booted Android)

```sh
adb shell 'ls -l /dev/block/by-name/' | grep -E 'efs|modem|persist|devinfo'
```

All seven (`efs efs_backup modem_userdata persist modem_a modem_b devinfo`)
must exist exactly once. Sizes go into the P0 notes (no contents, no hashes
of contents in the repo — the hashes live in the backup's SHA256SUMS).

## S3. Calibration backup (pick the first path that applies)

1. **Rooted Android:** run the dd-over-adb variant of `tools/nv-backup`
   (`PHONE_SSH` pointed at an adb wrapper, `/opt/homebrew/bin/bash
   tools/nv-backup -d spacewar DIR`).
2. **Unlocked bootloader, no root:** boot our initramfs **from RAM** —
   `fastboot boot kernel-spacewar/out/images/boot.img` (ABL boots it without
   flashing; the init finds no Arch root and stays in its guarded shell:
   everything `ro`, USB SSH up). Then `tools/phone-ssh` works and
   `/opt/homebrew/bin/bash tools/nv-backup -d spacewar DIR` runs as
   designed, with the storage guard enforcing read-only. **Stop condition:**
   if `fastboot boot` is refused, do not try anything else — record and stop.
3. **Locked bootloader:** the partitions are unreadable and the only way in
   (`fastboot flashing unlock`) wipes the phone. Stop; the owner decides.

Backup destination: `~/android/reference/spacewar/nv/<date>/` (created by
nv-backup, mode 700; holds IMEI — never committed, never shared). After it:
`tools/nv-backup -d spacewar --check DIR` must print SAME for all NV_PARTS.

Rebooting back into Android afterwards is the owner's call (AGENTS §1); note
that a stock boot dirties `efs`/`persist`/`modem_userdata` (fresh backup
afterwards, keep both).

## S4. Firmware source (no phone needed — status)

`~/android/reference/spacewar/stock/`: final-build partition images
(260618-1045, 22/22 relevant SHA256 OK) and the V3.2-250926-1631 full OTA
zip (payload.bin with every partition, incl. the logical ones
`extract-spacewar-firmware` needs). When the phone's exact build is known:
same-build full OTA if it differs from what's staged.

## Safety reminders (AGENTS.md §1)

Never flash bootloader, radio, persist or calibration. Slot A stays stock.
`fastboot boot` boots from RAM; `fastboot flash` of anything is out of scope
for P0 entirely. Identifying values (serial, IMEI, MAC) never go in the repo
or commits.
