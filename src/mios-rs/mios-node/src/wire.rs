// AI-hint: mios-node wire layer: the 16-byte-header binary frame protocol, the zero-copy frame buffer pool and the async Tokio TCP frame reader/writer and network actor (task_337).
// AI-related: usr/libexec/mios/node/buffer_pool.py, tests/test-node.py, src/mios-rs/mios-node/src/lib.rs, tests/test-node-mesh.py

pub mod protocol {
    // AI-hint: 16-byte fixed header binary wire protocol parser and generator for mios-node.
    // AI-related: src/mios-rs/mios-node/src/node.rs
    //! MiOS Binary Wire Protocol Specification & Framing Engine
    //! Header Format (16 Bytes Fixed, Big-Endian Network Byte Order):
    //!
    //! +-------------------------------------------------------------------+
    //! | Magic (2B: 0x4D 0x49) | Ver (1B) | MsgType (1B) | NodeID (4B: u32) |
    //! +-------------------------------------------------------------------+
    //! | PayloadLen (4B: u32)             | Checksum (4B: u32 CRC32)       |
    //! +-------------------------------------------------------------------+

    use anyhow::{anyhow, Result};
    use byteorder::{BigEndian, ByteOrder};
    use crc32fast::Hasher;
    use serde::{Deserialize, Serialize};

    pub const MIOS_MAGIC: u16 = 0x4D49; // 'MI'
    pub const MIOS_VERSION: u8 = 0x01;
    pub const HEADER_SIZE: usize = 16;

    #[repr(u8)]
    #[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
    pub enum MessageType {
        Heartbeat = 0x01,
        NodeAnnounce = 0x02,
        TaskOffload = 0x03,
        TaskResult = 0x04,
        StateSync = 0x05,
        StateAck = 0x06,
        Error = 0x07,
    }

    impl TryFrom<u8> for MessageType {
        type Error = anyhow::Error;

        fn try_from(value: u8) -> std::result::Result<Self, anyhow::Error> {
            match value {
                0x01 => Ok(MessageType::Heartbeat),
                0x02 => Ok(MessageType::NodeAnnounce),
                0x03 => Ok(MessageType::TaskOffload),
                0x04 => Ok(MessageType::TaskResult),
                0x05 => Ok(MessageType::StateSync),
                0x06 => Ok(MessageType::StateAck),
                0x07 => Ok(MessageType::Error),
                _ => Err(anyhow!("Unknown MiOS message opcode: 0x{:02X}", value)),
            }
        }
    }

    #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
    pub struct Header {
        pub magic: u16,
        pub version: u8,
        pub msg_type: MessageType,
        pub node_id: u32,
        pub payload_len: u32,
        pub checksum: u32,
    }

    impl Header {
        pub fn new(msg_type: MessageType, node_id: u32, payload_len: u32, checksum: u32) -> Self {
            Self {
                magic: MIOS_MAGIC,
                version: MIOS_VERSION,
                msg_type,
                node_id,
                payload_len,
                checksum,
            }
        }

        pub fn encode(&self, buf: &mut [u8]) -> Result<()> {
            if buf.len() < HEADER_SIZE {
                return Err(anyhow!("Buffer too small for MiOS header"));
            }
            BigEndian::write_u16(&mut buf[0..2], self.magic);
            buf[2] = self.version;
            buf[3] = self.msg_type as u8;
            BigEndian::write_u32(&mut buf[4..8], self.node_id);
            BigEndian::write_u32(&mut buf[8..12], self.payload_len);
            BigEndian::write_u32(&mut buf[12..16], self.checksum);
            Ok(())
        }

        pub fn decode(buf: &[u8]) -> Result<Self> {
            if buf.len() < HEADER_SIZE {
                return Err(anyhow!("Buffer too small for MiOS header decode"));
            }
            let magic = BigEndian::read_u16(&buf[0..2]);
            if magic != MIOS_MAGIC {
                return Err(anyhow!("Invalid MiOS magic: 0x{:04X}", magic));
            }
            let version = buf[2];
            if version != MIOS_VERSION {
                return Err(anyhow!("Unsupported MiOS protocol version: {}", version));
            }
            let msg_type = MessageType::try_from(buf[3])?;
            let node_id = BigEndian::read_u32(&buf[4..8]);
            let payload_len = BigEndian::read_u32(&buf[8..12]);
            let checksum = BigEndian::read_u32(&buf[12..16]);

            Ok(Self {
                magic,
                version,
                msg_type,
                node_id,
                payload_len,
                checksum,
            })
        }
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct Frame {
        pub header: Header,
        pub payload: Vec<u8>,
    }

