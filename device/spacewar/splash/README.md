# spacewar early-boot splash (fbsplash)

Omarchy-branded picture drawn into the device's initramfs and blitted to the
framebuffer by the **shared first-light init** (`omarchy-mobile/initramfs/init`)
*before* Arch starts, until Plymouth takes over. The display under Linux is
MSM-DRM + `panel-visionox-rm692e5` (see `device/spacewar/device.conf`):
`PANEL_SIZE=1080x2400`, portrait.

## How the frame is made

| Step | What happens |
|---|---|
| 1 | `make-splash.sh` renders one BGRA frame at `PANEL_SIZE` (1080×2400). |
| 2 | The frame is gzip `-9n` → `splash.bgra.gz` (≈20 kB gzipped / 10,368,000 raw bytes). |
| 3 | `bin/omamobile initramfs` (with `SPLASH_BGRA_GZ=<abs path>`) copies it to `stage/splash.bgra.gz`, which is packed as `/splash.bgra.gz` in the initramfs. |
| 4 | `initramfs/init` (shared by every device) sees `splash` on `/proc/cmdline` and writes the frame to `/dev/fb0` when the board matches. |

The canonical source is the **Omarchy Plymouth theme logo**
`/usr/share/plymouth/themes/omarchy/logo.png` (MIT, part of Omarchy). When
`magick` is installed on the build host and that logo exists,
`make-splash.sh` uses ImageMagick exactly like `bin/omamobile initramfs` does.
On a host without the theme or ImageMagick the script falls back to a supplied
PNG, or to the built-in placeholder shown below.

## Build your own frame (choose one)

```sh
# A) On an Omarchy build machine (the Omarchy theme + ImageMagick present):
make-splash.sh omarchy-mobile/device/spacewar/splash

# B) From a supplied PNG (the Omarchy logo, a theme artwork, any 8-bit RGBA):
make-splash.sh omarchy-mobile/device/spacewar/splash src/logo.png

# C) Built-in placeholder (fallback; NOT the trademarked Omarchy mark):
make-splash.sh omarchy-mobile/device/spacewar/splash
```

After step 1-2, build the initramfs with the splash:

```sh
cd /Volumes/MacMiniExt2TB/Programmieren/spacewar-linux
DEVICE=spacewar SPLASH_BGRA_GZ=$(pwd)/omarchy-mobile/device/spacewar/splash/splash.bgra.gz \
  omarchy-mobile/bin/omamobile initramfs
```

`bin/omamobile initramfs` validates that the file is a gzip whose decompressed
size is exactly `PANEL_SIZE × 4` bytes, then packs it as `stage/splash.bgra.gz`.

## Kernel console: what the CMDLINE does

`device/spacewar/device.conf` sets:

```
CMDLINE="console=ttyMSM0,115200n8 earlycon=qcom_geni,0x994000 clk_ignore_unused pd_ignore_unused printk.devkmsg=on panic=10 quiet splash loglevel=0 vt.global_cursor_default=0"
```

- `console=ttyMSM0` → kernel messages go to the debug UART (earlycon), *not*
  the on-screen console.
- `splash` → the shared init hides the console and draws the fbsplash.
- `quiet loglevel=0` → only KERN_EMERG is ever printed to any console.
- `vt.global_cursor_default=0` → VT cursor off.
- **`printk.devkmsg=on panic=10` kept on purpose** for post-mortem
  (`dmesg`, panic timeout). Remove `quiet`/`loglevel` if you must eyeball boot
  errors on the debug UART.

## Verification

```sh
# frame must be exactly 1080*2400*4 raw bytes, gzip OK, and non-trivial
python3 -c "import gzip; d=gzip.open('omarchy-mobile/device/spacewar/splash/splash.bgra.gz','rb').read(); assert len(d)==1080*2400*4; print('OK', len(d))"
gzip -t omarchy-mobile/device/spacewar/splash/splash.bgra.gz && echo "gzip OK"
```

## License note (Omarchy assets)

- **Omarchy itself is MIT** (see `omarchy-mobile/LICENSE`). The Plymouth theme
  and its `logo.png` are part of the Omarchy project and carry the same MIT
  license (copyright: Derek Ross and Omarchy Mobile contributors).
- The **early-boot splash** (`splash.bgra.gz`) is generated from the theme logo
  by `make-splash.sh`. The built-in fallback is a placeholder that I created —
  it is *not* the trademarked Omarchy mark and is only a stand-in until the
  theme logo is installed on the build host.
- `splash.bgra.gz` is a generated render of the MIT-licensed theme logo; it is
  derived work, not a separate asset. Attribution: *Omarchy (MIT), Derek Ross
  and Omarchy Mobile contributors*.
- Nothing here is written to the bootloader, `logo`/`splash` partitions or any
  ABL-managed partition (see `AGENTS.md` safety rules).

## Dependencies / preconditions

- `omarchy-mobile/initramfs/init` (shared) — fine, tracked in this repo.
- `INITRAMFS_MODULES` for the display stack: `fsa4480 msm panel-visionox-rm692e5
  spi-geni-qcom fts_tp` (already in `device/spacewar/device.conf`).
- The panel/drm drivers are loaded *before* the init draws the splash, which is
  why `INITRAMFS_FB_WAIT=1` is set: the init waits (up to 3 s) for `/dev/fb0`.
- Device symbol used by the init's board-matching: `COMPATIBLE=nothing,spacewar`.
