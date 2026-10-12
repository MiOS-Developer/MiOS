#!/usr/bin/env python3
# AI-hint: Parses mios.toml to generate MiOS-SBOM.csv, aggregating package metadata, Quadlet image references, and environment defaults to provide a comp...
# AI-doc: usr/share/doc/mios/manual/lib.md

import re
import csv
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CAT_META = {
    "repos":                   ("rpm-repo",       "External RPM repo enablement (no packages installed by name)"),
    "base":                    ("rpm-base",       "Base OS / first-pass install (security stack, tooling)"),
    "moby":                    ("rpm-container",  "moby-engine + buildx parity stack"),
    "uki":                     ("rpm-boot",       "Unified Kernel Image build dependencies"),
    "sbom-tools":              ("rpm-supply-chain","Software Bill of Materials generation"),
    "k3s-selinux-build":       ("rpm-build-dep",  "k3s-selinux policy build chain"),
    "kernel":                  ("rpm-kernel-aux", "Kernel modules-extra/devel/headers/tools (NOT kernel/kernel-core)"),
    "gnome":                   ("rpm-desktop",    "GNOME 50 desktop session"),
    "gnome-core-apps":         ("rpm-desktop",    "GNOME core applications"),
    "gpu-mesa":                ("rpm-gpu",        "Mesa userspace + Vulkan loaders"),
    "gpu-amd-compute":         ("rpm-gpu-compute","AMD ROCm compute stack"),
    "gpu-intel-compute":       ("rpm-gpu-compute","Intel oneAPI / NEO compute stack"),
    "gpu-nvidia":              ("rpm-gpu",        "NVIDIA proprietary stack (akmod, CDI toolkit)"),
    "virt":                    ("rpm-virt",       "KVM/QEMU + libvirt + Looking Glass build deps + KVMFR"),
    "containers":              ("rpm-container",  "Podman, runc, conmon, netavark, slirp4netns, fuse-overlayfs"),
    "self-build":              ("rpm-toolchain",  "Self-build toolchain (the image can rebuild itself)"),
    "boot":                    ("rpm-boot",       "Bootloader, plymouth, grubby, dracut"),
    "cockpit":                 ("rpm-mgmt",       "Cockpit web management"),
    "wintools":                ("rpm-windows-vm", "Windows VM tooling (virt-viewer, spice-gtk, virt-manager)"),
    "security":                ("rpm-security",   "SELinux, fapolicyd, USBGuard, audit, openscap, AIDE"),
    "gaming":                  ("rpm-gaming",     "Gaming stack (Steam runtime deps, Proton, Lutris)"),
    "guests":                  ("rpm-virt-guest", "Guest agents (virtio, spice, qemu-guest-agent)"),
    "storage":                 ("rpm-storage",    "Storage stack (LVM, MD, multipath, ZFS, BTRFS, XFS)"),
    "ceph":                    ("rpm-ceph",       "Ceph client/server packages"),
    "k3s":                     ("rpm-k3s",        "k3s prerequisites (downloaded binary)"),
    "ha":                      ("rpm-ha",         "Pacemaker/Corosync HA stack"),
    "utils":                   ("rpm-utils",      "Operator utilities"),
    "android":                 ("rpm-android",    "Waydroid + binder for Android container"),
    "looking-glass-build":     ("rpm-build-dep",  "Looking Glass B7 client build chain"),
    "cockpit-plugins-build":   ("rpm-build-dep",  "Cockpit plugin compilation"),
    "network-discovery":       ("rpm-net",        "mDNS/Avahi/SSDP/llmnr"),
    "phosh":                   ("rpm-desktop",    "Phosh mobile session (portrait/RDP)"),
    "updater":                 ("rpm-update",     "uupd / bootc-image-builder / rpm-ostree update path"),
    "freeipa":                 ("rpm-identity",   "FreeIPA / SSSD client (optional Day-2)"),
    "ai":                      ("rpm-ai",         "Local AI runtime + tooling"),
    "critical":                ("rpm-critical",   "Post-install rpm -q validation list"),
    "bloat":                   ("rpm-removed",    "Packages explicitly REMOVED post-install"),
    "nut":                     ("rpm-power",      "Network UPS Tools client"),
}

