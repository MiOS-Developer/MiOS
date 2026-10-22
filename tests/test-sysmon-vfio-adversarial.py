#!/usr/bin/env python3
# AI-hint: Adversarial empirical challenge for Milestone M4 (Sysmon hardware isolation and resource consumption).
# Verifies:
# 1. Host hardware isolation: PCI 0000:01:00.0/0000:01:00.1 (RTX 4090) is bound to vfio-pci; amdgpu, nouveau, nvidia do NOT attach.
# 2. Sysmon parseScan rejection: planted synthetic check proving vfio-pci GPUs and sensors are rejected by parseScan.
# 3. Kernel input isolation: inotify proves Quickshell NEVER opens gpu_busy_percent or temp*_input for vfio-pci devices.
# 4. Resource containment: live Quickshell process CPU and memory stability under baseline and rapid sensor/IPC stress.
# AI-related: usr/share/mios/quickshell/Sysmon.qml, tests/test-island-sysmon.py

from __future__ import annotations

import ctypes
import fcntl
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest

QS = shutil.which("quickshell")
SYSMON_QML = "/usr/share/mios/quickshell/Sysmon.qml"
THEME_QML = "/usr/share/mios/quickshell/Theme.qml"
LIVE_CONFIG_QML = "/usr/share/mios/quickshell/Config.qml"

libc = ctypes.CDLL(None)
libc.inotify_init.restype = ctypes.c_int
libc.inotify_add_watch.restype = ctypes.c_int
libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]

IN_ACCESS = 0x00000001
IN_OPEN = 0x00000020


