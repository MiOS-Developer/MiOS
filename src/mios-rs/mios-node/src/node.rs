// AI-hint: Micro-node networking daemon, peer discovery, and gossip protocol manager.
// AI-related: src/mios-rs/mios-node/src/main.rs, src/mios-rs/mios-node/src/wire.rs
//! MiOS Micro-Node Daemon & Network Peer Manager

use crate::executor::ExecutionEngine;
use crate::protocol::{
    Frame, HeartbeatPayload, MessageType, StateAckPayload, StateSyncPayload, TaskOffloadPayload,
    TaskResultPayload,
};
use crate::state_sync::StateStore;
use anyhow::Result;
use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use tokio::net::UdpSocket;
use tokio::time::{self, Duration};

#[derive(Debug, Clone)]
pub struct PeerInfo {
    pub node_id: u32,
    pub addr: SocketAddr,
    pub last_seen_secs: u64,
    pub uptime_secs: u64,
    pub cpu_load_pct: u8,
    pub mem_available_kb: u32,
    pub is_degraded: bool,
}

pub struct MiOSNode {
    pub node_id: u32,
    pub port: u16,
    pub state_store: Arc<Mutex<StateStore>>,
    pub execution_engine: Arc<ExecutionEngine>,
    pub routing_table: Arc<Mutex<HashMap<u32, PeerInfo>>>,
}

impl MiOSNode {
    pub fn new(node_id: u32, port: u16) -> Self {
        let state_store = Arc::new(Mutex::new(StateStore::new(node_id)));
        let execution_engine = Arc::new(ExecutionEngine::new(state_store.clone()));
        let routing_table = Arc::new(Mutex::new(HashMap::new()));

        Self {
            node_id,
            port,
            state_store,
            execution_engine,
            routing_table,
        }
    }

    pub async fn run(&self) -> Result<()> {
        let bind_addr = format!("0.0.0.0:{}", self.port);
        let socket = Arc::new(UdpSocket::bind(&bind_addr).await?);
        socket.set_broadcast(true)?;

        println!(
            "🚀 [MiOS Node {}] Online & Listening on UDP {}",
            self.node_id, bind_addr
        );

        // 1. Spawn Heartbeat broadcast task
        let node_id = self.node_id;
        // Peers listen on the port this node was started on, not on a literal.
        let broadcast = format!("255.255.255.255:{}", self.port);
        let broadcast_hb = broadcast.clone();
        let socket_hb = socket.clone();
        tokio::spawn(async move {
            let mut interval = time::interval(Duration::from_secs(3));
            let mut uptime = 0u64;
            loop {
                interval.tick().await;
                uptime += 3;
                let payload = HeartbeatPayload {
                    uptime_secs: uptime,
                    cpu_load_pct: 15,
                    mem_available_kb: 512000,
                    active_tasks: 0,
                };
                if let Ok(payload_bytes) = serde_json::to_vec(&payload) {
                    let frame = Frame::new(MessageType::Heartbeat, node_id, payload_bytes);
                    if let Ok(encoded) = frame.encode() {
                        let _ = socket_hb.send_to(&encoded, broadcast_hb.as_str()).await;
                    }
                }
            }
        });

        // 2. Spawn Anti-Entropy State Gossip task (every 10s)
        let state_store_gossip = self.state_store.clone();
        let socket_gossip = socket.clone();
        tokio::spawn(async move {
            let mut interval = time::interval(Duration::from_secs(10));
            loop {
                interval.tick().await;
                let (vc, elems) = {
                    let store = state_store_gossip.lock().unwrap();
                    (store.vector_clock.clone(), store.replicable_elements())
                };

                if !elems.is_empty() {
                    let sync_payload = StateSyncPayload {
                        vector_clock: vc,
                        mutations: elems,
                    };
                    if let Ok(payload_bytes) = serde_json::to_vec(&sync_payload) {
                        let frame = Frame::new(MessageType::StateSync, node_id, payload_bytes);
                        if let Ok(encoded) = frame.encode() {
                            let _ = socket_gossip.send_to(&encoded, broadcast.as_str()).await;
                        }
                    }
                }
            }
        });

        // Main UDP Receive Loop
        let mut buf = vec![0u8; 65535];
        loop {
            let (len, src_addr) = socket.recv_from(&mut buf).await?;
            if let Ok(frame) = Frame::decode(&buf[..len]) {
                if frame.header.node_id == self.node_id {
                    continue;
                }
                self.handle_frame(frame, src_addr, &socket).await?;
            }
        }
    }

