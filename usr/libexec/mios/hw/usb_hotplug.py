#!/usr/bin/env python3
# AI-hint: USB hotplug manager routing game controllers and audio DACs dynamically to guests.
# AI-related: usr/libexec/mios/hw/usb_hotplug.py, tests/test-hw.py
"""USB hotplug manager routing game controllers and audio DACs dynamically to guests.

Scans USB bus topology, classifies devices into eligible guest passthrough targets
(Xbox, PlayStation, Nintendo, 8BitDo, USB Audio DACs) while strictly protecting
host keyboards, mice, and critical inputs from accidental detachment.

Architectural Invariant:
Do NOT hotplug host keyboards or mice that would lock the operator out of the host OS.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mios-usb-hotplug")

# Known Controller Vendor IDs
CONTROLLER_VENDORS = {
    "045e": "Microsoft (Xbox)",
    "054c": "Sony (PlayStation)",
    "057e": "Nintendo",
    "2dc8": "8BitDo",
    "0e6f": "PDP (Xbox/Switch)",
    "24c6": "PowerA",
    "1532": "Razer",
    "0738": "Mad Catz",
}

# Known Controller Specific Product IDs (for vendors that make other peripherals like Logitech)
CONTROLLER_PIDS = {
    "046d": ["c216", "c218", "c219", "c21d", "c21f", "c242", "c24f", "c260", "c262", "c29b"],  # Logitech F310/F510/F710/G29
}

# Known USB DAC / Audio Vendor IDs
AUDIO_DAC_VENDORS = {
    "0d8c": "C-Media Audio DAC",
    "08bb": "Texas Instruments PCM DAC",
    "262a": "FiiO Electronics",
    "1235": "Focusrite Audio Engineering",
    "20b1": "XMOS Audio DAC",
    "2972": "Schiit Audio",
    "19f7": "RØDE Microphones / DAC",
    "041e": "Creative Sound Blaster",
    "1bcf": "USB Audio DAC",
}

class USBDeviceDescriptor:
    """Represents a USB physical or logical device inspected from sysfs."""

    def __init__(
        self,
        sysfs_path: str,
        vendor_id: str,
        product_id: str,
        bus_num: Optional[int] = None,
        dev_num: Optional[int] = None,
        product_name: str = "Unknown Device",
        manufacturer: str = "Unknown Manufacturer",
        device_class: str = "00",
        device_subclass: str = "00",
        device_protocol: str = "00",
        interfaces: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        self.sysfs_path = sysfs_path
        self.vendor_id = vendor_id.lower().replace("0x", "").zfill(4)
        self.product_id = product_id.lower().replace("0x", "").zfill(4)
        self.bus_num = bus_num
        self.dev_num = dev_num
        self.product_name = product_name
        self.manufacturer = manufacturer
        self.device_class = device_class.zfill(2)
        self.device_subclass = device_subclass.zfill(2)
        self.device_protocol = device_protocol.zfill(2)
        self.interfaces = interfaces or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sysfs_path": self.sysfs_path,
            "vendor_id": self.vendor_id,
            "product_id": self.product_id,
            "bus_num": self.bus_num,
            "dev_num": self.dev_num,
            "product_name": self.product_name,
            "manufacturer": self.manufacturer,
            "device_class": self.device_class,
            "device_subclass": self.device_subclass,
            "device_protocol": self.device_protocol,
            "interfaces": self.interfaces,
        }

class USBHotplugManager:
    """Discovers, filters, and generates hotplug XML for guest VM devices."""

    def __init__(
        self,
        sysfs_root: str = "/",
        target_domain: Optional[str] = None,
        dry_run: bool = False,
    ) -> None:
        self.sysfs_root = os.path.abspath(sysfs_root)
        self.target_domain = target_domain
        self.dry_run = dry_run

    @property
    def usb_devices_dir(self) -> str:
        """Return path to sysfs USB devices bus directory."""
        return os.path.join(self.sysfs_root, "sys", "bus", "usb", "devices")

    def _read_file_safe(self, path: str) -> Optional[str]:
        """Safely read string from a sysfs file."""
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError as e:
            logger.debug("Failed reading %s: %s", path, e)
            return None

    def scan_usb_devices(self) -> List[USBDeviceDescriptor]:
        """Scan all connected USB devices from sysfs."""
        pattern = os.path.join(self.usb_devices_dir, "*")
        paths = glob.glob(pattern)
        devices: List[USBDeviceDescriptor] = []

        for p in paths:
            # We look for device nodes that have idVendor and idProduct
            vid = self._read_file_safe(os.path.join(p, "idVendor"))
            pid = self._read_file_safe(os.path.join(p, "idProduct"))
            if not vid or not pid:
                continue

            bus = self._read_file_safe(os.path.join(p, "busnum"))
            dev = self._read_file_safe(os.path.join(p, "devnum"))
            prod_name = self._read_file_safe(os.path.join(p, "product")) or "Generic USB Device"
            mfg = self._read_file_safe(os.path.join(p, "manufacturer")) or "Generic Manufacturer"
            dclass = self._read_file_safe(os.path.join(p, "bDeviceClass")) or "00"
            dsubclass = self._read_file_safe(os.path.join(p, "bDeviceSubClass")) or "00"
            dproto = self._read_file_safe(os.path.join(p, "bDeviceProtocol")) or "00"

            # Inspect interfaces
            interfaces = []
            try:
                for entry in os.listdir(p):
                    idir = os.path.join(p, entry)
                    if os.path.isdir(idir):
                        iclass = self._read_file_safe(os.path.join(idir, "bInterfaceClass"))
                        if iclass:
                            isubclass = self._read_file_safe(os.path.join(idir, "bInterfaceSubClass"))
                            iproto = self._read_file_safe(os.path.join(idir, "bInterfaceProtocol"))
                            interfaces.append({
                                "interface_path": entry,
                                "bInterfaceClass": iclass.zfill(2),
                                "bInterfaceSubClass": (isubclass or "00").zfill(2),
                                "bInterfaceProtocol": (iproto or "00").zfill(2),
                            })
            except OSError:
                pass

            desc = USBDeviceDescriptor(
                sysfs_path=p,
                vendor_id=vid,
                product_id=pid,
                bus_num=int(bus) if bus and bus.isdigit() else None,
                dev_num=int(dev) if dev and dev.isdigit() else None,
                product_name=prod_name,
                manufacturer=mfg,
                device_class=dclass,
                device_subclass=dsubclass,
                device_protocol=dproto,
                interfaces=interfaces,
            )
            devices.append(desc)

        return devices

    def is_host_keyboard_or_mouse(self, dev: USBDeviceDescriptor) -> bool:
        """Evaluate if device is a host keyboard or mouse to prevent operator lockout."""
        # 1. Inspect interface classes for HID Boot Keyboard (03/01/01) and Boot Mouse (03/01/02)
        for iface in dev.interfaces:
            iclass = iface.get("bInterfaceClass", "00")
            isub = iface.get("bInterfaceSubClass", "00")
            iproto = iface.get("bInterfaceProtocol", "00")
            if iclass == "03":
                if isub in ("01", "00") and iproto == "01":  # Keyboard
                    return True
                if isub in ("01", "00") and iproto == "02":  # Mouse
                    return True

        # 2. Check product name heuristics
        lower_name = f"{dev.product_name} {dev.manufacturer}".lower()
        if "keyboard" in lower_name or "keychron" in lower_name or "typewriter" in lower_name:
            # If not explicitly a gamepad/controller
            if "controller" not in lower_name and "gamepad" not in lower_name:
                return True
        if "mouse" in lower_name or "touchpad" in lower_name or "trackball" in lower_name or "trackpoint" in lower_name:
            if "controller" not in lower_name and "gamepad" not in lower_name:
                return True

        # 3. Check device class level
        if dev.device_class == "03":
            if dev.device_protocol in ("01", "02"):
                return True

        return False

    def classify_device(self, dev: USBDeviceDescriptor) -> Dict[str, Any]:
        """Classify USB device into category: controller, audio_dac, host_input, or generic."""
        # First: Check safety blacklist
        if self.is_host_keyboard_or_mouse(dev):
            return {
                "category": "host_input",
                "eligible_for_passthrough": False,
                "reason": "Host keyboard or mouse protected by MiOS safety invariant (lockout prevention)",
            }

        # Check Controllers
        if dev.vendor_id in CONTROLLER_VENDORS:
            return {
                "category": "gamepad",
                "eligible_for_passthrough": True,
                "vendor_label": CONTROLLER_VENDORS[dev.vendor_id],
                "reason": f"Recognized controller vendor ({CONTROLLER_VENDORS[dev.vendor_id]})",
            }

        if dev.vendor_id in CONTROLLER_PIDS and dev.product_id in CONTROLLER_PIDS[dev.vendor_id]:
            return {
                "category": "gamepad",
                "eligible_for_passthrough": True,
                "vendor_label": "Logitech Gamepad",
                "reason": f"Recognized gamepad PID {dev.product_id}",
            }

        # Check Audio DACs
        if dev.vendor_id in AUDIO_DAC_VENDORS:
            return {
                "category": "audio_dac",
                "eligible_for_passthrough": True,
                "vendor_label": AUDIO_DAC_VENDORS[dev.vendor_id],
                "reason": f"Recognized Audio DAC vendor ({AUDIO_DAC_VENDORS[dev.vendor_id]})",
            }

        # Check USB Audio Class (Interface Class 01)
        has_audio_iface = any(iface.get("bInterfaceClass") == "01" for iface in dev.interfaces)
        if dev.device_class == "01" or has_audio_iface:
            return {
                "category": "audio_dac",
                "eligible_for_passthrough": True,
                "vendor_label": "USB Audio Device",
                "reason": "USB Audio Class (01) interface detected",
            }

        # Check Generic Joystick / Gamepad (HID class 03 without keyboard/mouse protocol)
        lower_name = f"{dev.product_name} {dev.manufacturer}".lower()
        if "controller" in lower_name or "gamepad" in lower_name or "joystick" in lower_name or "wheel" in lower_name or "flight stick" in lower_name:
            return {
                "category": "gamepad",
                "eligible_for_passthrough": True,
                "vendor_label": "Generic Gamepad/HID Controller",
                "reason": "Gamepad/Joystick descriptor in product name",
            }

        return {
            "category": "other",
            "eligible_for_passthrough": False,
            "reason": "Not an approved controller or audio device",
        }

    def generate_hostdev_xml(
        self,
        vendor_id: str,
        product_id: str,
        bus: Optional[int] = None,
        device: Optional[int] = None,
    ) -> str:
        """Generate libvirt USB hostdev XML snippet."""
        vid_clean = vendor_id.lower().replace("0x", "").zfill(4)
        pid_clean = product_id.lower().replace("0x", "").zfill(4)
        vid_hex = f"0x{vid_clean}"
        pid_hex = f"0x{pid_clean}"

        lines = [
            "<hostdev mode='subsystem' type='usb' managed='yes'>",
            "  <source>",
            f"    <vendor id='{vid_hex}'/>",
            f"    <product id='{pid_hex}'/>",
        ]
        if bus is not None and device is not None:
            lines.append(f"    <address bus='{bus}' device='{device}'/>")
        lines.append("  </source>")
        lines.append("</hostdev>")
        return "\n".join(lines)

    def attach_device(
        self,
        domain: str,
        vendor_id: str,
        product_id: str,
        bus: Optional[int] = None,
        device: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Attach USB device to running libvirt domain."""
        vid_norm = vendor_id.lower().replace("0x", "").zfill(4)
        pid_norm = product_id.lower().replace("0x", "").zfill(4)

        # Look up device in scanned devices to inspect descriptors and interfaces
        scanned_devices = self.scan_usb_devices()
        target_dev = None
        for dev in scanned_devices:
            if dev.vendor_id == vid_norm and dev.product_id == pid_norm:
                if (bus is None or dev.bus_num == bus) and (device is None or dev.dev_num == device):
                    target_dev = dev
                    break

        if target_dev is None:
            target_dev = USBDeviceDescriptor(
                sysfs_path="",
                vendor_id=vendor_id,
                product_id=product_id,
                bus_num=bus,
                dev_num=device,
            )

        if self.is_host_keyboard_or_mouse(target_dev):
            return {
                "status": "rejected",
                "reason": "Cannot attach host keyboard or mouse to guest (safety invariant)",
                "domain": domain,
                "vendor_id": vendor_id,
                "product_id": product_id,
            }

        xml_content = self.generate_hostdev_xml(vendor_id, product_id, bus, device)

        if self.dry_run:
            logger.info("[DRY-RUN] Would attach USB device %s:%s to domain '%s':\n%s", vendor_id, product_id, domain, xml_content)
            return {
                "status": "simulated",
                "domain": domain,
                "vendor_id": vendor_id,
                "product_id": product_id,
                "xml": xml_content,
            }

        # Write temporary XML file and execute virsh
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as tf:
            tf.write(xml_content)
            xml_path = tf.name

        try:
            cmd = ["virsh", "attach-device", domain, xml_path, "--live"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            return {
                "status": "ok" if res.returncode == 0 else "error",
                "returncode": res.returncode,
                "stdout": res.stdout.strip(),
                "stderr": res.stderr.strip(),
                "domain": domain,
                "vendor_id": vendor_id,
                "product_id": product_id,
                "xml": xml_content,
            }
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            return {
                "status": "error",
                "error": f"virsh execution failed: {e}",
                "domain": domain,
                "vendor_id": vendor_id,
                "product_id": product_id,
                "xml": xml_content,
            }
        finally:
            if os.path.exists(xml_path):
                os.remove(xml_path)

    def detach_device(
        self,
        domain: str,
        vendor_id: str,
        product_id: str,
        bus: Optional[int] = None,
        device: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Detach USB device from running libvirt domain."""
        xml_content = self.generate_hostdev_xml(vendor_id, product_id, bus, device)

        if self.dry_run:
            logger.info("[DRY-RUN] Would detach USB device %s:%s from domain '%s':\n%s", vendor_id, product_id, domain, xml_content)
            return {
                "status": "simulated",
                "domain": domain,
                "vendor_id": vendor_id,
                "product_id": product_id,
                "xml": xml_content,
            }

        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as tf:
            tf.write(xml_content)
            xml_path = tf.name

        try:
            cmd = ["virsh", "detach-device", domain, xml_path, "--live"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            return {
                "status": "ok" if res.returncode == 0 else "error",
                "returncode": res.returncode,
                "stdout": res.stdout.strip(),
                "stderr": res.stderr.strip(),
                "domain": domain,
                "vendor_id": vendor_id,
                "product_id": product_id,
                "xml": xml_content,
            }
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            return {
                "status": "error",
                "error": f"virsh execution failed: {e}",
                "domain": domain,
                "vendor_id": vendor_id,
                "product_id": product_id,
                "xml": xml_content,
            }
        finally:
            if os.path.exists(xml_path):
                os.remove(xml_path)

    def generate_udev_rules(self, domain: str = "win11") -> str:
        """Generate companion udev rule content for automatic passthrough."""
        lines = [
            "# /usr/lib/udev/rules.d/90-mios-usb-passthrough.rules",
            "# MiOS Automated USB Gamepad and Audio DAC Passthrough Rules",
            "",
            "# Controllers (Xbox, PS, Nintendo, 8BitDo)",
        ]
        for vid, desc in CONTROLLER_VENDORS.items():
            lines.append(
                f'ACTION=="add", SUBSYSTEM=="usb", ATTR{{idVendor}}=="{vid}", '
                f'RUN+="/usr/libexec/mios/hw/usb_hotplug.py --action=attach --domain={domain} --vendor-id={vid} --product-id=$attr{{idProduct}}"'
            )
            lines.append(
                f'ACTION=="remove", SUBSYSTEM=="usb", ENV{{ID_VENDOR_ID}}=="{vid}", '
                f'RUN+="/usr/libexec/mios/hw/usb_hotplug.py --action=detach --domain={domain} --vendor-id={vid} --product-id=$env{{ID_MODEL_ID}}"'
            )

        lines.append("")
        lines.append("# USB Audio DACs")
        for vid, desc in AUDIO_DAC_VENDORS.items():
            lines.append(
                f'ACTION=="add", SUBSYSTEM=="usb", ATTR{{idVendor}}=="{vid}", '
                f'RUN+="/usr/libexec/mios/hw/usb_hotplug.py --action=attach --domain={domain} --vendor-id={vid} --product-id=$attr{{idProduct}}"'
            )
            lines.append(
                f'ACTION=="remove", SUBSYSTEM=="usb", ENV{{ID_VENDOR_ID}}=="{vid}", '
                f'RUN+="/usr/libexec/mios/hw/usb_hotplug.py --action=detach --domain={domain} --vendor-id={vid} --product-id=$env{{ID_MODEL_ID}}"'
            )

        return "\n".join(lines) + "\n"

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS USB Controller & DAC Hotplug Passthrough Manager (task_366)"
    )
    parser.add_argument(
        "--action",
        choices=["scan", "attach", "detach", "generate-rules", "xml"],
        default="scan",
        help="Action to perform",
    )
    parser.add_argument("--domain", default="win11", help="Target libvirt domain name")
    parser.add_argument("--vendor-id", default=None, help="USB Vendor ID (e.g. 045e)")
    parser.add_argument("--product-id", default=None, help="USB Product ID (e.g. 028e)")
    parser.add_argument("--bus", type=int, default=None, help="USB Bus number")
    parser.add_argument("--device", type=int, default=None, help="USB Device number")
    parser.add_argument("--sysfs-root", default="/", help="Sysfs root path for mocks/testing")
    parser.add_argument("--dry-run", action="store_true", help="Simulate virsh commands")
    parser.add_argument("--json", action="store_true", help="Output in machine-readable JSON")

    args = parser.parse_args()

    mgr = USBHotplugManager(
        sysfs_root=args.sysfs_root,
        target_domain=args.domain,
        dry_run=args.dry_run,
    )

    if args.action == "scan":
        devices = mgr.scan_usb_devices()
        classified = []
        for dev in devices:
            info = dev.to_dict()
            info["classification"] = mgr.classify_device(dev)
            classified.append(info)

        if args.json:
            print(json.dumps(classified, indent=2))
        else:
            print(f"=== Discovered {len(classified)} USB Devices ===")
            for d in classified:
                cat = d["classification"]["category"]
                elig = d["classification"]["eligible_for_passthrough"]
                print(f"[{d['vendor_id']}:{d['product_id']}] {d['manufacturer']} - {d['product_name']}")
                print(f"    Category: {cat} (Eligible: {elig}) -> {d['classification']['reason']}")
    elif args.action == "generate-rules":
        rules = mgr.generate_udev_rules(domain=args.domain)
        print(rules)
    elif args.action == "xml":
        if not args.vendor_id or not args.product_id:
            print("Error: --vendor-id and --product-id required for xml generation", file=sys.stderr)
            return 1
        xml = mgr.generate_hostdev_xml(args.vendor_id, args.product_id, args.bus, args.device)
        print(xml)
    elif args.action in ("attach", "detach"):
        if not args.vendor_id or not args.product_id:
            print("Error: --vendor-id and --product-id required for attach/detach", file=sys.stderr)
            return 1
        if args.action == "attach":
            res = mgr.attach_device(args.domain, args.vendor_id, args.product_id, args.bus, args.device)
        else:
            res = mgr.detach_device(args.domain, args.vendor_id, args.product_id, args.bus, args.device)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"Action '{args.action}' -> {res['status']}: {res}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
