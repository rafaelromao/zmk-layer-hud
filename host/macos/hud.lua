-- zmk-layer-hud: macOS host (Hammerspoon).
--
-- Two always-on-top, borderless webviews on the recording display:
--   * hud/index.html  (top-right)    the keymap from your keymap-drawer YAML (see the config);
--                                    keys and combos light as they are typed; the banner names the
--                                    active layer as reported by the keyboard.
--   * hud/keys.html   (bottom-left)  the typed-keys strip.
--
-- Load without touching ~/.hammerspoon (needs `require("hs.ipc")` there once):
--   hs -c "zmkhud = dofile('/ABS/PATH/host/macos/hud.lua')"
--   hs -c "zmkhud.stop()"            -- or the ✕ button on the panel
--
-- One feed: host/hudfeed.py --stdout, run as an hs.task. It converts the keymap-drawer YAML named
-- in ~/.config/zmk-layer-hud/config.yaml into {"kind":"keymap",…} (re-sent when the file changes)
-- and reads the keyboard's raw HID reports (python-hidapi) for {"kind":"layers",…} and every key
-- and modifier event. Hammerspoon only injects those lines into the pages: no event tap, no other
-- source than the keyboard. Input Monitoring: the task inherits Hammerspoon's grant.
--
-- The Linux host is host/linux/ (the same pages, driven over a WebSocket by hudfeed.py).

local M = {}

local HERE = debug.getinfo(1, "S").source:sub(2):match("(.*/)") or "./"
local ROOT = HERE .. "../../"
local PAGES = ROOT .. "hud/"
local HUDFEED = ROOT .. "host/hudfeed.py"

-- panel: 2 hands × (4×62 + 3×6) + 34 gap + 2×14 padding + 2 px border = 598
M.width, M.height = 598, 392
M.margin = 24
M.screen = nil -- hs.screen; nil = the external display, else the primary
M.lastKeys = {}
M.lastLayers = nil
M.lastKeymap = nil
M.python = nil -- resolved in start(): the first python3 that exists in PYTHONS

-- The interpreter needs hidapi and keymap-drawer: the repo's own virtualenv first (make venv),
-- then a system python that happens to have them.
local PYTHONS = { os.getenv("ZMKHUD_PYTHON"), ROOT .. ".venv/bin/python3", "/opt/homebrew/bin/python3",
  "/usr/local/bin/python3", "/usr/bin/python3" }

local wv, kv, readyTimer, ucc, feed, feedTimer
local ready = false
local queue = {}

