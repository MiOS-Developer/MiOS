// AI-hint: Ed25519 mutual handshake, X25519 ECDH key exchange, HKDF-SHA256 key derivation, and ChaCha20-Poly1305 wire AEAD for mios-node.
// AI-related: src/mios-rs/mios-node/src/wire.rs, tests/test-node-mesh.py
//! MiOS Node Cryptographic Handshake & Wire Encryption Engine (T-388 / AGY-1986)
//!
//! Provides mutual identity authentication using Ed25519 signatures, forward secrecy via X25519
//! ephemeral Diffie-Hellman key exchange, HKDF-SHA256 session key derivation, and ChaCha20-Poly1305
//! authenticated symmetric frame payload encryption.

use crate::protocol::Frame;
use anyhow::{anyhow, Result};
use byteorder::{ByteOrder, LittleEndian};
use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// Fill cryptographic keys/nonces from the operating system; failure has no deterministic fallback.
pub fn random_bytes<const N: usize>() -> Result<[u8; N]> {
    let mut bytes = [0u8; N];
    getrandom::getrandom(&mut bytes).map_err(|e| anyhow!("OS randomness unavailable: {e}"))?;
    Ok(bytes)
}

pub const TAG_SIZE: usize = 16;
pub const KEY_SIZE: usize = 32;

// ============================================================================
// 1. SHA-256 HMAC & HKDF-SHA256
// ============================================================================

pub fn hmac_sha256(key: &[u8], data: &[u8]) -> [u8; 32] {
    let mut k = [0u8; 64];
    if key.len() > 64 {
        let hash = Sha256::digest(key);
        k[..32].copy_from_slice(&hash);
    } else {
        k[..key.len()].copy_from_slice(key);
    }

    let mut ipad = [0x36u8; 64];
    let mut opad = [0x5cu8; 64];
    for i in 0..64 {
        ipad[i] ^= k[i];
        opad[i] ^= k[i];
    }

    let mut inner_hasher = Sha256::new();
    inner_hasher.update(ipad);
    inner_hasher.update(data);
    let inner_hash = inner_hasher.finalize();

    let mut outer_hasher = Sha256::new();
    outer_hasher.update(opad);
    outer_hasher.update(inner_hash);
    let outer_hash = outer_hasher.finalize();

    let mut out = [0u8; 32];
    out.copy_from_slice(&outer_hash);
    out
}

pub fn hkdf_sha256_expand(prk: &[u8; 32], info: &[u8], okm_len: usize) -> Vec<u8> {
    let mut okm = Vec::with_capacity(okm_len);
    let mut prev = Vec::new();
    let mut counter: u8 = 1;

    while okm.len() < okm_len {
        let mut input = Vec::new();
        input.extend_from_slice(&prev);
        input.extend_from_slice(info);
        input.push(counter);

        let t = hmac_sha256(prk, &input);
        prev = t.to_vec();

        let needed = (okm_len - okm.len()).min(32);
        okm.extend_from_slice(&t[..needed]);
        counter += 1;
    }

    okm
}

pub fn hkdf_sha256(salt: &[u8], ikm: &[u8], info: &[u8], len: usize) -> Vec<u8> {
    let default_salt = [0u8; 32];
    let s = if salt.is_empty() {
        &default_salt[..]
    } else {
        salt
    };
    let prk = hmac_sha256(s, ikm);
    hkdf_sha256_expand(&prk, info, len)
}

// ============================================================================
// 2. RFC 7748 X25519 Curve Arithmetic (Montgomery Ladder)
// ============================================================================

pub fn x25519(k: &[u8; 32], u: &[u8; 32]) -> [u8; 32] {
    let point = curve25519_dalek::montgomery::MontgomeryPoint(*u);
    let result = point.mul_clamped(*k);
    result.0
}

pub const X25519_BASEPOINT: [u8; 32] = [
    9, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
];

pub fn x25519_public_key(priv_key: &[u8; 32]) -> [u8; 32] {
    x25519(priv_key, &X25519_BASEPOINT)
}

