#!/usr/bin/env python3
# AI-hint: Real-binary tests for the per-profile GPU display/render routing: hyprland-session device sets (dGPU lights
# only its card, the iGPU stays headless), nested-session render node (dGPU boots -> the iGPU's render node, never the
# dGPU's), hyprland-greeter on the profile's display (tuigreet hosted in cage + terminal on the dGPU card, VT fallback),
# per-profile kernel console args (fbcon=map:10 in the dGPU entry only, the iGPU entry byte-identical) and the read-only
# boot display report. Each block pairs a positive check of the candidate with a negative control that shows the
# pre-change code (bcd6b3f22: the shipped binary/launcher and that commit's SSOT) fails the same expectation.
# Run: MIOS_GEN=<candidate mios-gen> python3 -I tests/test-gpu-display-routing.py
#   MIOS_GEN_BASELINE (default /usr/bin/mios-gen) = the pre-change binary; MIOS_ROUTING_BASE (default bcd6b3f22).
# Touches no live state: hermetic SSOT roots, a mock greetd socket, a private headless cage on its own runtime dir.
# AI-related: tools/native/mios-gen/src/gpu_topology.rs, tools/native/mios-gen/src/wl_device_proxy.rs, tools/native/mios-gen/src/greeter.rs, tools/native/mios-gen/src/nested_session.rs, tools/native/mios-gen/src/render.rs, usr/libexec/mios/mios-greeter-session, usr/share/mios/mios.toml, usr/share/doc/mios/desktop-gpu-routing.md
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GEN = os.environ.get("MIOS_GEN", os.path.join(ROOT, "tools/native/target/x86_64-unknown-linux-musl/release/mios-gen"))

def _resolve_baseline_gen():
    override = os.environ.get("MIOS_GEN_BASELINE")
    if override:
        return override
    known_good = "/home/mios/mios-desktop-work-1/mios-gen.good-dc7ac4e9"
    if os.path.exists(known_good) and os.access(known_good, os.X_OK):
        return known_good
    return "/usr/bin/mios-gen"

BASE_GEN = _resolve_baseline_gen()
BASE = os.environ.get("MIOS_ROUTING_BASE", "bcd6b3f22")

