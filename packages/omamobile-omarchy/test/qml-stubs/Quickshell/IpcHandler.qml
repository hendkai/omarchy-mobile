// SPDX-License-Identifier: MIT
import QtQuick

// Stand-in for Quickshell's IpcHandler (and so Omarchy's ShellIpc base): the
// target and its functions only have to exist for the shell to load; the
// test calls the handlers' functions itself, no socket is involved.
QtObject {
  id: handler

  property string target: ""
  property bool enabled: true

  function functionNames() {
    var names = []
    for (var key in handler) {
      if (typeof handler[key] === "function" && key !== "functionNames") names.push(key)
    }
    return names
  }
}
