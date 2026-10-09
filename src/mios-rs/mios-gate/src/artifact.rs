// AI-hint: Artifact verification gate for mios-gate: validates OCI descriptor closure, non-executing SafeTensors/GGUF headers, and OpenAI SFT/DPO JSONL datasets.
// AI-related: src/mios-rs/mios-gate/src/main.rs, usr/share/doc/mios/adr/0027-dual-tier-oci-ai-artifacts.md, ROADMAP.md (task_3227)

use crate::Report;
use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};

const CHECK: &str = "artifact";

const SECRET_PATTERNS: [&str; 5] = [
    "ghp_",
    "sk-",
    "BEGIN PRIVATE KEY",
    "BEGIN OPENSSH PRIVATE KEY",
    "password =",
];

fn cannot_run(why: impl Into<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok: false,
        could_not_run: Some(why.into()),
        summary: String::new(),
        findings: Vec::new(),
    }
}

/// Checks OpenAI Chat SFT format in `var/lib/mios/training/sft.jsonl`.
fn check_sft(path: &Path, findings: &mut Vec<String>) {
    let content = match std::fs::read_to_string(path) {
        Ok(c) => c,
        Err(e) => {
            findings.push(format!(
                "{}: failed to read SFT dataset: {e}",
                path.display()
            ));
            return;
        }
    };

    let mut line_count = 0;
    for (idx, line) in content.lines().enumerate() {
        let line_no = idx + 1;
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        line_count += 1;

        // Check secret patterns
        scan_secrets(path, line_no, line, findings);

        // Parse JSON
        let val: serde_json::Value = match serde_json::from_str(trimmed) {
            Ok(v) => v,
            Err(e) => {
                findings.push(format!(
                    "{}:{}: failed to parse JSON: {e}",
                    path.display(),
                    line_no
                ));
                continue;
            }
        };

        // Validate "messages" array
        let Some(messages) = val.get("messages").and_then(|m| m.as_array()) else {
            findings.push(format!(
                "{}:{}: missing or invalid 'messages' array",
                path.display(),
                line_no
            ));
            continue;
        };

        if messages.is_empty() {
            findings.push(format!(
                "{}:{}: 'messages' array is empty",
                path.display(),
                line_no
            ));
            continue;
        }

        let mut has_assistant = false;
        for (m_idx, msg) in messages.iter().enumerate() {
            let Some(role) = msg.get("role").and_then(|r| r.as_str()) else {
                findings.push(format!(
                    "{}:{}: message[{m_idx}] missing string 'role'",
                    path.display(),
                    line_no
                ));
                continue;
            };

            if role != "system" && role != "user" && role != "assistant" {
                findings.push(format!(
                    "{}:{}: message[{m_idx}] invalid role {:?}, must be system, user, or assistant",
                    path.display(),
                    line_no,
                    role
                ));
            }

            let Some(content_str) = msg.get("content").and_then(|c| c.as_str()) else {
                findings.push(format!(
                    "{}:{}: message[{m_idx}] missing string 'content'",
                    path.display(),
                    line_no
                ));
                continue;
            };

            if content_str.trim().is_empty() {
                findings.push(format!(
                    "{}:{}: message[{m_idx}] 'content' is empty",
                    path.display(),
                    line_no
                ));
            }

            if role == "assistant" && !content_str.trim().is_empty() {
                has_assistant = true;
            }
        }

        if !has_assistant {
            findings.push(format!(
                "{}:{}: missing assistant message with non-empty content",
                path.display(),
                line_no
            ));
        }
    }

    if line_count == 0 {
        findings.push(format!("{}: SFT dataset is empty", path.display()));
    }
}

