-- zmk-layer-hud in Noctalia's bar: the keyboard glyph with a WPM beside it -- the live one, or the
-- session's average or top, as a right-click picked -- full while the HUD is shown, dimmed while it
-- is hidden (still counting); the glyph crossed out when it is not running. A click shows or hides
-- the HUD, or starts it. A HUD from another copy of zmk-layer-hud is that copy's to show, hide or
-- stop, which this copy's command line refuses: the glyph stays crossed out, and the tooltip says
-- where it runs. Resting the pointer on it shows the HUD's stats: what a click does, the session's
-- name, and the same rows the HUD shows (`stats:` in its config).
--
-- What the HUD says -- shown or hidden, the WPMs, the session, its stats -- is $STATE/panel.json
-- (host/panelstate.py), read again every second. Whether a HUD stands behind that file is the
-- command line's to say (`status --json`, panelstate.hud): asked when panel.json names a new pid,
-- when it goes away, and every third second after, for a panel.json a HUD that died left behind
-- says something untrue.
--
-- `zmk-layer-hud menubar enable` writes the three marks below in: the shell does not start commands
-- through a login shell, so neither PATH nor XDG_STATE_HOME can be counted on. Anything that is not
-- an absolute path means a hand-copied plugin, which falls back to the defaults.

local COMMAND = "__ZMK_LAYER_HUD_COMMAND__"
local STATE = "__ZMK_LAYER_HUD_STATE__"
local START_WITH = "__ZMK_LAYER_HUD_START_WITH__"

if COMMAND:sub(1, 1) ~= "/" then
  COMMAND = noctalia.expandPath("~/.local/bin/zmk-layer-hud")
end
if STATE:sub(1, 1) ~= "/" then
  local base = noctalia.getenv("XDG_STATE_HOME") or noctalia.expandPath("~/.local/state")
  STATE = noctalia.getenv("ZMKHUD_STATE") or (base .. "/zmk-layer-hud")
end
if START_WITH:find("__", 1, true) then
  START_WITH = ""            -- hand-copied: no uwsm-app to keep a started HUD out of the shell's cgroup
end

local ORDER = { "current", "average", "top" }
local status = nil           -- the last `status --json`: running, said, icon, where, wpm_choice
local panel = nil            -- the last panel.json: pid, shown, wpm, avg_wpm, top_wpm, session, stats
local askedPid = 0           -- the pid (or 0) `status --json` was last asked about
local ticks = 0
local render = function() end

local function shq(s)
  return "'" .. tostring(s):gsub("'", "'\\''") .. "'"
end

-- The command line's word on the HUD. A panel.json no HUD panel stands behind -- one left by a HUD
-- that died, its pid handed out again -- stops being taken for a running HUD here.
local function ask()
  noctalia.runAsync("env ZMKHUD_STATE=" .. shq(STATE) .. " " .. shq(COMMAND) .. " status --json",
    function(res)
      if res.exitCode ~= 0 then
        return
      end
      local h = noctalia.json.decode(res.stdout)
      if type(h) ~= "table" then
        return
      end
      status = h
      render()
    end)
end

local function run(verb, ...)
  local extra = { ... }
  local pre = verb == "start" and START_WITH ~= "" and (START_WITH .. " ") or ""
  noctalia.runAsync(pre .. "env ZMKHUD_STATE=" .. shq(STATE) .. " " .. shq(COMMAND) .. " " .. verb
    .. (#extra > 0 and (" " .. table.concat(extra, " ")) or ""))
end

-- The number the bar and the tooltip show for a choice: an em dash while there is none yet (no
-- average before ten seconds of typing, no top before a full window of it).
local function wpmOf(choice)
  if not panel then
    return "—"
  end
  local n
  if choice == "average" then
    n = panel.avg_wpm
  elseif choice == "top" then
    n = panel.top_wpm
  else
    n = panel.wpm
  end
  if type(n) ~= "number" then
    return "—"
  end
  return tostring(n)
end

render = function()
  local h = status
  if not h or not h.running or not h.said then
    barWidget.setGlyph("keyboard-off")
    barWidget.setText("")
    barWidget.setColor("on_surface_variant", "script")
    barWidget.setGlyphColor("on_surface_variant", "script")
    barWidget.setTooltip("ZMK layer HUD is not running: click to start it")
    return
  end
  if h.icon == "another copy" then
    barWidget.setGlyph("keyboard-off")
    barWidget.setText("")
    barWidget.setColor("on_surface_variant", "script")
    barWidget.setGlyphColor("on_surface_variant", "script")
    barWidget.setTooltip("ZMK layer HUD: the one running is " .. (h.where or "another copy")
      .. "'s to show, hide or stop")
    return
  end
  local shown = type(panel) == "table" and panel.shown == true
  barWidget.setGlyph("keyboard")
  barWidget.setText(wpmOf(h.wpm_choice or "current"))
  if shown then
    barWidget.setColor("on_surface")           -- back to the widget's own colour
    barWidget.setGlyphColor("on_surface")
  else
    barWidget.setColor("on_surface_variant", "script")
    barWidget.setGlyphColor("on_surface_variant", "script")
  end
  local rows = {
    { key = "HUD", value = shown and "shown: a click hides it, and it keeps counting"
      or "hidden and counting: a click shows it" },
  }
  if type(panel) == "table" then
    if type(panel.session) == "string" and panel.session ~= "" then
      rows[#rows + 1] = { key = "Session", value = panel.session }
    end
    if type(panel.stats) == "table" then
      for _, row in ipairs(panel.stats) do
        if type(row) == "table" then
          rows[#rows + 1] = { key = tostring(row[1] or ""), value = tostring(row[2] or "") }
        end
      end
    end
  end
  barWidget.setTooltip(rows)
end

function update()
  noctalia.setUpdateInterval(1000)
  ticks = ticks + 1
  local raw = noctalia.readFile(STATE .. "/panel.json")
  local st = nil
  if raw then
    st = noctalia.json.decode(raw)
  end
  if type(st) == "table" and type(st.pid) == "number" and type(st.shown) == "boolean" then
    panel = st
    if st.pid ~= askedPid then
      askedPid = st.pid      -- a HUD has just come (or a new one): asked now, not at the next tick
      ask()
    end
  else
    panel = nil
    if askedPid ~= 0 then
      askedPid = 0
      ask()
    end
  end
  if ticks % 3 == 1 then
    ask()                    -- the standing question: every third second, first of them at once
  end
  render()
end

function onClick()
  if status and status.running and status.said then
    run("toggle")
  else
    run("start")
  end
end

-- Which WPM the bar shows, around the three: the live one, the session's average or its top. The
-- same `menubar wpm` the macOS icon and the Omarchy widget set it through; the next tick reads the
-- answer back from `status --json`.
function onRightClick()
  local choice = (status and status.wpm_choice) or "current"
  local at = 1
  for i, name in ipairs(ORDER) do
    if name == choice then
      at = i
    end
  end
  local next_choice = ORDER[at % #ORDER + 1]
  run("menubar", "wpm", next_choice)
  if status then
    status.wpm_choice = next_choice
  end
  render()
end