    impl Frame {
        pub fn new(msg_type: MessageType, node_id: u32, payload: Vec<u8>) -> Self {
            let mut hasher = Hasher::new();
            hasher.update(&payload);
            let checksum = hasher.finalize();

            let header = Header::new(msg_type, node_id, payload.len() as u32, checksum);
            Self { header, payload }
        }

        pub fn encode(&self) -> Result<Vec<u8>> {
            let total_len = HEADER_SIZE + self.payload.len();
            let mut buf = vec![0u8; total_len];
            self.header.encode(&mut buf[0..HEADER_SIZE])?;
            buf[HEADER_SIZE..].copy_from_slice(&self.payload);
            Ok(buf)
        }

        pub fn decode(buf: &[u8]) -> Result<Self> {
            let header = Header::decode(buf)?;
            let expected_end = HEADER_SIZE + header.payload_len as usize;
            if buf.len() < expected_end {
                return Err(anyhow!(
                    "Incomplete payload: expected {} bytes, got {}",
                    header.payload_len,
                    buf.len() - HEADER_SIZE
                ));
            }
            let payload = buf[HEADER_SIZE..expected_end].to_vec();

            let mut hasher = Hasher::new();
            hasher.update(&payload);
            let actual_checksum = hasher.finalize();

            if actual_checksum != header.checksum {
                return Err(anyhow!(
                    "CRC32 mismatch: expected 0x{:08X}, got 0x{:08X}",
                    header.checksum,
                    actual_checksum
                ));
            }

            Ok(Self { header, payload })
        }
    }