local function js(code)
  if not wv then return end
  if not ready then queue[#queue + 1] = code; return end
  wv:evaluateJavaScript(code)
end

local function flush()
  ready = true
  for _, code in ipairs(queue) do wv:evaluateJavaScript(code) end
  queue = {}
end

-- ---------- the feed (hudfeed.py) ----------

local feedBuffer = ""

local function onFeedLine(line)
  local ok, msg = pcall(hs.json.decode, line)
  if not ok or type(msg) ~= "table" then
    print("zmkhud: hudfeed said: " .. line)
    return
  end
  if msg.kind == "key" then
    -- The line is already JSON: hand it to both pages verbatim.
    if msg.type == "keyDown" then
      -- zmkhud.lastKeys: the last raw events, newest last, for diagnosis:
      --   hs -c "return table.concat(zmkhud.lastKeys, '\n')"
      M.lastKeys[#M.lastKeys + 1] = os.date("%H:%M:%S ") .. line
      if #M.lastKeys > 10 then table.remove(M.lastKeys, 1) end
    end
    js("hud.key(" .. line .. ")")
    if kv and msg.type == "keyDown" then kv:evaluateJavaScript("keys.key(" .. line .. ")") end
  elseif msg.kind == "layers" and type(msg.ids) == "table" then
    M.lastLayers = os.date("%H:%M:%S ") .. line
    -- An empty Lua table encodes as [] here, which is what setLayers wants.
    js("hud.setLayers(" .. hs.json.encode(msg.ids) .. ")")
  elseif msg.kind == "keymap" then
    M.lastKeymap = os.date("%H:%M:%S ") .. tostring(msg.source)
    js("hud.load(" .. line .. ")")
  end
end

local function startFeed()
  if feed and feed:isRunning() then return end
  if not M.python then
    print("zmkhud: no python3 found (set ZMKHUD_PYTHON or run make venv); nothing will be shown")
    return
  end
  feedBuffer = ""
  local args = { HUDFEED, "--stdout", "--no-ws" }
  if os.getenv("ZMKHUD_CONFIG") then args[#args + 1] = "--config"; args[#args + 1] = os.getenv("ZMKHUD_CONFIG") end
  feed = hs.task.new(M.python, function(exit, out, err)
    print(string.format("zmkhud: hudfeed exited (%s)%s", tostring(exit), err and err ~= "" and (": " .. err) or ""))
    if wv then
      -- Keep restarting while the HUD is up (the keyboard may have been unplugged). The timer
      -- must stay referenced or it is garbage-collected before it fires.
      feedTimer = hs.timer.doAfter(3, startFeed)
    end
  end, function(_, out, err)
    if err and err ~= "" then
      for line in err:gmatch("[^\n]+") do print("zmkhud: " .. line) end
    end
    if out and out ~= "" then
      feedBuffer = feedBuffer .. out
      while true do
        local nl = feedBuffer:find("\n", 1, true)
        if not nl then break end
        local line = feedBuffer:sub(1, nl - 1)
        feedBuffer = feedBuffer:sub(nl + 1)
        if line ~= "" then onFeedLine(line) end
      end
    end
    return true
  end, args)
  feed:start()
end

local function stopFeed()
  if feedTimer then feedTimer:stop(); feedTimer = nil end
  if feed then
    local t = feed
    feed = nil
    if t:isRunning() then t:terminate() end
  end
end

-- ---------- windows ----------

-- The recording display is the external one when there is one (the laptop keeps OBS and the
-- script); otherwise the screen under the mouse.
local function recordingScreen()
  local primary = hs.screen.primaryScreen()
  for _, s in ipairs(hs.screen.allScreens()) do
    if s ~= primary then return s end
  end
  return hs.mouse.getCurrentScreen() or primary
end

local function frame()
  local f = (M.screen or recordingScreen()):frame()
  -- top-right corner, M.margin from both edges (the typed-keys strip owns the bottom band)
  return hs.geometry.rect(f.x + f.w - M.width - M.margin, f.y + M.margin, M.width, M.height)
end

local function stripFrame()
  local f = (M.screen or recordingScreen()):frame()
  return hs.geometry.rect(f.x + M.margin, f.y + f.h - 72 - M.margin, 900, 72)
end

local function overlay(w)
  w:windowStyle({ "borderless", "nonactivating" })
  w:level(hs.drawing.windowLevels.overlay)
  w:behavior(hs.drawing.windowBehaviors.canJoinAllSpaces + hs.drawing.windowBehaviors.stationary)
  w:transparent(true)
  w:allowTextEntry(false)
  w:shadow(false)
  return w
end

function M.start()
  if wv then return M end
  for _, p in ipairs(PYTHONS) do
    if p and hs.fs.attributes(p) then M.python = p; break end
  end
  -- The page's ✕ button posts "close" through this controller.
  ucc = hs.webview.usercontent.new("zmkhud"):setCallback(function(msg)
    if msg and msg.body == "close" then M.stop() end
  end)
  wv = overlay(hs.webview.new(frame(), { developerExtrasEnabled = false }, ucc))
  wv:navigationCallback(function(action)
    if action == "didFinishNavigation" then flush() end
  end)
  wv:url("file://" .. PAGES .. "index.html")
  wv:show()

  -- Typed-keys visualizer: bottom-left of the recording display, fed by the same events.
  kv = overlay(hs.webview.new(stripFrame(), { developerExtrasEnabled = false }))
  kv:url("file://" .. PAGES .. "keys.html")
  kv:show()

  -- If WebKit never reports the load, unblock the bridge anyway.
  readyTimer = hs.timer.doAfter(2, function() if not ready then flush() end end)

  startFeed()
  return M
end

function M.stop()
  stopFeed()
  if readyTimer then readyTimer:stop(); readyTimer = nil end
  if wv then wv:delete(); wv = nil end
  if kv then kv:delete(); kv = nil end
  ready = false
end

-- Move to another screen: zmkhud.moveTo(hs.screen.allScreens()[2])
function M.moveTo(screen)
  M.screen = screen
  if wv then wv:frame(frame()) end
  if kv then kv:frame(stripFrame()) end
end

-- Resize (points), keeping the panel's aspect (598×392): zmkhud.resize(480, 315)
function M.resize(w, h)
  M.width, M.height = w, h
  if wv then
    wv:frame(frame())
    js(string.format("document.documentElement.style.zoom = %.3f", w / 598))
  end
end

-- Without the keyboard: zmkhud.layers({2, 22}) sets the layers by hand.
function M.layers(ids)
  js("hud.setLayers(" .. hs.json.encode(ids or {}) .. ")")
end

-- zmkhud.selftest(): what the HUD sees — for `hs -c "print(zmkhud.selftest())"`.
function M.selftest()
  return string.format("ready=%s feed=%s python=%s keymap=%s layers=%s lastKey=%s",
    tostring(ready), tostring(feed and feed:isRunning()), tostring(M.python),
    tostring(M.lastKeymap), tostring(M.lastLayers), tostring(M.lastKeys[#M.lastKeys]))
end

-- KeyCastr draws its own stacking bubbles wherever it was last placed; the strip replaces it.
local kc = hs.application.get("KeyCastr")
if kc then kc:kill(); print("zmkhud: quit KeyCastr (the HUD's typed-keys strip replaces it)") end

return M.start()