/// Checks OpenAI Preference DPO format in `var/lib/mios/training/dpo.jsonl`.
fn check_dpo(path: &Path, findings: &mut Vec<String>) {
    let content = match std::fs::read_to_string(path) {
        Ok(c) => c,
        Err(e) => {
            findings.push(format!(
                "{}: failed to read DPO dataset: {e}",
                path.display()
            ));
            return;
        }
    };

    let mut line_count = 0;
    for (idx, line) in content.lines().enumerate() {
        let line_no = idx + 1;
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        line_count += 1;

        // Check secret patterns
        scan_secrets(path, line_no, line, findings);

        // Parse JSON
        let val: serde_json::Value = match serde_json::from_str(trimmed) {
            Ok(v) => v,
            Err(e) => {
                findings.push(format!(
                    "{}:{}: failed to parse JSON: {e}",
                    path.display(),
                    line_no
                ));
                continue;
            }
        };

        // Validate "input" object with non-empty "messages" array
        let Some(input_obj) = val.get("input").and_then(|i| i.as_object()) else {
            findings.push(format!(
                "{}:{}: missing or invalid 'input' object",
                path.display(),
                line_no
            ));
            continue;
        };

        let Some(input_messages) = input_obj.get("messages").and_then(|m| m.as_array()) else {
            findings.push(format!(
                "{}:{}: missing or invalid 'messages' array in 'input'",
                path.display(),
                line_no
            ));
            continue;
        };

        if input_messages.is_empty() {
            findings.push(format!(
                "{}:{}: 'input.messages' array is empty",
                path.display(),
                line_no
            ));
        } else {
            for (m_idx, msg) in input_messages.iter().enumerate() {
                let Some(role) = msg.get("role").and_then(|r| r.as_str()) else {
                    findings.push(format!(
                        "{}:{}: input message[{m_idx}] missing string 'role'",
                        path.display(),
                        line_no
                    ));
                    continue;
                };

                if role != "system" && role != "user" && role != "assistant" {
                    findings.push(format!(
                        "{}:{}: input message[{m_idx}] invalid role {:?}",
                        path.display(),
                        line_no,
                        role
                    ));
                }

                let Some(content_str) = msg.get("content").and_then(|c| c.as_str()) else {
                    findings.push(format!(
                        "{}:{}: input message[{m_idx}] missing string 'content'",
                        path.display(),
                        line_no
                    ));
                    continue;
                };

                if content_str.trim().is_empty() {
                    findings.push(format!(
                        "{}:{}: input message[{m_idx}] 'content' is empty",
                        path.display(),
                        line_no
                    ));
                }
            }
        }

        // Validate "preferred_output"
        let pref = val.get("preferred_output");
        match pref.and_then(|p| p.as_array()) {
            Some(pref_arr) => {
                validate_assistant_messages(path, line_no, "preferred_output", pref_arr, findings);
            }
            None => {
                findings.push(format!(
                    "{}:{}: missing or invalid 'preferred_output' array",
                    path.display(),
                    line_no
                ));
            }
        }

        // Validate "non_preferred_output"
        let non_pref = val.get("non_preferred_output");
        match non_pref.and_then(|np| np.as_array()) {
            Some(non_pref_arr) => {
                validate_assistant_messages(
                    path,
                    line_no,
                    "non_preferred_output",
                    non_pref_arr,
                    findings,
                );
            }
            None => {
                findings.push(format!(
                    "{}:{}: missing or invalid 'non_preferred_output' array",
                    path.display(),
                    line_no
                ));
            }
        }

        // Validate distinct outputs
        if let (Some(p), Some(np)) = (pref, non_pref) {
            if p == np {
                findings.push(format!(
                    "{}:{}: 'preferred_output' and 'non_preferred_output' are identical",
                    path.display(),
                    line_no
                ));
            }
        }
    }

    if line_count == 0 {
        findings.push(format!("{}: DPO dataset is empty", path.display()));
    }
}

/// Validates that an array of messages contains only valid assistant messages with non-empty content.
fn validate_assistant_messages(
    path: &Path,
    line_no: usize,
    field_name: &str,
    messages: &[serde_json::Value],
    findings: &mut Vec<String>,
) {
    if messages.is_empty() {
        findings.push(format!(
            "{}:{}: '{}' array is empty",
            path.display(),
            line_no,
            field_name
        ));
        return;
    }

    for (idx, msg) in messages.iter().enumerate() {
        let Some(role) = msg.get("role").and_then(|r| r.as_str()) else {
            findings.push(format!(
                "{}:{}: {}[{idx}] missing string 'role'",
                path.display(),
                line_no,
                field_name
            ));
            continue;
        };

        if role != "assistant" {
            findings.push(format!(
                "{}:{}: {}[{idx}] invalid role {:?}, expected 'assistant'",
                path.display(),
                line_no,
                field_name,
                role
            ));
        }

        let Some(content_str) = msg.get("content").and_then(|c| c.as_str()) else {
            findings.push(format!(
                "{}:{}: {}[{idx}] missing string 'content'",
                path.display(),
                line_no,
                field_name
            ));
            continue;
        };

        if content_str.trim().is_empty() {
            findings.push(format!(
                "{}:{}: {}[{idx}] 'content' is empty",
                path.display(),
                line_no,
                field_name
            ));
        }
    }
}

/// Scans a text line for high-entropy secrets and private token patterns.
fn scan_secrets(path: &Path, line_no: usize, line: &str, findings: &mut Vec<String>) {
    for pattern in SECRET_PATTERNS {
        if line.contains(pattern) {
            findings.push(format!(
                "{}:{}: forbidden secret or private token pattern detected: {:?}",
                path.display(),
                line_no,
                pattern
            ));
        }
    }
}

/// Scans directories for safe weight deserialization.
fn scan_weights(dir: &Path, findings: &mut Vec<String>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };

    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            scan_weights(&path, findings);
        } else if path.is_file() {
            check_weight_file(&path, findings);
        }
    }
}

/// Verifies individual weight file format.
fn check_weight_file(path: &Path, findings: &mut Vec<String>) {
    let file_name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
    let label = path.display().to_string();
    if !is_weight_name(file_name) {
        return;
    }
    let len = match std::fs::metadata(path) {
        Ok(meta) => meta.len(),
        Err(e) => {
            findings.push(format!("{label}: failed to read metadata: {e}"));
            return;
        }
    };
    match File::open(path) {
        Ok(mut f) => check_weight_stream(&label, file_name, len, &mut f, findings),
        Err(e) => findings.push(format!("{label}: failed to open weight file: {e}")),
    }
}

/// Pickle-family extensions: loading them executes code.
fn is_unsafe_weight_name(file_name: &str) -> bool {
    let lower = file_name.to_ascii_lowercase();
    [".pt", ".bin", ".pickle", ".pkl"]
        .iter()
        .any(|ext| lower.ends_with(ext))
}

