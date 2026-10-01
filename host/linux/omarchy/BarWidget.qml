import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland
import qs.Commons
import qs.Ui

// zmk-layer-hud in Omarchy's bar: the live WPM and the keyboard glyph, full while the HUD is shown,
// dimmed while it is hidden (still counting); the glyph crossed out when it is not running. A click shows or hides it,
// or starts it; a right-click offers the same and Quit, each beside its global shortcut.
//
// The shortcuts are Hyprland binds that `zmk-layer-hud start` writes (host/shortcuts.py); their
// labels are in <state>/shortcuts.json, written with them.
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
  property var keys: ({})             // {"toggle": "Ctrl+Alt+L", "power": "Ctrl+Alt+Super+L"}
  property int pid: 0
  property int wpm: 0

  function read(text) {
    try {
      const st = JSON.parse(text)
      root.pid = st.pid
      root.wpm = typeof st.wpm === "number" ? st.wpm : 0
      root.hud = st.shown ? "shown" : "hidden"
    } catch (e) {
      root.stopped()
    }
  }
  function stopped() { root.hud = "stopped"; root.pid = 0; root.wpm = 0 }

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

  readonly property string glyph: String.fromCodePoint(root.hud === "stopped" ? 0xF0310 : 0xF030C)   // nf-md-keyboard(-off)

  implicitWidth: content.width + 16
  implicitHeight: button.implicitHeight

  FileView {
    id: panelFile       // not `state`: that is every Item's own property
    path: root.stateDir + "/panel.json"
    watchChanges: true
    onFileChanged: reload()
    onLoaded: root.read(text())
    onLoadFailed: root.stopped()
  }

  FileView {
    id: keysFile
    path: root.stateDir + "/shortcuts.json"
    watchChanges: true
    onFileChanged: reload()
    onLoaded: { try { root.keys = JSON.parse(text()) } catch (e) { root.keys = ({}) } }
    onLoadFailed: root.keys = ({})
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
  // widget is placed. It is the button -- hover, press, tooltip -- and the row over it is what it
  // shows, drawn here so the glyph can be centred by its ink: a Nerd Font glyph is drawn wider than
  // the room its text takes, so text centred by that room looks off to one side.
  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    pressable: true
    labelVisible: false
    text: root.glyph            // not drawn (the row below is); there so the button never takes itself for empty
    dimmed: root.hud !== "shown"
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

  TextMetrics {
    id: ink
    font: glyphText.font
    text: root.glyph
  }

  // The live WPM, then the glyph: the number on the left, while the HUD runs, shown or hidden.
  Row {
    id: content
    anchors.centerIn: parent
    spacing: 5
    opacity: root.hud === "shown" ? 1 : 0.5

    Text {
      anchors.verticalCenter: parent.verticalCenter
      visible: root.hud !== "stopped"
      text: String(root.wpm)
      color: root.bar ? root.bar.foreground : "white"
      font.family: root.bar ? root.bar.fontFamily : "monospace"
      font.pixelSize: 12
    }

    // Halfway between the room the glyph's text takes and the width of its ink, and shifted half
    // as far: by the room alone the glyph sat off to one side, by the ink alone too far the other.
    Item {
      anchors.verticalCenter: parent.verticalCenter
      width: Math.ceil((ink.tightBoundingRect.width + ink.advanceWidth) / 2)
      height: glyphText.implicitHeight
      Text {
        id: glyphText
        x: -ink.tightBoundingRect.x / 2
        text: root.glyph
        color: root.bar ? root.bar.foreground : "white"
        font.family: root.bar ? root.bar.fontFamily : "monospace"
        font.pixelSize: 14
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
    implicitWidth: 260
    implicitHeight: items.implicitHeight + 8

    Rectangle {
      anchors.fill: parent
      // The bar's own colour, opaque: a transparent bar would leave the menu see-through.
      color: root.bar ? Qt.rgba(root.bar.background.r, root.bar.background.g, root.bar.background.b, 1) : "#1a1b26"
      border.color: Qt.rgba(1, 1, 1, 0.12)
      border.width: 1

      Column {
        id: items
        anchors.fill: parent
        anchors.margins: 4

        Repeater {
          // The third of each is the shortcut that does the same.
          model: root.hud === "stopped" ? [["Start HUD", "start", "power"]]
            : [[root.hud === "shown" ? "Hide HUD" : "Show HUD", root.hud === "shown" ? "hide" : "show", "toggle"],
               ["Quit HUD", "stop", "power"]]
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
            Text {
              anchors.verticalCenter: parent.verticalCenter
              anchors.right: parent.right
              anchors.rightMargin: 8
              text: root.keys[modelData[2]] || ""
              opacity: 0.5
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
