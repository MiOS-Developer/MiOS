<!-- AI-hint: Manual pages distilled from the source comments of a2a, sanitized, each passage anchored to the comment it came from. -->

# a2a

### Mutual capability exchange and cryptographic attestation...

Mutual capability exchange and cryptographic attestation authenticator for A2A nodes.

    Provides Ed25519 keypair generation, AgentCard creation with RFC-8785 canonical signing,
    clock-skew resilient verification, and capability negotiation.

<!-- mios-src:5c678e4fb3bf from usr/libexec/mios/a2a/attestation.py:122-127 -->

### Creates and signs an AgentCard with cryptographic proof of...

Creates and signs an AgentCard with cryptographic proof of authenticity.

        Payload contains: agent_name, node_id, capabilities, endpoints, issued_at,
        expires_at, nonce, and public_key. The detached signature 'sig' is generated over
        the RFC-8785 canonical JSON bytes.

<!-- mios-src:86cf7eaad3c3 from usr/libexec/mios/a2a/attestation.py:203-209 -->

### Validates AgentCard authenticity, signature, timestamps...

Validates AgentCard authenticity, signature, timestamps, and clock skew.

        Returns True only if the cryptographic signature is valid over the canonical payload,
        timestamps are within acceptable clock skew bounds, and expiration has not occurred.

<!-- mios-src:7cfd9d86f6a8 from usr/libexec/mios/a2a/attestation.py:242-247 -->

### Attests client card authenticity and negotiates mutual...

Attests client card authenticity and negotiates mutual capabilities.

        Returns (True, granted_capabilities) if the card is authentic and all required
        capabilities are present; returns (False, missing_capabilities) otherwise.

<!-- mios-src:b9b51841848a from usr/libexec/mios/a2a/attestation.py:338-343 -->
### Validates AgentCard authenticity, signature, timestamps...

Validates AgentCard authenticity, signature, timestamps, and clock skew.

        Returns True only if the cryptographic signature is valid over the canonical payload
        under ``trusted_public_key`` (resolved by the caller from provisioning -- see
        :func:`resolve_trusted_key`), timestamps are within acceptable clock skew bounds,
        and expiration has not occurred. With no trusted key the card is unverified: False.

<!-- mios-src:443596a8c760 from usr/libexec/mios/a2a/attestation.py:237-244 -->

### The provisioned passport public key of a REGISTERED...

The provisioned passport public key of a REGISTERED identity.

    Delegates to the one AgentCard trust-key resolver, ``_load_card_trusted_key``
    in mios-mcp-server (the key its card signing is bound to):
    ``[passport].key_dir/<agent_id>/public.key`` from the mios.toml SSOT. The
    identity is the operator's choice, never the card's claim. Raises when the
    identity is malformed or nothing usable is provisioned.

<!-- mios-src:6b90d6a35506 from usr/libexec/mios/a2a/attestation.py:389-396 -->