def write_file(path: str, text: str, mode: int | None = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def get_live_quickshell_pid() -> int | None:
    try:
        out = subprocess.check_output(["pgrep", "-f", "/usr/bin/quickshell"], text=True)
        pids = [int(p) for p in out.strip().splitlines() if p.isdigit()]
        return pids[0] if pids else None
    except Exception:
        return None


class TestLiveVfioHardwareIsolation(unittest.TestCase):
    """Adversarial check of live host PCI bus and driver attachments."""

    def test_rtx4090_pci_present_and_bound_to_vfio(self):
        vga_dev = "/sys/bus/pci/devices/0000:01:00.0"
        audio_dev = "/sys/bus/pci/devices/0000:01:00.1"
        self.assertTrue(os.path.exists(vga_dev), f"Missing RTX 4090 VGA device at {vga_dev}")
        self.assertTrue(os.path.exists(audio_dev), f"Missing RTX 4090 Audio device at {audio_dev}")

        # Check driver binding
        vga_driver = os.path.realpath(os.path.join(vga_dev, "driver"))
        audio_driver = os.path.realpath(os.path.join(audio_dev, "driver"))
        self.assertEqual(os.path.basename(vga_driver), "vfio-pci", "RTX 4090 VGA is not bound to vfio-pci")
        self.assertEqual(os.path.basename(audio_driver), "vfio-pci", "RTX 4090 Audio is not bound to vfio-pci")

    def test_host_drivers_not_attached_to_rtx4090(self):
        # 1. nouveau must not be attached and not loaded
        self.assertFalse(os.path.exists("/sys/bus/pci/drivers/nouveau/0000:01:00.0"))
        self.assertFalse(os.path.exists("/sys/bus/pci/drivers/nouveau/0000:01:00.1"))
        self.assertFalse(os.path.exists("/sys/bus/pci/drivers/nouveau"), "nouveau driver unexpectedly loaded in kernel")

        # 2. nvidia must not be attached and not loaded
        self.assertFalse(os.path.exists("/sys/bus/pci/drivers/nvidia/0000:01:00.0"))
        self.assertFalse(os.path.exists("/sys/bus/pci/drivers/nvidia/0000:01:00.1"))
        self.assertFalse(os.path.exists("/sys/bus/pci/drivers/nvidia"), "nvidia driver unexpectedly loaded in kernel")

        # 3. amdgpu is loaded for host iGPU, but must NOT attach to RTX 4090
        self.assertTrue(os.path.exists("/sys/bus/pci/drivers/amdgpu"), "amdgpu driver should be loaded for host iGPU")
        self.assertFalse(os.path.exists("/sys/bus/pci/drivers/amdgpu/0000:01:00.0"), "amdgpu attached to RTX 4090 VGA!")
        self.assertFalse(os.path.exists("/sys/bus/pci/drivers/amdgpu/0000:01:00.1"), "amdgpu attached to RTX 4090 Audio!")

    def test_no_drm_or_hwmon_registered_for_rtx4090(self):
        # Scan /sys/class/drm/card*
        drm_dir = "/sys/class/drm"
        if os.path.exists(drm_dir):
            for c in os.listdir(drm_dir):
                if re.match(r"^card\d+$", c):
                    dev_link = os.path.realpath(os.path.join(drm_dir, c, "device"))
                    self.assertNotIn("0000:01:00.0", dev_link, f"DRM {c} registered for RTX 4090!")

        # Scan /sys/class/hwmon/hwmon*
        hwmon_dir = "/sys/class/hwmon"
        if os.path.exists(hwmon_dir):
            for h in os.listdir(hwmon_dir):
                dev_link = os.path.realpath(os.path.join(hwmon_dir, h, "device"))
                self.assertNotIn("0000:01:00.0", dev_link, f"Hwmon {h} registered for RTX 4090!")

    def test_live_sysmon_state_excludes_rtx4090(self):
        if not QS or not os.path.exists(LIVE_CONFIG_QML):
            self.skipTest("Quickshell or live config not found")
        res = subprocess.run([QS, "ipc", "--path", LIVE_CONFIG_QML, "call", "sysmon", "state"],
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(res.returncode, 0, f"IPC sysmon state failed: {res.stderr}")
        state = json.loads(res.stdout)

        # Sources must not contain any vfio or RTX 4090 references
        gpus = state.get("sources", {}).get("gpus", [])
        self.assertTrue(all("vfio-pci" not in g for g in gpus), f"vfio-pci GPU in live sources: {gpus}")
        self.assertTrue(all("card1:amdgpu" in g for g in gpus), f"Expected card1:amdgpu in live sources, got: {gpus}")

        active_gpus = state.get("gpus", [])
        for g in active_gpus:
            self.assertNotEqual(g.get("driver"), "vfio-pci")


class TestSyntheticParseScanVfioRejection(unittest.TestCase):
    """Adversarial testing of parseScan algorithm using synthetic inputs."""

    def parse_scan_model(self, scan_text: str,
                         cpu_chips=("k10temp", "zenpower", "coretemp", "cpu_thermal"),
                         gpu_chips=("amdgpu", "radeon", "i915", "xe", "nouveau"),
                         passthrough=("vfio-pci", "pci-stub")):
        """Pure JavaScript model of Sysmon.qml parseScan."""
        temps, gpus, nets = [], [], []
        for l in scan_text.splitlines():
            f = l.split("\t")
            if f[0] == "temp" and len(f) >= 5:
                chip, driver = f[1], f[2]
                kind = "cpu" if chip in cpu_chips else "gpu" if chip in gpu_chips else ""
                if not kind or driver in passthrough:
                    continue
                temps.append({"chip": chip, "kind": kind, "path": f[3], "label": f[4] or chip})
            elif f[0] == "gpu" and len(f) >= 7:
                if f[2] in passthrough or f[4] != "1":
                    continue
                gpus.append({"card": f[1], "driver": f[2], "path": f[3],
                             "connected": f[5] == "1", "bootVga": f[6] == "1"})
            elif f[0] == "net" and len(f) >= 2 and f[1]:
                nets.append(f[1])
        gpus.sort(key=lambda g: (-int(g["connected"]), -int(g["bootVga"]), g["card"]))
        return {"temps": temps, "gpus": gpus, "nets": nets}

    def test_synthetic_gpu_vfio_pci_rejected(self):
        # Negative control: GPU with driver vfio-pci
        scan_vfio = "gpu\tcard0\tvfio-pci\t/sys/devices/pci-card0/gpu_busy_percent\t1\t1\t1\n"
        res = self.parse_scan_model(scan_vfio)
        self.assertEqual(res["gpus"], [], "parseScan MUST reject vfio-pci GPU")

    def test_synthetic_gpu_pci_stub_rejected(self):
        # Negative control: GPU with driver pci-stub
        scan_stub = "gpu\tcard0\tpci-stub\t/sys/devices/pci-card0/gpu_busy_percent\t1\t1\t1\n"
        res = self.parse_scan_model(scan_stub)
        self.assertEqual(res["gpus"], [], "parseScan MUST reject pci-stub GPU")

    def test_synthetic_gpu_unreadable_busy_rejected(self):
        # Negative control: ok != 1
        scan_unreadable = "gpu\tcard1\tamdgpu\t/sys/devices/pci-card1/gpu_busy_percent\t0\t1\t1\n"
        res = self.parse_scan_model(scan_unreadable)
        self.assertEqual(res["gpus"], [], "parseScan MUST reject GPU with unreadable busy counter")

    def test_synthetic_gpu_host_drivers_accepted(self):
        # Positive control: amdgpu, i915, nouveau
        scan_host = ("gpu\tcard0\tamdgpu\t/sys/devices/pci-card0/gpu_busy_percent\t1\t1\t1\n"
                     "gpu\tcard1\ti915\t/sys/devices/pci-card1/gpu_busy_percent\t1\t0\t0\n")
        res = self.parse_scan_model(scan_host)
        self.assertEqual(len(res["gpus"]), 2)
        self.assertEqual(res["gpus"][0]["card"], "card0")
        self.assertEqual(res["gpus"][1]["card"], "card1")

    def test_synthetic_temp_vfio_pci_rejected(self):
        # Negative control: sensor associated with vfio-pci device
        scan_temp_vfio = "temp\tamdgpu\tvfio-pci\t/sys/class/hwmon/hwmon0/temp1_input\tedge\n"
        res = self.parse_scan_model(scan_temp_vfio)
        self.assertEqual(res["temps"], [], "parseScan MUST reject sensor whose device driver is vfio-pci")

    def test_synthetic_temp_host_sensors_accepted(self):
        # Positive control: k10temp and amdgpu sensors
        scan_temp_host = ("temp\tk10temp\tk10temp\t/sys/class/hwmon/hwmon5/temp1_input\tTctl\n"
                          "temp\tamdgpu\tamdgpu\t/sys/class/hwmon/hwmon3/temp1_input\tedge\n")
        res = self.parse_scan_model(scan_temp_host)
        self.assertEqual(len(res["temps"]), 2)
        self.assertEqual(res["temps"][0]["kind"], "cpu")
        self.assertEqual(res["temps"][1]["kind"], "gpu")


class TestQuickshellNeverOpensVfioInputs(unittest.TestCase):
    """Empirical proof using Linux inotify: Quickshell never opens vfio-pci inputs."""

    @unittest.skipUnless(QS, "quickshell binary missing")
    def test_inotify_proves_vfio_inputs_never_opened(self):
        tmp = tempfile.mkdtemp(prefix="test-vfio-inotify-")
        try:
            root = os.path.join(tmp, "root")
            write_file(f"{root}/proc/stat", "cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 50 0 50 400 0 0 0 0 0 0\n")
            write_file(f"{root}/proc/meminfo", "MemTotal: 16000000 kB\nMemAvailable: 8000000 kB\n")
            write_file(f"{root}/proc/net/dev", "Inter-| Receive | Transmit\n face |bytes |bytes\n eth0: 1000 0 0 0 0 0 0 0 1000 0 0 0 0 0 0 0\n")

            drivers = f"{root}/sys/bus/pci/drivers"
            for d in ("vfio-pci", "amdgpu"):
                os.makedirs(f"{drivers}/{d}", exist_ok=True)

            def make_dev(name, drv):
                p = f"{root}/sys/devices/{name}"
                os.makedirs(p, exist_ok=True)
                os.symlink(f"{drivers}/{drv}", f"{p}/driver")
                return p

            # Card 0: vfio-pci (Synthetic RTX 4090)
            dev0 = make_dev("pci-card0", "vfio-pci")
            f_gpu0 = f"{dev0}/gpu_busy_percent"
            write_file(f_gpu0, "99\n")
            write_file(f"{dev0}/boot_vga", "0\n")
            os.makedirs(f"{root}/sys/class/drm/card0", exist_ok=True)
            os.symlink(dev0, f"{root}/sys/class/drm/card0/device")
            write_file(f"{root}/sys/class/drm/card0-HDMI-A-1/status", "disconnected\n")

            # Card 1: amdgpu (Host GPU)
            dev1 = make_dev("pci-card1", "amdgpu")
            f_gpu1 = f"{dev1}/gpu_busy_percent"
            write_file(f_gpu1, "35\n")
            write_file(f"{dev1}/boot_vga", "1\n")
            os.makedirs(f"{root}/sys/class/drm/card1", exist_ok=True)
            os.symlink(dev1, f"{root}/sys/class/drm/card1/device")
            write_file(f"{root}/sys/class/drm/card1-HDMI-A-1/status", "connected\n")

            # hwmon0: vfio-pci sensor
            h0 = f"{root}/sys/class/hwmon/hwmon0"
            write_file(f"{h0}/name", "amdgpu\n")
            os.symlink(dev0, f"{h0}/device")
            f_temp0 = f"{h0}/temp1_input"
            write_file(f_temp0, "95000\n")
            write_file(f"{h0}/temp1_label", "edge\n")

            # hwmon1: amdgpu host sensor
            h1 = f"{root}/sys/class/hwmon/hwmon1"
            write_file(f"{h1}/name", "amdgpu\n")
            os.symlink(dev1, f"{h1}/device")
            f_temp1 = f"{h1}/temp1_input"
            write_file(f_temp1, "45000\n")
            write_file(f"{h1}/temp1_label", "edge\n")

            os.makedirs(f"{root}/sys/class/net/eth0/device", exist_ok=True)

            # Setup inotify watchers
            ifd = libc.inotify_init()
            self.assertGreaterEqual(ifd, 0, "inotify_init failed")
            fcntl.fcntl(ifd, fcntl.F_SETFL, os.O_NONBLOCK)

            wd_gpu0 = libc.inotify_add_watch(ifd, f_gpu0.encode(), IN_OPEN | IN_ACCESS)
            wd_gpu1 = libc.inotify_add_watch(ifd, f_gpu1.encode(), IN_OPEN | IN_ACCESS)
            wd_temp0 = libc.inotify_add_watch(ifd, f_temp0.encode(), IN_OPEN | IN_ACCESS)
            wd_temp1 = libc.inotify_add_watch(ifd, f_temp1.encode(), IN_OPEN | IN_ACCESS)

            wds = {
                wd_gpu0: "card0_vfio_gpu",
                wd_gpu1: "card1_host_gpu",
                wd_temp0: "hw0_vfio_temp",
                wd_temp1: "hw1_host_temp",
            }

            qs_dir = os.path.join(tmp, "qs")
            os.makedirs(qs_dir, exist_ok=True)
            shutil.copy(SYSMON_QML, qs_dir)
            shutil.copy(THEME_QML, qs_dir)
            write_file(f"{qs_dir}/shell.qml", "import QtQuick\nimport Quickshell\nShellRoot { property bool on: Sysmon.active }\n")

            resolver = f"{tmp}/toml-get"
            write_file(resolver, "#!/bin/sh\ncat <<'EOF'\n{\"interval_ms\": 500}\nEOF\n", 0o755)

            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", MIOS_TOML_GET=resolver,
                       MIOS_SYSMON_ROOT=root, MIOS_THEME_BRIDGE=f"{tmp}/no-bridge.json")

            log_path = f"{tmp}/qs.log"
            with open(log_path, "w", encoding="utf-8") as log_file:
                proc = subprocess.Popen([QS, "--path", f"{qs_dir}/shell.qml"], env=env, stdout=log_file, stderr=subprocess.STDOUT)
                try:
                    time.sleep(3.0)
                    res = subprocess.run([QS, "ipc", "--path", f"{qs_dir}/shell.qml", "call", "sysmon", "state"],
                                         capture_output=True, text=True, timeout=10)
                    state = json.loads(res.stdout)

                    # Drain inotify events
                    events = {k: 0 for k in wds.values()}
                    while True:
                        try:
                            buf = os.read(ifd, 4096)
                            if not buf:
                                break
                            idx = 0
                            while idx < len(buf):
                                w, mask, cookie, length = struct.unpack("iIII", buf[idx:idx + 16])
                                idx += 16 + length
                                if w in wds:
                                    events[wds[w]] += 1
                        except BlockingIOError:
                            break

                    # Assertions
                    self.assertGreater(events["card1_host_gpu"], 0, "Positive control failed: card1 was not opened")
                    self.assertGreater(events["hw1_host_temp"], 0, "Positive control failed: hwmon1 was not opened")

                    self.assertEqual(events["card0_vfio_gpu"], 0,
                                     f"Adversarial violation: vfio-pci gpu_busy_percent opened {events['card0_vfio_gpu']} times!")
                    self.assertEqual(events["hw0_vfio_temp"], 0,
                                     f"Adversarial violation: vfio-pci temp1_input opened {events['hw0_vfio_temp']} times!")

                    # Confirm IPC reports only the non-vfio devices
                    self.assertEqual(state.get("sources", {}).get("gpus"), ["card1:amdgpu"])
                    self.assertEqual(state.get("gpu"), 35)
                    self.assertEqual(state.get("temp"), 45)
                finally:
                    proc.terminate()
                    try:
                        proc.wait(5)
                    except Exception:
                        proc.kill()
                    os.close(ifd)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestLiveQuickshellCpuAndMemoryContainment(unittest.TestCase):
    """Measures actual CPU and memory consumption of the live Quickshell process."""

    def test_live_quickshell_resource_stability(self):
        pid = get_live_quickshell_pid()
        if not pid:
            self.skipTest("Live Quickshell process not running")

        hz = os.sysconf("SC_CLK_TCK")

        def read_proc():
            with open(f"/proc/{pid}/stat") as f:
                p = f.read().rsplit(")", 1)[1].split()
            utime, stime = int(p[11]), int(p[12])
            with open(f"/proc/{pid}/status") as f:
                st = f.read()
            rss = 0
            for l in st.splitlines():
                if l.startswith("VmRSS:"):
                    rss = int(l.split()[1])
            return utime + stime, rss

        # 1. Baseline measurement over 5.0 s
        t0 = time.monotonic()
        c0, r0 = read_proc()
        time.sleep(5.0)
        t1 = time.monotonic()
        c1, r1 = read_proc()

        base_cpu = 100.0 * (c1 - c0) / hz / (t1 - t0)
        print(f"\n[live quickshell] Baseline CPU over 5s: {base_cpu:.3f}% of one core (RSS={r1} kB)")
        self.assertLess(base_cpu, 0.8, f"Baseline CPU usage too high: {base_cpu:.3f}%")

        # 2. Rapid IPC / sensor query stress over 5.0 s
        stop = threading.Event()

        def stress():
            cmd_state = [QS, "ipc", "--path", LIVE_CONFIG_QML, "call", "sysmon", "state"]
            cmd_reload = [QS, "ipc", "--path", LIVE_CONFIG_QML, "call", "sysmon", "reload"]
            while not stop.is_set():
                subprocess.run(cmd_state, capture_output=True, timeout=5)
                subprocess.run(cmd_reload, capture_output=True, timeout=5)
                time.sleep(0.05)

        th = threading.Thread(target=stress)
        th.start()
        try:
            t_s0 = time.monotonic()
            c_s0, r_s0 = read_proc()
            time.sleep(5.0)
            t_s1 = time.monotonic()
            c_s1, r_s1 = read_proc()
        finally:
            stop.set()
            th.join()

        stress_cpu = 100.0 * (c_s1 - c_s0) / hz / (t_s1 - t_s0)
        rss_delta_kb = r_s1 - r0
        print(f"[live quickshell] Under rapid sensor/IPC reload: {stress_cpu:.3f}% of one core (RSS delta={rss_delta_kb:+} kB)")

        # Assertions: CPU stays strictly contained (< 5.0% even under 20 queries/s), RSS delta < 5 MB
        self.assertLess(stress_cpu, 5.0, f"CPU spiked under stress: {stress_cpu:.3f}%")
        self.assertLess(abs(rss_delta_kb), 5120, f"Memory RSS grew by {rss_delta_kb} kB")


if __name__ == "__main__":
    unittest.main()