    // Payload structs
    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct HeartbeatPayload {
        pub uptime_secs: u64,
        pub cpu_load_pct: u8,
        pub mem_available_kb: u32,
        pub active_tasks: u16,
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct TaskOffloadPayload {
        pub task_id: u64,
        pub tier: u8,         // 1 = Wasm, 2 = Native
        pub target_arch: u16, // 0 = Agnostic, 1 = x86_64, 2 = AArch64, 3 = RISC-V 64
        pub memory_limit_bytes: u32,
        pub execution_timeout_ms: u32,
        pub code_bytes: Vec<u8>,
        pub input_data: Vec<u8>,
        pub signature: Option<Vec<u8>>, // Ed25519 signature for Tier 2 native binaries
        pub public_key: Option<Vec<u8>>, // Ed25519 public key
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct TaskResultPayload {
        pub task_id: u64,
        pub success: bool,
        pub exit_code: i32,
        pub output_data: Vec<u8>,
        pub error_msg: Option<String>,
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct StateSyncPayload {
        pub vector_clock: crate::state_sync::VectorClock,
        pub mutations: Vec<crate::state_sync::StateElement>,
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct StateAckPayload {
        pub node_id: u32,
        pub applied_count: usize,
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_header_encode_decode() {
            let header = Header::new(MessageType::Heartbeat, 101, 64, 0x12345678);
            let mut buf = [0u8; HEADER_SIZE];
            header.encode(&mut buf).unwrap();

            let decoded = Header::decode(&buf).unwrap();
            assert_eq!(header, decoded);
        }

        #[test]
        fn test_frame_encode_decode_with_crc() {
            let payload_data = b"Hello MiOS edge node protocol!".to_vec();
            let frame = Frame::new(MessageType::TaskOffload, 42, payload_data.clone());

            let encoded = frame.encode().unwrap();
            let decoded = Frame::decode(&encoded).unwrap();

            assert_eq!(decoded.header.node_id, 42);
            assert_eq!(decoded.header.msg_type, MessageType::TaskOffload);
            assert_eq!(decoded.payload, payload_data);
        }

        #[test]
        fn test_crc_corruption_detection() {
            let payload_data = b"Sensitive task data".to_vec();
            let frame = Frame::new(MessageType::TaskOffload, 1, payload_data);

            let mut encoded = frame.encode().unwrap();
            let last_idx = encoded.len() - 1;
            encoded[last_idx] ^= 0xFF;

            let result = Frame::decode(&encoded);
            assert!(result.is_err());
            assert!(result.unwrap_err().to_string().contains("CRC32 mismatch"));
        }
    }
}

pub mod buffer_pool {
    //! MiOS Zero-Copy Network Buffer Pool
    //!
    //! Provides bucketed pre-allocation (Small 256B, Medium 4KB, Large 64KB, Huge 1MB),
    //! RAII auto-recycling via drop guards, bounded memory footprint, zero-copy slicing,
    //! and allocation telemetry.

    use anyhow::{anyhow, Result};
    use serde::{Deserialize, Serialize};
    use std::ops::{Deref, DerefMut};
    use std::sync::{Arc, Mutex};

    /// Buffer size tiers for bucketed memory allocation.
    #[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
    pub enum BucketTier {
        Small = 256,    // 256 B: 16B header, heartbeats, node announce, acks
        Medium = 4096,  // 4 KB: standard payloads, telemetry
        Large = 65536,  // 64 KB: CRDT state sync batches, medium chunks
        Huge = 1048576, // 1 MB: Wasm modules, native code payloads
    }

    impl BucketTier {
        pub fn capacity_bytes(&self) -> usize {
            *self as usize
        }

        /// Maximum number of buffers retained in the pool for this tier.
        pub fn max_pool_capacity(&self) -> usize {
            match self {
                BucketTier::Small => 256,
                BucketTier::Medium => 64,
                BucketTier::Large => 32,
                BucketTier::Huge => 8,
            }
        }

        /// Selects the smallest tier that fits the requested size.
        pub fn from_size(size: usize) -> Self {
            if size <= BucketTier::Small.capacity_bytes() {
                BucketTier::Small
            } else if size <= BucketTier::Medium.capacity_bytes() {
                BucketTier::Medium
            } else if size <= BucketTier::Large.capacity_bytes() {
                BucketTier::Large
            } else {
                BucketTier::Huge
            }
        }
    }

    /// Telemetry metrics for buffer pool performance.
    #[derive(Debug, Default, Clone, PartialEq, Eq, Serialize, Deserialize)]
    pub struct PoolStats {
        pub allocations: u64,
        pub recycles: u64,
        pub pool_hits: u64,
        pub pool_misses: u64,
        pub active_leased: u64,
    }

    /// Thread-safe bucketed memory buffer pool.
    pub struct BufferPool {
        small_bucket: Mutex<Vec<Vec<u8>>>,
        medium_bucket: Mutex<Vec<Vec<u8>>>,
        large_bucket: Mutex<Vec<Vec<u8>>>,
        huge_bucket: Mutex<Vec<Vec<u8>>>,
        stats: Mutex<PoolStats>,
    }

    impl BufferPool {
        pub fn new() -> Arc<Self> {
            Arc::new(Self {
                small_bucket: Mutex::new(Vec::with_capacity(BucketTier::Small.max_pool_capacity())),
                medium_bucket: Mutex::new(Vec::with_capacity(
                    BucketTier::Medium.max_pool_capacity(),
                )),
                large_bucket: Mutex::new(Vec::with_capacity(BucketTier::Large.max_pool_capacity())),
                huge_bucket: Mutex::new(Vec::with_capacity(BucketTier::Huge.max_pool_capacity())),
                stats: Mutex::new(PoolStats::default()),
            })
        }

        /// Pre-populates the pool with a specified number of buffers per tier.
        pub fn preallocate(self: &Arc<Self>, small_count: usize, medium_count: usize) {
            let mut smalls = self.small_bucket.lock().unwrap();
            for _ in 0..small_count.min(BucketTier::Small.max_pool_capacity()) {
                smalls.push(Vec::with_capacity(BucketTier::Small.capacity_bytes()));
            }

            let mut mediums = self.medium_bucket.lock().unwrap();
            for _ in 0..medium_count.min(BucketTier::Medium.max_pool_capacity()) {
                mediums.push(Vec::with_capacity(BucketTier::Medium.capacity_bytes()));
            }
        }

        /// Leases a buffer suitable for the requested size hint.
        pub fn acquire(self: &Arc<Self>, size_hint: usize) -> PooledBuffer {
            let tier = BucketTier::from_size(size_hint);
            self.acquire_exact(tier)
        }

        /// Leases a buffer of the exact requested tier.
        pub fn acquire_exact(self: &Arc<Self>, tier: BucketTier) -> PooledBuffer {
            let bucket_guard = match tier {
                BucketTier::Small => &self.small_bucket,
                BucketTier::Medium => &self.medium_bucket,
                BucketTier::Large => &self.large_bucket,
                BucketTier::Huge => &self.huge_bucket,
            };

            let mut bucket = bucket_guard.lock().unwrap();
            let (buf, is_hit) = if let Some(reused) = bucket.pop() {
                (reused, true)
            } else {
                (Vec::with_capacity(tier.capacity_bytes()), false)
            };
            drop(bucket);

            let mut stats = self.stats.lock().unwrap();
            stats.allocations += 1;
            stats.active_leased += 1;
            if is_hit {
                stats.pool_hits += 1;
            } else {
                stats.pool_misses += 1;
            }
            drop(stats);

            PooledBuffer {
                buffer: Some(buf),
                tier,
                pool: Some(Arc::clone(self)),
            }
        }

        /// Internal recycling hook invoked by `PooledBuffer::drop`.
        pub(crate) fn recycle(&self, tier: BucketTier, mut buf: Vec<u8>) {
            buf.clear();
            let max_cap = tier.max_pool_capacity();

            let bucket_guard = match tier {
                BucketTier::Small => &self.small_bucket,
                BucketTier::Medium => &self.medium_bucket,
                BucketTier::Large => &self.large_bucket,
                BucketTier::Huge => &self.huge_bucket,
            };

            let mut bucket = bucket_guard.lock().unwrap();
            let was_recycled = if bucket.len() < max_cap {
                bucket.push(buf);
                true
            } else {
                false
            };
            drop(bucket);

            let mut stats = self.stats.lock().unwrap();
            if stats.active_leased > 0 {
                stats.active_leased -= 1;
            }
            if was_recycled {
                stats.recycles += 1;
            }
        }

        pub fn get_stats(&self) -> PoolStats {
            self.stats.lock().unwrap().clone()
        }

        pub fn bucket_depths(&self) -> (usize, usize, usize, usize) {
            (
                self.small_bucket.lock().unwrap().len(),
                self.medium_bucket.lock().unwrap().len(),
                self.large_bucket.lock().unwrap().len(),
                self.huge_bucket.lock().unwrap().len(),
            )
        }
    }

    /// RAII smart pointer wrapping a pooled buffer with zero-copy operations.
    pub struct PooledBuffer {
        buffer: Option<Vec<u8>>,
        tier: BucketTier,
        pool: Option<Arc<BufferPool>>,
    }

    impl PooledBuffer {
        /// Creates an unpooled standalone buffer.
        pub fn standalone(tier: BucketTier) -> Self {
            Self {
                buffer: Some(Vec::with_capacity(tier.capacity_bytes())),
                tier,
                pool: None,
            }
        }

        pub fn tier(&self) -> BucketTier {
            self.tier
        }

        pub fn capacity(&self) -> usize {
            self.buffer.as_ref().map_or(0, |b| b.capacity())
        }

        pub fn len(&self) -> usize {
            self.buffer.as_ref().map_or(0, |b| b.len())
        }

        pub fn is_empty(&self) -> bool {
            self.len() == 0
        }

        pub fn clear(&mut self) {
            if let Some(buf) = self.buffer.as_mut() {
                buf.clear();
            }
        }

        pub fn extend_from_slice(&mut self, slice: &[u8]) {
            if let Some(buf) = self.buffer.as_mut() {
                buf.extend_from_slice(slice);
            }
        }

        pub fn as_slice(&self) -> &[u8] {
            self.buffer.as_ref().map_or(&[], |b| b.as_slice())
        }

        pub fn as_mut_slice(&mut self) -> &mut [u8] {
            self.buffer.as_mut().map_or(&mut [], |b| b.as_mut_slice())
        }

        /// Zero-copy subslice inspection.
        pub fn slice(&self, start: usize, end: usize) -> Result<&[u8]> {
            let b = self
                .buffer
                .as_ref()
                .ok_or_else(|| anyhow!("Buffer already released"))?;
            if start > end || end > b.len() {
                return Err(anyhow!(
                    "Slice range {}..{} out of bounds (len: {})",
                    start,
                    end,
                    b.len()
                ));
            }
            Ok(&b[start..end])
        }

        /// Splits off the first `at` bytes, copying only what is necessary and keeping the rest.
        pub fn split_prefix(&mut self, at: usize) -> Result<Vec<u8>> {
            let b = self
                .buffer
                .as_mut()
                .ok_or_else(|| anyhow!("Buffer already released"))?;
            if at > b.len() {
                return Err(anyhow!(
                    "Split prefix index {} exceeds buffer length {}",
                    at,
                    b.len()
                ));
            }
            let prefix = b[..at].to_vec();
            b.drain(..at);
            Ok(prefix)
        }

        /// Consumes the pooled buffer without recycling, returning the underlying Vec.
        pub fn into_vec(mut self) -> Vec<u8> {
            let pool = self.pool.take();
            if let Some(p) = pool {
                let mut stats = p.stats.lock().unwrap();
                if stats.active_leased > 0 {
                    stats.active_leased -= 1;
                }
            }
            self.buffer.take().unwrap_or_default()
        }
    }

    impl Deref for PooledBuffer {
        type Target = [u8];

        fn deref(&self) -> &Self::Target {
            self.as_slice()
        }
    }

    impl DerefMut for PooledBuffer {
        fn deref_mut(&mut self) -> &mut Self::Target {
            self.as_mut_slice()
        }
    }

    impl Drop for PooledBuffer {
        fn drop(&mut self) {
            if let Some(buf) = self.buffer.take() {
                if let Some(pool) = self.pool.take() {
                    pool.recycle(self.tier, buf);
                }
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_bucket_tier_resolution() {
            assert_eq!(BucketTier::from_size(16), BucketTier::Small);
            assert_eq!(BucketTier::from_size(256), BucketTier::Small);
            assert_eq!(BucketTier::from_size(257), BucketTier::Medium);
            assert_eq!(BucketTier::from_size(4096), BucketTier::Medium);
            assert_eq!(BucketTier::from_size(4097), BucketTier::Large);
            assert_eq!(BucketTier::from_size(65536), BucketTier::Large);
            assert_eq!(BucketTier::from_size(65537), BucketTier::Huge);
        }

        #[test]
        fn test_raii_buffer_recycling() {
            let pool = BufferPool::new();

            {
                let mut buf1 = pool.acquire(100);
                assert_eq!(buf1.tier(), BucketTier::Small);
                buf1.extend_from_slice(b"12345678");
                assert_eq!(buf1.len(), 8);
                assert_eq!(buf1.as_slice(), b"12345678");

                let stats = pool.get_stats();
                assert_eq!(stats.allocations, 1);
                assert_eq!(stats.pool_misses, 1);
                assert_eq!(stats.active_leased, 1);
            } // buf1 dropped here -> recycled into small_bucket

            let stats = pool.get_stats();
            assert_eq!(stats.recycles, 1);
            assert_eq!(stats.active_leased, 0);

            // Next allocation should hit the recycled buffer
            {
                let mut buf2 = pool.acquire(100);
                assert_eq!(buf2.tier(), BucketTier::Small);
                assert_eq!(buf2.len(), 0); // Cleared upon recycling
                buf2.extend_from_slice(b"reused");

                let stats2 = pool.get_stats();
                assert_eq!(stats2.allocations, 2);
                assert_eq!(stats2.pool_hits, 1);
                assert_eq!(stats2.active_leased, 1);
            }
        }

        #[test]
        fn test_zero_copy_slicing_and_split() {
            let pool = BufferPool::new();
            let mut buf = pool.acquire(500); // Medium tier
            buf.extend_from_slice(b"HEADER_16BYTES__PAYLOAD_BODY_DATA");

            let sub = buf.slice(0, 16).unwrap();
            assert_eq!(sub, b"HEADER_16BYTES__");

            let payload_sub = buf.slice(16, buf.len()).unwrap();
            assert_eq!(payload_sub, b"PAYLOAD_BODY_DATA");

            let prefix = buf.split_prefix(16).unwrap();
            assert_eq!(prefix, b"HEADER_16BYTES__");
            assert_eq!(buf.as_slice(), b"PAYLOAD_BODY_DATA");
        }
    }
}

pub mod net {
    //! MiOS Async TCP Frame Reader, Writer & Network Actor
    //!
    //! Implements high-concurrency, asynchronous TCP stream framing over the 16-byte fixed binary header
    //! wire protocol (Magic 0x4D49, Version 1, Opcode, NodeID, PayloadLen, CRC32).

    use crate::protocol::{Frame, Header, HEADER_SIZE, MIOS_MAGIC, MIOS_VERSION};
    use anyhow::{anyhow, Result};
    use byteorder::{BigEndian, ByteOrder};
    use crc32fast::Hasher;
    use std::collections::HashMap;
    use std::net::SocketAddr;
    use std::sync::Arc;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::{TcpListener, TcpStream};
    use tokio::sync::{mpsc, RwLock};

    pub const MAX_PAYLOAD_LEN: usize = 64 * 1024 * 1024; // 64 MB payload ceiling

    /// Codec for reading and writing 16-byte framed packets asynchronously over Tokio streams.
    pub struct AsyncFrameCodec;

    impl AsyncFrameCodec {
        /// Reads a single complete frame from an asynchronous reader.
        /// Handles partial reads by looping until all header bytes and payload bytes are received.
        pub async fn read_frame<R: AsyncReadExt + Unpin>(reader: &mut R) -> Result<Frame> {
            let mut header_buf = [0u8; HEADER_SIZE];
            reader.read_exact(&mut header_buf).await.map_err(|e| {
                anyhow!(
                    "Failed to read frame header ({} bytes expected): {}",
                    HEADER_SIZE,
                    e
                )
            })?;

            let header = Header::decode(&header_buf)?;

            if (header.payload_len as usize) > MAX_PAYLOAD_LEN {
                return Err(anyhow!(
                    "Payload length {} exceeds maximum allowed ceiling {}",
                    header.payload_len,
                    MAX_PAYLOAD_LEN
                ));
            }

            let mut payload = vec![0u8; header.payload_len as usize];
            if !payload.is_empty() {
                reader.read_exact(&mut payload).await.map_err(|e| {
                    anyhow!(
                        "Failed to read frame payload ({} bytes expected): {}",
                        header.payload_len,
                        e
                    )
                })?;
            }

            let mut hasher = Hasher::new();
            hasher.update(&payload);
            let actual_checksum = hasher.finalize();

            if actual_checksum != header.checksum {
                return Err(anyhow!(
                    "CRC32 mismatch: expected 0x{:08X}, got 0x{:08X}",
                    header.checksum,
                    actual_checksum
                ));
            }

            Ok(Frame { header, payload })
        }

        /// Serializes and writes a complete frame asynchronously to a writer, then flushes.
        pub async fn write_frame<W: AsyncWriteExt + Unpin>(
            writer: &mut W,
            frame: &Frame,
        ) -> Result<()> {
            let encoded = frame.encode()?;
            writer
                .write_all(&encoded)
                .await
                .map_err(|e| anyhow!("Failed to write frame ({} bytes): {}", encoded.len(), e))?;
            writer
                .flush()
                .await
                .map_err(|e| anyhow!("Failed to flush writer after frame output: {}", e))?;
            Ok(())
        }
    }

    /// In-memory stream buffer for accumulating chunked TCP streams and extracting complete frames.
    #[derive(Debug, Default, Clone)]
    pub struct FrameStreamBuffer {
        buffer: Vec<u8>,
    }

    impl FrameStreamBuffer {
        pub fn new() -> Self {
            Self {
                buffer: Vec::with_capacity(4096),
            }
        }

        /// Feeds incoming raw byte chunks into the stream buffer.
        pub fn feed(&mut self, data: &[u8]) {
            self.buffer.extend_from_slice(data);
        }

        /// Current unparsed bytes in buffer.
        pub fn len(&self) -> usize {
            self.buffer.len()
        }

        pub fn is_empty(&self) -> bool {
            self.buffer.is_empty()
        }

        /// Attempts to parse and pop the next complete Frame from the buffer.
        /// Returns:
        /// - `Ok(Some(Frame))` if a complete, valid frame was extracted.
        /// - `Ok(None)` if more bytes are needed.
        /// - `Err(e)` if header or CRC32 validation fails.
        pub fn try_pop_frame(&mut self) -> Result<Option<Frame>> {
            if self.buffer.len() < HEADER_SIZE {
                return Ok(None);
            }

            // Validate magic before consuming
            let magic = BigEndian::read_u16(&self.buffer[0..2]);
            if magic != MIOS_MAGIC {
                return Err(anyhow!("Invalid MiOS magic: 0x{:04X}", magic));
            }

            let version = self.buffer[2];
            if version != MIOS_VERSION {
                return Err(anyhow!("Unsupported MiOS protocol version: {}", version));
            }

            let payload_len = BigEndian::read_u32(&self.buffer[8..12]) as usize;
            if payload_len > MAX_PAYLOAD_LEN {
                return Err(anyhow!(
                    "Payload length {} exceeds maximum allowed ceiling {}",
                    payload_len,
                    MAX_PAYLOAD_LEN
                ));
            }

            let total_frame_len = HEADER_SIZE + payload_len;
            if self.buffer.len() < total_frame_len {
                // Need more bytes
                return Ok(None);
            }

            // We have enough bytes: decode frame
            let frame_bytes: Vec<u8> = self.buffer.drain(0..total_frame_len).collect();
            let frame = Frame::decode(&frame_bytes)?;
            Ok(Some(frame))
        }

        /// Clears any residual data in the buffer.
        pub fn clear(&mut self) {
            self.buffer.clear();
        }
    }

    /// Message dispatched to or from the Network Actor.
    #[derive(Debug, Clone)]
    pub struct NetMessage {
        pub frame: Frame,
        pub peer_addr: SocketAddr,
    }

    /// Asynchronous TCP Frame Actor for mios-node.
    /// Manages incoming listener connections, per-peer read/write loops, and channel multiplexing.
    pub struct NetActor {
        pub node_id: u32,
        pub bind_addr: SocketAddr,
        pub tx_incoming: mpsc::Sender<NetMessage>,
        pub peer_writers: Arc<RwLock<HashMap<SocketAddr, mpsc::Sender<Frame>>>>,
    }

    impl NetActor {
        pub fn new(
            node_id: u32,
            bind_addr: SocketAddr,
            tx_incoming: mpsc::Sender<NetMessage>,
        ) -> Self {
            Self {
                node_id,
                bind_addr,
                tx_incoming,
                peer_writers: Arc::new(RwLock::new(HashMap::new())),
            }
        }

        /// Starts the TCP listener and accepts incoming connections until cancelled.
        pub async fn run(&self, rx_outbound: Option<mpsc::Receiver<NetMessage>>) -> Result<()> {
            let listener = TcpListener::bind(self.bind_addr)
                .await
                .map_err(|e| anyhow!("Failed to bind TCP listener on {}: {}", self.bind_addr, e))?;

            let peer_writers = self.peer_writers.clone();

            // Spawn outbound routing task if rx_outbound is provided
            if let Some(mut rx) = rx_outbound {
                let writers_clone = peer_writers.clone();
                tokio::spawn(async move {
                    while let Some(msg) = rx.recv().await {
                        let writers = writers_clone.read().await;
                        if let Some(tx) = writers.get(&msg.peer_addr) {
                            let _ = tx.send(msg.frame).await;
                        }
                    }
                });
            }

            loop {
                let (stream, peer_addr) = listener.accept().await?;
                let tx_in = self.tx_incoming.clone();
                let writers = peer_writers.clone();

                let (tx_peer_out, rx_peer_out) = mpsc::channel::<Frame>(64);
                {
                    let mut w = writers.write().await;
                    w.insert(peer_addr, tx_peer_out);
                }

                tokio::spawn(async move {
                    let _ = Self::handle_connection(stream, peer_addr, tx_in, rx_peer_out).await;
                    let mut w = writers.write().await;
                    w.remove(&peer_addr);
                });
            }
        }

        /// Handles a single bidirectional framed TCP connection.
        async fn handle_connection(
            stream: TcpStream,
            peer_addr: SocketAddr,
            tx_incoming: mpsc::Sender<NetMessage>,
            mut rx_outbound: mpsc::Receiver<Frame>,
        ) -> Result<()> {
            let (mut reader, mut writer) = stream.into_split();

            // Read loop task
            let tx_in_clone = tx_incoming.clone();
            let read_task = tokio::spawn(async move {
                while let Ok(frame) = AsyncFrameCodec::read_frame(&mut reader).await {
                    let msg = NetMessage { frame, peer_addr };
                    if tx_in_clone.send(msg).await.is_err() {
                        break;
                    }
                }
            });

            // Write loop task
            let write_task = tokio::spawn(async move {
                while let Some(frame) = rx_outbound.recv().await {
                    if AsyncFrameCodec::write_frame(&mut writer, &frame)
                        .await
                        .is_err()
                    {
                        break;
                    }
                }
            });

            tokio::select! {
                _ = read_task => {},
                _ = write_task => {},
            }

            Ok(())
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use crate::protocol::MessageType;
        use tokio::io::duplex;

        #[tokio::test]
        async fn test_async_frame_codec_roundtrip() {
            let (mut client, mut server) = duplex(1024);

            let frame = Frame::new(
                MessageType::Heartbeat,
                101,
                b"{\"uptime_secs\":120}".to_vec(),
            );

            let send_handle = tokio::spawn(async move {
                AsyncFrameCodec::write_frame(&mut client, &frame)
                    .await
                    .unwrap();
            });

            let recv_handle =
                tokio::spawn(
                    async move { AsyncFrameCodec::read_frame(&mut server).await.unwrap() },
                );

            send_handle.await.unwrap();
            let received = recv_handle.await.unwrap();

            assert_eq!(received.header.node_id, 101);
            assert_eq!(received.header.msg_type, MessageType::Heartbeat);
            assert_eq!(received.payload, b"{\"uptime_secs\":120}");
        }

        #[tokio::test]
        async fn test_stream_buffer_chunked_feeding() {
            let mut buf = FrameStreamBuffer::new();

            let frame1 = Frame::new(MessageType::TaskOffload, 42, b"TASK_CHUNK_1".to_vec());
            let frame2 = Frame::new(MessageType::StateSync, 42, b"STATE_CHUNK_2".to_vec());

            let mut raw = frame1.encode().unwrap();
            raw.extend_from_slice(&frame2.encode().unwrap());

            // Feed byte-by-byte
            let mut popped = Vec::new();
            for byte in raw {
                buf.feed(&[byte]);
                while let Ok(Some(f)) = buf.try_pop_frame() {
                    popped.push(f);
                }
            }

            assert_eq!(popped.len(), 2);
            assert_eq!(popped[0].header.msg_type, MessageType::TaskOffload);
            assert_eq!(popped[0].payload, b"TASK_CHUNK_1");
            assert_eq!(popped[1].header.msg_type, MessageType::StateSync);
            assert_eq!(popped[1].payload, b"STATE_CHUNK_2");
        }

        #[tokio::test]
        async fn test_stream_buffer_invalid_magic() {
            let mut buf = FrameStreamBuffer::new();
            let mut corrupted = [0u8; 16];
            corrupted[0] = 0xAA;
            corrupted[1] = 0xBB;
            buf.feed(&corrupted);

            let result = buf.try_pop_frame();
            assert!(result.is_err());
            assert!(result
                .unwrap_err()
                .to_string()
                .contains("Invalid MiOS magic"));
        }
    }
}
