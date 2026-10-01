import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland
import qs.Commons
import qs.Ui

// zmk-layer-hud in Omarchy's bar: the keyboard glyph, full while the HUD is shown, dimmed while
// it is hidden (still counting), crossed out when it is not running. A click shows or hides it,
// or starts it; a right-click offers the same and Quit.
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

  function run(verb) {
    const env = ["env", "ZMKHUD_STATE=" + root.stateDir, root.command]
    Quickshell.execDetached((verb === "start" ? root.startWith : []).concat(env, [verb]))
  }

  // The right-click menu. The bar keeps one popup open at a time: it asks the one open to close.
  function openMenu() {
    if (root.bar && root.bar.requestPopout) root.bar.requestPopout(root)
    menu.visible = true
  }
  function closeMenu() {
    menu.visible = false
    if (root.bar && root.bar.releasePopout) root.bar.releasePopout(root)
  }
  function closeForPopoutSwitch() { menu.visible = false }

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
      if (mouseButton === Qt.RightButton) {
        if (menu.visible) root.closeMenu(); else root.openMenu()
      } else if (mouseButton === Qt.LeftButton) {
        root.run(root.hud === "stopped" ? "start" : "toggle")
      }
    }
  }

  PopupWindow {
    id: menu
    visible: false
    color: "transparent"
    anchor.item: root
    anchor.edges: root.bar && root.bar.position === "bottom" ? Edges.Top : Edges.Bottom
    anchor.gravity: root.bar && root.bar.position === "bottom" ? Edges.Top : Edges.Bottom
    implicitWidth: 150
    implicitHeight: items.implicitHeight + 8

    Rectangle {
      anchors.fill: parent
      color: root.bar ? root.bar.background : "#1a1b26"
      border.color: Qt.rgba(1, 1, 1, 0.12)
      border.width: 1

      Column {
        id: items
        anchors.fill: parent
        anchors.margins: 4

        Repeater {
          model: root.hud === "stopped" ? [["Start HUD", "start"]]
            : [[root.hud === "shown" ? "Hide HUD" : "Show HUD", root.hud === "shown" ? "hide" : "show"],
               ["Quit HUD", "stop"]]
          delegate: Rectangle {
            required property var modelData
            width: items.width
            height: 26
            color: row.containsMouse ? Qt.rgba(1, 1, 1, 0.1) : "transparent"
            Text {
              anchors.verticalCenter: parent.verticalCenter
              x: 8
              text: modelData[0]
              color: root.bar ? root.bar.foreground : "white"
              font.family: root.bar ? root.bar.fontFamily : "monospace"
              font.pixelSize: 12
            }
            MouseArea {
              id: row
              anchors.fill: parent
              hoverEnabled: true
              onClicked: { root.closeMenu(); root.run(modelData[1]) }
            }
          }
        }
      }
    }
  }

  // A click anywhere else closes the menu.
  HyprlandFocusGrab {
    windows: [menu]
    active: menu.visible
    onCleared: root.closeMenu()
  }
}