// ============================================================================
// 3. RFC 8439 ChaCha20 & Poly1305 AEAD Engine
// ============================================================================

fn chacha20_quarter_round(st: &mut [u32; 16], a: usize, b: usize, c: usize, d: usize) {
    st[a] = st[a].wrapping_add(st[b]);
    st[d] ^= st[a];
    st[d] = st[d].rotate_left(16);
    st[c] = st[c].wrapping_add(st[d]);
    st[b] ^= st[c];
    st[b] = st[b].rotate_left(12);
    st[a] = st[a].wrapping_add(st[b]);
    st[d] ^= st[a];
    st[d] = st[d].rotate_left(8);
    st[c] = st[c].wrapping_add(st[d]);
    st[b] ^= st[c];
    st[b] = st[b].rotate_left(7);
}

fn chacha20_block(key: &[u8; 32], counter: u32, nonce: &[u8; 12]) -> [u8; 64] {
    let mut state = [0u32; 16];
    state[0] = 0x61707865; // "expa"
    state[1] = 0x3320646e; // "nd 3"
    state[2] = 0x79622d32; // "2-by"
    state[3] = 0x6b206574; // "te k"

    for i in 0..8 {
        state[4 + i] = LittleEndian::read_u32(&key[i * 4..(i + 1) * 4]);
    }
    state[12] = counter;
    for i in 0..3 {
        state[13 + i] = LittleEndian::read_u32(&nonce[i * 4..(i + 1) * 4]);
    }

    let mut working = state;
    for _ in 0..10 {
        // Column rounds
        chacha20_quarter_round(&mut working, 0, 4, 8, 12);
        chacha20_quarter_round(&mut working, 1, 5, 9, 13);
        chacha20_quarter_round(&mut working, 2, 6, 10, 14);
        chacha20_quarter_round(&mut working, 3, 7, 11, 15);
        // Diagonal rounds
        chacha20_quarter_round(&mut working, 0, 5, 10, 15);
        chacha20_quarter_round(&mut working, 1, 6, 11, 12);
        chacha20_quarter_round(&mut working, 2, 7, 8, 13);
        chacha20_quarter_round(&mut working, 3, 4, 9, 14);
    }

    let mut out = [0u8; 64];
    for i in 0..16 {
        let val = working[i].wrapping_add(state[i]);
        LittleEndian::write_u32(&mut out[i * 4..(i + 1) * 4], val);
    }
    out
}

pub fn chacha20_crypt(key: &[u8; 32], counter: u32, nonce: &[u8; 12], data: &[u8]) -> Vec<u8> {
    let mut out = vec![0u8; data.len()];
    let mut cur_counter = counter;
    let mut offset = 0;

    while offset < data.len() {
        let block = chacha20_block(key, cur_counter, nonce);
        let block_len = (data.len() - offset).min(64);
        for i in 0..block_len {
            out[offset + i] = data[offset + i] ^ block[i];
        }
        offset += block_len;
        cur_counter += 1;
    }
    out
}

