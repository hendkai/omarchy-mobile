// SPDX-License-Identifier: MIT
import QtQuick

// Stand-in for Quickshell's Singleton root type: Omarchy 4.5's Commons use it
// for shared singletons (AudioNodes, InputDevices, Style, I18n, ...), with
// their Processes, FileViews and Timers as plain children. The type only has
// to exist and carry properties and children; the test drives those itself.
QtObject {
  default property list<QtObject> data
}
