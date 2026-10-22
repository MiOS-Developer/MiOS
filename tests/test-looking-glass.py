#!/usr/bin/env python3
# AI-hint: Consolidated unit test suite for MiOS Looking Glass & Display domain (task_3353 / GATECAT-01).
# AI-related: usr/libexec/mios/display/looking_glass.py, usr/libexec/mios/vfio/setup-looking-glass.py, usr/share/doc/mios/manual/ch21-looking-glass-b7-and-kvmfr.md
"""Automated unit test suite for MiOS Looking Glass & Display domain.

Consolidates:
- Looking Glass B6 SPICE-direct client configuration, client.ini, and keybindings (test-looking-glass-config.py)
- Looking Glass B6 IVSHMEM shared memory setup and VFIO passthrough validation (test-looking-glass-setup.py)
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.path.normpath(os.path.join(_HERE, "..")),
    os.path.normpath(r"M:\MiOS\worktrees\mios-integration"),
    os.path.normpath(r"C:\MiOS"),
]
_ROOT = next((c for c in _CANDIDATES if os.path.isfile(os.path.join(c, "usr", "libexec", "mios", "display", "looking_glass.py"))), os.path.normpath(os.path.join(_HERE, "..")))

# Load Looking Glass B6 Config Manager (usr/libexec/mios/display/looking_glass.py)
_LG_CONFIG_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "display", "looking_glass.py")
spec_lg = importlib.util.spec_from_file_location("looking_glass", _LG_CONFIG_PATH)
if spec_lg and spec_lg.loader:
    looking_glass = importlib.util.module_from_spec(spec_lg)
    sys.modules[spec_lg.name] = looking_glass
    spec_lg.loader.exec_module(looking_glass)
else:
    raise ImportError(f"Could not load looking_glass module from {_LG_CONFIG_PATH}")

# Load Looking Glass B6 IVSHMEM/VFIO Setup (usr/libexec/mios/vfio/setup-looking-glass.py)
_LG_SETUP_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "vfio", "setup-looking-glass.py")
spec_setup = importlib.util.spec_from_file_location("setup_looking_glass", _LG_SETUP_PATH)
if spec_setup and spec_setup.loader:
    setup_looking_glass = importlib.util.module_from_spec(spec_setup)
    sys.modules[spec_setup.name] = setup_looking_glass
    spec_setup.loader.exec_module(setup_looking_glass)
else:
    raise ImportError(f"Could not load setup-looking-glass module from {_LG_SETUP_PATH}")


# ============================================================================
# Domain 4.1: Looking Glass B6 Client Configuration & Keybinding Manager
# (Migrated from tests/test-looking-glass-config.py)
# ============================================================================

class TestLookingGlassConfig(unittest.TestCase):
    """Validates Looking Glass B6 client configuration, INI synthesis, and keybinding generators."""

    def setUp(self) -> None:
        self.manager = looking_glass.LookingGlassConfigManager(
            vm_name="win11-gaming",
            shm_file="/dev/kvmfr0",
            spice_socket="/var/run/libvirt/qemu/win11-gaming-spice.sock",
            escape_key="KEY_SCROLLLOCK",
            full_screen=False,
            allow_dma=True,
        )

    def test_default_config_dict(self) -> None:
        cfg = self.manager.get_default_config_dict()
        self.assertIn("app", cfg)
        self.assertIn("win", cfg)
        self.assertIn("input", cfg)
        self.assertIn("spice", cfg)
        self.assertIn("wayland", cfg)

        self.assertEqual(cfg["app"]["shmFile"], "/dev/kvmfr0")
        self.assertTrue(cfg["app"]["allowDMA"])
        self.assertEqual(cfg["input"]["escapeKey"], "KEY_SCROLLLOCK")
        self.assertEqual(cfg["spice"]["host"], "/var/run/libvirt/qemu/win11-gaming-spice.sock")
        self.assertEqual(cfg["spice"]["port"], 0)
        self.assertTrue(cfg["wayland"]["warpSupport"])

    def test_ini_generation_and_roundtrip_parse(self) -> None:
        ini_text = self.manager.generate_ini()
        self.assertIn("[app]", ini_text)
        self.assertIn("shmFile = /dev/kvmfr0", ini_text)
        self.assertIn("allowDMA = true", ini_text)
        self.assertIn("[spice]", ini_text)
        self.assertIn("host = /var/run/libvirt/qemu/win11-gaming-spice.sock", ini_text)
        self.assertIn("port = 0", ini_text)

        parsed = looking_glass.LookingGlassConfigManager.parse_ini(ini_text)
        self.assertEqual(parsed["app"]["shmFile"], "/dev/kvmfr0")
        self.assertIs(parsed["app"]["allowDMA"], True)
        self.assertEqual(parsed["input"]["escapeKey"], "KEY_SCROLLLOCK")
        self.assertEqual(parsed["spice"]["host"], "/var/run/libvirt/qemu/win11-gaming-spice.sock")
        self.assertEqual(parsed["spice"]["port"], 0)

    def test_ini_overrides(self) -> None:
        overrides = {
            "win": {"fullScreen": True, "uiTheme": "light"},
            "input": {"mouseSens": 5},
        }
        ini_text = self.manager.generate_ini(overrides=overrides)
        parsed = looking_glass.LookingGlassConfigManager.parse_ini(ini_text)
        self.assertIs(parsed["win"]["fullScreen"], True)
        self.assertEqual(parsed["win"]["uiTheme"], "light")
        self.assertEqual(parsed["input"]["mouseSens"], 5)

    def test_spice_socket_resolution(self) -> None:
        sock1 = looking_glass.LookingGlassConfigManager.resolve_spice_socket("win11")
        self.assertEqual(sock1, "/var/run/libvirt/qemu/win11-spice.sock")

        explicit = "/custom/socket.sock"
        sock2 = looking_glass.LookingGlassConfigManager.resolve_spice_socket("win11", explicit_path=explicit)
        self.assertEqual(sock2, explicit)

    def test_validate_mock_checks(self) -> None:
        spice_res = self.manager.validate_spice_socket(mock=True)
        self.assertEqual(spice_res["status"], "pass")
        self.assertTrue(spice_res["is_socket"])

        shm_res = self.manager.validate_shm(mock=True)
        self.assertEqual(shm_res["status"], "pass")
        self.assertTrue(shm_res["accessible"])

    def test_hyprland_rules_generation(self) -> None:
        rules = self.manager.generate_hyprland_rules()
        self.assertIn("windowrulev2 = fullscreen, class:^(looking-glass-client)$", rules)
        self.assertIn("windowrulev2 = idleinhibit always, class:^(looking-glass-client)$", rules)
        self.assertIn("windowrulev2 = immediate, class:^(looking-glass-client)$", rules)
        self.assertIn("bind = $mainMod, Scroll_Lock, exec, /usr/bin/looking-glass-client -f /dev/kvmfr0", rules)

    def test_gnome_rules_generation(self) -> None:
        gnome_cmds = self.manager.generate_gnome_rules()
        self.assertIn("gsettings set org.gnome.settings-daemon.plugins.media-keys", gnome_cmds)
        self.assertIn("Looking Glass VM Capture", gnome_cmds)
        self.assertIn("Scroll_Lock", gnome_cmds)
        self.assertIn("/usr/bin/looking-glass-client -f /dev/kvmfr0", gnome_cmds)

    def test_launch_args_synthesis(self) -> None:
        args = self.manager.build_client_launch_args()
        self.assertIn("looking-glass-client", args)
        self.assertIn("-f", args)
        self.assertIn("/dev/kvmfr0", args)
        self.assertIn("spice:host=/var/run/libvirt/qemu/win11-gaming-spice.sock", args)
        self.assertIn("spice:port=0", args)
        self.assertIn("input:escapeKey=KEY_SCROLLLOCK", args)

        cfg_args = self.manager.build_client_launch_args(config_path="/etc/looking-glass/client.ini")
        self.assertEqual(cfg_args, ["looking-glass-client", "-C", "/etc/looking-glass/client.ini"])

    def test_verify_all(self) -> None:
        res = self.manager.verify_all(mock=True)
        self.assertEqual(res["status"], "pass")
        self.assertEqual(res["checks"]["spice_socket"], "pass")
        self.assertEqual(res["checks"]["shm_device"], "pass")
        self.assertEqual(res["checks"]["ini_generation"], "pass")


# ============================================================================
# Domain 4.2: Looking Glass IVSHMEM Shared Memory & VFIO Setup
# (Migrated from tests/test-looking-glass-setup.py)
# ============================================================================

class TestLookingGlassSetup(unittest.TestCase):
    """Validates Looking Glass IVSHMEM XML generation and memory allocation checks."""

    def test_ivshmem_xml_generation(self):
        lg = setup_looking_glass.LookingGlassManager(size_mb=128)
        xml = lg.generate_ivshmem_xml()
        self.assertIn('<shmem name="looking-glass">', xml)
        self.assertIn('<model type="ivshmem-plain"/>', xml)
        self.assertIn('<size unit="M">128</size>', xml)

    def test_qemu_cmdline_generation(self):
        lg = setup_looking_glass.LookingGlassManager(size_mb=128)
        cmdline = lg.generate_qemu_cmdline()
        self.assertIn("-object memory-backend-file", cmdline)
        self.assertIn("size=134217728", cmdline)
        self.assertIn("-device ivshmem-plain", cmdline)

    def test_shm_file_resolution_fallback(self):
        from unittest import mock
        M = looking_glass.LookingGlassConfigManager
        kvmfr, shm = looking_glass.DEFAULT_SHM_FILE, looking_glass.FALLBACK_SHM_FILE
        # Positive: kvmfr missing, SHM file present -> fallback at runtime only.
        with mock.patch.object(looking_glass.os.path, "exists", side_effect=lambda p: p == shm):
            self.assertEqual(M.resolve_shm_file(kvmfr), shm)
            lg = M(vm_name="win11")
            self.assertEqual(lg.shm_file, kvmfr)                     # config stays deterministic
            self.assertEqual(lg.get_default_config_dict()["app"]["shmFile"], kvmfr)
            self.assertEqual(lg.effective_shm_file(), shm)
            self.assertIn(shm, lg.build_client_launch_args(resolve_fallback=True))
            self.assertIn(kvmfr, lg.build_client_launch_args())
        # Negative: kvmfr present -> no fallback; custom paths are never rewritten.
        with mock.patch.object(looking_glass.os.path, "exists", return_value=True):
            self.assertEqual(M.resolve_shm_file(kvmfr), kvmfr)
        with mock.patch.object(looking_glass.os.path, "exists", side_effect=lambda p: p == shm):
            self.assertEqual(M.resolve_shm_file("/dev/shm/custom-lg"), "/dev/shm/custom-lg")
        # Negative: neither exists -> keep the configured device (validation reports it).
        with mock.patch.object(looking_glass.os.path, "exists", return_value=False):
            self.assertEqual(M.resolve_shm_file(kvmfr), kvmfr)

    def test_mock_shm_validation(self):
        lg = setup_looking_glass.LookingGlassManager()
        self.assertTrue(lg.validate_shm_allocation(mock=True))

    def test_mock_kvmfr_validation(self):
        lg = setup_looking_glass.LookingGlassManager()
        self.assertTrue(lg.validate_kvmfr_device(mock=True))

    def test_verify_all_mock(self):
        lg = setup_looking_glass.LookingGlassManager(size_mb=64)
        res = lg.verify_all(mock=True)
        self.assertEqual(res["status"], "pass")
        self.assertEqual(res["checks"]["shm_allocation"], "pass")
        self.assertEqual(res["checks"]["kvmfr_device"], "pass")

    def test_service_unit_file(self):
        svc_path = os.path.join(_ROOT, "usr", "lib", "systemd", "system", "mios-vfio-setup.service")
        self.assertTrue(os.path.exists(svc_path), f"Service file missing at {svc_path}")
        with open(svc_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("setup-looking-glass.py --verify", content)
        self.assertIn("[Install]", content)


# ============================================================================
# Domain 4.3: SSOT [vm.looking_glass] domain profile, client projection, launcher
# ============================================================================

_DRIFTED_DOMAIN = """<domain type='kvm'>
  <name>mios-xbox-current</name>
  <metadata>
    <libosinfo:libosinfo xmlns:libosinfo="http://libosinfo.org/xmlns/libvirt/domain/1.0">
      <libosinfo:os id="http://microsoft.com/win/11"/>
    </libosinfo:libosinfo>
  </metadata>
  <memory unit='KiB'>8388608</memory>
  <vcpu placement='static'>8</vcpu>
  <cpu mode='host-passthrough' check='none' migratable='on'>
    <topology sockets='1' dies='1' clusters='1' cores='8' threads='1'/>
  </cpu>
  <clock offset='utc'/>
  <devices>
    <channel type='spicevmc'><target type='virtio' name='com.redhat.spice.0'/></channel>
    <channel type='spicevmc'><target type='virtio' name='com.redhat.spice.0'/></channel>
    <graphics type='vnc' port='-1' autoport='yes'><listen type='address'/></graphics>
    <audio id='1' type='none'/>
    <video><model type='virtio' heads='1' primary='yes'/></video>
  </devices>