/// Names the weight rules apply to: pickle-family, GGUF and SafeTensors.
pub(crate) fn is_weight_name(file_name: &str) -> bool {
    let lower = file_name.to_ascii_lowercase();
    is_unsafe_weight_name(file_name) || lower.ends_with(".gguf") || lower.ends_with(".safetensors")
}

/// The weight rules over a stream positioned at the start of a `len`-byte
/// file named `file_name`, reported under `label`. Shared by files on disk
/// and entries inside OCI layer tarballs.
pub(crate) fn check_weight_stream(
    label: &str,
    file_name: &str,
    len: u64,
    r: &mut impl Read,
    findings: &mut Vec<String>,
) {
    let lower = file_name.to_ascii_lowercase();
    if is_unsafe_weight_name(file_name) {
        findings.push(format!(
            "{label}: unsafe weights deserialization format rejected (.pt, .bin, .pickle, .pkl)"
        ));
    } else if lower.ends_with(".gguf") {
        let mut magic = [0u8; 4];
        match r.read_exact(&mut magic) {
            Ok(()) if &magic != b"GGUF" => findings.push(format!(
                "{label}: invalid GGUF magic header: expected b\"GGUF\", found {magic:?}"
            )),
            Ok(()) => {}
            Err(e) => findings.push(format!("{label}: failed to read GGUF magic header: {e}")),
        }
    } else if lower.ends_with(".safetensors") {
        if len < 8 {
            findings.push(format!(
                "{label}: safetensors file size ({len} bytes) is less than 8 bytes"
            ));
            return;
        }
        let mut buf = [0u8; 8];
        if let Err(e) = r.read_exact(&mut buf) {
            findings.push(format!("{label}: failed to read safetensors header: {e}"));
            return;
        }
        let header_len = u64::from_le_bytes(buf);
        let payload = len - 8;
        if header_len == 0 {
            findings.push(format!("{label}: safetensors header length is 0"));
        } else if header_len > payload {
            findings.push(format!(
                "{label}: safetensors header length ({header_len}) exceeds payload size ({payload})"
            ));
        } else if header_len > SAFETENSORS_MAX_HEADER {
            findings.push(format!(
                "{label}: safetensors header length ({header_len}) exceeds the {SAFETENSORS_MAX_HEADER}-byte format limit"
            ));
        } else {
            check_safetensors_header(label, r, header_len, payload - header_len, findings);
        }
    }
}

/// The SafeTensors format caps the JSON header at 100 MB.
const SAFETENSORS_MAX_HEADER: u64 = 100_000_000;

/// Reads and decodes the SafeTensors JSON header: it must be an object whose
/// entries (other than `__metadata__`) each declare a string `dtype`, an
/// integer `shape` array and `data_offsets` `[begin, end]` inside the data
/// section that follows the header.
fn check_safetensors_header(
    label: &str,
    r: &mut impl Read,
    header_len: u64,
    data_len: u64,
    findings: &mut Vec<String>,
) {
    let mut header = vec![0u8; header_len as usize];
    if let Err(e) = r.read_exact(&mut header) {
        findings.push(format!(
            "{label}: failed to read safetensors JSON header: {e}"
        ));
        return;
    }
    let obj = match serde_json::from_slice::<serde_json::Value>(&header) {
        Ok(serde_json::Value::Object(o)) => o,
        Ok(_) => {
            findings.push(format!("{label}: safetensors header is not a JSON object"));
            return;
        }
        Err(e) => {
            findings.push(format!(
                "{label}: safetensors header is not valid JSON: {e}"
            ));
            return;
        }
    };
    for (name, tensor) in &obj {
        if name == "__metadata__" {
            continue;
        }
        let dtype_ok = tensor.get("dtype").and_then(|d| d.as_str()).is_some();
        let shape_ok = tensor
            .get("shape")
            .and_then(|s| s.as_array())
            .is_some_and(|a| a.iter().all(|v| v.as_u64().is_some()));
        let offsets = tensor
            .get("data_offsets")
            .and_then(|o| o.as_array())
            .filter(|a| a.len() == 2)
            .and_then(|a| Some((a[0].as_u64()?, a[1].as_u64()?)));
        if !dtype_ok || !shape_ok {
            findings.push(format!(
                "{label}: safetensors tensor {name:?} lacks a string 'dtype' or an integer 'shape'"
            ));
        }
        match offsets {
            Some((begin, end)) if begin <= end && end <= data_len => {}
            Some((begin, end)) => findings.push(format!(
                "{label}: safetensors tensor {name:?} data_offsets [{begin}, {end}] fall outside the {data_len}-byte data section"
            )),
            None => findings.push(format!(
                "{label}: safetensors tensor {name:?} lacks integer 'data_offsets' [begin, end]"
            )),
        }
    }
}

/// Recursively discovers OCI layout directories (containing `oci-layout`).
fn find_oci_layout_dirs(root: &Path) -> Vec<PathBuf> {
    let mut layouts = Vec::new();
    if root.join("oci-layout").is_file() {
        layouts.push(root.to_path_buf());
    }
    find_oci_layouts_recursive(root, 0, &mut layouts);
    layouts.sort();
    layouts.dedup();
    layouts
}