FROMSOURCE = [
    ("looking-glass-b7",     "from-source",         "KVM/QEMU shared-memory display protocol",
     "Built in automation/69-bake-lookingglass-client.sh from upstream source"),
    ("kvmfr",                "from-source",         "Kernel module for Looking Glass shared memory",
     "Built/baked into image via automation/68-bake-kvmfr.sh"),
    ("k3s-binary",           "from-source",         "Lightweight Kubernetes runtime",
     "Downloaded from upstream releases by automation/36-ceph-k3s.sh"),
    ("k3s-selinux-policy",   "from-source",         "SELinux policy for k3s",
     "Compiled in automation/37-k3s-selinux.sh"),
    ("aichat",               "from-source",         "Terminal AI chat client",
     "Downloaded from upstream releases by automation/37-aichat.sh"),
    ("aichat-ng",            "from-source",         "aichat fork",
     "Downloaded from upstream releases by automation/37-aichat.sh"),
    ("cosign-v2",            "from-source",         "Sigstore container signing tool (v2 keyless)",
     "Downloaded from upstream releases by automation/49-cosign-policy.sh"),
    ("mios-selinux-modules", "from-source-policy",  "Custom SELinux .te modules",
     "Compiled in usr/share/selinux/packages/mios; loaded post-build"),
    ("bibata-cursor-theme",  "from-source",         "Bibata cursor theme",
     "Downloaded tarball in automation/57-gnome.sh"),
]

OCI_IMAGES = [
    ("ghcr.io/ublue-os/ucore-hci:stable-nvidia",        "oci-base",      "Primary base image (uCore HCI, NVIDIA variant)",
     "FROM line in Containerfile (override via MIOS_BASE_IMAGE)"),
    ("ghcr.io/ublue-os/ucore-hci:stable",               "oci-base-alt",  "Base image variant without NVIDIA",
     "Selectable via build-arg"),
    ("ghcr.io/ublue-os/ucore:stable",                   "oci-base-alt",  "Minimal uCore (no HCI extras)",
     "Selectable via build-arg"),
    ("quay.io/centos-bootc/bootc-image-builder:latest", "oci-tool",      "BIB: disk image builder (RAW/ISO/QCOW2/VHDX/WSL2)",
     "MIOS_BIB_IMAGE in Justfile / config/artifacts/*.toml"),
    ("quay.io/centos-bootc/centos-bootc:stream10",      "oci-tool",      "Rechunker fallback context",
     "MIOS_IMG_RECHUNK in mios-build-local.ps1"),
    ("docker.io/library/alpine:latest",                 "oci-tool",      "Helper image fallback",
     "FallbackHash / FallbackConvert in mios-build-local.ps1"),
    ("anchore/syft:latest",                             "oci-tool",      "CycloneDX/SPDX SBOM generator",
     "Justfile sbom + automation/90-generate-sbom.sh"),
    ("quay.io/ceph/ceph:latest",                        "oci-quadlet",   "Ceph storage cluster (mios-ceph)",
     "etc/containers/systemd/mios-ceph.container"),
    ("docker.io/rancher/k3s:latest",                    "oci-quadlet",   "Kubernetes control plane (mios-k3s)",
     "etc/containers/systemd/mios-k3s.container"),
    ("docker.io/ollama/ollama:latest",                  "oci-quadlet",   "Ollama inference server",
     "usr/share/containers/systemd/ollama.container"),
    ("docker.io/crowdsecurity/crowdsec:latest",         "oci-quadlet",   "CrowdSec sovereign IPS dashboard",
     "usr/share/containers/systemd/crowdsec-dashboard.container"),
    ("docker.io/guacamole/guacamole:latest",            "oci-quadlet",   "Apache Guacamole web frontend",
     "usr/share/containers/systemd/mios-guacamole.container"),
    ("docker.io/guacamole/guacd:latest",                "oci-quadlet",   "Guacamole proxy daemon",
     "usr/share/containers/systemd/guacd.container"),
    ("docker.io/library/postgres:latest",               "oci-quadlet",   "PostgreSQL backing Guacamole",
     "usr/share/containers/systemd/guacamole-postgres.container"),
    ("quay.io/poseidon/matchbox:latest",                "oci-quadlet",   "PXE boot hub (matchbox)",
     "usr/share/containers/systemd/mios-pxe-hub.container"),
]