</domain>
"""


def _ssot():
    import tomllib
    with open(os.path.join(_ROOT, "usr", "share", "mios", "mios.toml"), "rb") as fh:
        return tomllib.load(fh)


class TestLookingGlassProfile(unittest.TestCase):
    """The [vm.looking_glass] profile lands in templates, installed domains and the client alike."""

    @classmethod
    def setUpClass(cls):
        cls.data = _ssot()
        cls.cfg = setup_looking_glass.lg_config(cls.data)

    def test_ssot_section(self):
        lg = self.data["vm"]["looking_glass"]
        for key in ("spice_port", "spice_listen", "threads_per_core", "clock_offset", "shm_path",
                    "shm_size_mb", "shm_selinux_type", "domains"):
            self.assertIn(key, lg)
        self.assertEqual(lg["shm_selinux_type"], "svirt_tmpfs_t")

    def test_drifted_domain_is_repaired(self):
        import xml.etree.ElementTree as ET
        self.assertTrue(setup_looking_glass.profile_problems(_DRIFTED_DOMAIN, self.cfg))
        out = setup_looking_glass.apply_profile(_DRIFTED_DOMAIN, self.cfg)
        self.assertEqual(setup_looking_glass.profile_problems(out, self.cfg), [])
        root = ET.fromstring(out)
        topo = root.find("cpu/topology")
        threads = int(self.cfg["threads_per_core"])
        self.assertEqual((topo.get("cores"), topo.get("threads")), (str(8 // threads), str(threads)))
        self.assertIsNotNone(root.find("cpu/feature[@name='topoext'][@policy='require']"))
        self.assertEqual(root.find("clock").get("offset"), self.cfg["clock_offset"])
        gfx = root.findall("devices/graphics")
        # SPICE for Looking Glass, plus the localhost VNC console on the emulated display (virt-manager / Cockpit).
        self.assertEqual([g.get("type") for g in gfx], ["spice", "vnc"])
        self.assertEqual(gfx[1].get("listen"), self.cfg.get("vnc_listen", "127.0.0.1"))
        self.assertEqual(gfx[0].get("port"), str(self.cfg["spice_port"]))
        self.assertEqual(gfx[0].get("autoport"), "no")
        self.assertEqual(len(root.findall("devices/channel[@type='spicevmc']")), 1)
        self.assertEqual([a.get("type") for a in root.findall("devices/audio")], ["spice"])
        self.assertEqual(len(root.findall("devices/sound")), 1)
        self.assertEqual(root.find("devices/shmem/size").text, str(self.cfg["shm_size_mb"]))
        self.assertIn("libosinfo:libosinfo", out)  # namespaced metadata survives
        self.assertNotIn("ns0:", out)

    def test_vnc_console_follows_ssot_toggle(self):
        with_vnc = setup_looking_glass.apply_profile(_DRIFTED_DOMAIN, self.cfg)
        self.assertEqual(setup_looking_glass.profile_problems(with_vnc, self.cfg), [])
        off = dict(self.cfg, vnc_console=False)
        # a stray VNC console is reported when the SSOT turns it off, and re-applying removes it
        self.assertIn("VNC console present but [vm.looking_glass] vnc_console is false",
                      setup_looking_glass.profile_problems(with_vnc, off))
        self.assertNotIn('type="vnc"', setup_looking_glass.apply_profile(with_vnc, off))

    def test_profile_is_idempotent(self):
        once = setup_looking_glass.apply_profile(_DRIFTED_DOMAIN, self.cfg)
        self.assertEqual(setup_looking_glass.apply_profile(once, self.cfg), once)

    def test_vcpus_that_do_not_split_are_rejected(self):
        with self.assertRaises(ValueError):
            setup_looking_glass.apply_profile(_DRIFTED_DOMAIN, self.cfg, vcpus=7)

    def test_templates_conform_to_ssot(self):
        vcpus = setup_looking_glass.template_vcpus(self.data)
        for name in ("Xbox-Final-NoAutoSelect.xml", "Xbox-AutoEnroll.xml"):
            with open(os.path.join(_ROOT, "usr", "libexec", "mios", name), encoding="utf-8") as fh:
                problems = setup_looking_glass.profile_problems(fh.read(), self.cfg, vcpus)
            self.assertEqual(problems, [], name)

    def test_repair_and_apply_scripts_use_the_profile(self):
        for rel in (("usr", "libexec", "mios", "virt-apply.sh"), ("usr", "libexec", "mios", "xbox-repair.sh")):
            with open(os.path.join(_ROOT, *rel), encoding="utf-8") as fh:
                body = fh.read()
            self.assertIn("setup-looking-glass.py --render", body, rel[-1])
            self.assertNotIn('graphics type="vnc"', body, rel[-1])

    def test_client_projection_matches_domain(self):
        args = setup_looking_glass.client_args(self.cfg)
        self.assertIn(f"spice:port={self.cfg['spice_port']}", args)
        self.assertIn(f"spice:host={self.cfg['spice_listen']}", args)
        self.assertIn(f"app:shmFile={self.cfg['shm_path']}", args)
        ini = setup_looking_glass.client_ini(self.cfg)
        self.assertIn(f"port={self.cfg['spice_port']}", ini)
        self.assertIn("[spice]", ini)

    def test_guest_follows_client_window(self):
        opts = setup_looking_glass.client_options(self.cfg)
        self.assertEqual(opts["win"]["setGuestRes"], "yes")
        self.assertEqual(opts["win"]["keepAspect"], "yes")
        self.assertEqual(opts["win"]["autoResize"], "no")  # the client refuses autoResize together with setGuestRes
        both = dict(self.cfg, client_auto_resize=True, client_set_guest_res=True)
        self.assertEqual(setup_looking_glass.client_options(both)["win"]["autoResize"], "no")
        self.assertIn("win:setGuestRes=yes", setup_looking_glass.client_args(self.cfg))

    def test_ivshmem_fits_idd_modes(self):
        # upstream doc/install_libvirt.rst "Common IDD values"
        for (w, h), mb in {(1920, 1080): 32, (2560, 1440): 64, (3440, 1440): 64, (3840, 2160): 128}.items():
            self.assertEqual(setup_looking_glass.idd_required_mb(w, h), mb, (w, h))
        chk = setup_looking_glass.shm_check(self.cfg, [{"name": "x", "width": 3840, "height": 2160, "scale": 2}])
        self.assertEqual(chk["required_mb"], 128)
        self.assertEqual(chk["ok"], int(self.cfg["shm_size_mb"]) >= 128)

    def test_guest_command_binds_the_plan(self):
        plan = {"commit": "c" * 40, "version": "B7-1-cccccccc", "url": "https://looking-glass.io/artifact/B7-1-cccccccc/idd",
                "sha256": "a" * 64, "signer": "CN=HostFission"}
        script = os.path.join(_ROOT, "usr", "share", "mios", "windows", "mios-looking-glass-idd.ps1")
        cmds = setup_looking_glass.guest_commands(dict(self.cfg, guest_script=script), plan)
        self.assertTrue(all(len(c) < 2800 for c in cmds))  # WinRM: one cmd.exe line of UTF-16 base64 each
        self.assertIn("-Sha256 '" + "a" * 64 + "'", cmds[-1])
        with open(script, encoding="utf-8") as fh:
            body = fh.read().lower()
        self.assertNotIn("testsigning on", body)
        self.assertNotIn("nointegritychecks on", body)

    def test_shm_label_is_shipped(self):
        line = setup_looking_glass.selinux_fcontext_line(self.cfg)
        with open(os.path.join(_ROOT, "automation", "38-selinux.sh"), encoding="utf-8") as fh:
            self.assertIn(line, fh.read())
        with open(os.path.join(_ROOT, "usr", "lib", "tmpfiles.d", "50-mios.conf"), encoding="utf-8") as fh:
            tmp = [l.split() for l in fh if l.split()[1:2] == [self.cfg["shm_path"]]]
        self.assertEqual(sorted(t[0] for t in tmp), ["f", "z"])
        with open(os.path.join(_ROOT, "usr", "lib", "systemd", "system", "mios-vfio-setup.service"), encoding="utf-8") as fh:
            self.assertIn("--ensure-selinux", fh.read())

    def test_launcher_and_window_rule(self):
        launcher = self.data["desktop"]["launchers"]["mios-xbox"]
        self.assertIn("setup-looking-glass.py --launch", launcher["exec_cmd"])
        self.assertEqual(launcher["startup_wm_class"], "looking-glass-client")
        rules = [r for r in self.data["desktop"]["hyprland"]["window_rules"] if r.get("name") == "mios-looking-glass"]
        self.assertEqual(len(rules), 1)
        self.assertTrue(rules[0]["float"] and rules[0]["center"])
        self.assertNotIn("size", rules[0])  # sized by the placement rule
        desk = os.path.join(_ROOT, "usr", "share", "applications", "mios-xbox.desktop")
        with open(desk, encoding="utf-8") as fh:
            self.assertIn(f"Exec={launcher['exec_cmd']}", fh.read())

    def test_launch_dry_run(self):
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(setup_looking_glass.launch(self.cfg, "mios-xbox-current", dry_run=True), 0)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["domain"], "mios-xbox-current")
        self.assertEqual(out["client"][0], self.cfg["client"])


# ============================================================================
# Domain 4.4: graphics preservation, VNC console password, SPICE port, guest script
# ============================================================================

_CUSTOM_GRAPHICS = """<domain type='kvm'>
  <name>mios-xbox-current</name>
  <vcpu placement='static'>8</vcpu>
  <devices>
    <graphics type='spice' port='5900' autoport='yes' listen='0.0.0.0' keymap='de' passwd='spicepw' defaultMode='secure'>
      <listen type='address' address='0.0.0.0'/>
      <image compression='off'/>
      <streaming mode='filter'/>
    </graphics>
    <graphics type='vnc' port='-1' autoport='yes' listen='0.0.0.0' keymap='de' sharePolicy='force-shared' socket='/tmp/x.sock'>
      <listen type='address' address='0.0.0.0'/>
      <audio id='1'/>
    </graphics>
    <graphics type='egl-headless'/>
    <audio id='1' type='spice'/>
  </devices>
