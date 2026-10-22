-- mios-rdp tier-1 test compositor: no plugins, no exec, no hooks
hl.config({["misc"] = {["disable_hyprland_logo"] = true, ["disable_splash_rendering"] = true, ["background_color"] = "rgba(202020ff)"},
           ["animations"] = {["enabled"] = false}, ["decoration"] = {["blur"] = {["enabled"] = false}, ["shadow"] = {["enabled"] = false}},
           ["input"] = {["kb_layout"] = "us", ["follow_mouse"] = 1}, ["general"] = {["gaps_in"] = 0, ["gaps_out"] = 0, ["border_size"] = 0},
           ["xwayland"] = {["enabled"] = false}})
hl.monitor({output = "WAYLAND-1", mode = "320x200", position = "0x0", scale = 1})
hl.workspace_rule({workspace = "name:scaletest", monitor = "HEADLESS-MIOS-SCALETEST", default = true})
hl.window_rule({name = "mios-scaletest-ws", match = {class = "^(mios-scaletest)$"}, workspace = "name:scaletest"})
