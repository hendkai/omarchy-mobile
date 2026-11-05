# omamobile-device-spacewar

Omarchy Mobile device support for the Nothing Phone (1) (`nothing,spacewar`,
Snapdragon 778G+ / SM7325-AE): the ALSA UCM (HiFi and the CS-Voice call
routing), the feedbackd theme that lights the Glyph LEDs, the haptics udev
rule, the Bluetooth SCO offload, the deep-only suspend policy and the soft-ISP
camera tuning files. The kernel is `linux-omamobile-spacewar`
(sc7280-mainline + the spacewar patches); the GPU (Adreno 642L, freedreno) is
mainline and covered by the `mesa` build.

## Attribution

The device configuration here is distilled from the spacewar mainline work:
the sc7280-mainline kernel project (notably the Nothing Phone (1) device
tree), the spacewar-pmos (woodyst) device tree this package carries files
from, and postmarketOS' `device-nothing-spacewar` (the libssc mount-matrix
rule). Those projects did the hardware bring-up; this package only packs
their configuration for Omarchy Mobile.

## What is deliberately not here yet

- The Wi-Fi regulatory domain (`cfg80211 ieee80211_regdom=`) is the owner's
  country, not the image's; set it with `iw reg set` or the desktop's network
  settings.
- The full telephony WirePlumber stack (hands-free profile forcing, the
  call-time suspend policy) arrives with the telephony milestone.
- The suspend/wake watchdogs and diagnostics (battery wake sources, BT-UART
  wake, IPA stall detector) are bring-up tools, not image defaults.