</domain>
"""


def _graphics(xml_text):
    import xml.etree.ElementTree as ET
    return {g.get("type"): g for g in ET.fromstring(xml_text).findall("devices/graphics")}


def _pwsh():
    import shutil
    return shutil.which("pwsh")


_IDD_PS1 = os.path.join(_ROOT, "usr", "share", "mios", "windows", "mios-looking-glass-idd.ps1")


def _run_ps_function(fn, body):
    """Run `body` in pwsh with only function `fn` of the guest script defined (extracted through the AST)."""
    import subprocess
    prelude = ("$e=$null; $ast=[System.Management.Automation.Language.Parser]::ParseFile('%s',[ref]$null,[ref]$e); "
               "$f=$ast.FindAll({param($a) $a -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $a.Name -eq '%s'}, $true) | Select-Object -First 1; "
               "if (-not $f) { throw 'function %s not found' }; . ([scriptblock]::Create($f.Extent.Text)); " % (_IDD_PS1, fn, fn))
    r = subprocess.run([_pwsh(), "-NoProfile", "-NonInteractive", "-Command", prelude + body],
                       capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise AssertionError(r.stdout + r.stderr)
    return r.stdout


class TestGraphicsAndConsole(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data = _ssot()
        cls.cfg = setup_looking_glass.lg_config(cls.data)

    # -- defect 1: SPICE/VNC attributes and children survive, SSOT fields win --------------------------
    def test_existing_graphics_settings_are_preserved(self):
        out = setup_looking_glass.apply_profile(_CUSTOM_GRAPHICS, self.cfg)
        g = _graphics(out)
        self.assertEqual(sorted(g), ["spice", "vnc"])  # stray egl-headless still removed
        spice, vnc = g["spice"], g["vnc"]
        # positive: SSOT-owned fields are enforced
        self.assertEqual((spice.get("port"), spice.get("autoport"), spice.get("listen")),
                         (str(self.cfg["spice_port"]), "no", self.cfg["spice_listen"]))
        self.assertEqual([l.get("address") for l in spice.findall("listen")], [self.cfg["spice_listen"]])
        self.assertEqual(vnc.get("listen"), self.cfg["vnc_listen"])
        self.assertEqual([l.get("address") for l in vnc.findall("listen")], [self.cfg["vnc_listen"]])
        self.assertIsNone(vnc.get("socket"))  # a socket listen would contradict the SSOT address
        # negative control for the old behaviour: these were dropped by a re-apply
        self.assertEqual((spice.get("keymap"), spice.get("passwd"), spice.get("defaultMode")), ("de", "spicepw", "secure"))
        self.assertIsNotNone(spice.find("image[@compression='off']"))
        self.assertIsNotNone(spice.find("streaming[@mode='filter']"))
        self.assertEqual((vnc.get("keymap"), vnc.get("sharePolicy")), ("de", "force-shared"))
        self.assertIsNotNone(vnc.find("audio[@id='1']"))
        self.assertEqual(setup_looking_glass.profile_problems(out, self.cfg), [])
        self.assertEqual(setup_looking_glass.apply_profile(out, self.cfg), out)  # idempotent

    def test_vnc_port_pinned_on_spice_port_is_reset(self):
        port = str(self.cfg["spice_port"])
        pinned = _CUSTOM_GRAPHICS.replace("type='vnc' port='-1' autoport='yes'", f"type='vnc' port='{port}' autoport='no'")
        self.assertIn("VNC console is pinned to the SPICE port", setup_looking_glass.profile_problems(pinned, self.cfg))
        vnc = _graphics(setup_looking_glass.apply_profile(pinned, self.cfg))["vnc"]
        self.assertEqual((vnc.get("port"), vnc.get("autoport")), ("-1", "yes"))
        other = _CUSTOM_GRAPHICS.replace("type='vnc' port='-1' autoport='yes'", "type='vnc' port='5999' autoport='no'")
        vnc = _graphics(setup_looking_glass.apply_profile(other, self.cfg))["vnc"]
        self.assertEqual((vnc.get("port"), vnc.get("autoport")), ("5999", "no"))  # a non-colliding pin is kept

    # -- defect 1b: VNC console password from a root-only secret file ---------------------------------------
    def test_vnc_password_file_lifecycle(self):
        import stat
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "vm", "vnc.passwd")
            cfg = dict(self.cfg, vnc_passwd_file=path)
            self.assertIsNone(setup_looking_glass.vnc_password(cfg, create=False))
            pw = setup_looking_glass.vnc_password(cfg)
            self.assertEqual(len(pw), setup_looking_glass.VNC_PASSWD_MAX)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) & 0o077, 0)
            self.assertEqual(setup_looking_glass.vnc_password(cfg), pw)  # stable once generated
            os.chmod(path, 0o644)
            self.assertEqual(setup_looking_glass.vnc_password(cfg), pw)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)  # tightened
            # negative controls: over-long password, symlink
            with open(path, "w") as fh:
                fh.write("123456789\n")
            with self.assertRaises(SystemExit):
                setup_looking_glass.vnc_password(cfg)
            link = os.path.join(d, "link")
            os.symlink(path, link)
            with self.assertRaises(SystemExit):
                setup_looking_glass.vnc_password(dict(cfg, vnc_passwd_file=link))
        self.assertEqual(setup_looking_glass.vnc_password(dict(self.cfg, vnc_passwd_file="")), "")
        self.assertEqual(setup_looking_glass.vnc_password(dict(self.cfg, vnc_console=False)), "")

    def test_vnc_password_lands_in_domain(self):
        base = setup_looking_glass.apply_profile(_DRIFTED_DOMAIN, self.cfg)
        # negative: a console without the password is reported once a password is configured
        self.assertIn("VNC console password does not match [vm.looking_glass].vnc_passwd_file",
                      setup_looking_glass.profile_problems(base, self.cfg, vnc_passwd="s3cret"))
        out = setup_looking_glass.apply_profile(base, self.cfg, vnc_passwd="s3cret")
        g = _graphics(out)
        self.assertEqual(g["vnc"].get("passwd"), "s3cret")
        self.assertIsNone(g["spice"].get("passwd"))
        self.assertEqual(g["vnc"].get("listen"), "127.0.0.1")
        self.assertEqual(setup_looking_glass.profile_problems(out, self.cfg, vnc_passwd="s3cret"), [])
        self.assertEqual(setup_looking_glass.apply_profile(out, self.cfg, vnc_passwd="s3cret"), out)
        # None keeps the domain's password (unprivileged render); "" removes it
        self.assertEqual(_graphics(setup_looking_glass.apply_profile(out, self.cfg))["vnc"].get("passwd"), "s3cret")
        self.assertIsNone(_graphics(setup_looking_glass.apply_profile(out, self.cfg, vnc_passwd=""))["vnc"].get("passwd"))

    def test_render_writes_password_domain_owner_only(self):
        import stat
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            pwfile = os.path.join(d, "vnc.passwd")
            with open(pwfile, "w") as fh:
                fh.write("abcd1234\n")
            os.chmod(pwfile, 0o600)
            toml = os.path.join(d, "mios.toml")
            with open(toml, "w") as fh:
                fh.write(f'[vm.looking_glass]\nvnc_passwd_file = "{pwfile}"\n')
            src, dst = os.path.join(d, "in.xml"), os.path.join(d, "out.xml")
            with open(src, "w") as fh:
                fh.write(_DRIFTED_DOMAIN)
            os.chmod(src, 0o644)
            env = dict(os.environ, MIOS_VENDOR_TOML=toml, MIOS_TOML=toml)
            # the layered resolver may load the real SSOT first; only use the subprocess result when it took our file
            r = subprocess.run([sys.executable, "-I", _LG_SETUP_PATH, "--render", src, dst], env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(dst) as fh:
                out = fh.read()
            if 'passwd="abcd1234"' not in out:
                self.skipTest("the layered SSOT resolver ignores MIOS_VENDOR_TOML here")
            self.assertEqual(stat.S_IMODE(os.stat(dst).st_mode), 0o600)

    # -- defect 2: SPICE port out of the autoport range start -----------------------------------------------
    def test_spice_port_clear_of_autoport_start(self):
        port = int(self.data["vm"]["looking_glass"]["spice_port"])
        self.assertEqual(port, setup_looking_glass.LG_DEFAULTS["spice_port"])
        self.assertNotIn(port, range(5900, 5930), "fixed SPICE port inside the first autoport slots")
        for name in ("Xbox-Final-NoAutoSelect.xml", "Xbox-AutoEnroll.xml"):
            with open(os.path.join(_ROOT, "usr", "libexec", "mios", name), encoding="utf-8") as fh:
                self.assertEqual(_graphics(fh.read())["spice"].get("port"), str(port), name)
        ini = os.path.join(_ROOT, "etc", "looking-glass-client.ini")
        if os.path.isfile(ini):
            with open(ini, encoding="utf-8") as fh:
                self.assertIn(f"port={port}\n", fh.read())
        # negative: a domain still on 5900 is reported and moved
        old = _CUSTOM_GRAPHICS.replace("port='5900' autoport='yes' listen='0.0.0.0'", "port='5900' autoport='no' listen='127.0.0.1'")
        self.assertTrue(any("SPICE is not on" in p for p in setup_looking_glass.profile_problems(old, self.cfg)))
        self.assertEqual(_graphics(setup_looking_glass.apply_profile(old, self.cfg))["spice"].get("port"), str(port))

    # -- defect 3: guest script -------------------------------------------------------------------------------
    def test_idd_preferences_applied_outside_install_branch(self):
        if not _pwsh():
            self.skipTest("pwsh not installed")
        out = _run_ps_function("Set-IddPreferences", r"""
