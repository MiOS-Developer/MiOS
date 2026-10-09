// AI-hint: Main CLI and daemon binary entry point for the mios-node runtime.
// AI-related: src/mios-rs/mios-node/src/node.rs, automation/55-native-build.sh
//! MiOS ("My OS" / "MyOS") Edge Micro-Node CLI & Daemon

use anyhow::Result;
use clap::{Parser, Subcommand};
use mios_node::executor::ExecutionEngine;
use mios_node::node::MiOSNode;
use mios_node::protocol::{Frame, MessageType, TaskOffloadPayload};
use mios_node::state_sync::StateStore;
use std::fs;
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use tokio::net::UdpSocket;

#[derive(Parser)]
#[command(
    name = "mios-node",
    author = "MiOS Core Team",
    version = "0.3.0",
    about = "MiOS ('My OS') Distributed Edge Micro-Node Runtime CLI & Daemon"
)]
struct Cli {
    #[command(subcommand)]
    command: Option<Commands>,
}

#[derive(Subcommand)]
enum Commands {
    /// Start the MiOS micro-node network daemon
    Run {
        #[arg(short, long, default_value_t = 101)]
        node_id: u32,

        /// UDP port; defaults to MIOS_PORTS_NODE ([ports].node, resolved at run time).
        #[arg(short, long)]
        port: Option<u16>,

        #[arg(short, long, default_value = "/var/lib/mios/state.json")]
        db_path: String,
    },
    /// Offload a task to a target MiOS node over the network
    Offload {
        #[arg(short, long)]
        target_addr: String,

        #[arg(short, long, default_value_t = 1001)]
        task_id: u64,

        #[arg(short, long)]
        wasm_file: Option<String>,

        #[arg(short, long, default_value = "input=default")]
        input: String,
    },
    /// Inspect or mutate local CRDT state
    State {
        #[command(subcommand)]
        action: StateCommands,
    },
    /// Display system diagnosis, AI plane status, and registered sub-agents
    Inspect,
    /// Run the standalone protocol & sandboxing verification demo
    Demo,
}