LAUNCHER = os.path.join(ROOT, "usr/libexec/mios/mios-greeter-session")
MOCK = os.path.join(HERE, "greeter/mock_greetd.py")
VKBD = os.path.join(HERE, "greeter/vkbd.py")
CLEAN = {k: v for k, v in os.environ.items() if not k.startswith(("MIOS_", "ALACRITTY_")) and k not in (
    "WAYLAND_DISPLAY", "DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "GREETD_SOCK", "TMUX", "TMUX_PANE")}


def git_show(rev, path):
    return subprocess.run(["git", "-C", ROOT, "show", f"{rev}:{path}"], capture_output=True, text=True, check=True).stdout


def drm_gpus():
    """{pci: (card, render)} of this machine, from sysfs (read only)."""
    out = {}
    for c in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
        if "-" in os.path.basename(c):
            continue
        pci = os.path.basename(os.path.realpath(c + "/device"))
        out.setdefault(pci, ["/dev/dri/" + os.path.basename(c), None])
    for r in sorted(glob.glob("/sys/class/drm/renderD*")):
        pci = os.path.basename(os.path.realpath(r + "/device"))
        if pci in out:
            out[pci][1] = "/dev/dri/" + os.path.basename(r)
    return out


def identities():
    try:
        return {p: open(f"/etc/mios/hyprland/{p}-pci").read().strip() for p in ("igpu", "dgpu")}
    except OSError:
        return {}


GPUS = drm_gpus()
IDS = identities()
TWO_GPUS = len(IDS) == 2 and all(IDS[p] in GPUS for p in IDS)


def hermetic_root(ssot_text, extra=None):
    d = tempfile.mkdtemp(prefix="mios-routing-")
    os.makedirs(os.path.join(d, "usr/share/mios"))
    with open(os.path.join(d, "usr/share/mios/mios.toml"), "w") as f:
        f.write(ssot_text)
    os.makedirs(os.path.join(d, "etc/mios/hyprland"))
    for p, pci in IDS.items():
        with open(os.path.join(d, f"etc/mios/hyprland/{p}-pci"), "w") as f:
            f.write(pci + "\n")
    for rel in (extra or []):
        path = os.path.join(d, rel.lstrip("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(path, 0o755)
    return d


def run(binary, *args, env=None, check=True):
    home = tempfile.mkdtemp(prefix="mios-routing-home-")
    try:
        e = dict(CLEAN, HOME=home)
        e.update(env or {})
        r = subprocess.run([binary, *args], capture_output=True, text=True, env=e, timeout=120)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    if check and r.returncode != 0:
        raise AssertionError(f"{binary} {' '.join(args)} -> {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


NEW_SSOT = open(os.path.join(ROOT, "usr/share/mios/mios.toml")).read()
OLD_SSOT = git_show(BASE, "usr/share/mios/mios.toml")


@unittest.skipUnless(TWO_GPUS and os.access(GEN, os.X_OK), "needs the two-GPU host identities and the candidate")
class SessionDevices(unittest.TestCase):
    def plan(self, binary, ssot, profile):
        root = hermetic_root(ssot)
        self.addCleanup(shutil.rmtree, root, True)
        return json.loads(run(binary, "hyprland-session", "--root", root, "--hardware-profile", profile, "--dry-run").stdout)

    def test_dgpu_lights_only_the_dgpu_card_and_keeps_the_igpu_headless(self):
        p = self.plan(GEN, NEW_SSOT, "dgpu")
        self.assertEqual(p["devices"], [GPUS[IDS["dgpu"]][0]], "AQ_DRM_DEVICES = the dGPU card only")
        self.assertEqual(p["headless_devices"], [GPUS[IDS["igpu"]][0]])
        self.assertEqual(p["render_node"], GPUS[IDS["dgpu"]][1])

    def test_igpu_profile_is_unchanged(self):
        self.assertEqual(self.plan(GEN, NEW_SSOT, "igpu")["devices"], [GPUS[IDS["igpu"]][0]])
        self.assertEqual(self.plan(BASE_GEN, OLD_SSOT, "igpu")["devices"], [GPUS[IDS["igpu"]][0]])

    def test_opt_in_lights_both(self):
        p = self.plan(GEN, NEW_SSOT.replace('include_other_devices = false\n# The login greeter on this GPU', 'include_other_devices = true\n# The login greeter on this GPU', 1), "dgpu")
        self.assertEqual(p["devices"], [GPUS[IDS["dgpu"]][0], GPUS[IDS["igpu"]][0]])

    def test_negative_control_pre_change_dgpu_session_drives_the_igpu_too(self):
        p = self.plan(BASE_GEN, OLD_SSOT, "dgpu")
        self.assertNotEqual(p["devices"], [GPUS[IDS["dgpu"]][0]], "the old dGPU session also lit the iGPU outputs")
        self.assertEqual(len(p["devices"]), 2)


@unittest.skipUnless(TWO_GPUS and os.access(GEN, os.X_OK), "needs the two-GPU host identities and the candidate")
class NestedRender(unittest.TestCase):
    def plan(self, binary, ssot, profile):
        root = hermetic_root(ssot)
        self.addCleanup(shutil.rmtree, root, True)
        rt = tempfile.mkdtemp(prefix="mios-routing-rt-")
        self.addCleanup(shutil.rmtree, rt, True)
        return json.loads(run(binary, "nested-session", "--root", root, "--layout", "pip", "--no-devices", "--plan",
                              env={"XDG_RUNTIME_DIR": rt, "MIOS_DESKTOP_HARDWARE_PROFILE": profile}).stdout)

    def test_dgpu_boot_auto_degrades_visibly_while_the_nvidia_parent_cannot_show_igpu_buffers(self):
        r = self.plan(GEN, NEW_SSOT, "dgpu")["render"]
        self.assertEqual((r["policy"], r["rule"], r["mode"]), ("auto", "igpu", "inherit"))
        self.assertIn("cross_vendor_present", r["degraded"])

    def test_dgpu_boot_nested_session_renders_on_the_headless_igpu(self):
        for ssot in (NEW_SSOT.replace("cross_vendor_present = false", "cross_vendor_present = true", 1),
                     NEW_SSOT.replace('\nrender_node = "auto"', '\nrender_node = "igpu"', 1)):
            r = self.plan(GEN, ssot, "dgpu")["render"]
            self.assertEqual(r["mode"], "device")
            self.assertEqual(r["node"], GPUS[IDS["igpu"]][1])
            self.assertNotEqual(r["node"], GPUS[IDS["dgpu"]][1])
            self.assertEqual(r["pci"], IDS["igpu"])

    def test_igpu_boot_nested_session_follows_its_profile_rule(self):
        r = self.plan(GEN, NEW_SSOT, "igpu")["render"]
        self.assertIn(r["mode"], ("device", "software"))
        if r["mode"] == "device":
            self.assertEqual(r["node"], GPUS[IDS["igpu"]][1])

    def test_explicit_overrides(self):
        for value, mode in (("inherit", "inherit"), ("software", "software"), ("dgpu", "device")):
            ssot = NEW_SSOT.replace('\nrender_node = "auto"', f'\nrender_node = "{value}"', 1)
            r = self.plan(GEN, ssot, "dgpu")["render"]
            self.assertEqual(r["mode"], mode, value)
        bad = NEW_SSOT.replace('\nrender_node = "auto"', '\nrender_node = "/dev/dri/card0"', 1)
        root = hermetic_root(bad)
        self.addCleanup(shutil.rmtree, root, True)
        rt = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rt, True)
        r = run(GEN, "nested-session", "--root", root, "--no-devices", "--plan", check=False,
                env={"XDG_RUNTIME_DIR": rt, "MIOS_DESKTOP_HARDWARE_PROFILE": "dgpu"})
        self.assertNotEqual(r.returncode, 0)

    def test_negative_control_pre_change_nested_session_has_no_render_choice(self):
        # The pre-change binary reads the current SSOT (the shipped binary may need newer keys than the base commit's).
        ssot = re.sub(r'^\s*capture_guard\s*=.*$\n?', '', NEW_SSOT, flags=re.MULTILINE)
        p = self.plan(BASE_GEN, ssot, "dgpu")
        self.assertNotIn("render", p, "the old nested session inherits the parent's main device (the dGPU)")


@unittest.skipUnless(TWO_GPUS and os.access(GEN, os.X_OK), "needs the two-GPU host identities and the candidate")
class GreeterDisplay(unittest.TestCase):
    STUBS = ["/usr/libexec/mios/mios-greeter-session", "/usr/bin/cage", "/usr/bin/alacritty"]

    def plan(self, profile, ssot=NEW_SSOT, stubs=None, kind="tuigreet"):
        root = hermetic_root(ssot, self.STUBS if stubs is None else stubs)
        self.addCleanup(shutil.rmtree, root, True)
        return json.loads(run(GEN, "hyprland-greeter", "--kind", kind, "--root", root, "--hardware-profile", profile, "--dry-run").stdout)

    def test_dgpu_boot_hosts_tuigreet_on_the_dgpu_card(self):
        p = self.plan("dgpu")
        self.assertEqual((p["display"], p["drm_device"], p["reason"]), ("compositor", GPUS[IDS["dgpu"]][0], ""))
        a = p["argv"]
        self.assertEqual(a[:3], ["/usr/libexec/mios/mios-greeter-session", "--client", "terminal"])
        self.assertEqual(a[a.index("--drm-device") + 1], GPUS[IDS["dgpu"]][0])
        hosted = a[a.index("--") + 1:]
        self.assertEqual(hosted[0], "/usr/bin/tuigreet", "the same tuigreet is hosted and is the VT fallback")
        self.assertEqual(hosted[hosted.index("--cmd") + 1], "/usr/bin/mios-gen hyprland-session --hardware-profile dgpu")

    def test_igpu_boot_keeps_the_vt_greeter(self):
        p = self.plan("igpu")
        self.assertEqual((p["display"], p["argv"][0]), ("vt", "/usr/bin/tuigreet"))

    def test_glass_kind_gets_the_dgpu_card(self):
        p = self.plan("dgpu", kind="glass")
        a = p["argv"]
        self.assertEqual(a[0], "/usr/libexec/mios/mios-greeter-session")
        self.assertEqual(a[a.index("--drm-device") + 1], GPUS[IDS["dgpu"]][0])
        self.assertLess(a.index("--drm-device"), a.index("--"))

    def test_missing_compositor_or_terminal_degrades_to_the_vt_greeter(self):
        for stubs, why in ((["/usr/libexec/mios/mios-greeter-session", "/usr/bin/alacritty"], "cage"),
                           (["/usr/libexec/mios/mios-greeter-session", "/usr/bin/cage"], "terminal"),
                           ([], "launcher")):
            p = self.plan("dgpu", stubs=stubs)
            self.assertEqual((p["display"], p["argv"][0]), ("vt", "/usr/bin/tuigreet"), why)
            self.assertIn(why, p["reason"])

    def test_negative_control_pre_change_greeter_stays_on_the_vt(self):
        # The shipped binary has no --root; it reads the live SSOT and this boot's command line.
        r = run(BASE_GEN, "hyprland-greeter", "--kind", "tuigreet", "--dry-run")
        p = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(p["argv"][0], "/usr/bin/tuigreet")
        self.assertNotIn("--client", p["argv"])
        self.assertNotIn("display", p)


SRC_ENTRY = """title Fedora Linux (7.2.11-200.fc44.x86_64)
version 7.2.11-200.fc44.x86_64
linux /vmlinuz-7.2.11-200.fc44.x86_64
initrd /initramfs-7.2.11-200.fc44.x86_64.img $tuned_initrd
options root=/dev/mapper/fedora-root ro rd.lvm.lv=fedora/root rhgb quiet amd_iommu=on iommu=pt rd.driver.pre=vfio-pci vfio-pci.ids=10de:2684,10de:22ba $tuned_params rd.driver.blacklist=nouveau,nova_core modprobe.blacklist=nouveau,nova_core
grub_users $grub_users
grub_arg --unrestricted
grub_class fedora
"""


@unittest.skipUnless(os.access(GEN, os.X_OK), "candidate missing")
class BootArgs(unittest.TestCase):
    def entries(self, binary, ssot, src=SRC_ENTRY):
        root = hermetic_root(ssot)
        self.addCleanup(shutil.rmtree, root, True)
        s = os.path.join(root, "source.conf")
        with open(s, "w") as f:
            f.write(src)
        run(binary, "render-hyprland", "--root", root, "--boot-entry", s)
        run(binary, "render-hyprland", "--root", root, "--boot-entry", s, "--check")
        out = {}
        for p in ("igpu", "dgpu"):
            with open(os.path.join(root, f"boot/loader/entries/mios-desktop-{p}.conf")) as f:
                out[p] = f.read()
        return out

    @staticmethod
    def options(text):
        return next(l for l in text.splitlines() if l.startswith("options ")).split()[1:]

    def test_dgpu_entry_maps_the_console_to_the_dgpu_and_igpu_entry_is_byte_identical(self):
        new, old = self.entries(GEN, NEW_SSOT), self.entries(BASE_GEN, OLD_SSOT)
        d = self.options(new["dgpu"])
        self.assertEqual([a for a in d if a.startswith("fbcon=")], ["fbcon=map:10"])
        self.assertEqual(self.options(old["dgpu"]) + ["fbcon=map:10"], d, "only fbcon=map:10 is added")
        self.assertEqual(new["igpu"], old["igpu"], "the iGPU (and so the recovery) entry is unchanged")
        self.assertFalse(any(a.startswith(("fbcon=", "video=")) for a in self.options(new["igpu"])))

    def test_a_stale_fbcon_in_the_source_is_replaced(self):
        src = SRC_ENTRY.replace(" quiet ", " quiet fbcon=map:0 ")
        d = self.options(self.entries(GEN, NEW_SSOT, src)["dgpu"])
        self.assertEqual([a for a in d if a.startswith("fbcon=")], ["fbcon=map:10"])

    def test_negative_control_pre_change_dgpu_entry_leaves_the_console_on_the_boot_vga(self):
        self.assertFalse(any(a.startswith("fbcon=") for a in self.options(self.entries(BASE_GEN, OLD_SSOT)["dgpu"])))


@unittest.skipUnless(os.access(GEN, os.X_OK), "candidate missing")
class BootReport(unittest.TestCase):
    def fixture(self):
        d = tempfile.mkdtemp(prefix="mios-routing-sys-")
        self.addCleanup(shutil.rmtree, d, True)
        for pci, drv, vga in (("0000:01:00.0", "nvidia", "0"), ("0000:7a:00.0", "amdgpu", "1")):
            os.makedirs(f"{d}/devices/{pci}")
            os.makedirs(f"{d}/drivers/{drv}")
            os.symlink(f"{d}/drivers/{drv}", f"{d}/devices/{pci}/driver")
            open(f"{d}/devices/{pci}/boot_vga", "w").write(vga + "\n")
        for node, pci in (("card0", "0000:01:00.0"), ("renderD129", "0000:01:00.0"), ("card1", "0000:7a:00.0"), ("renderD128", "0000:7a:00.0")):
            os.makedirs(f"{d}/sys/{node}")
            os.symlink(f"{d}/devices/{pci}", f"{d}/sys/{node}/device")
        os.makedirs(f"{d}/vt/vtcon1")
        open(f"{d}/vt/vtcon1/name", "w").write("(M) frame buffer device\n")
        with open(f"{d}/kmsg", "w") as f:
            f.write("6,1,1,-;simple-framebuffer simple-framebuffer.0: [drm] fb0: simpledrmdrmfb frame buffer device\n"
                    "6,2,2,-;amdgpu 0000:7a:00.0: [drm] fb0: amdgpudrmfb frame buffer device\n"
                    "6,3,3,-;nvidia 0000:01:00.0: [drm] fb1: nvidia-drmdrmfb frame buffer device\n")
        root = hermetic_root(NEW_SSOT)
        self.addCleanup(shutil.rmtree, root, True)
        with open(f"{root}/etc/mios/hyprland/dgpu-pci", "w") as f:
            f.write("0000:01:00.0\n")
        return d, root

    def report(self, cmdline):
        d, root = self.fixture()
        with open(f"{d}/cmdline", "w") as f:
            f.write(cmdline + "\n")
        r = run(GEN, "boot-display-report", "--root", root, "--kmsg", f"{d}/kmsg", "--cmdline", f"{d}/cmdline",
                "--sysfs-drm", f"{d}/sys", "--dri", "/dev/dri", "--vtconsole", f"{d}/vt")
        return json.loads(r.stdout)

    def test_dgpu_boot_with_the_new_karg_puts_tty1_on_the_dgpu(self):
        v = self.report("quiet mios.desktop.gpu=dgpu fbcon=map:10")["verdict"]
        self.assertEqual((v["tty1_fb"], v["profile_fb"], v["tty1_on_profile_gpu"], v["recommended_fbcon"]), (1, 1, True, "fbcon=map:10"))

    def test_negative_control_todays_dgpu_cmdline_leaves_tty1_on_the_igpu(self):
        v = self.report("quiet mios.desktop.gpu=dgpu")["verdict"]
        self.assertEqual((v["tty1_fb"], v["tty1_on_profile_gpu"]), (0, False))


def wait_for(pred, timeout, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return pred()


HAVE_GUI = all(shutil.which(b) for b in ("cage", "alacritty", "grim")) and os.access("/usr/bin/tuigreet", os.X_OK)


class HostedRig:
    """A mock greetd + the launcher in --client terminal mode inside a private headless cage."""

    def __init__(self, tc, launcher=LAUNCHER, hosted=None, extra=(), env=None):
        self.tc = tc
        self.tmp = tempfile.mkdtemp(prefix="mios-hosted-")
        base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
        self.rt = tempfile.mkdtemp(prefix="mgr-", dir=base)
        os.chmod(self.rt, 0o700)
        self.transcript = os.path.join(self.tmp, "transcript.jsonl")
        self.stub_out = os.path.join(self.tmp, "stub.json")
        self.stub = os.path.join(self.tmp, "tuigreet-stub")
        with open(self.stub, "w") as f:
            # Records its PID (== the launcher's: the launcher exec'd it on the VT) and whether a terminal hosted it.
            f.write("#!/bin/sh\nprintf '{\"pid\": %s, \"hosted\": \"%s\"}\\n' \"$$\" \"${ALACRITTY_WINDOW_ID:+yes}\" >> "
                    + self.stub_out + "\nexit ${MIOS_TEST_STUB_RC:-0}\n")
        os.chmod(self.stub, 0o755)
        self.cfg = os.path.join(self.tmp, "terminal.toml")
        self.sock = os.path.join(self.rt, "greetd.sock")
        self.mock = subprocess.Popen([sys.executable, "-I", MOCK, self.sock, self.transcript, "--user", "mios=hunter2"],
                                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tc.assertTrue(wait_for(lambda: os.path.exists(self.sock), 5), "mock greetd did not start")
        e = dict(CLEAN, XDG_RUNTIME_DIR=self.rt, WLR_BACKENDS="headless", WLR_LIBINPUT_NO_DEVICES="1",
                 GREETD_SOCK=self.sock, MIOS_GREETER_HOST_CHILD=GEN)
        e.update(env or {})
        self.hosted = hosted or [self.stub]
        self.log = open(os.path.join(self.tmp, "launcher.log"), "w+")
        args = ["--client", "terminal", "--terminal", shutil.which("alacritty") or "/usr/bin/alacritty",
                "--terminal-config", self.cfg, "--start-timeout", "20", "--heartbeat-timeout", "30", *extra]
        self.proc = subprocess.Popen(["/bin/sh", launcher, *args, "--", *self.hosted], env=e, start_new_session=True,
                                     stdout=self.log, stderr=subprocess.STDOUT)

    def status(self):
        for p in glob.glob(os.path.join(self.rt, "mios-greeter.*/status")):
            try:
                return open(p).read().strip()
            except OSError:
                pass
        return ""

    def stub_runs(self):
        try:
            return [json.loads(l) for l in open(self.stub_out) if l.strip()]
        except OSError:
            return []

    def keys(self, *steps):
        r = subprocess.run([sys.executable, "-I", VKBD, "--allow-close", *steps], timeout=60, capture_output=True, text=True,
                           env=dict(CLEAN, XDG_RUNTIME_DIR=self.rt, WAYLAND_DISPLAY="wayland-0"))
        self.tc.assertEqual(r.returncode, 0, r.stderr)

    def shot(self, path):
        subprocess.run(["grim", path], timeout=20, capture_output=True, env=dict(CLEAN, XDG_RUNTIME_DIR=self.rt, WAYLAND_DISPLAY="wayland-0"))

    def transcript_in(self):
        try:
            return [json.loads(l)["msg"] for l in open(self.transcript) if l.strip() and json.loads(l)["dir"] == "in"]
        except OSError:
            return []

    def output(self):
        self.log.flush()
        self.log.seek(0)
        return self.log.read()

    def wait_exit(self, timeout):
        try:
            return self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def close(self):
        for p in (self.proc, self.mock):
            try:
                os.killpg(p.pid, signal.SIGKILL)  # only the groups this rig started
            except ProcessLookupError:
                pass
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                pass
        self.log.close()
        shutil.rmtree(self.rt, ignore_errors=True)
        if not os.environ.get("MIOS_GREETER_KEEP"):
            shutil.rmtree(self.tmp, ignore_errors=True)


def terminal_config():
    """The terminal config the candidate writes at greeter start (palette + font from the worktree SSOT)."""
    root = hermetic_root(NEW_SSOT, GreeterDisplay.STUBS)
    try:
        p = json.loads(run(GEN, "hyprland-greeter", "--kind", "tuigreet", "--root", root, "--hardware-profile", "dgpu",
                           "--dry-run").stdout)
    finally:
        shutil.rmtree(root, True)
    return p["terminal_config_text"]


@unittest.skipUnless(HAVE_GUI and os.access(GEN, os.X_OK) and TWO_GPUS, "cage/alacritty/grim/tuigreet or the candidate missing")
class HostedGreeter(unittest.TestCase):
    def rig(self, **kw):
        r = HostedRig(self, **kw)
        self.addCleanup(r.close)
        with open(r.cfg, "w") as f:
            f.write(terminal_config())
        return r

    def test_real_tuigreet_hosted_in_cage_and_terminal_logs_in_through_greetd(self):
        cmd = "/usr/bin/mios-gen hyprland-session --hardware-profile dgpu"
        r = self.rig(hosted=["/usr/bin/tuigreet", "--time", "--asterisks", "--cmd", cmd],
                     extra=["--drm-device", GPUS[IDS["dgpu"]][0]])
        self.assertTrue(wait_for(lambda: r.status() == "ready", 25), "hosted greeter never ready:\n" + r.output())
        time.sleep(2.0)
        preview = os.environ.get("MIOS_GREETER_PREVIEW")
        if preview:
            r.shot(preview)
        r.keys("text:mios", "key:Enter", "sleep:800", "text:hunter2", "key:Enter")
        self.assertEqual(r.wait_exit(30), 0, r.output())
        msgs = r.transcript_in()
        start = [m for m in msgs if m.get("type") == "start_session"]
        self.assertTrue(start, msgs)
        self.assertIn("hyprland-session", " ".join(start[0]["cmd"]))
        self.assertIn("started terminal greeter", r.output())
        self.assertIn(f"DRM {GPUS[IDS['dgpu']][0]}", r.output())
        self.assertNotIn("starting text greeter", r.output(), "no VT fallback on success")

    def test_hosted_greeter_failure_falls_back_to_the_vt_greeter(self):
        r = self.rig(env={"MIOS_TEST_STUB_RC": "3"})
        self.assertTrue(wait_for(lambda: len(r.stub_runs()) >= 2, 30), r.output())
        runs = r.stub_runs()
        self.assertEqual(runs[0]["hosted"], "yes", "first run inside the terminal")
        self.assertEqual(runs[1]["pid"], r.proc.pid, "then exec'd by the launcher on the VT")
        self.assertIn("exit 3", r.output())

    def test_compositor_failure_falls_back_immediately(self):
        r = self.rig(env={"MIOS_GREETER_CAGE": "/bin/false"})
        self.assertTrue(wait_for(lambda: r.stub_runs(), 15), r.output())
        self.assertEqual(r.stub_runs()[0]["pid"], r.proc.pid)

    def test_bad_drm_device_falls_back(self):
        r = self.rig(extra=["--drm-device", "/nonexistent/dri/card9"])
        self.assertTrue(wait_for(lambda: r.stub_runs(), 15), r.output())
        self.assertEqual(r.stub_runs()[0]["pid"], r.proc.pid)
        self.assertIn("not a character device", r.output())

    def test_negative_control_pre_change_launcher_never_hosts_the_text_greeter(self):
        old_dir = tempfile.mkdtemp(prefix="mios-old-launcher-")
        self.addCleanup(shutil.rmtree, old_dir, True)
        old = os.path.join(old_dir, "mios-greeter-session")
        with open(old, "w") as f:
            f.write(git_show(BASE, "usr/libexec/mios/mios-greeter-session"))
        r = self.rig(launcher=old, env={"MIOS_GREETER_QS": "/nonexistent/qs"})
        self.assertTrue(wait_for(lambda: r.stub_runs(), 15), r.output())
        self.assertEqual(r.stub_runs()[0]["hosted"], "", "the old launcher runs tuigreet on the VT only")
        self.assertIn("ignoring unknown option --client", r.output())


if __name__ == "__main__":
    unittest.main(verbosity=2)