fn find_oci_layouts_recursive(dir: &Path, depth: usize, layouts: &mut Vec<PathBuf>) {
    if depth > 4 {
        return;
    }
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let Ok(file_type) = entry.file_type() else {
            continue;
        };
        if file_type.is_dir() {
            let path = entry.path();
            let name = entry.file_name();
            let name_str = name.to_string_lossy();
            if name_str.starts_with('.') || name_str == "target" || name_str == "node_modules" {
                continue;
            }
            if path.join("oci-layout").is_file() {
                layouts.push(path);
            } else {
                find_oci_layouts_recursive(&path, depth + 1, layouts);
            }
        }
    }
}

/// Deepest index -> index -> manifest chain the walker follows before reporting.
const MAX_DESCRIPTOR_DEPTH: usize = 8;

/// Parses `sha256`/`sha512` lowercase-hex digests only, so none can escape `blobs/`.
fn parse_digest(digest: &str) -> Option<(&str, &str)> {
    let (algo, hash) = digest.split_once(':')?;
    let want = match algo {
        "sha256" => 64,
        "sha512" => 128,
        _ => return None,
    };
    if hash.len() != want || !hash.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')) {
        return None;
    }
    Some((algo, hash))
}

/// Resolves an OCI digest (e.g. `sha256:<hash>`) to its location in `blobs/<algo>/<hash>`.
fn resolve_blob_path(oci_dir: &Path, digest: &str) -> Option<PathBuf> {
    let (algo, hash) = parse_digest(digest)?;
    Some(oci_dir.join("blobs").join(algo).join(hash))
}

/// Streams `path` through the digest's algorithm and returns the lowercase hex.
fn hash_file(path: &Path, algo: &str) -> std::io::Result<String> {
    use sha2::Digest;
    fn run<D: sha2::Digest>(mut f: File, mut d: D) -> std::io::Result<Vec<u8>> {
        let mut buf = vec![0u8; 1 << 16];
        loop {
            let n = f.read(&mut buf)?;
            if n == 0 {
                break;
            }
            d.update(&buf[..n]);
        }
        Ok(d.finalize().to_vec())
    }
    let f = File::open(path)?;
    let raw = match algo {
        "sha512" => run(f, sha2::Sha512::new())?,
        _ => run(f, sha2::Sha256::new())?,
    };
    Ok(raw.iter().map(|b| format!("{b:02x}")).collect())
}

/// Verifies one descriptor: the blob exists, its size and its digest match.
/// When `expand` is set the blob is a manifest or an index, so its config,
/// layers and child manifests are verified too, recursively. Returns the
/// descriptor's digest when the blob itself verified.
fn verify_descriptor(
    oci_dir: &Path,
    desc: &serde_json::Value,
    context: &str,
    expand: bool,
    depth: usize,
    visited: &mut std::collections::BTreeSet<String>,
    findings: &mut Vec<String>,
) {
    let Some(digest) = desc.get("digest").and_then(|d| d.as_str()) else {
        findings.push(format!("{context}: missing string 'digest'"));
        return;
    };
    let Some(expected_size) = desc.get("size").and_then(|s| s.as_u64()) else {
        findings.push(format!("{context}: missing integer 'size'"));
        return;
    };
    let (Some((algo, hash)), Some(blob_path)) =
        (parse_digest(digest), resolve_blob_path(oci_dir, digest))
    else {
        findings.push(format!("{context}: invalid digest format {:?}", digest));
        return;
    };
    if !blob_path.is_file() {
        findings.push(format!(
            "{context}: blob {digest} not found at {}",
            blob_path.display()
        ));
        return;
    }
    match std::fs::metadata(&blob_path) {
        Ok(meta) if meta.len() != expected_size => {
            findings.push(format!(
                "{context}: blob {digest} size mismatch: expected {expected_size} bytes, found {} bytes",
                meta.len()
            ));
            return;
        }
        Ok(_) => {}
        Err(e) => {
            findings.push(format!(
                "{context}: failed to read metadata for blob {}: {e}",
                blob_path.display()
            ));
            return;
        }
    }
    match hash_file(&blob_path, algo) {
        Ok(actual) if actual != hash => {
            findings.push(format!(
                "{context}: blob {digest} digest mismatch: content hashes to {algo}:{actual}"
            ));
            return;
        }
        Ok(_) => {}
        Err(e) => {
            findings.push(format!(
                "{context}: failed to hash blob {}: {e}",
                blob_path.display()
            ));
            return;
        }
    }
    if !expand {
        if let Some(media_type) = desc.get("mediaType").and_then(|m| m.as_str()) {
            crate::artifact_layers::scan_layer(&blob_path, media_type, context, findings);
        }
        return;
    }
    if !visited.insert(digest.to_string()) {
        return;
    }
    if depth >= MAX_DESCRIPTOR_DEPTH {
        findings.push(format!(
            "{context}: descriptor nesting deeper than {MAX_DESCRIPTOR_DEPTH} levels"
        ));
        return;
    }

    let child = match std::fs::read_to_string(&blob_path)
        .map_err(|e| e.to_string())
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).map_err(|e| e.to_string()))
    {
        Ok(v) => v,
        Err(e) => {
            findings.push(format!(
                "{context}: failed to parse manifest JSON at {}: {e}",
                blob_path.display()
            ));
            return;
        }
    };

    if let Some(config_desc) = child.get("config") {
        verify_descriptor(
            oci_dir,
            config_desc,
            &format!("manifest {digest} config"),
            false,
            depth + 1,
            visited,
            findings,
        );
    }
    if let Some(layers) = child.get("layers").and_then(|l| l.as_array()) {
        for (l_idx, layer_desc) in layers.iter().enumerate() {
            verify_descriptor(
                oci_dir,
                layer_desc,
                &format!("manifest {digest} layer[{l_idx}]"),
                false,
                depth + 1,
                visited,
                findings,
            );
        }
    }
    // A nested index: every child manifest is walked the same way.
    if let Some(nested) = child.get("manifests").and_then(|m| m.as_array()) {
        for (n_idx, n_desc) in nested.iter().enumerate() {
            verify_descriptor(
                oci_dir,
                n_desc,
                &format!("index {digest} manifests[{n_idx}]"),
                true,
                depth + 1,
                visited,
                findings,
            );
        }
    }
}