// Poly1305 one-time authenticator modulo 2^130 - 5 using 26-bit limbs
pub fn poly1305_mac(key: &[u8; 32], msg: &[u8]) -> [u8; 16] {
    let mut r = [0u8; 16];
    r.copy_from_slice(&key[0..16]);
    // Clamp r per RFC 7539
    r[3] &= 15;
    r[7] &= 15;
    r[11] &= 15;
    r[15] &= 15;
    r[4] &= 252;
    r[8] &= 252;
    r[12] &= 252;

    let r0 = (LittleEndian::read_u32(&r[0..4]) as u64) & 0x3ffffff;
    let r1 = ((LittleEndian::read_u32(&r[3..7]) >> 2) as u64) & 0x3ffff03;
    let r2 = ((LittleEndian::read_u32(&r[6..10]) >> 4) as u64) & 0x3ffc0ff;
    let r3 = ((LittleEndian::read_u32(&r[9..13]) >> 6) as u64) & 0x3f03fff;
    let r4 = ((LittleEndian::read_u32(&r[12..16]) >> 8) as u64) & 0x00fffff;

    let s1 = r1 * 5;
    let s2 = r2 * 5;
    let s3 = r3 * 5;
    let s4 = r4 * 5;

    let mut h0: u64 = 0;
    let mut h1: u64 = 0;
    let mut h2: u64 = 0;
    let mut h3: u64 = 0;
    let mut h4: u64 = 0;

    let mut offset = 0;
    while offset < msg.len() {
        let chunk_len = (msg.len() - offset).min(16);
        let mut block = [0u8; 17];
        block[..chunk_len].copy_from_slice(&msg[offset..offset + chunk_len]);
        block[chunk_len] = 1; // 0x01 byte appended

        let w0 = (LittleEndian::read_u32(&block[0..4]) as u64) & 0x3ffffff;
        let w1 = ((LittleEndian::read_u32(&block[3..7]) >> 2) as u64) & 0x3ffffff;
        let w2 = ((LittleEndian::read_u32(&block[6..10]) >> 4) as u64) & 0x3ffffff;
        let w3 = ((LittleEndian::read_u32(&block[9..13]) >> 6) as u64) & 0x3ffffff;
        let w4 =
            ((LittleEndian::read_u32(&block[12..16]) >> 8) as u64) | ((block[16] as u64) << 24);

        h0 += w0;
        h1 += w1;
        h2 += w2;
        h3 += w3;
        h4 += w4;

        let d0 = (h0 as u128) * (r0 as u128)
            + (h1 as u128) * (s4 as u128)
            + (h2 as u128) * (s3 as u128)
            + (h3 as u128) * (s2 as u128)
            + (h4 as u128) * (s1 as u128);
        let d1 = (h0 as u128) * (r1 as u128)
            + (h1 as u128) * (r0 as u128)
            + (h2 as u128) * (s4 as u128)
            + (h3 as u128) * (s3 as u128)
            + (h4 as u128) * (s2 as u128);
        let d2 = (h0 as u128) * (r2 as u128)
            + (h1 as u128) * (r1 as u128)
            + (h2 as u128) * (r0 as u128)
            + (h3 as u128) * (s4 as u128)
            + (h4 as u128) * (s3 as u128);
        let d3 = (h0 as u128) * (r3 as u128)
            + (h1 as u128) * (r2 as u128)
            + (h2 as u128) * (r1 as u128)
            + (h3 as u128) * (r0 as u128)
            + (h4 as u128) * (s4 as u128);
        let d4 = (h0 as u128) * (r4 as u128)
            + (h1 as u128) * (r3 as u128)
            + (h2 as u128) * (r2 as u128)
            + (h3 as u128) * (r1 as u128)
            + (h4 as u128) * (r0 as u128);

        let mut c: u128;
        c = d0 >> 26;
        h0 = (d0 & 0x3ffffff) as u64;
        let td1 = d1 + c;
        c = td1 >> 26;
        h1 = (td1 & 0x3ffffff) as u64;
        let td2 = d2 + c;
        c = td2 >> 26;
        h2 = (td2 & 0x3ffffff) as u64;
        let td3 = d3 + c;
        c = td3 >> 26;
        h3 = (td3 & 0x3ffffff) as u64;
        let td4 = d4 + c;
        c = td4 >> 26;
        h4 = (td4 & 0x3ffffff) as u64;
        h0 += (c * 5) as u64;
        c = (h0 >> 26) as u128;
        h0 &= 0x3ffffff;
        h1 += c as u64;

        offset += chunk_len;
    }

    // Fully reduce
    let mut c = h0 >> 26;
    h0 &= 0x3ffffff;
    h1 += c;
    c = h1 >> 26;
    h1 &= 0x3ffffff;
    h2 += c;
    c = h2 >> 26;
    h2 &= 0x3ffffff;
    h3 += c;
    c = h3 >> 26;
    h3 &= 0x3ffffff;
    h4 += c;
    c = h4 >> 26;
    h4 &= 0x3ffffff;
    h0 += c * 5;
    c = h0 >> 26;
    h0 &= 0x3ffffff;
    h1 += c;

    // Compute h + 5
    let mut g0 = h0 + 5;
    c = g0 >> 26;
    g0 &= 0x3ffffff;
    let mut g1 = h1 + c;
    c = g1 >> 26;
    g1 &= 0x3ffffff;
    let mut g2 = h2 + c;
    c = g2 >> 26;
    g2 &= 0x3ffffff;
    let mut g3 = h3 + c;
    c = g3 >> 26;
    g3 &= 0x3ffffff;
    let g4 = (h4 + c).wrapping_sub(1 << 26);

    let mask = (g4 >> 63).wrapping_sub(1);
    let nmask = !mask;
    h0 = (h0 & nmask) | (g0 & mask);
    h1 = (h1 & nmask) | (g1 & mask);
    h2 = (h2 & nmask) | (g2 & mask);
    h3 = (h3 & nmask) | (g3 & mask);
    h4 = (h4 & nmask) | (g4 & mask);

    let h128 = (h0 as u128)
        | ((h1 as u128) << 26)
        | ((h2 as u128) << 52)
        | ((h3 as u128) << 78)
        | ((h4 as u128) << 104);

    let s_val = LittleEndian::read_u128(&key[16..32]);
    let tag_val = h128.wrapping_add(s_val);

    let mut tag = [0u8; 16];
    LittleEndian::write_u128(&mut tag, tag_val);
    tag
}