    async fn handle_frame(
        &self,
        frame: Frame,
        src_addr: SocketAddr,
        socket: &UdpSocket,
    ) -> Result<()> {
        match frame.header.msg_type {
            MessageType::Heartbeat => {
                if let Ok(payload) = serde_json::from_slice::<HeartbeatPayload>(&frame.payload) {
                    let mut table = self.routing_table.lock().unwrap();
                    table.insert(
                        frame.header.node_id,
                        PeerInfo {
                            node_id: frame.header.node_id,
                            addr: src_addr,
                            last_seen_secs: payload.uptime_secs,
                            uptime_secs: payload.uptime_secs,
                            cpu_load_pct: payload.cpu_load_pct,
                            mem_available_kb: payload.mem_available_kb,
                            is_degraded: false,
                        },
                    );
                    println!(
                        "❤️ [MiOS Node {}] Heartbeat from Peer {} ({})",
                        self.node_id, frame.header.node_id, src_addr
                    );
                }
            }
            MessageType::StateSync => {
                if let Ok(payload) = serde_json::from_slice::<StateSyncPayload>(&frame.payload) {
                    let applied_count = {
                        let mut store = self.state_store.lock().unwrap();
                        store.merge_remote_store(payload.vector_clock, payload.mutations)
                    };

                    println!(
                        "🔄 [MiOS Node {}] StateSync from Peer {}: Merged {} state elements",
                        self.node_id, frame.header.node_id, applied_count
                    );

                    let ack = StateAckPayload {
                        node_id: self.node_id,
                        applied_count,
                    };
                    if let Ok(ack_bytes) = serde_json::to_vec(&ack) {
                        let ack_frame = Frame::new(MessageType::StateAck, self.node_id, ack_bytes);
                        let _ = socket.send_to(&ack_frame.encode()?, src_addr).await;
                    }
                }
            }
            MessageType::StateAck => {
                if let Ok(payload) = serde_json::from_slice::<StateAckPayload>(&frame.payload) {
                    println!(
                        "👍 [MiOS Node {}] StateAck from Peer {}: {} elements synced",
                        self.node_id, payload.node_id, payload.applied_count
                    );
                }
            }
            MessageType::TaskOffload => {
                if let Ok(payload) = serde_json::from_slice::<TaskOffloadPayload>(&frame.payload) {
                    println!(
                        "⚡ [MiOS Node {}] Received Task Offload Request ID {} from Node {}",
                        self.node_id, payload.task_id, frame.header.node_id
                    );

                    let result = self.execution_engine.execute_task(&payload);
                    let result_bytes = serde_json::to_vec(&result)?;

                    let resp_frame =
                        Frame::new(MessageType::TaskResult, self.node_id, result_bytes);
                    socket.send_to(&resp_frame.encode()?, src_addr).await?;
                }
            }
            MessageType::TaskResult => {
                if let Ok(payload) = serde_json::from_slice::<TaskResultPayload>(&frame.payload) {
                    println!(
                        "✅ [MiOS Node {}] Received Task Result ID {} (Success: {})",
                        self.node_id, payload.task_id, payload.success
                    );
                    if let Ok(output_str) = String::from_utf8(payload.output_data) {
                        println!("   Result Output: {}", output_str);
                    }
                }
            }
            _ => {}
        }
        Ok(())
    }
}
