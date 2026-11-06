// SPDX-License-Identifier: MIT
pragma Singleton
import QtQuick

// Stand-in for Quickshell's Pipewire singleton: Commons/UntypedInput reads
// defaultAudioSource; null is "none", as on a machine without PipeWire.
QtObject {
  property var defaultAudioSource: null
  property var defaultAudioSink: null
}