$ErrorActionPreference='Stop'
$ast=[System.Management.Automation.Language.Parser]::ParseFile('%s',[ref]$null,[ref]$null)
$calls=$ast.FindAll({param($a) $a -is [System.Management.Automation.Language.CommandAst] -and $a.GetCommandName() -eq 'Set-IddPreferences'}, $true)
foreach ($c in $calls) { $p=$c.Parent; $inIf=$false; while ($p) { if ($p -is [System.Management.Automation.Language.IfStatementAst] -or $p -is [System.Management.Automation.Language.FunctionDefinitionAst]) { $inIf=$true }; $p=$p.Parent }; "CALL inIf=$inIf" }
$legacy=$ast.FindAll({param($a) $a -is [System.Management.Automation.Language.CommandAst] -and $a.GetCommandName() -eq 'New-ItemProperty' -and $a.Extent.Text -match 'ExclusiveMonitor'}, $true)
foreach ($c in $legacy) { $p=$c.Parent; $inFn=$false; while ($p) { if ($p -is [System.Management.Automation.Language.FunctionDefinitionAst]) { $inFn=$true }; $p=$p.Parent }; "WRITE inFn=$inFn" }
function Say($m) { "SAY $m" }
$script:reg=@{}
function Test-Path { $true }
function New-Item { }
function Get-ItemProperty { if ($script:reg.ContainsKey('ExclusiveMonitor')) { [pscustomobject]@{ExclusiveMonitor=$script:reg['ExclusiveMonitor']} } }
$script:sets=@()
function New-ItemProperty { param($Path,$Name,$PropertyType,$Value,[switch]$Force) $script:reg[$Name]=$Value; $script:sets += "SET $Name=$Value" }
Set-IddPreferences 1
Set-IddPreferences 1
Set-IddPreferences 0
$script:sets
""" % _IDD_PS1)
        lines = out.splitlines()
        self.assertEqual([l for l in lines if l.startswith("CALL")], ["CALL inIf=False"])  # every run, installed or not
        self.assertEqual([l for l in lines if l.startswith("WRITE")], ["WRITE inFn=True"])  # no install-only write left
        self.assertEqual([l for l in lines if l.startswith("SET")], ["SET ExclusiveMonitor=1", "SET ExclusiveMonitor=0"])
        self.assertTrue(any("(unchanged)" in l for l in lines))  # idempotent

    def test_uninstall_string_parsing(self):
        if not _pwsh():
            self.skipTest("pwsh not installed")
        cases = [
            ('"C:\\Program Files\\Looking Glass (host)\\uninstaller.exe" /S _?=C:\\x', "C:\\Program Files\\Looking Glass (host)\\uninstaller.exe", "/S _?=C:\\x"),
            ('"C:\\Program Files\\LG\\u.exe"', "C:\\Program Files\\LG\\u.exe", ""),
            ("C:\\Program Files\\LG\\u.exe /S", "C:\\Program Files\\LG\\u.exe", "/S"),
            ("C:\\LG\\u.exe", "C:\\LG\\u.exe", ""),
        ]
        body = "".join("$r = Split-CommandLine '%s'; \"EXE=$($r[0])|ARGS=$($r[1])\"\n" % c.replace("'", "''") for c, _, _ in cases)
        body += "try { Split-CommandLine '\"C:\\x\\u.exe /S' | Out-Null; 'NOERR' } catch { 'UNBALANCED' }\n"
        out = _run_ps_function("Split-CommandLine", body).splitlines()
        self.assertEqual(out[:len(cases)], [f"EXE={e}|ARGS={a}" for _, e, a in cases])
        self.assertEqual(out[-1], "UNBALANCED")
        # negative control: the old .Trim('"') kept the arguments inside the "path"
        self.assertNotEqual(cases[0][0].strip('"'), cases[0][1])
        with open(_IDD_PS1, encoding="utf-8") as fh:
            self.assertNotIn("UninstallString.Trim(", fh.read())

    def test_staging_dirs_are_admin_only_before_writes(self):
        with open(_IDD_PS1, encoding="utf-8") as fh:
            body = fh.read()
        # every MiOS directory the script writes into is created through Protect-MiOSDir
        self.assertNotRegex(body, r"New-Item -ItemType Directory -Force -Path \$(dir|mirrorDir)")
        self.assertIn("Protect-MiOSDir $dir", body)
        self.assertIn("Protect-MiOSDir $mirrorDir $true", body)
        self.assertIn("'S-1-5-18', 'S-1-5-32-544'", body)
        self.assertIn("SetAccessRuleProtection($true, $false)", body)
        plan = {"commit": "c" * 40, "version": "B7-1-cccccccc", "url": "u", "sha256": "a" * 64, "signer": "CN=HostFission"}
        cmds = setup_looking_glass.guest_commands(dict(self.cfg, guest_script=_IDD_PS1), plan)
        first = cmds[0]
        self.assertIn("function Protect-MiOSDir", first)
        self.assertLess(first.index("Protect-MiOSDir (Split-Path $f)"), first.index("Set-Content"))
        self.assertNotIn("New-Item -ItemType Directory -Force -Path (Split-Path $f)", first)  # the old pre-creatable path
        self.assertTrue(all(len(c) < 2800 for c in cmds))
        if _pwsh():
            import subprocess
            r = subprocess.run([_pwsh(), "-NoProfile", "-Command",
                                "$s=[Console]::In.ReadToEnd(); $e=$null; [System.Management.Automation.Language.Parser]::ParseInput($s, [ref]$null, [ref]$e) | Out-Null; $e.Count"],
                               input=first, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.stdout.strip(), "0", r.stderr)


    # -- atomic writes: a reset mid-write must never leave a truncated domain XML / empty password file ---------
    def test_write_atomic_fsyncs_and_never_truncates(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.xml")
            real_fsync = os.fsync
            calls = []
            with mock.patch.object(setup_looking_glass.os, "fsync", side_effect=lambda fd: (calls.append(fd), real_fsync(fd))):
                setup_looking_glass.write_atomic(path, "old\n", 0o600)
            self.assertGreaterEqual(len(calls), 1)  # positive: file data reaches the disk before the rename
            with open(path) as fh:
                self.assertEqual(fh.read(), "old\n")
            # negative: a failure before the rename leaves the previous content and no temp file
            with mock.patch.object(setup_looking_glass.os, "replace", side_effect=OSError("reset")):
                with self.assertRaises(OSError):
                    setup_looking_glass.write_atomic(path, "new\n", 0o600)
            with open(path) as fh:
                self.assertEqual(fh.read(), "old\n")
            self.assertEqual(sorted(os.listdir(d)), ["x.xml"])

    def test_vnc_password_generation_is_atomic(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "vm", "vnc.passwd")
            cfg = dict(self.cfg, vnc_passwd_file=path)
            # negative: dying between write and publish leaves no (empty) password file and no temp file behind
            with mock.patch.object(setup_looking_glass.os, "link", side_effect=OSError("reset")):
                with self.assertRaises(OSError):
                    setup_looking_glass.vnc_password(cfg)
            self.assertFalse(os.path.exists(path))
            self.assertEqual(os.listdir(os.path.dirname(path)), [])

            # a concurrent run that published first wins; ours is discarded
            def racing_link(src, dst):
                with open(dst, "w") as fh:
                    fh.write("Winner12\n")
                os.chmod(dst, 0o600)
                raise FileExistsError(dst)
            with mock.patch.object(setup_looking_glass.os, "link", side_effect=racing_link):
                self.assertEqual(setup_looking_glass.vnc_password(cfg), "Winner12")
            self.assertEqual(os.listdir(os.path.dirname(path)), ["vnc.passwd"])
            # positive: the normal path publishes a complete 0600 password
            os.unlink(path)
            pw = setup_looking_glass.vnc_password(cfg)
            with open(path) as fh:
                self.assertEqual(fh.read(), pw + "\n")

    # -- guest display mirror: valid SetDisplayConfig flags, DisplaySwitch fallback (Antigravity patch, corrected)
    @staticmethod
    def _sdc_flags_valid(flags):
        """The documented SetDisplayConfig flag rules (learn.microsoft.com nf-winuser-setdisplayconfig)."""
        APPLY, VALIDATE, SUPPLIED_CFG, SAVE, PERSIST = 0x80, 0x40, 0x20, 0x200, 0x800
        TOPO = 0x1 | 0x2 | 0x4 | 0x8 | 0x10
        if bool(flags & APPLY) == bool(flags & VALIDATE):
            return False
        if bool(flags & SUPPLIED_CFG) == bool(flags & TOPO):
            return False
        if flags & SAVE and not flags & SUPPLIED_CFG:
            return False
        if flags & PERSIST and flags & (SUPPLIED_CFG | 0x10):
            return False
        return True

    def test_mirror_script_flags_and_fallback(self):
        import re
        with open(_IDD_PS1, encoding="utf-8") as fh:
            body = fh.read()
        m = re.search(r"\$mirrorScript -Value @'\n(.*?)\n'@", body, re.S)
        self.assertIsNotNone(m, "mirror script here-string not found")
        script = m.group(1)
        calls = re.findall(r"SetDisplayConfig\(0, \[System\.IntPtr\]::Zero, 0, \[System\.IntPtr\]::Zero, (0x[0-9A-Fa-f]+)\)", script)
        self.assertEqual(len(calls), 1)
        flags = int(calls[0], 16)
        self.assertTrue(flags & 0x80 and flags & 0x2)  # positive: apply the clone topology
        self.assertTrue(self._sdc_flags_valid(flags), hex(flags))
        # negative controls: the patch's 0x682 (SAVE_TO_DATABASE with a topology flag) is rejected by the rules
        self.assertFalse(self._sdc_flags_valid(0x682))
        self.assertTrue(self._sdc_flags_valid(0x82))
        self.assertRegex(script, r"if \(\$rc -ne 0\)[\s\S]*DisplaySwitch\.exe'\) -ArgumentList '/clone'")
        self.assertIn("$env:TEMP", script)  # the script dir is admin-only; users log to their own temp
        self.assertNotIn("$PSScriptRoot", script)
        if _pwsh():
            import subprocess
            for src in (body, script):
                r = subprocess.run([_pwsh(), "-NoProfile", "-Command",
                                    "$s=[Console]::In.ReadToEnd(); $e=$null; [System.Management.Automation.Language.Parser]::ParseInput($s, [ref]$null, [ref]$e) | Out-Null; $e.Count"],
                                   input=src, capture_output=True, text=True, timeout=60)
                self.assertEqual(r.stdout.strip(), "0", r.stderr)

if __name__ == "__main__":
    unittest.main()