def main(out_path: Path):
    rows = []

    toml_path = ROOT / "usr/share/mios/mios.toml"
    with toml_path.open("rb") as fh:
        toml = tomllib.load(fh)
    pkg_tables = toml.get("packages", {}) or {}
    for cat, table in sorted(pkg_tables.items()):
        if not isinstance(table, dict):
            continue
        pkgs = table.get("pkgs", []) or []
        if not pkgs:
            continue
        classification, purpose = CAT_META.get(cat, (f"rpm-{cat}", "(uncategorized)"))
        for pkg in pkgs:
            pkg = (pkg or "").strip()
            if not pkg:
                continue
            rows.append({
                "section":        f"packages-{cat}",
                "package":        pkg,
                "classification": classification,
                "purpose":        purpose,
                "notes":          f"From usr/share/mios/mios.toml [packages.{cat}].pkgs",
            })

    for name, classification, purpose, notes in FROMSOURCE:
        rows.append({
            "section":        "from-source",
            "package":        name,
            "classification": classification,
            "purpose":        purpose,
            "notes":          notes,
        })

    for img, classification, purpose, notes in OCI_IMAGES:
        rows.append({
            "section":        "oci-image",
            "package":        img,
            "classification": classification,
            "purpose":        purpose,
            "notes":          notes,
        })

    flat_seen = set()

    toml_path = ROOT / "usr/share/mios/mios.toml"
    if toml_path.is_file():
        try:
            try:
                import tomllib  # Python 3.11+
            except ImportError:
                import tomli as tomllib  # type: ignore
            with open(toml_path, "rb") as fh:
                doc = tomllib.load(fh)
            flatpaks = (doc.get("desktop") or {}).get("flatpaks") or []
            if isinstance(flatpaks, dict):
                flatpaks = flatpaks.get("install") or []
            for fp in flatpaks:
                fp = str(fp).strip()
                if fp and fp not in flat_seen:
                    flat_seen.add(fp)
                    rows.append({
                        "section":        "flatpak-default",
                        "package":        fp,
                        "classification": "flatpak",
                        "purpose":        "Default Flatpak installed at first boot",
                        "notes":          "From usr/share/mios/mios.toml [desktop].flatpaks",
                    })
        except Exception as e:
            print(f"WARN: failed to parse {toml_path}: {e}", file=sys.stderr)

    for env_path in [".env.mios"]:
        p = ROOT / env_path
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'MIOS_DESKTOP_FLATPAKS\s*=\s*["\']([^"\']*)["\']', text)
        if m and m.group(1).strip():
            for fp in m.group(1).split(","):
                fp = fp.strip()
                if fp and fp not in flat_seen:
                    flat_seen.add(fp)
                    rows.append({
                        "section":        "flatpak-default",
                        "package":        fp,
                        "classification": "flatpak",
                        "purpose":        "Default Flatpak installed at first boot",
                        "notes":          f"From {env_path} (legacy fallback)",
                    })

    fieldnames = ["section", "package", "classification", "purpose", "notes"]
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n",
                                quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    print(f"Wrote {out_path} -- {len(rows)} entries", file=sys.stderr)

if __name__ == "__main__":
    out = ROOT / "MiOS-SBOM.csv"
    main(out)
