// SPDX-License-Identifier: MIT
import QtQuick

// Stand-in for Quickshell's Hyprland global: Omarchy 4.5's Style imports the
// module but binds nothing of it, so an empty object is enough for the lock
// to load (test/lock-glance).
QtObject {
}