/// Validates an OCI image layout directory for Merkle descriptor closure.
fn check_oci_layout(oci_dir: &Path, findings: &mut Vec<String>) {
    let layout_path = oci_dir.join("oci-layout");
    match std::fs::read_to_string(&layout_path) {
        Ok(text) => match serde_json::from_str::<serde_json::Value>(&text) {
            Ok(val) => {
                let version = val.get("imageLayoutVersion").and_then(|v| v.as_str());
                match version {
                    Some(v) if !v.trim().is_empty() => {}
                    Some(_) => findings.push(format!(
                        "{}: 'imageLayoutVersion' is empty",
                        layout_path.display()
                    )),
                    None => findings.push(format!(
                        "{}: missing 'imageLayoutVersion' in oci-layout",
                        layout_path.display()
                    )),
                }
            }
            Err(e) => findings.push(format!(
                "{}: failed to parse oci-layout as JSON: {e}",
                layout_path.display()
            )),
        },
        Err(e) => findings.push(format!(
            "{}: failed to read oci-layout: {e}",
            layout_path.display()
        )),
    }

    let index_path = oci_dir.join("index.json");
    if !index_path.is_file() {
        findings.push(format!(
            "{}: missing index.json in OCI layout",
            oci_dir.display()
        ));
        return;
    }

    let index_val = match std::fs::read_to_string(&index_path) {
        Ok(text) => match serde_json::from_str::<serde_json::Value>(&text) {
            Ok(v) => v,
            Err(e) => {
                findings.push(format!(
                    "{}: failed to parse index.json as JSON: {e}",
                    index_path.display()
                ));
                return;
            }
        },
        Err(e) => {
            findings.push(format!(
                "{}: failed to read index.json: {e}",
                index_path.display()
            ));
            return;
        }
    };

    let manifests = match index_val.get("manifests").and_then(|m| m.as_array()) {
        Some(m) if !m.is_empty() => m,
        Some(_) => {
            findings.push(format!(
                "{}: 'manifests' array is empty in index.json",
                index_path.display()
            ));
            return;
        }
        None => {
            findings.push(format!(
                "{}: missing 'manifests' array in index.json",
                index_path.display()
            ));
            return;
        }
    };

    let mut visited = std::collections::BTreeSet::new();
    for (m_idx, manifest_desc) in manifests.iter().enumerate() {
        verify_descriptor(
            oci_dir,
            manifest_desc,
            &format!("{}: manifests[{m_idx}]", index_path.display()),
            true,
            0,
            &mut visited,
            findings,
        );
    }
}

