#!/bin/sh
# SPDX-License-Identifier: MIT
#
# spacewar early-boot splash (fbsplash) generator.
#
# Omarchy Mobile's first-light init draws this picture straight onto the
# framebuffer (/dev/fb0) as soon as "splash" is on the kernel command line
# (initramfs/init, shared by every device), then Plymouth takes over with
# the real Omarchy theme animation. The picture is a single BGRA frame at the
# device's PANEL_SIZE, gzip'd -9n, and is packed as /splash.bgra.gz into the
# initramfs by `bin/omamobile initramfs`.
#
# Output: splash.bgra.gz  (raw BGRA, PANEL_SIZE, gzipped)
#   raw bytes = WIDTH*HEIGHT*4  (here 1080*2400*4 = 10368000)

set -eu

REPO_ROOT=$(CDPATH= cd "$(dirname "$0")/../.." && pwd)
OUT_DIR="${1:-${REPO_ROOT}/device/spacewar/splash}"
PANEL_SIZE="${PANEL_SIZE:-1080x2400}"
OUT_FILE="${OUT_DIR}/splash.bgra.gz"
LOG_PREFIX="make-splash:"

usage() {
  echo "usage: $0 [OUT_DIR]  (PANEL_SIZE defaults to 1080x2400)" >&2
  exit 1
}

if [ "$#" -gt 1 ]; then usage; fi
[ -n "$1" ] && OUT_DIR="$1"
mkdir -p "$OUT_DIR" || { echo "$LOG_PREFIX cannot create $OUT_DIR" >&2; exit 1; }

case $PANEL_SIZE in
  ''|*[!0-9x]*) echo "$LOG_PREFIX PANEL_SIZE must be WxH" >&2; exit 1;;