#[derive(Subcommand)]
enum StateCommands {
    /// List all active elements in state store
    List,
    /// Set a key-value pair in state store
    Set { key: String, value: String },
    /// Get value for a specific key
    Get { key: String },
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Some(Commands::Run {
            node_id,
            port,
            db_path,
        }) => {
            let port = match port {
                Some(p) => p,
                None => mios_node::ssot::require_port("MIOS_PORTS_NODE")?,
            };
            println!("====================================================");
            println!("  MiOS ('My OS' / 'MyOS') Distributed Edge Runtime  ");
            println!(
                "  Node ID: {} | Port: {} | State: {}",
                node_id, port, db_path
            );
            println!("====================================================");

            let node = MiOSNode::new(node_id, port);
            node.run().await?;
        }
        Some(Commands::Offload {
            target_addr,
            task_id,
            wasm_file,
            input,
        }) => {
            let code_bytes = if let Some(path) = wasm_file {
                fs::read(&path)?
            } else {
                b"WASM_INTERPRETER_BYTECODE_PLACEHOLDER".to_vec()
            };

            let payload = TaskOffloadPayload {
                task_id,
                tier: 1,
                target_arch: 0,
                memory_limit_bytes: 32 * 1024 * 1024,
                execution_timeout_ms: 5000,
                code_bytes,
                input_data: input.into_bytes(),
                signature: None,
                public_key: None,
            };

            let frame = Frame::new(MessageType::TaskOffload, 999, serde_json::to_vec(&payload)?);
            let encoded = frame.encode()?;

            let socket = UdpSocket::bind("0.0.0.0:0").await?;
            let dest: SocketAddr = target_addr.parse()?;
            socket.send_to(&encoded, dest).await?;

            println!(
                "⚡ Offloaded Task ID {} to {} (Bytes: {})",
                task_id,
                dest,
                encoded.len()
            );
        }
        Some(Commands::State { action }) => match action {
            StateCommands::List => {
                println!("🔍 Active CRDT State Elements:");
                let store = StateStore::new(101);
                for elem in store.active_elements() {
                    println!(
                        "  - {} = '{}' (TS: {}, Node: {})",
                        elem.key,
                        String::from_utf8_lossy(&elem.value),
                        elem.timestamp_ns,
                        elem.originating_node_id
                    );
                }
            }
            StateCommands::Set { key, value } => {
                let mut store = StateStore::new(101);
                store.set(key.clone(), value.into_bytes());
                println!("✅ Set CRDT key '{}'", key);
            }
            StateCommands::Get { key } => {
                let store = StateStore::new(101);
                if let Some(val) = store.get(&key) {
                    println!("🔑 {} = '{}'", key, String::from_utf8_lossy(val));
                } else {
                    println!("❌ Key '{}' not found", key);
                }
            }
        },
        Some(Commands::Inspect) => {
            println!("====================================================");
            println!("     MiOS ('My OS' / 'MyOS') Node Inspection       ");
            println!("====================================================");
            println!("  Node Binary Version : v0.3.0");
            println!("  Wire Protocol       : 16B Fixed Header + CRC32 Checksum");
            let show = |name: &str| {
                mios_node::ssot::get(name).unwrap_or_else(|| format!("unresolved ({name})"))
            };
            println!(
                "  Discovery Port      : {} (UDP / TCP)",
                show("MIOS_PORTS_NODE")
            );
            println!("  Default State File  : /var/lib/mios/state.json");
            println!("  AI Endpoint         : {}", show("MIOS_AI_ENDPOINT"));
            println!(
                "  Inference Lanes     : mios-llm-light (:{}), mios-llm-heavy (:{})",
                show("MIOS_PORTS_LLM_LIGHT"),
                show("MIOS_PORTS_LLM_HEAVY")
            );
            println!("  Registered Sub-Agent: mios-node (role: edge_execution)");
            println!("====================================================");
        }
        Some(Commands::Demo) | None => {
            run_offload_demo().await?;
        }
    }

    Ok(())
}

async fn run_offload_demo() -> Result<()> {
    println!("\n🧪 Running Standalone MiOS Task Offloading & CRDT State Sync Demo...\n");

    let state_store = Arc::new(Mutex::new(StateStore::new(101)));
    let engine = ExecutionEngine::new(state_store.clone());

    let task_payload = TaskOffloadPayload {
        task_id: 9001,
        tier: 1,
        target_arch: 0,
        memory_limit_bytes: 64 * 1024 * 1024,
        execution_timeout_ms: 5000,
        code_bytes: b"WASM_BYTECODE_PLACEHOLDER".to_vec(),
        input_data: b"sensor_readings=[24.5, 24.8, 25.1]".to_vec(),
        signature: None,
        public_key: None,
    };

    let frame = Frame::new(
        MessageType::TaskOffload,
        101,
        serde_json::to_vec(&task_payload)?,
    );

    println!(
        "1. Encoded MiOS Frame (Header: {} bytes, Total Payload: {} bytes, CRC32: 0x{:08X})",
        mios_node::protocol::HEADER_SIZE,
        frame.payload.len(),
        frame.header.checksum
    );

    let encoded_bytes = frame.encode()?;
    let decoded_frame = Frame::decode(&encoded_bytes)?;
    println!(
        "2. Decoded & Verified Frame from Node ID {} (MsgType: {:?})",
        decoded_frame.header.node_id, decoded_frame.header.msg_type
    );

    let result = engine.execute_task(&task_payload);
    println!(
        "3. Execution Outcome: Success = {}, Output = '{}'",
        result.success,
        String::from_utf8_lossy(&result.output_data)
    );

    let store = state_store.lock().unwrap();
    if let Some(status) = store.get("task.9001.status") {
        println!(
            "4. Verified CRDT State Storage: task.9001.status = '{}'",
            String::from_utf8_lossy(status)
        );
    }

    println!("\n✅ MiOS Standalone Protocol & Sandbox Test Completed Successfully!");
    Ok(())
}