pub fn chacha20_poly1305_encrypt(
    key: &[u8; 32],
    nonce: &[u8; 12],
    aad: &[u8],
    plaintext: &[u8],
) -> Vec<u8> {
    // 1. Generate Poly1305 key via ChaCha20 block 0
    let poly_block = chacha20_block(key, 0, nonce);
    let mut poly_key = [0u8; 32];
    poly_key.copy_from_slice(&poly_block[0..32]);

    // 2. Encrypt plaintext starting at counter = 1
    let ciphertext = chacha20_crypt(key, 1, nonce, plaintext);

    // 3. Construct MAC data: AAD || pad || Ciphertext || pad || len(AAD) || len(Ciphertext)
    let mut mac_data = Vec::new();
    mac_data.extend_from_slice(aad);
    if !aad.len().is_multiple_of(16) {
        mac_data.resize(mac_data.len() + (16 - (aad.len() % 16)), 0);
    }
    mac_data.extend_from_slice(&ciphertext);
    if !ciphertext.len().is_multiple_of(16) {
        mac_data.resize(mac_data.len() + (16 - (ciphertext.len() % 16)), 0);
    }
    let mut len_buf = [0u8; 16];
    LittleEndian::write_u64(&mut len_buf[0..8], aad.len() as u64);
    LittleEndian::write_u64(&mut len_buf[8..16], ciphertext.len() as u64);
    mac_data.extend_from_slice(&len_buf);

    // 4. Compute Poly1305 tag
    let tag = poly1305_mac(&poly_key, &mac_data);

    let mut out = ciphertext;
    out.extend_from_slice(&tag);
    out
}

