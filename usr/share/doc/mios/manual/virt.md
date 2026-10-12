<!-- AI-hint: Manual pages distilled from the source comments of virt, sanitized, each passage anchored to the comment it came from. -->

# virt

### Frees previously allocated hugepages upon VM...

Frees previously allocated hugepages upon VM shutdown/teardown.

<!-- mios-src:0efdf137cfc8 from usr/libexec/mios/virt/hugepages_mgr.py:212-214 -->

### WS-VFIO (task_513): Ephemeral Cloud-Hypervisor MicroVM...

WS-VFIO (task_513): Ephemeral Cloud-Hypervisor MicroVM Orchestrator & Virtio-VSOCK Agent Tool Bridge.

Spawns hardware-isolated microVM sandboxes in <50ms for untrusted agent tool execution:
- Launches cloud-hypervisor with direct kernel boot (--kernel, --initramfs, DAX pmem).
- Bridges host orchestrator and guest payload runner via high-throughput Virtio-VSOCK (AF_VSOCK:5200).
- Mounts scoped transient virtiofs workspace directories.
- Completely zeros and destroys microVM guest memory immediately upon tool completion.

<!-- mios-src:d339fe30b79c from usr/libexec/mios/virt/microvm_bridge.py:5-13 -->

### microvm_migrate.py — task_912 WS-HCI Zero-downtime MicroVM...

microvm_migrate.py — task_912 WS-HCI
Zero-downtime MicroVM state serialization and live migration handover engine.

Serializes Cloud-Hypervisor/QEMU microVM CPU registers, dirty memory pages,
and virtio-pmem DAX file descriptors to achieve <50ms live handover latency.

<!-- mios-src:a42f759368fd from usr/libexec/mios/virt/microvm_migrate.py:4-10 -->

### mios_microvm.py — task_677 WS-VFIO Virtio-PMEM direct DAX...

mios_microvm.py — task_677 WS-VFIO
Virtio-PMEM direct DAX memory storage manager for ephemeral microVM
sandboxes.  Allocates anonymous host memfd buffers, populates them with a
base rootfs image, and passes them to Cloud-Hypervisor via --pmem dax=on.
Guest kernel is booted with root=/dev/pmem0 rootflags=dax to bypass page
cache and deliver >20 GB/s ephemeral I/O through host RAM.

On VM exit the memfd is destroyed, instantly reclaiming RAM with zero
NVMe write amplification.

Usage:
  python3 /usr/libexec/mios/virt/mios_microvm.py launch --rootfs <image.raw> [--memory 4G] [--cpus 4]
  python3 /usr/libexec/mios/virt/mios_microvm.py status
  python3 /usr/libexec/mios/virt/mios_microvm.py destroy <vm-id>

<!-- mios-src:c76fe63da7e6 from usr/libexec/mios/virt/mios_microvm.py:6-21 -->

### MiOS Inter-VM PipeWire Low-Latency Audio Bridge. Manages...

MiOS Inter-VM PipeWire Low-Latency Audio Bridge.

Manages Scream IVSHMEM audio sinks, calculates buffer latency math enforcing
sub-5ms SLA (e.g. 64/48000 = 1.33ms), generates libvirt domain IVSHMEM XML snippets,
synthesizes systemd service units, and configures PipeWire JACK environment overrides.

<!-- mios-src:704997c3cf9b from usr/libexec/mios/virt/pipewire_bridge.py:4-10 -->

### MiOS Dynamic Runtime VFIO Device Unbind and Rebind Utility....

MiOS Dynamic Runtime VFIO Device Unbind and Rebind Utility.
Safely switches PCIe devices (GPUs, Audio companions) between host drivers (nvidia, amdgpu, i915, nouveau)
and vfio-pci without rebooting using sysfs driver_override, bind, and unbind interfaces.
Prevents unbinding primary host display rendering Wayland compositors unless explicitly forced.
Enforces whole-device passthrough across all slot siblings.

<!-- mios-src:3b5220bbaff8 from usr/libexec/mios/virt/vfio_bind.py:4-10 -->

### MiOS VirtIO-FS Shared Directory Mount Daemon and Libvirt...

MiOS VirtIO-FS Shared Directory Mount Daemon and Libvirt XML Generator.
Configures high-performance virtiofsd daemons mapping host persistent directories (/var/home/mios/Shared)
into guest VMs with POSIX ACLs, extended attributes, and optional DAX (Direct Access) memory windows.
Enforces modern VirtIO-FS protocol over legacy 9p filesystems, guaranteeing sub-millisecond IO and
flawless file locking across the host-guest boundary.

<!-- mios-src:38001b38d5bc from usr/libexec/mios/virt/virtiofs_mount.py:4-10 -->

### Verifies existence and permissions of the shared host...

Verifies existence and permissions of the shared host directory.
        Defaults to /var/home/mios/Shared (adheres to Invariant 1: /var persists by default).

<!-- mios-src:f14a902268c0 from usr/libexec/mios/virt/virtiofs_mount.py:53-56 -->

### MiOS Virtual TPM2 (swtpm) Provisioning and Domain XML...

MiOS Virtual TPM2 (swtpm) Provisioning and Domain XML Generator.
Provisions isolated, persistent TPM2 emulator instances per VM under /var/lib/libvirt/swtpm/<vm_id>/
and ephemeral control/data UNIX sockets under /run/libvirt/swtpm/<vm_id>-swtpm.sock.
Enforces strict state directory isolation (never sharing state between VM instances) and persistent
state retention across bootc OS updates adhering to Architectural Invariant 1 (/var persists by default).

<!-- mios-src:cf2acdc0ea80 from usr/libexec/mios/virt/vtpm_provision.py:4-10 -->
### mios_microvm.py — task_677 / task_750 WS-VFIO SquashFS template...

mios_microvm.py — task_677 / task_750 WS-VFIO
SquashFS template streaming over Unix-socket NBD with ephemeral RAM overlay
and Virtio-PMEM direct DAX memory storage manager for ephemeral microVM sandboxes.

Exports compressed base rootfs SquashFS image over local Unix domain socket
using qemu-nbd --socket=/run/mios/nbd.sock --read-only.
Launches Cloud-Hypervisor microVM attaching virtual block device mapped to NBD socket;
in guest initramfs, mounts NBD read-only and overlays tmpfs RAM upperdir.
Enables massive concurrency (>100 VMs sharing a single 800MB template) with sub-15ms boot.

<!-- mios-src:f707131221c5 from usr/libexec/mios/virt/mios_microvm.py:5-15 -->
