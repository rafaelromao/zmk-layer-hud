import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// zmk-layer-hud in Omarchy's bar: a WPM and the keyboard glyph, full while the HUD is shown, dimmed
// while it is hidden (still counting); the glyph crossed out when it is not running. The WPM is the
// live one, or the session's average or top, as the menu picked. A click shows or hides the HUD, or
// starts it. A right-click offers the three WPMs, each with its number now, then the same and Quit,
// each beside its global shortcut. Resting the pointer on it opens the HUD's stats column, laid out
// across: the same tiles the HUD shows (`stats:` in its config), in the shell's own popout.
//
// The shortcuts are Hyprland binds that `zmk-layer-hud start` writes (host/shortcuts.py); their
// labels are in <state>/shortcuts.json, written with them. Which WPM the bar shows is in
// <state>/menubar.json, set through `zmk-layer-hud menubar wpm`, as the macOS icon sets it.
//
// The HUD says whether it is shown, and what its stats column shows, in <state>/panel.json
// (host/panelstate.py), written whole by a rename -- which can drop a file watch, so a timer reads
// it again as well.
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
  property var avgWpm: null           // the session's average and top, once the HUD has said them
  property var topWpm: null
  property string session: ""         // its name, where the HUD's column shows it
  property var rows: []               // the HUD's stats column: [[label, value], ...]
  property string rowsText: ""        // ...as it was read, so the same rows are not taken again
  property string wpmChoice: "current"    // which WPM the bar shows: current | average | top
  property bool menuOpen: false
  property bool hoverOpen: false

  readonly property color popupText: Color.popups.text
  readonly property string fontName: root.bar ? root.bar.fontFamily : Style.font.family

  function read(text) {
    try {
      const st = JSON.parse(text)
      root.pid = st.pid
      root.wpm = typeof st.wpm === "number" ? st.wpm : 0
      root.avgWpm = typeof st.avg_wpm === "number" ? st.avg_wpm : null
      root.topWpm = typeof st.top_wpm === "number" ? st.top_wpm : null
      root.session = typeof st.session === "string" ? st.session : ""
      root.takeRows(Array.isArray(st.stats) ? st.stats : [])
      root.hud = st.shown ? "shown" : "hidden"
    } catch (e) {
      root.stopped()
    }
  }
  function takeRows(rows) {
    const text = JSON.stringify(rows)
    if (text !== root.rowsText) { root.rowsText = text; root.rows = rows }
  }
  function stopped() {
    root.hud = "stopped"; root.pid = 0; root.wpm = 0
    root.avgWpm = null; root.topWpm = null; root.session = ""; root.takeRows([])
  }

  // The number the bar and the menu show for a choice: — while there is none yet (no average
  // before ten seconds of typing, no top before a full window of it).
  function wpmOf(choice) {
    const n = choice === "average" ? root.avgWpm : choice === "top" ? root.topWpm : root.wpm
    return n === null || n === undefined ? "—" : String(n)
  }

  function run(verb, more) {
    const env = ["env", "ZMKHUD_STATE=" + root.stateDir, root.command]
    Quickshell.execDetached((verb === "start" ? root.startWith : []).concat(env, [verb], more || []))
  }

  // The bar keeps one popout open at a time: it asks the one open to close when another opens.
  function close() { root.menuOpen = false }
  function closeForPopoutSwitch() { root.menuOpen = false }

  // The hover panel's owner, through which the bar closes it. PopupCard's own close() would assign
  // its `open`, and with that undo the binding to hoverOpen.
  QtObject {
    id: hoverOwner
    function close() { root.hoverOpen = false }
  }

  // Open once the pointer has rested on the icon, as a tooltip does, and shut a moment after it
  // has left both the icon and the panel. Never over the menu, and only for a HUD that runs and has
  // said what its column shows.
  readonly property bool pointerIn: button.tooltipHovered || hoverCard.containsMouse
  readonly property bool canHover: root.hud !== "stopped" && root.rows.length > 0 && !root.menuOpen
  onPointerInChanged: { hoverTimer.interval = root.pointerIn ? 400 : 150; hoverTimer.restart() }
  onCanHoverChanged: if (!root.canHover) root.hoverOpen = false

  Timer {
    id: hoverTimer
    interval: 400
    onTriggered: root.hoverOpen = root.pointerIn && root.canHover
  }

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

  FileView {
    id: choiceFile
    path: root.stateDir + "/menubar.json"
    watchChanges: true
    onFileChanged: reload()
    onLoaded: {
      try {
        const c = JSON.parse(text()).wpm
        root.wpmChoice = ["current", "average", "top"].indexOf(c) >= 0 ? c : "current"
      } catch (e) {
        root.wpmChoice = "current"
      }
    }
    onLoadFailed: root.wpmChoice = "current"
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
      choiceFile.reload()     // written by a rename too, and maybe not there when the watch began
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
    // While the HUD runs the hover panel says it all. The tooltip says how to start it, or, for a
    // HUD from before it said its stats, what a click does.
    tooltipText: root.hud === "stopped" ? "ZMK layer HUD is not running: click to start it"
      : root.rows.length ? ""
      : root.hud === "shown" ? "ZMK layer HUD: click to hide it (it keeps counting)"
      : "ZMK layer HUD, hidden and counting: click to show it"
    onPressed: (mouseButton) => {
      root.hoverOpen = false
      if (mouseButton === Qt.RightButton) {
        root.menuOpen = !root.menuOpen
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

  // The WPM, then the glyph: the number on the left, while the HUD runs, shown or hidden.
  Row {
    id: content
    anchors.centerIn: parent
    spacing: 5
    opacity: root.hud === "shown" ? 1 : 0.5

    Text {
      anchors.verticalCenter: parent.verticalCenter
      visible: root.hud !== "stopped"
      text: root.wpmOf(root.wpmChoice)
      color: root.bar ? root.bar.foreground : "white"
      font.family: root.fontName
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
        font.family: root.fontName
        font.pixelSize: 14
      }
    }
  }

  // The right-click menu, drawn as the tray draws its menus. Its rows change only with the HUD's
  // state; the numbers in them follow the HUD while it is open.
  readonly property var menuRows: root.hud === "stopped"
    ? [{ kind: "item", text: "Start HUD", verb: "start", shortcut: "power" }]
    : [{ kind: "wpm", text: "Current WPM", choice: "current" },
       { kind: "wpm", text: "Average WPM", choice: "average" },
       { kind: "wpm", text: "Top WPM", choice: "top" },
       { kind: "separator" },
       { kind: "item", text: root.hud === "shown" ? "Hide HUD" : "Show HUD",
         verb: root.hud === "shown" ? "hide" : "show", shortcut: "toggle" },
       { kind: "item", text: "Quit HUD", verb: "stop", shortcut: "power" }]

  PopupCard {
    id: menu
    anchorItem: root
    bar: root.bar
    owner: root
    open: root.menuOpen
    padding: Style.space(8)
    borderColor: Qt.rgba(root.popupText.r, root.popupText.g, root.popupText.b, 0.45)
    contentWidth: menu.fittedContentWidth(Style.space(232))
    contentHeight: menu.fittedContentHeight(menuColumn.implicitHeight, Style.space(420))

    Column {
      id: menuColumn
      anchors.fill: parent
      spacing: 0

      Repeater {
        model: root.menuRows

        delegate: Item {
          id: menuRow
          required property var modelData
          readonly property bool separator: modelData.kind === "separator"
          readonly property bool choice: modelData.kind === "wpm"

          width: menuColumn.width
          implicitHeight: separator ? Style.space(11) : Style.space(30)

          Rectangle {
            visible: menuRow.separator
            anchors.left: parent.left
            anchors.leftMargin: Style.space(10)
            anchors.right: parent.right
            anchors.rightMargin: Style.space(10)
            anchors.verticalCenter: parent.verticalCenter
            height: 1
            color: Color.popups.border
            opacity: 0.45
          }

          Rectangle {
            visible: !menuRow.separator
            anchors.fill: parent
            radius: Math.max(2, Style.cornerRadius)
            color: rowMouse.containsMouse ? Style.hoverFillFor(root.popupText, root.popupText) : "transparent"
          }

          // The WPM the bar shows is checked.
          Text {
            textFormat: Text.PlainText
            visible: menuRow.choice
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            width: Style.space(22)
            horizontalAlignment: Text.AlignHCenter
            text: root.wpmChoice === menuRow.modelData.choice ? "" : ""
            color: root.popupText
            font.family: root.fontName
            font.pixelSize: Style.font.bodySmall
          }

          Text {
            textFormat: Text.PlainText
            visible: !menuRow.separator
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: Style.space(28)
            anchors.right: aside.left
            anchors.rightMargin: Style.space(8)
            text: menuRow.modelData.text || ""
            color: root.popupText
            font.family: root.fontName
            font.pixelSize: Style.font.bodySmall
            elide: Text.ElideRight
          }

          // A WPM's number now, or the shortcut that does what the row does.
          Text {
            id: aside
            textFormat: Text.PlainText
            visible: !menuRow.separator
            anchors.verticalCenter: parent.verticalCenter
            anchors.right: parent.right
            anchors.rightMargin: Style.space(10)
            text: menuRow.choice ? root.wpmOf(menuRow.modelData.choice) : (root.keys[menuRow.modelData.shortcut] || "")
            color: menuRow.choice ? root.popupText : Qt.darker(root.popupText, 1.5)
            font.family: root.fontName
            font.pixelSize: Style.font.bodySmall
            font.bold: menuRow.choice
          }

          MouseArea {
            id: rowMouse
            anchors.fill: parent
            hoverEnabled: true
            enabled: !menuRow.separator
            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
            onClicked: {
              const row = menuRow.modelData
              root.close()
              if (row.kind === "wpm") {
                root.wpmChoice = row.choice          // at once; the file says so too, a moment later
                root.run("menubar", ["wpm", row.choice])
              } else {
                root.run(row.verb)
              }
            }
          }
        }
      }
    }
  }

  // The HUD's stats column, laid out across: a title, the session's name and what a click does,
  // then one tile per row, the number over its label, two rows of them where they are few enough.
  PopupCard {
    id: hoverCard
    anchorItem: root
    bar: root.bar
    owner: hoverOwner
    triggerMode: "hover"
    open: root.hoverOpen
    readonly property real insetX: hoverCard.padding * 2 + Border.left(hoverCard.borderSpec) + Border.right(hoverCard.borderSpec)
    contentWidth: hoverCard.fittedContentWidth(Math.max(tiles.implicitWidth, header.implicitWidth) + insetX)
    contentHeight: hoverCard.fittedContentHeight(cardColumn.implicitHeight)

    Column {
      id: cardColumn
      anchors.fill: parent
      spacing: Style.space(10)

      Item {
        id: header
        width: parent.width
        implicitWidth: Math.max(title.implicitWidth + Style.space(16) + sessionName.implicitWidth, hint.implicitWidth)
        implicitHeight: title.implicitHeight + Style.space(2) + hint.implicitHeight

        Text {
          id: title
          textFormat: Text.PlainText
          text: "ZMK layer HUD"
          color: root.popupText
          font.family: root.fontName
          font.pixelSize: Style.font.subtitle
          font.bold: true
        }

        Text {
          id: sessionName
          textFormat: Text.PlainText
          anchors.right: parent.right
          anchors.baseline: title.baseline
          text: root.session
          color: Qt.darker(root.popupText, 1.5)
          font.family: root.fontName
          font.pixelSize: Style.font.caption
        }

        Text {
          id: hint
          textFormat: Text.PlainText
          anchors.top: title.bottom
          anchors.topMargin: Style.space(2)
          text: root.hud === "shown" ? "Shown: a click hides it, and it keeps counting"
            : "Hidden and counting: a click shows it"
          color: Qt.darker(root.popupText, 1.5)
          font.family: root.fontName
          font.pixelSize: Style.font.caption
        }
      }

      PanelSeparator {
        foreground: root.popupText
      }

      Grid {
        id: tiles
        columns: Math.max(1, Math.min(root.rows.length, Math.max(4, Math.ceil(root.rows.length / 2))))
        spacing: Style.space(6)
        // Every tile as wide as the widest one's number or label needs.
        readonly property real tileWidth: {
          let w = Style.space(64)
          for (let i = 0; i < children.length; i++) w = Math.max(w, children[i].natural || 0)
          return w
        }

        // By count: a number that changes is written into its tile, not a new tile.
        Repeater {
          model: root.rows.length

          delegate: BorderSurface {
            id: tile
            required property int index
            readonly property var row: root.rows[index] || ["", ""]
            readonly property real natural: Math.max(value.implicitWidth, label.implicitWidth) + Style.space(20)

            width: tiles.tileWidth
            height: value.implicitHeight + label.implicitHeight + Style.space(16)
            radius: Style.spacing.labelGap
            color: Style.normalFillFor(root.popupText, Color.accent)
            borderSpec: Border.controlSpec("normal", root.popupText, Color.accent)

            Text {
              id: value
              textFormat: Text.PlainText
              anchors.horizontalCenter: parent.horizontalCenter
              anchors.top: parent.top
              anchors.topMargin: Style.space(7)
              text: tile.row[1]
              color: root.popupText
              font.family: root.fontName
              font.pixelSize: Style.font.title
              font.bold: true
            }

            Text {
              id: label
              textFormat: Text.PlainText
              anchors.horizontalCenter: parent.horizontalCenter
              anchors.top: value.bottom
              anchors.topMargin: Style.space(2)
              text: tile.row[0]
              color: Qt.darker(root.popupText, 1.5)
              font.family: root.fontName
              font.pixelSize: Style.font.caption
            }
          }
        }
      }
    }
  }
}