pub fn check(root: &Path) -> Report {
    if !root.is_dir() {
        return cannot_run(format!(
            "root directory {} is not a directory",
            root.display()
        ));
    }

    let mut findings = Vec::new();

    // a) OpenAI Chat SFT format validation
    let sft_path = root.join("var/lib/mios/training/sft.jsonl");
    if sft_path.is_file() {
        check_sft(&sft_path, &mut findings);
    } else {
        findings.push(format!(
            "{}: SFT training dataset file is missing",
            sft_path.display()
        ));
    }

    // b) OpenAI Preference DPO format validation
    let dpo_path = root.join("var/lib/mios/training/dpo.jsonl");
    if dpo_path.is_file() {
        check_dpo(&dpo_path, &mut findings);
    } else {
        findings.push(format!(
            "{}: DPO training dataset file is missing",
            dpo_path.display()
        ));
    }

    // c) Non-executing safe weights deserialization
    let training_dir = root.join("var/lib/mios/training");
    if training_dir.is_dir() {
        scan_weights(&training_dir, &mut findings);
    }
    let models_dir = root.join("models");
    if models_dir.is_dir() {
        scan_weights(&models_dir, &mut findings);
    }

    // e) OCI Merkle Descriptor Closure
    let oci_layouts = find_oci_layout_dirs(root);
    for oci_dir in oci_layouts {
        check_oci_layout(&oci_dir, &mut findings);
    }

    let ok = findings.is_empty();
    let summary = if ok {
        "all AI artifacts conform to schema, safe deserialization, and OCI descriptor closure"
            .to_string()
    } else {
        format!("{} artifact violation(s) found", findings.len())
    };

    Report {
        check: CHECK.to_string(),
        ok,
        could_not_run: None,
        summary,
        findings,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn setup_valid_workspace(root: &Path) {
        let training = root.join("var/lib/mios/training");
        std::fs::create_dir_all(&training).unwrap();
        std::fs::write(
            training.join("sft.jsonl"),
            r#"{"messages":[{"role":"system","content":"sys prompt"},{"role":"user","content":"user question"},{"role":"assistant","content":"helpful response"}]}"#,
        )
        .unwrap();
        std::fs::write(
            training.join("dpo.jsonl"),
            r#"{"input":{"messages":[{"role":"system","content":"sys prompt"},{"role":"user","content":"user question"}]},"preferred_output":[{"role":"assistant","content":"preferred response"}],"non_preferred_output":[{"role":"assistant","content":"inferior response"}]}"#,
        )
        .unwrap();
    }

    #[test]
    fn test_clean_training_datasets() {
        let repo_root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let report = check(&repo_root);
        assert!(
            report.ok,
            "real repository should pass artifact verification, but findings were:\n{:#?}",
            report.findings
        );
        assert!(report.findings.is_empty());
        assert_eq!(
            report.summary,
            "all AI artifacts conform to schema, safe deserialization, and OCI descriptor closure"
        );
    }

    #[test]
    fn test_sft_missing_messages_fails() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let sft_path = tmp.path().join("var/lib/mios/training/sft.jsonl");
        std::fs::write(
            &sft_path,
            r#"{"role":"user","content":"no messages array"}"#,
        )
        .unwrap();
        let report = check(tmp.path());
        assert!(!report.ok);
        assert!(report
            .findings
            .iter()
            .any(|f| f.contains("missing or invalid 'messages' array")));
    }

    #[test]
    fn test_sft_invalid_role_fails() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let sft_path = tmp.path().join("var/lib/mios/training/sft.jsonl");
        std::fs::write(
            &sft_path,
            r#"{"messages":[{"role":"operator","content":"hello"}]}"#,
        )
        .unwrap();
        let report = check(tmp.path());
        assert!(!report.ok);
        assert!(report.findings.iter().any(|f| f.contains("invalid role")));
    }

    #[test]
    fn test_dpo_identical_outputs_fails() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let dpo_path = tmp.path().join("var/lib/mios/training/dpo.jsonl");
        std::fs::write(
            &dpo_path,
            r#"{"input":{"messages":[{"role":"user","content":"q"}]},"preferred_output":[{"role":"assistant","content":"same answer"}],"non_preferred_output":[{"role":"assistant","content":"same answer"}]}"#,
        )
        .unwrap();
        let report = check(tmp.path());
        assert!(!report.ok);
        assert!(report.findings.iter().any(|f| f.contains("identical")));
    }

    #[test]
    fn test_pickle_file_rejected() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let models_dir = tmp.path().join("models");
        std::fs::create_dir_all(&models_dir).unwrap();
        std::fs::write(models_dir.join("weights.pickle"), b"pickle bytecode").unwrap();
        let report = check(tmp.path());
        assert!(!report.ok);
        assert!(report.findings.iter().any(|f| f.contains(".pickle")));
    }

    #[test]
    fn test_secret_token_rejected() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let sft_path = tmp.path().join("var/lib/mios/training/sft.jsonl");
        std::fs::write(
            &sft_path,
            r#"{"messages":[{"role":"user","content":"my token is ghp_1234567890abcdef"},{"role":"assistant","content":"redacted"}]}"#,
        )
        .unwrap();
        let report = check(tmp.path());
        assert!(!report.ok);
        assert!(report
            .findings
            .iter()
            .any(|f| f.contains("forbidden secret or private token")));
    }

    #[test]
    fn test_oci_missing_blob_fails() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let oci_dir = tmp.path().join("artifacts/modelkit");
        std::fs::create_dir_all(&oci_dir).unwrap();
        std::fs::write(
            oci_dir.join("oci-layout"),
            r#"{"imageLayoutVersion": "1.0.0"}"#,
        )
        .unwrap();
        std::fs::write(
            oci_dir.join("index.json"),
            r#"{
                "schemaVersion": 2,
                "manifests": [
                    {
                        "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "digest": "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
                        "size": 123
                    }
                ]
            }"#,
        )
        .unwrap();
        // The blob sha256:ba781... does not exist under oci_dir/blobs/sha256/
        let report = check(tmp.path());
        assert!(!report.ok);
        assert!(report
            .findings
            .iter()
            .any(|f| f.contains("manifests[0]") && f.contains("not found")));
    }

    #[test]
    fn test_gguf_validation() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let models_dir = tmp.path().join("models");
        std::fs::create_dir_all(&models_dir).unwrap();

        // Invalid GGUF header
        let bad_gguf = models_dir.join("bad.gguf");
        std::fs::write(&bad_gguf, b"NOPE_NOT_GGUF").unwrap();
        let report = check(tmp.path());
        assert!(!report.ok);
        assert!(report
            .findings
            .iter()
            .any(|f| f.contains("invalid GGUF magic header")));

        // Valid GGUF header
        std::fs::write(&bad_gguf, b"GGUF\x03\x00\x00\x00").unwrap();
        let report2 = check(tmp.path());
        assert!(report2.ok);
    }

    #[test]
    fn test_safetensors_validation() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let models_dir = tmp.path().join("models");
        std::fs::create_dir_all(&models_dir).unwrap();

        // Safetensors too short (< 8 bytes)
        let st = models_dir.join("model.safetensors");
        std::fs::write(&st, b"short").unwrap();
        let report = check(tmp.path());
        assert!(!report.ok);
        assert!(report
            .findings
            .iter()
            .any(|f| f.contains("less than 8 bytes")));

        // Valid safetensors: 8 byte header length + JSON header
        let header = b"{}";
        let header_len = (header.len() as u64).to_le_bytes();
        let mut valid_bytes = Vec::new();
        valid_bytes.extend_from_slice(&header_len);
        valid_bytes.extend_from_slice(header);
        std::fs::write(&st, &valid_bytes).unwrap();
        let report2 = check(tmp.path());
        assert!(report2.ok);
    }

    fn sha256_hex(bytes: &[u8]) -> String {
        use sha2::Digest;
        sha2::Sha256::digest(bytes)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect()
    }

    /// Writes `bytes` as a blob and returns its descriptor JSON.
    fn put_blob(blobs: &Path, media: &str, bytes: &[u8]) -> String {
        let hash = sha256_hex(bytes);
        std::fs::write(blobs.join(&hash), bytes).unwrap();
        format!(
            r#"{{"mediaType":"{media}","digest":"sha256:{hash}","size":{}}}"#,
            bytes.len()
        )
    }

    /// A layout with one image manifest (config + one layer), reached from
    /// index.json through `nest` intermediate nested indexes. Returns the
    /// layout dir and the layer's blob path.
    fn build_layout(root: &Path, nest: usize) -> (PathBuf, PathBuf) {
        build_layout_with(
            root,
            nest,
            "application/vnd.oci.image.layer.v1.tar",
            &tar_of(&[("docs/README", b"dummy layer")]),
        )
    }

    fn build_layout_with(
        root: &Path,
        nest: usize,
        media: &str,
        layer: &[u8],
    ) -> (PathBuf, PathBuf) {
        let oci_dir = root.join("artifacts/modelkit");
        let blobs = oci_dir.join("blobs/sha256");
        std::fs::create_dir_all(&blobs).unwrap();
        std::fs::write(
            oci_dir.join("oci-layout"),
            r#"{"imageLayoutVersion": "1.0.0"}"#,
        )
        .unwrap();
        let layer_desc = put_blob(&blobs, media, layer);
        let cfg_desc = put_blob(&blobs, "application/vnd.oci.image.config.v1+json", b"{}");
        let manifest = format!(
            r#"{{"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json","config":{cfg_desc},"layers":[{layer_desc}]}}"#
        );
        let mut desc = put_blob(
            &blobs,
            "application/vnd.oci.image.manifest.v1+json",
            manifest.as_bytes(),
        );
        for _ in 0..nest {
            let index = format!(
                r#"{{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json","manifests":[{desc}]}}"#
            );
            desc = put_blob(
                &blobs,
                "application/vnd.oci.image.index.v1+json",
                index.as_bytes(),
            );
        }
        std::fs::write(
            oci_dir.join("index.json"),
            format!(r#"{{"schemaVersion":2,"manifests":[{desc}]}}"#),
        )
        .unwrap();
        (oci_dir, blobs.join(sha256_hex(layer)))
    }

    #[test]
    fn test_oci_valid_closure() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        build_layout(tmp.path(), 0);
        let report = check(tmp.path());
        assert!(report.ok, "findings: {:#?}", report.findings);
    }

    #[test]
    fn test_oci_same_size_tampered_blob_fails() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let (_, layer) = build_layout(tmp.path(), 0);
        let mut bytes = std::fs::read(&layer).unwrap();
        let at = bytes.windows(5).position(|w| w == b"dummy").unwrap();
        bytes[at] = b'D'; // same size, different content
        std::fs::write(&layer, bytes).unwrap();
        let report = check(tmp.path());
        assert!(!report.ok);
        assert!(
            report
                .findings
                .iter()
                .any(|f| f.contains("layer[0]") && f.contains("digest mismatch")),
            "findings: {:#?}",
            report.findings
        );
    }

    #[test]
    fn test_oci_digest_path_traversal_rejected() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let (oci_dir, _) = build_layout(tmp.path(), 0);
        // A file outside blobs/ that a traversing digest would otherwise reach.
        std::fs::write(tmp.path().join("artifacts/outside"), b"xx").unwrap();
        std::fs::write(
            oci_dir.join("index.json"),
            r#"{"schemaVersion":2,"manifests":[{"mediaType":"application/vnd.oci.image.manifest.v1+json","digest":"sha256:../../../outside","size":2}]}"#,
        )
        .unwrap();
        let report = check(tmp.path());
        assert!(!report.ok);
        assert!(
            report
                .findings
                .iter()
                .any(|f| f.contains("invalid digest format")),
            "findings: {:#?}",
            report.findings
        );
        assert!(!report
            .findings
            .iter()
            .any(|f| f.contains("failed to parse manifest JSON")));
    }

    #[test]
    fn test_oci_nested_index_is_walked() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let (_, layer) = build_layout(tmp.path(), 2);
        let report = check(tmp.path());
        assert!(report.ok, "findings: {:#?}", report.findings);
        std::fs::remove_file(&layer).unwrap();
        let report = check(tmp.path());
        assert!(!report.ok);
        assert!(
            report
                .findings
                .iter()
                .any(|f| f.contains("layer[0]") && f.contains("not found")),
            "a dangling layer two nested indexes down must be found: {:#?}",
            report.findings
        );
    }

    #[test]
    fn test_safetensors_header_is_parsed() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let models_dir = tmp.path().join("models");
        std::fs::create_dir_all(&models_dir).unwrap();
        let st = models_dir.join("model.safetensors");
        let write = |header: &[u8], data: usize| {
            let mut b = (header.len() as u64).to_le_bytes().to_vec();
            b.extend_from_slice(header);
            b.extend(std::iter::repeat_n(0u8, data));
            std::fs::write(&st, b).unwrap();
        };

        write(
            br#"{"w":{"dtype":"F32","shape":[2],"data_offsets":[0,8]}}"#,
            8,
        );
        let report = check(tmp.path());
        assert!(report.ok, "findings: {:#?}", report.findings);

        write(b"not json at all!", 0);
        let report = check(tmp.path());
        assert!(report
            .findings
            .iter()
            .any(|f| f.contains("header is not valid JSON")));

        write(
            br#"{"w":{"dtype":"F32","shape":[2],"data_offsets":[0,64]}}"#,
            8,
        );
        let report = check(tmp.path());
        assert!(
            report
                .findings
                .iter()
                .any(|f| f.contains("fall outside the 8-byte data section")),
            "findings: {:#?}",
            report.findings
        );
    }

    /// A tar holding `files` as (path, bytes).
    fn tar_of(files: &[(&str, &[u8])]) -> Vec<u8> {
        let mut b = tar::Builder::new(Vec::new());
        for (path, bytes) in files {
            let mut h = tar::Header::new_gnu();
            h.set_size(bytes.len() as u64);
            h.set_mode(0o644);
            h.set_cksum();
            b.append_data(&mut h, path, *bytes).unwrap();
        }
        b.into_inner().unwrap()
    }

    fn gzip(bytes: &[u8]) -> Vec<u8> {
        use std::io::Write;
        let mut e = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast());
        e.write_all(bytes).unwrap();
        e.finish().unwrap()
    }

    fn safetensors_bytes() -> Vec<u8> {
        let header = br#"{"w":{"dtype":"F32","shape":[2],"data_offsets":[0,8]}}"#;
        let mut b = (header.len() as u64).to_le_bytes().to_vec();
        b.extend_from_slice(header);
        b.extend_from_slice(&[0u8; 8]);
        b
    }

    #[test]
    fn test_layer_with_safe_weights_passes() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let st = safetensors_bytes();
        let layer = tar_of(&[
            ("models/m.safetensors", &st),
            ("models/m.gguf", b"GGUF\x03\x00\x00\x00"),
        ]);
        build_layout_with(
            tmp.path(),
            0,
            "application/vnd.oci.image.layer.v1.tar",
            &layer,
        );
        let report = check(tmp.path());
        assert!(report.ok, "findings: {:#?}", report.findings);
    }

    #[test]
    fn test_pickle_inside_gzip_layer_rejected() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let layer = gzip(&tar_of(&[("models/model.pkl", b"\x80\x04pickle")]));
        build_layout_with(
            tmp.path(),
            0,
            "application/vnd.oci.image.layer.v1.tar+gzip",
            &layer,
        );
        let report = check(tmp.path());
        assert!(
            report
                .findings
                .iter()
                .any(|f| f.contains("layer[0] models/model.pkl") && f.contains("unsafe weights")),
            "findings: {:#?}",
            report.findings
        );
    }

    #[test]
    fn test_bad_gguf_inside_zstd_layer_rejected() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let tar = tar_of(&[("models/m.gguf", b"NOPE\x03\x00\x00\x00")]);
        let layer = ruzstd::encoding::compress_to_vec(
            &tar[..],
            ruzstd::encoding::CompressionLevel::Fastest,
        );
        build_layout_with(
            tmp.path(),
            0,
            "application/vnd.kitops.modelkit.model.v1.tar+zstd",
            &layer,
        );
        let report = check(tmp.path());
        assert!(
            report
                .findings
                .iter()
                .any(|f| f.contains("models/m.gguf") && f.contains("invalid GGUF magic")),
            "findings: {:#?}",
            report.findings
        );
    }

    #[test]
    fn test_unscannable_layer_is_a_finding() {
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        let layer = tar_of(&[("models/m.gguf", b"GGUF\x03\x00\x00\x00")]);
        build_layout_with(
            tmp.path(),
            0,
            "application/vnd.oci.image.layer.v1.tar+lz4",
            &layer,
        );
        let report = check(tmp.path());
        assert!(
            report
                .findings
                .iter()
                .any(|f| f.contains("unsupported compression")),
            "findings: {:#?}",
            report.findings
        );

        // A gzip media type over bytes that are not gzip: never a silent pass.
        let tmp = tempfile::tempdir().unwrap();
        setup_valid_workspace(tmp.path());
        build_layout_with(
            tmp.path(),
            0,
            "application/vnd.oci.image.layer.v1.tar+gzip",
            b"not gzip at all",
        );
        let report = check(tmp.path());
        assert!(!report.ok, "a corrupt gzip layer passed");
    }
}
