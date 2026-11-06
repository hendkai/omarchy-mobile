// SPDX-License-Identifier: MIT
import QtQuick

// Stand-in for Quickshell's SplitParser: the test's Process stub feeds it the
// responder's whole output as .text, then streamFinished(); this emits read()
// once per line, as the real one does per chunk.
QtObject {
  id: parser

  property string text: ""
  property string splitMarker: "\n"

  signal read(string data)
  signal streamFinished()

  onTextChanged: {
    if (!text) return
    var lines = text.split("\n")
    for (var i = 0; i < lines.length; i++) {
      if (lines[i] !== "" || i < lines.length - 1) parser.read(lines[i])
    }
  }
}