pub fn chacha20_poly1305_decrypt(
    key: &[u8; 32],
    nonce: &[u8; 12],
    aad: &[u8],
    ciphertext_with_tag: &[u8],
) -> Result<Vec<u8>> {
    if ciphertext_with_tag.len() < TAG_SIZE {
        return Err(anyhow!("Ciphertext too short for Poly1305 MAC tag"));
    }

    let ct_len = ciphertext_with_tag.len() - TAG_SIZE;
    let ciphertext = &ciphertext_with_tag[..ct_len];
    let expected_tag = &ciphertext_with_tag[ct_len..];

    // 1. Generate Poly1305 key
    let poly_block = chacha20_block(key, 0, nonce);
    let mut poly_key = [0u8; 32];
    poly_key.copy_from_slice(&poly_block[0..32]);

    // 2. Recompute MAC data
    let mut mac_data = Vec::new();
    mac_data.extend_from_slice(aad);
    if !aad.len().is_multiple_of(16) {
        mac_data.resize(mac_data.len() + (16 - (aad.len() % 16)), 0);
    }
    mac_data.extend_from_slice(ciphertext);
    if !ciphertext.len().is_multiple_of(16) {
        mac_data.resize(mac_data.len() + (16 - (ciphertext.len() % 16)), 0);
    }
    let mut len_buf = [0u8; 16];
    LittleEndian::write_u64(&mut len_buf[0..8], aad.len() as u64);
    LittleEndian::write_u64(&mut len_buf[8..16], ciphertext.len() as u64);
    mac_data.extend_from_slice(&len_buf);

    let actual_tag = poly1305_mac(&poly_key, &mac_data);

    // Constant-time tag comparison
    let mut diff = 0u8;
    for i in 0..16 {
        diff |= expected_tag[i] ^ actual_tag[i];
    }

    if diff != 0 {
        return Err(anyhow!(
            "Poly1305 authentication MAC mismatch - ciphertext tampered"
        ));
    }

    // 3. Decrypt ciphertext
    let plaintext = chacha20_crypt(key, 1, nonce, ciphertext);
    Ok(plaintext)
}

// ============================================================================
// 4. Ed25519 Identity & Mutual Handshake
// ============================================================================

#[derive(Clone)]
pub struct NodeIdentity {
    pub node_id: u32,
    pub signing_key: SigningKey,
}

impl NodeIdentity {
    pub fn from_bytes(node_id: u32, secret_key_bytes: &[u8; 32]) -> Self {
        let signing_key = SigningKey::from_bytes(secret_key_bytes);
        Self {
            node_id,
            signing_key,
        }
    }

    pub fn verifying_key(&self) -> VerifyingKey {
        self.signing_key.verifying_key()
    }

    pub fn public_key_bytes(&self) -> [u8; 32] {
        self.verifying_key().to_bytes()
    }

    pub fn sign(&self, msg: &[u8]) -> [u8; 64] {
        self.signing_key.sign(msg).to_bytes()
    }

