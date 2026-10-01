import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// zmk-layer-hud in Omarchy's bar: the keyboard glyph, full while the HUD is shown, dimmed while
// it is hidden (still counting), crossed out when it is not running. A click shows or hides it,
// or starts it.
//
// The HUD says whether it is shown in <state>/panel.json (host/panelstate.py), written whole by a
// rename -- which can drop a file watch, so a timer reads it again as well.
BarWidget {
  id: root
  moduleName: "rafaelromao.zmk-layer-hud"

  // `zmk-layer-hud menubar enable` writes these in: the shell does not start commands through a
  // login shell, so neither PATH nor XDG_STATE_HOME can be counted on. Anything that is not an
  // absolute path means a hand-copied plugin, which falls back to the defaults.
  readonly property string installedCommand: "__ZMK_LAYER_HUD_COMMAND__"
  readonly property string installedState: "__ZMK_LAYER_HUD_STATE__"
  readonly property string installedStartWith: "__ZMK_LAYER_HUD_START_WITH__"
  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string command: installedCommand.charAt(0) === "/"
    ? installedCommand : home + "/.local/bin/zmk-layer-hud"
  readonly property string stateDir: installedState.charAt(0) === "/"
    ? installedState : (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/zmk-layer-hud"
  // What a start runs through (uwsm-app --), so a HUD the bar started outlives a shell restart.
  readonly property var startWith: installedStartWith.indexOf("__") === 0
    ? [] : installedStartWith.split(" ").filter(w => w.length)

  property string hud: "stopped"      // shown | hidden | stopped
  property int pid: 0

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

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

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

  // A direct child, not a Loader's: bars style their widgets by walking the tree once when the
  // widget is placed.
  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    pressable: true
    dimmed: root.hud !== "shown"
    text: String.fromCodePoint(root.hud === "stopped" ? 0xF0310 : 0xF030C)   // nf-md-keyboard(-off)
    tooltipText: root.hud === "shown" ? "ZMK layer HUD: click to hide it (it keeps counting)"
      : root.hud === "hidden" ? "ZMK layer HUD, hidden and counting: click to show it"
      : "ZMK layer HUD is not running: click to start it"
    onPressed: (mouseButton) => {
      if (mouseButton !== Qt.LeftButton) return
      const env = ["env", "ZMKHUD_STATE=" + root.stateDir, root.command]
      if (root.hud === "stopped")
        Quickshell.execDetached(root.startWith.concat(env, ["start"]))
      else
        Quickshell.execDetached(env.concat(["toggle"]))
    }
  }
}
