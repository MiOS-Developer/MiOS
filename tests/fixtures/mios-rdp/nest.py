#!/usr/bin/python3 -I
"""Private nested Hyprland for tier-1 tests (clipboard, keyboard, mouse): its aquamarine window is parked on
the live session's hidden special workspace special:mios-rdp-nest by a class-scoped rule that is enabled only
while it opens; no D-Bus at all (DBUS_SESSION_BUS_ADDRESS=disabled:), no plugins, no exec, no portals; its own
seat, so its clipboard and keyboard focus are not the operator's. Library: Nest().start() / .stop()."""
import json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
RT = "/run/user/1000"
CONF = os.path.join(HERE, "nest.lua")

NEST_LUA = r'''-- mios-rdp tier-1 test compositor: no plugins, no exec, no hooks
hl.config({["misc"] = {["disable_hyprland_logo"] = true, ["disable_splash_rendering"] = true, ["background_color"] = "rgba(202020ff)"},
           ["animations"] = {["enabled"] = false}, ["decoration"] = {["blur"] = {["enabled"] = false}, ["shadow"] = {["enabled"] = false}},
           ["input"] = {["kb_layout"] = "us", ["follow_mouse"] = 1}, ["general"] = {["gaps_in"] = 0, ["gaps_out"] = 0, ["border_size"] = 0},
           ["xwayland"] = {["enabled"] = false}})
hl.monitor({output = "WAYLAND-1", mode = "320x200", position = "0x0", scale = 1})
hl.workspace_rule({workspace = "name:scaletest", monitor = "HEADLESS-MIOS-SCALETEST", default = true})
hl.window_rule({name = "mios-scaletest-ws", match = {class = "^(mios-scaletest)$"}, workspace = "name:scaletest"})
'''


def live_instance():
    out = json.loads(subprocess.run(["hyprctl", "instances", "-j"], capture_output=True, text=True).stdout or "[]")
    for i in out:
        try:
            env = open(f"/proc/{i['pid']}/environ", "rb").read().split(b"\0")
        except OSError:
            continue
        if not any(e.startswith((b"WAYLAND_DISPLAY=", b"DISPLAY=")) and len(e.split(b"=", 1)[1]) for e in env):
            return i["instance"], i["wl_socket"]
    raise SystemExit("live instance not found")


class Nest:
    def __init__(self):
        self.live, self.live_wl = live_instance()
        self.sig = self.wl = None
        self.proc = None

    def hc_live(self, *a):
        return subprocess.run(["hyprctl", "-i", self.live, *a], capture_output=True, text=True, timeout=5).stdout.strip()

    def hc(self, *a):
        return subprocess.run(["hyprctl", "-i", self.sig, *a], capture_output=True, text=True, timeout=5).stdout.strip()

    def env(self, **extra):
        """Environment for a client of the nested compositor."""
        e = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE")}
        e.update(XDG_RUNTIME_DIR=RT, WAYLAND_DISPLAY=self.wl, HYPRLAND_INSTANCE_SIGNATURE=self.sig,
                 DBUS_SESSION_BUS_ADDRESS="disabled:", GDK_DEBUG="no-portals", GTK_USE_PORTAL="0")
        e.update(extra)
        return e

    def start(self, log):
        with open(CONF, "w") as f:
            f.write(NEST_LUA)
        before = set(os.listdir(f"{RT}/hypr"))
        r = self.hc_live("eval", 'if not rawget(_G, "mios_rdp_nest_rule") then _G.mios_rdp_nest_rule = hl.window_rule({name = "mios-rdp-nest", '
                         'match = {class = "^(aquamarine)$"}, workspace = "special:mios-rdp-nest silent", no_initial_focus = true}) '
                         'else _G.mios_rdp_nest_rule:set_enabled(true) end')
        env = {k: v for k, v in os.environ.items() if k not in ("HYPRLAND_INSTANCE_SIGNATURE", "HYPRLAND_CMD", "NOTIFY_SOCKET", "DISPLAY")}
        env.update(HYPRLAND_NO_SD_VARS="1", HYPRLAND_NO_SD_NOTIFY="1", HYPRLAND_NO_CRASHREPORTER="1",
                   WAYLAND_DISPLAY=self.live_wl, XDG_RUNTIME_DIR=RT, DBUS_SESSION_BUS_ADDRESS="disabled:")
        self.proc = subprocess.Popen(["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=2G", "-p", "MemorySwapMax=0",
                                      "-p", "CPUQuota=200%", "Hyprland", "--config", CONF],
                                     env=env, stdout=open(log, "w"), stderr=subprocess.STDOUT)
        for _ in range(100):
            time.sleep(0.1)
            new = [d for d in set(os.listdir(f"{RT}/hypr")) - before if os.path.exists(f"{RT}/hypr/{d}/.socket.sock")]
            if new:
                self.sig = new[0]
                break
        time.sleep(1.5)
        self.hc_live("eval", 'if rawget(_G, "mios_rdp_nest_rule") then _G.mios_rdp_nest_rule:set_enabled(false) end')
        if not self.sig:
            self.stop()
            raise SystemExit("nested Hyprland did not start")
        inst = json.loads(subprocess.run(["hyprctl", "instances", "-j"], capture_output=True, text=True).stdout or "[]")
        self.wl = next(i["wl_socket"] for i in inst if i["instance"] == self.sig)
        win = [c for c in json.loads(self.hc_live("-j", "clients") or "[]") if c.get("class") == "aquamarine" and c.get("pid") == self.nested_pid()]
        return {"sig": self.sig, "wl": self.wl, "rule": r, "window": [(c["workspace"]["name"], c["mapped"], c["hidden"]) for c in win]}

    def nested_pid(self):
        try:
            return int(open(f"{RT}/hypr/{self.sig}/hyprland.lock").read().split("\n")[0])
        except (OSError, ValueError):
            return -1

    def stop(self):
        pid = self.nested_pid() if self.sig else -1
        if self.sig:
            self.hc("dispatch", "hl.dsp.exit()")
        time.sleep(1.5)
        if pid > 0:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        if self.proc:
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.hc_live("eval", 'if rawget(_G, "mios_rdp_nest_rule") then _G.mios_rdp_nest_rule:set_enabled(false) end')
        if self.sig and self.sig != self.live and os.path.isdir(f"{RT}/hypr/{self.sig}") and not os.path.exists(f"/proc/{pid}"):
            subprocess.run(["rm", "-r", "--", f"{RT}/hypr/{self.sig}"])
        left = [c["address"] for c in json.loads(self.hc_live("-j", "clients") or "[]") if c.get("class") == "aquamarine" and c.get("pid") == pid]
        return {"nested_windows_left": left}


if __name__ == "__main__":
    n = Nest()
    print(json.dumps(n.start(os.path.join(HERE, "nest.log"))))
    print(n.hc("-j", "monitors"))
    time.sleep(2)
    print(json.dumps(n.stop()))