esac
W=${PANEL_SIZE%x*}
H=${PANEL_SIZE#*x}
raw=$((W * H * 4))

# ---------------------------------------------------------------------------
# Canonical source: the Omarchy Plymouth theme logo, installed on an Omarchy
# machine at /usr/share/plymouth/themes/omarchy/logo.png. Rendering is done
# with ImageMagick (magick) the same way `bin/omamobile initramfs` does it:
#   logo on #1a1b26 background, centred, BGRA, 8-bit.
# ---------------------------------------------------------------------------
if command -v magick >/dev/null 2>&1 && [ -f /usr/share/plymouth/themes/omarchy/logo.png ]; then
  echo "$LOG_PREFIX using Omarchy Plymouth theme logo (magick)"
  magick -size "${PANEL_SIZE}" xc:'#1a1b26' \
    /usr/share/plymouth/themes/omarchy/logo.png -gravity center -composite \
    -alpha off -alpha on -depth 8 BGRA:- | gzip -9n > "$OUT_FILE"
  echo "$LOG_PREFIX theme render: $(wc -c < "$OUT_FILE") bytes -> $OUT_FILE"
  exit 0
fi

# ---------------------------------------------------------------------------
# Fallback source: a PNG supplied by the user (the Omarchy logo, a theme
# artwork, or any 8-bit RGBA image). We re-render it at PANEL_SIZE to BGRA.
# ---------------------------------------------------------------------------
SRC_PNG="${2:-}"
if [ -n "$SRC_PNG" ]; then
  if [ ! -f "$SRC_PNG" ]; then
    echo "$LOG_PREFIX no file $SRC_PNG" >&2; exit 1
  fi
  echo "$LOG_PREFIX using supplied PNG $SRC_PNG"
  python3 - "$SRC_PNG" "$OUT_FILE" "$W" "$H" "$raw" <<'PY'
import sys, subprocess, struct
src, out, W, H, raw = sys.argv[1:6]
# Convert to 8-bit BGRA raw via PIL (on a Mac without ImageMagick this is the
# reliable path). gamma 2.2 -> linear-ish sRGB to keep the logo from looking
# washed out on the #1a1b26 background.
from PIL import Image
im = Image.open(src).convert("RGBA")
# If the source is already the theme size we keep it; otherwise scale to fill.
if im.size != (int(W), int(H)):
    im = im.resize((int(W), int(H)), Image.LANCZOS)
bg = Image.new("RGBA", (int(W), int(H)), (0x1a, 0x1b, 0x26, 255))
bg.alpha_composite(im)
arr = list(bg.getdata())  # RGBA order in PIL
# Convert RGBA -> BGRA
buf = bytearray(int(W)*int(H)*4)
for i in range(len(arr)):
    r, g, b, a = arr[i]
    buf[i*4+0] = b; buf[i*4+1] = g; buf[i*4+2] = r; buf[i*4+3] = a
open(out, "wb").write(buf)
PY
  gzip -t "$OUT_FILE" 2>/dev/null || { echo "$LOG_PREFIX $OUT_FILE is not valid gzip" >&2; exit 1; }
  echo "$LOG_PREFIX supplied-PNG render: $(wc -c < "$OUT_FILE") bytes -> $OUT_FILE"
  exit 0
fi

# ---------------------------------------------------------------------------
# Last-resort fallback: draw a clean, Omarchy-branded frame ourselves so the
# device still shows a branded boot picture even on a build host without the
# theme or any logo. This is a placeholder until the real theme logo is
# installed (see runbook). It is NOT the trademarked Omarchy mark.
# ---------------------------------------------------------------------------
echo "$LOG_PREFIX no Omarchy theme logo or input PNG: drawing built-in placeholder" >&2
python3 - "$OUT_FILE" "$W" "$H" "$raw" <<'PY'
import sys
out, W, H, raw = sys.argv[1:5]
W, H = int(W), int(H)
from PIL import Image, ImageDraw
import math
# Dark Tokyo-Night base: the theme's #1a1b26, as a solid RGBA frame.
bg = Image.new("RGBA", (W, H), (0x1a, 0x1b, 0x26, 255))
d = ImageDraw.Draw(bg)
cx, cy = W // 2, H // 2
s = min(W, H) // 2 - 44  # mark size
# Soft teal->indigo glow (concentric rounded rings).
for k, t in enumerate([0.6, 0.8, 1.0]):
    rr = int(34 + (74 - 34) * t)
    gg = int(138 + (92 - 138) * t)
    bb = int(202 + (148 - 202) * t)
    alpha = int(110 * t)
    d.rounded_rectangle([cx - s - 16 + k, cy - s - 16 + k,
                         cx + s + 16 - k, cy + s + 16 - k],
                        radius=s + 16 - k, outline=(rr, gg, bb, alpha), width=2)
# The mark: rounded square + stylized Om glyph (two stacked arcs).
d.rounded_rectangle([cx - s, cy - s, cx + s, cy + s], radius=s,
                    outline=(74, 138, 238, 255), width=max(s // 8, 4))
d.arc([cx - s//2 + 6, cy - s//2 + 16, cx + s//2 - 6, cy + s//2 - 16],
      20, 160, fill=(180, 222, 255, 255), width=max(s // 12, 3))
d.arc([cx - s//2 + 6, cy + 2, cx + s//2 - 6, cy + s//2 - 16],
      200, 340, fill=(180, 222, 255, 255), width=max(s // 12, 3))
d.ellipse([cx - s//3, cy - s//3, cx + s//3, cy + s//3],
          fill=(180, 222, 255, 255))
bg.save(out, format="PNG")
PY
# PIL saves RGBA; convert to BGRA + gzip, exactly like the magick path
python3 - "$OUT_FILE" "$W" "$H" <<'PY'
import sys
out, W, H = sys.argv[1:4]
from PIL import Image
im = Image.open(out).convert("RGBA")
bg = Image.new("RGBA", (int(W), int(H)), (0x1a, 0x1b, 0x26, 255))
bg.alpha_composite(im)
arr = list(bg.getdata())
buf = bytearray(int(W) * int(H) * 4)
for i in range(len(arr)):
    r, g, b, a = arr[i]
    buf[i * 4 + 0] = b; buf[i * 4 + 1] = g; buf[i * 4 + 2] = r; buf[i * 4 + 3] = a
open(out, "wb").write(buf)
PY
gzip -9n < "$OUT_FILE" > "${OUT_FILE}.tmp" && mv "${OUT_FILE}.tmp" "$OUT_FILE"
echo "$LOG_PREFIX built-in placeholder render: $(wc -c < "$OUT_FILE") bytes -> $OUT_FILE"
