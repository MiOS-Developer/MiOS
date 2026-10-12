<!-- AI-hint: Chapter 28: Dynamic Network and Firewall Management. Covers managing port firewalls via firewalld command hooks. Explains how ports are dynamically resolved and bound. Documents Tailscale integration with system firewall rules. -->

# Chapter 28: Dynamic Network and Firewall Management

> Part VI: Storage, Network & Web Planes of the [MiOS manual](../manual.md).

This chapter covers the documentation for **Dynamic Network and Firewall Management** under MiOS.

### <a name="28_firewalld_rule_generation"></a>28.Firewalld Rule Generation: Firewalld Rule Generation

> Path Reference: `/usr/share/doc/mios/manual.md#28_firewalld_rule_generation`

#### Overview

Firewall rules isolate host services and control outbound networks.

## Rules
- **Tool**: Configured via firewalld policies.
- **Gating**: `mios-gen egress-firewall` projects the outbound rules from SSOT; its [native implementation](../../../../../tools/native/mios-gen/src/main.rs) supports off, audit and enforce modes.
- **Logs**: Blocked network events are logged in system journals.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.

---

### <a name="28_dynamic_port_allocation"></a>28.Dynamic Port Allocation: Dynamic Port Allocation

> Path Reference: `/usr/share/doc/mios/manual.md#28_dynamic_port_allocation`

#### Overview

Ports are allocated dynamically during build and boot phases.

## Allocation
- **Script**: Handled by [35-render-ports.sh](automation/35-render-ports.sh).
- **Mappings**: Maps host interfaces to container ports.
- **Validation**: Enforces unique allocations to prevent startup collisions.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.

---

### <a name="28_vpn_and_tailscale_routing"></a>28.VPN and Tailscale Routing: VPN and Tailscale Routing

> Path Reference: `/usr/share/doc/mios/manual.md#28_vpn_and_tailscale_routing`

#### Overview

VPN integrations secure communication across network devices.

## Settings
- **Interface**: Uses Tailscale virtual adapters.
- **Routing**: Resolves local addresses through private tunnels.
- **Firewall**: Integrates VPN paths with local rules.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.
