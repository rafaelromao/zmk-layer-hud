// zmk-layer-hud in Omarchy's bar: the keyboard glyph, full while the HUD is shown, dimmed while
// it is hidden (still counting), crossed out when it is not running. A click shows or hides it,
// or starts it. Installed by `zmk-layer-hud menubar enable`, which writes the command and the
// state directory into this plugin's defaults.
//
// The HUD says whether it is shown in <stateDir>/panel.json (host/panelstate.py), written whole
// by a rename -- which can drop a file watch, so a timer reads it again as well.
import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root

  property var bar: null
  property string moduleName: ""
  property var settings: null

  function setting(key, fallback) {
    const value = root.settings ? root.settings[key] : undefined
    return value === undefined || value === null || value === "" ? fallback : String(value)
  }

  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string command: setting("command", home + "/.local/bin/zmk-layer-hud")
  readonly property string stateDir: setting("stateDir",
    (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/zmk-layer-hud")
  readonly property var startWith: setting("startWith", "").split(" ").filter(w => w.length)

  property string hud: "stopped"      // shown | hidden | stopped
  property int pid: 0

  readonly property bool vertical: root.bar ? root.bar.vertical === true : false
  readonly property int barSize: root.bar ? Number(root.bar.barSize) : 26

  implicitWidth: root.vertical ? root.barSize : glyph.implicitWidth + 15
  implicitHeight: root.vertical ? glyph.implicitHeight + 12 : root.barSize

  function read(text) {
    try {
      const st = JSON.parse(text)
      root.pid = st.pid
      root.hud = st.shown ? "shown" : "hidden"
    } catch (e) {
      root.stopped()
    }
  }
  function stopped() { root.hud = "stopped"; root.pid = 0 }

  function tip() {
    if (root.hud === "shown") return "ZMK layer HUD: click to hide it (it keeps counting)"
    if (root.hud === "hidden") return "ZMK layer HUD, hidden and counting: click to show it"
    return "ZMK layer HUD is not running: click to start it"
  }

  FileView {
    id: panelFile       // not `state`: that is every Item's own property
    path: root.stateDir + "/panel.json"
    watchChanges: true
    onFileChanged: reload()
    onLoaded: root.read(text())
    onLoadFailed: root.stopped()
  }

  // A HUD that died without a word leaves its panel.json behind: its pid says whether it lives.
  Process {
    id: alive
    command: ["kill", "-0", String(root.pid)]
    onExited: (code, status) => { if (code !== 0) root.stopped() }
  }

  Timer {
    interval: 3000
    running: true
    repeat: true
    onTriggered: {
      panelFile.reload()
      if (root.pid > 0 && !alive.running) alive.running = true
    }
  }

  Text {
    id: glyph
    anchors.centerIn: parent
    color: root.bar ? root.bar.foreground : "white"
    font.family: root.bar ? root.bar.fontFamily : "monospace"
    font.pixelSize: 14
    opacity: root.hud === "shown" ? 1 : 0.45
    text: String.fromCodePoint(root.hud === "stopped" ? 0xF0310 : 0xF030C)   // nf-md-keyboard(-off)
  }

  MouseArea {
    anchors.fill: parent
    hoverEnabled: true
    onClicked: {
      const env = ["env", "ZMKHUD_STATE=" + root.stateDir, root.command]
      if (root.hud === "stopped")
        Quickshell.execDetached(root.startWith.concat(env, ["start"]))
      else
        Quickshell.execDetached(env.concat(["toggle"]))
    }
    onEntered: if (root.bar) root.bar.showTooltip(root, root.tip())
    onExited: if (root.bar) root.bar.hideTooltip(root)
  }
}
