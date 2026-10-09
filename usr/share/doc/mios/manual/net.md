<!-- AI-hint: Manual pages distilled from the source comments of net, sanitized, each passage anchored to the comment it came from. -->

# net

### cilium_bgp.py — task_703 WS-NODE Cilium native BGP peering and...

cilium_bgp.py — task_703 WS-NODE
Cilium native BGP peering and dual-stack ECMP LoadBalancer ingress manager.

Configures Cilium BGP Control Plane, peers eBPF datapaths to upstream ToR routers,
and announces dual-stack VIPs with sub-100ms BFD failover.

<!-- mios-src:533b9af69ae1 from usr/libexec/mios/net/cilium_bgp.py:4-10 -->

### MiOS Edge Mesh Zero-Configuration mDNS/DNS-SD Peer...

MiOS Edge Mesh Zero-Configuration mDNS/DNS-SD Peer Discovery & WireGuard Daemon.

Automates peer discovery and cryptographic mesh network configuration across local LAN:
1. mDNS Service Announcement: Registers `_mios-mesh._udp` with node ID, WireGuard public key, and mesh IP.
2. DNS-SD Browser: Discovers neighboring MiOS nodes and verifies mutual attestation keys.
3. WireGuard Config Synthesizer: Dynamically writes and updates WireGuard peer blocks (`[Peer]`).

<!-- mios-src:508cfead5545 from usr/libexec/mios/net/mdns_mesh.py:4-11 -->

### netavark_isolate.py — task_685 WS-APP Declarative Netavark...

netavark_isolate.py — task_685 WS-APP
Declarative Netavark network isolation and rootless nftables firewall manager.

Configures isolated Netavark bridge networks, applies rootless nftables rules
blocking inter-bridge lateral traversal, and verifies host port bindings strictly
adhere to 127.0.0.1 or 10.0.0.0/8 mesh IPs (no 0.0.0.0 leaks).

<!-- mios-src:9c4932c8d4f4 from usr/libexec/mios/net/netavark_isolate.py:4-11 -->

### MiOS NetworkManager Offline Connection Keyfile Pre-Seeder....

MiOS NetworkManager Offline Connection Keyfile Pre-Seeder.

Generates standard NetworkManager .nmconnection keyfiles for offline network pre-seeding
(Wi-Fi WPA-PSK/WPA3-SAE, Ethernet DHCP/Static).

Enforces strict security invariants:
- File permissions MUST be exactly 0600 (-rw-------).
- Keyfiles MUST NOT be world-readable or accessible by unprivileged users.

<!-- mios-src:fd9046c5f1f6 from usr/libexec/mios/net/nm_preseed.py:5-14 -->

### WS-NODE (task_509): PTP IEEE 1588 Hardware Timestamping &...

WS-NODE (task_509): PTP IEEE 1588 Hardware Timestamping & Chrony NTS Smooth Clock Synchronization Daemon.

Maintains sub-microsecond cluster clock synchronization and monotonic ordering:
- Probes network interfaces via ethtool -T for PTP HW timestamping (SOF_TIMESTAMPING_TX/RX_HARDWARE, PHC).
- Generates hardened ptp4l and phc2sys configurations for boundary/slave clocks.
- Configures Chrony with Network Time Security (NTS) and strictly smooth slewing (makestep 0 0)
  to safeguard PostgreSQL, Raft, and Merkle audit chain transaction ordering against backwards clock jumps.
- Monitors clock jitter, drift, offset, and NTS authentication telemetry.

<!-- mios-src:c8f46d0387ec from usr/libexec/mios/net/ptp_time_sync.py:5-14 -->

### wireguard_roam.py — task_697 WS-NODE Dynamic WireGuard...

wireguard_roam.py — task_697 WS-NODE
Dynamic WireGuard endpoint roaming daemon and adaptive Path MTU prober.

Listens for netlink RTM_NEWADDR events, updates peer endpoints in <50ms, and
dynamically clamps tunnel MTU between 1280 and 1420 bytes.

<!-- mios-src:274df885ed84 from usr/libexec/mios/net/wireguard_roam.py:4-10 -->
