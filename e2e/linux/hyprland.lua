-- The smallest Hyprland config the end-to-end run needs (e2e/linux/run-in-arch.sh).
--
-- Supplying any config stops Hyprland generating one, whose first start opens a floating
-- hyprland-welcome window on top of everything. Scale 1 keeps the virtual monitor's logical and
-- physical pixels equal, so a screenshot is the layout. The Lua form is what Hyprland 0.57 reads;
-- its first two lines follow the one public headless run of it on a hosted runner, which found
-- both (https://github.com/alexandru-savinov/agterm-omarchy/issues/63).
--
-- host/linux/hud.sh then writes the HUD's global shortcuts into this directory
-- (host/shortcuts.py: zmk-layer-hud.lua, read by a line it adds at the end of this file) and has
-- Hyprland read its config again, which is part of what the run checks.
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = "1" })

hl.config({
    misc = {
        disable_hyprland_logo = true,
        disable_splash_rendering = true,
    },
    animations = {
        enabled = false,
    },
})
