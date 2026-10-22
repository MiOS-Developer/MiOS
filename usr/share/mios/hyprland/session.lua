-- AI-hint: Load the native MiOS projection with vendor, host and user SSOT layers; degrade to a minimal session when it fails.
-- AI-related: tools/native/mios-gen/src/render.rs (render_hyprland::LOADER), usr/share/mios/hyprland/hyprland.lua
MIOS_SESSION = {}
local function mios_quote(s)
 return "'" .. tostring(s):gsub("'", "'\\''") .. "'"
end
local function mios_load()
 local errfile = (os.getenv("XDG_RUNTIME_DIR") or "/tmp") .. "/mios-hyprland-projection.err"
 local p = io.popen("/usr/bin/mios-gen render-hyprland --root / --runtime 2>" .. mios_quote(errfile), "r")
 if not p then
  return false, "cannot run /usr/bin/mios-gen"
 end
 local config = p:read("*a") or ""
 -- Hyprland can reap the child before pclose; validate the returned Lua instead.
 p:close()
 if not config:match("^%-%- AI%-hint:") then
  local f = io.open(errfile, "r")
  local detail = f and f:read("*a") or ""
  if f then f:close() end
  detail = detail:gsub("%s+$", "")
  return false, "projection failed: " .. (detail ~= "" and detail or "no Lua output")
 end
 local chunk, err = load(config, "=MiOS SSOT")
 if not chunk then
  return false, "projection is not valid Lua: " .. tostring(err)
 end
 return pcall(chunk)
end
local ok, err = mios_load()
local state = MIOS_SESSION
local problems = state.errors or {}
if not ok then
 table.insert(problems, 1, "MiOS: Hyprland SSOT " .. tostring(err) .. " -- minimal session active")
 if io and io.stderr then io.stderr:write(problems[1] .. "\n") end
 if not state.binds then
  local terminal = "alacritty -e env -u TMUX -u TMUX_PANE -u TMUX_TMPDIR /usr/libexec/mios/mios-terminal"
  local launcher = "quickshell ipc --path /usr/share/mios/quickshell/Config.qml --any-display call drawer toggle"
  for _, key in ipairs({ "Q", "Return" }) do
   pcall(hl.bind, "SUPER + " .. key, hl.dsp.exec_cmd(terminal))
  end
  for _, key in ipairs({ "SPACE", "R" }) do
   pcall(hl.bind, "SUPER + " .. key, hl.dsp.exec_cmd(launcher))
  end
 end
 if not state.hooks then
  hl.on("hyprland.start", function()
   hl.exec_cmd("dbus-update-activation-environment --systemd WAYLAND_DISPLAY DISPLAY HYPRLAND_INSTANCE_SIGNATURE XDG_CURRENT_DESKTOP QT_QPA_PLATFORMTHEME MIOS_THEME_BRIDGE GTK_MODULES XDG_SESSION_DESKTOP XDG_SESSION_TYPE AQ_DRM_DEVICES WLR_DRM_DEVICES DRI_PRIME MIOS_DESKTOP_HARDWARE_PROFILE && systemctl --user start mios-desktop.target")
  end)
  hl.on("hyprland.shutdown", function()
   hl.exec_cmd("systemctl --user stop mios-desktop.target graphical-session.target")
  end)
 end
end
if #problems > 0 then
 local message = table.concat(problems, " | "):sub(1, 900)
 hl.on("hyprland.start", function()
  hl.exec_cmd("hyprctl notify 3 60000 0 " .. mios_quote(message))
 end)
end