    pub fn verify(verifying_key_bytes: &[u8; 32], msg: &[u8], sig_bytes: &[u8; 64]) -> Result<()> {
        let vk = VerifyingKey::from_bytes(verifying_key_bytes)
            .map_err(|e| anyhow!("Invalid Ed25519 verifying key: {}", e))?;
        let sig = Signature::from_bytes(sig_bytes);
        vk.verify(msg, &sig)
            .map_err(|e| anyhow!("Ed25519 signature verification failed: {}", e))
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HandshakeInitPacket {
    pub sender_node_id: u32,
    pub id_pubkey: [u8; 32],
    pub ephemeral_pubkey: [u8; 32],
    pub signature: Vec<u8>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HandshakeRespPacket {
    pub sender_node_id: u32,
    pub id_pubkey: [u8; 32],
    pub ephemeral_pubkey: [u8; 32],
    pub signature: Vec<u8>,
}

/// Established encrypted session between two nodes.
pub struct NodeCryptoSession {
    pub local_node_id: u32,
    pub remote_node_id: u32,
    pub tx_key: [u8; 32],
    pub rx_key: [u8; 32],
    pub tx_nonce: u64,
    pub rx_nonce: u64,
}

impl NodeCryptoSession {
    pub fn new(
        local_node_id: u32,
        remote_node_id: u32,
        tx_key: [u8; 32],
        rx_key: [u8; 32],
    ) -> Self {
        Self {
            local_node_id,
            remote_node_id,
            tx_key,
            rx_key,
            tx_nonce: 0,
            rx_nonce: 0,
        }
    }

    fn make_nonce(counter: u64, node_id: u32) -> [u8; 12] {
        [
            counter.to_le_bytes().as_slice(),
            node_id.to_be_bytes().as_slice(),
        ]
        .concat()
        .try_into()
        .expect("eight counter bytes and four node-id bytes")
    }

    pub fn encrypt_payload(&mut self, plaintext: &[u8]) -> Vec<u8> {
        let nonce = Self::make_nonce(self.tx_nonce, self.local_node_id);
        self.tx_nonce += 1;
        let aad = self.local_node_id.to_be_bytes();
        chacha20_poly1305_encrypt(&self.tx_key, &nonce, &aad, plaintext)
    }

    pub fn decrypt_payload(&mut self, ciphertext: &[u8]) -> Result<Vec<u8>> {
        let nonce = Self::make_nonce(self.rx_nonce, self.remote_node_id);
        self.rx_nonce += 1;
        let aad = self.remote_node_id.to_be_bytes();
        chacha20_poly1305_decrypt(&self.rx_key, &nonce, &aad, ciphertext)
    }

    /// Parity twin: usr/libexec/mios/node/crypto.py (SessionCipher.encrypt_frame)
    pub fn encrypt_frame(&mut self, frame: &Frame) -> Result<Frame> {
        let encrypted_payload = self.encrypt_payload(&frame.payload);
        Ok(Frame::new(
            frame.header.msg_type,
            self.local_node_id,
            encrypted_payload,
        ))
    }

    /// Parity twin: usr/libexec/mios/node/crypto.py (SessionCipher.decrypt_frame)
    pub fn decrypt_frame(&mut self, frame: &Frame) -> Result<Frame> {
        let decrypted_payload = self.decrypt_payload(&frame.payload)?;
        Ok(Frame::new(
            frame.header.msg_type,
            frame.header.node_id,
            decrypted_payload,
        ))
    }
}

/// Orchestrates mutual cryptographic handshakes between edge nodes.
pub struct CryptoHandshake;

impl CryptoHandshake {
    pub fn create_init(identity: &NodeIdentity, ephemeral_priv: &[u8; 32]) -> HandshakeInitPacket {
        let eph_pub = x25519_public_key(ephemeral_priv);
        let sig = identity.sign(&eph_pub);

        HandshakeInitPacket {
            sender_node_id: identity.node_id,
            id_pubkey: identity.public_key_bytes(),
            ephemeral_pubkey: eph_pub,
            signature: sig.to_vec(),
        }
    }

    pub fn process_init_and_respond(
        identity: &NodeIdentity,
        ephemeral_priv: &[u8; 32],
        init: &HandshakeInitPacket,
    ) -> Result<(HandshakeRespPacket, NodeCryptoSession)> {
        if init.signature.len() != 64 {
            return Err(anyhow!("Invalid signature length"));
        }
        let mut sig_arr = [0u8; 64];
        sig_arr.copy_from_slice(&init.signature);

        // 1. Verify sender's Ed25519 signature over their ephemeral pubkey
        NodeIdentity::verify(&init.id_pubkey, &init.ephemeral_pubkey, &sig_arr)?;

        // 2. Compute ephemeral public key and shared secret
        let eph_pub = x25519_public_key(ephemeral_priv);
        let shared_secret = x25519(ephemeral_priv, &init.ephemeral_pubkey);

        // 3. Derive symmetric session keys via HKDF-SHA256
        let info = b"mios-mesh-wire-v1-session-keys";
        let derived_keys = hkdf_sha256(&[], &shared_secret, info, 64);

        let mut k1 = [0u8; 32];
        let mut k2 = [0u8; 32];
        k1.copy_from_slice(&derived_keys[0..32]);
        k2.copy_from_slice(&derived_keys[32..64]);

        // Responder transmits with k2, receives with k1
        let session = NodeCryptoSession::new(identity.node_id, init.sender_node_id, k2, k1);

        // 4. Sign own ephemeral pubkey
        let sig = identity.sign(&eph_pub);
        let resp = HandshakeRespPacket {
            sender_node_id: identity.node_id,
            id_pubkey: identity.public_key_bytes(),
            ephemeral_pubkey: eph_pub,
            signature: sig.to_vec(),
        };

        Ok((resp, session))
    }

    pub fn finalize_init(
        identity: &NodeIdentity,
        ephemeral_priv: &[u8; 32],
        resp: &HandshakeRespPacket,
    ) -> Result<NodeCryptoSession> {
        if resp.signature.len() != 64 {
            return Err(anyhow!("Invalid signature length"));
        }
        let mut sig_arr = [0u8; 64];
        sig_arr.copy_from_slice(&resp.signature);

        // 1. Verify responder's Ed25519 signature over their ephemeral pubkey
        NodeIdentity::verify(&resp.id_pubkey, &resp.ephemeral_pubkey, &sig_arr)?;

        // 2. Compute shared secret
        let shared_secret = x25519(ephemeral_priv, &resp.ephemeral_pubkey);

        // 3. Derive symmetric session keys
        let info = b"mios-mesh-wire-v1-session-keys";
        let derived_keys = hkdf_sha256(&[], &shared_secret, info, 64);

        let mut k1 = [0u8; 32];
        let mut k2 = [0u8; 32];
        k1.copy_from_slice(&derived_keys[0..32]);
        k2.copy_from_slice(&derived_keys[32..64]);

        // Initiator transmits with k1, receives with k2
        Ok(NodeCryptoSession::new(
            identity.node_id,
            resp.sender_node_id,
            k1,
            k2,
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_x25519_diffie_hellman() {
        let priv_a = [0x42u8; 32];
        let priv_b = [0x99u8; 32];

        let pub_a = x25519_public_key(&priv_a);
        let pub_b = x25519_public_key(&priv_b);

        let shared_a = x25519(&priv_a, &pub_b);
        let shared_b = x25519(&priv_b, &pub_a);

        assert_eq!(shared_a, shared_b);
    }

    #[test]
    fn test_chacha20_poly1305_roundtrip() {
        let key = [0x55u8; 32];
        let nonce = random_bytes::<12>().unwrap();
        let aad = b"header_data";
        let plaintext = b"Sensitive payload for edge compute";

        let encrypted = chacha20_poly1305_encrypt(&key, &nonce, aad, plaintext);
        assert_eq!(encrypted.len(), plaintext.len() + TAG_SIZE);

        let decrypted = chacha20_poly1305_decrypt(&key, &nonce, aad, &encrypted).unwrap();
        assert_eq!(decrypted, plaintext);
    }

    #[test]
    fn test_tamper_detection_mac_failure() {
        let key = [0x77u8; 32];
        let nonce = random_bytes::<12>().unwrap();
        let aad = b"aad";
        let plaintext = b"Secret bytes";

        let mut encrypted = chacha20_poly1305_encrypt(&key, &nonce, aad, plaintext);
        // Corrupt 1 byte
        encrypted[0] ^= 0x01;

        let result = chacha20_poly1305_decrypt(&key, &nonce, aad, &encrypted);
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("MAC mismatch"));
    }

    #[test]
    fn test_mutual_crypto_handshake_session() {
        let id_a = NodeIdentity::from_bytes(101, &[1u8; 32]);
        let id_b = NodeIdentity::from_bytes(202, &[2u8; 32]);

        let eph_a = [3u8; 32];
        let eph_b = [4u8; 32];

        // 1. Node A creates init
        let init = CryptoHandshake::create_init(&id_a, &eph_a);

        // 2. Node B processes init and creates resp + session B
        let (resp, mut session_b) =
            CryptoHandshake::process_init_and_respond(&id_b, &eph_b, &init).unwrap();

        // 3. Node A finalizes init and creates session A
        let mut session_a = CryptoHandshake::finalize_init(&id_a, &eph_a, &resp).unwrap();

        // 4. Test encrypted communication A -> B
        let plain_a = b"Hello Node B from Node A";
        let enc_a = session_a.encrypt_payload(plain_a);
        let dec_b = session_b.decrypt_payload(&enc_a).unwrap();
        assert_eq!(dec_b, plain_a);

        // 5. Test encrypted communication B -> A
        let plain_b = b"Ack Node A from Node B";
        let enc_b = session_b.encrypt_payload(plain_b);
        let dec_a = session_a.decrypt_payload(&enc_b).unwrap();
        assert_eq!(dec_a, plain_b);
    }
}
