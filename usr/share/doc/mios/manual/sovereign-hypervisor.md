<!-- AI-hint: Sovereign Hypervisor supervisor manual: KVMFR shared memory verification, modular virtqemud sockets, and sibling domain orchestration for management seat and gaming guest. -->
<!-- AI-related: usr/libexec/mios/launch-sovereign-hypervisor.sh, usr/share/doc/mios/manual/ch21-looking-glass-b7-and-kvmfr.md, usr/libexec/mios/Xbox-Final-NoAutoSelect.xml, usr/share/mios/mios.toml -->

# Sovereign Hypervisor Domain Supervisor (`launch-sovereign-hypervisor.sh`)

## 1. Role and Architectural Topology

The Sovereign Hypervisor Supervisor (`usr/libexec/mios/launch-sovereign-hypervisor.sh`) is the unified pre-flight validator and launcher for MiOS virtualization on bare-metal hosts and blades.

It establishes the Layer 1 (L1) hypervisor substrate and orchestrates two sibling Layer 2 (L2) virtual machines:
- **Management Seat (`mios`)**: The primary Linux user workstation running Hyprland with direct scanout (`direct_scanout = 1`), housing Looking Glass client display, audio, and user tooling.
- **Gaming Guest (`mios-xbox`)**: The Windows 11 gaming guest VM with whole-device discrete GPU passthrough and Looking Glass B7 IVSHMEM frame-sharing.

## 2. Launch Sequence & Validation Steps

The supervisor executes four sequential validation phases before domain start:

### Step 1: Looking Glass IVSHMEM Device Node (`/dev/kvmfr0`)
- Validates character device node existence: `/dev/kvmfr0`.
- Verifies octal permissions `0660` and group ownership `kvm` (`root:kvm`).
- If absent, attempts automated kernel module insertion: `modprobe kvmfr static_size_mb=128`.

### Step 2: Virtual Network Bridge (`virbr0`)
- Checks network interface operational status for `virbr0`.
- Verifies libvirt virtual network `default` is active via `virsh net-info default`.
- Automatically activates and autostarts network if inactive: `virsh net-start default && virsh net-autostart default`.

### Step 3: Modular Libvirt Sockets Activation
In accordance with modern systemd modular libvirt architecture, validates and starts the four dedicated daemon sockets:
- `virtqemud.socket` (QEMU domain driver)
- `virtnetworkd.socket` (Virtual network driver)
- `virtlogd.socket` (Domain log daemon)
- `virtstoraged.socket` (Storage pool daemon)

### Step 4: Sibling Domain XML Integrity Verification
- Inspects `Xbox-Final-NoAutoSelect.xml` (or `--xml <path>`).
- Confirms the presence of the 128 MB IVSHMEM device node: `<shmem name="looking-glass"><size unit="M">128</size></shmem>`.
- Verifies whole-device RTX dGPU PCI hostdev declaration.
- Confirms management seat compositor direct scanout policy.

## 3. CLI Invocation Modes

- `--check`: Executes pre-flight validation steps 1-4 without launching VMs.
- `--status`: Queries and reports current state of the KVMFR device, bridge, modular sockets, and sibling domains.
- `--dry-run`: Simulates the entire sequence, printing commands without modifying system state.
- `--start`: Executes full pre-flight and boots sibling domains (default behavior).
- `--mock`: Forces mock hypervisor mode for validation in headless CI or nested container testbeds (`MIOS_MOCK_HYPERVISOR=1`).
