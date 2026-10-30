// AI-hint: Static build-time preparation for negotiated RDPDR logon and connection-local dynamic channel extension hooks. Validate known upstream boundaries before mutation; run before dependency compilation.
// AI-related: automation/71-bake-rdp-server.sh, src/mios-rdp-server/crates/mios-rdp-server/src/remote_storage.rs
// AI-functions: prepare, replacement
#![forbid(unsafe_code)]

use std::fs;
use std::io::Write;
use std::path::Path;

const START: &str = "    fn handle_client_capability_response(";
const END: &str = "    fn handle_device_list_announce(";
const OLD: &str = "        self.state = RdpdrServerState::Active;\n        Ok(Vec::new())";
const NEW: &str = r#"        self.state = RdpdrServerState::Active;
        // MiOS: negotiated RDPDR logon notification. A client waits for
        // this before announcing drives when both peers advertise it.
        let notify_logon = response.capabilities.iter().any(CapabilityMessage::mios_supports_user_logged_on);
        Ok(if notify_logon {
            vec![SvcMessage::from(RdpdrPdu::UserLoggedon)]
        } else {
            Vec::new()
        })"#;
const EFS_EXTENSION: &str = r#"
    /// Whether this general capability negotiates the server logon notification.
    pub fn mios_supports_user_logged_on(&self) -> bool {
        matches!(&self.capability_data, CapabilityData::General(general)
            if general.extended_pdu.contains(ExtendedPdu::RDPDR_USER_LOGGEDON_PDU))
    }
"#;

fn replacement(source: &str) -> Result<Option<String>, String> {
    let start = source
        .find(START)
        .ok_or("upstream RDPDR capability handler changed; review compatibility preparation")?;
    let end = source[start..]
        .find(END)
        .map(|end| start + end)
        .ok_or("upstream RDPDR device handler changed; review compatibility preparation")?;
    let block = &source[start..end];
    if block.contains("RdpdrPdu::UserLoggedon") {
        return Ok(None);
    }
    if block.matches(OLD).count() != 1 {
        return Err(
            "upstream RDPDR capability handler has an unknown shape; no source was changed".into(),
        );
    }
    let mut result = String::with_capacity(source.len() + NEW.len());
    result.push_str(&source[..start]);
    result.push_str(&block.replacen(OLD, NEW, 1));
    result.push_str(&source[end..]);
    Ok(Some(result))
}

// Extension hooks keep the compositor/display and all negotiated DVCs in the
// upstream session. Camera device names are discovered only after connection.
fn camera_extension(source: &str) -> Result<Option<String>, String> {
    if source.contains("// MiOS: connection-local dynamic channel extension") {
        return Ok(None);
    }
    let edits = [
        (
            "    fn attach(&self, acceptor: &mut Acceptor);",
            r#"    fn attach(&self, acceptor: &mut Acceptor);
    // MiOS: connection-local dynamic channel extension
    fn attach_dynamic(&self, _channels: &mut dvc::DrdynvcServer, _sender: mpsc::UnboundedSender<ServerEvent>) {}"#,
        ),
        (
            "    Echo(EchoServerMessage),",
            r#"    Echo(EchoServerMessage),
    /// Run a connection-local extension and write its resulting SVC messages.
    DvcAction(Box<dyn FnOnce(&mut dvc::DrdynvcServer) -> PduResult<Vec<ironrdp_svc::SvcMessage>> + Send>),"#,
        ),
        (
            "            Self::Echo(..) => f.write_str(\"Echo(..)\"),",
            "            Self::Echo(..) => f.write_str(\"Echo(..)\"),\n            Self::DvcAction(..) => f.write_str(\"DvcAction(..)\"),",
        ),
        (
            "        acceptor.attach_static_channel(dvc);",
            r#"        let mut dvc = dvc;
        for factory in &self.static_channel_factories {
            factory.attach_dynamic(&mut dvc, self.ev_sender.clone());
        }
        acceptor.attach_static_channel(dvc);"#,
        ),
        (
            "                ServerEvent::Echo(msg) => match msg {",
            r#"                ServerEvent::DvcAction(action) => {
                    let messages = {
                        let Some(drdynvc) = conn.static_channels.get_by_type_mut::<dvc::DrdynvcServer>()
                            .and_then(|svc| svc.channel_processor_downcast_mut::<dvc::DrdynvcServer>()) else { continue; };
                        match action(drdynvc) {
                            Ok(messages) => messages,
                            Err(error) => { warn!(?error, "Dynamic channel extension failed"); continue; }
                        }
                    };
                    if messages.is_empty() { continue; }
                    let channel_id = conn.get_channel_id_by_type::<dvc::DrdynvcServer>()
                        .ok_or_else(|| ServerError::channel("DRDYNVC channel not found"))?;
                    let data = server_encode_svc_messages(messages, channel_id, user_channel_id)
                        .map_err(ServerError::encode)?;
                    writer.write_all(&data).await.map_err(|e| ServerError::io("write_all", e))?;
                }
                ServerEvent::Echo(msg) => match msg {"#,
        ),
    ];
    for (old, _) in edits {
        if source.matches(old).count() != 1 {
            return Err(format!(
                "upstream dynamic-channel extension boundary changed: {old}; no source changed"
            ));
        }
    }
    let mut result = source.to_owned();
    for (old, new) in edits {
        result = result.replacen(old, new, 1);
    }
    Ok(Some(result))
}

fn prepare(root: &Path) -> Result<bool, String> {
    let root = root.canonicalize().map_err(|e| e.to_string())?;
    let path = root.join("crates/ironrdp-rdpdr/src/server.rs");
    let canonical = path.canonicalize().map_err(|e| e.to_string())?;
    if !canonical.starts_with(&root) {
        return Err("RDPDR source resolves outside the supplied upstream checkout".into());
    }
    let dynamic_path = root.join("crates/ironrdp-server/src/server.rs");
    if !dynamic_path
        .canonicalize()
        .map_err(|e| e.to_string())?
        .starts_with(&root)
    {
        return Err("dynamic channel source resolves outside upstream checkout".into());
    }
    let dynamic_source = fs::read_to_string(&dynamic_path).map_err(|e| e.to_string())?;
    let dynamic_update = camera_extension(&dynamic_source)?;
    let source = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let server_update = replacement(&source)?;
    // A future upstream implementation needs no MiOS capability accessor.
    if server_update.is_none() && !source.contains("// MiOS: negotiated RDPDR logon notification.")
    {
        if let Some(updated) = dynamic_update {
            write_atomic(&dynamic_path, &updated)?;
            return Ok(true);
        }
        return Ok(false);
    }
    let efs = root.join("crates/ironrdp-rdpdr/src/pdu/efs.rs");
    if !efs
        .canonicalize()
        .map_err(|e| e.to_string())?
        .starts_with(&root)
    {
        return Err("RDPDR capability source resolves outside upstream checkout".into());
    }
    let efs_source = fs::read_to_string(&efs).map_err(|e| e.to_string())?;
    let efs_update = if efs_source.contains("pub fn mios_supports_user_logged_on(") {
        None
    } else {
        if efs_source.matches("impl CapabilityMessage {").count() != 1 {
            return Err("upstream capability API changed; no source was changed".into());
        }
        Some(efs_source.replacen(
            "impl CapabilityMessage {",
            &format!("impl CapabilityMessage {{{EFS_EXTENSION}"),
            1,
        ))
    };
    let changed = server_update.is_some() || efs_update.is_some() || dynamic_update.is_some();
    if let Some(updated) = dynamic_update {
        write_atomic(&dynamic_path, &updated)?;
    }
    if let Some(updated) = server_update {
        write_atomic(&path, &updated)?;
    }
    if let Some(updated) = efs_update {
        write_atomic(&efs, &updated)?;
    }
    Ok(changed)
}

fn write_atomic(path: &Path, updated: &str) -> Result<(), String> {
    let temporary = path.with_file_name("server.rs.mios-prepare");
    let mut output = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|e| e.to_string())?;
    let write = (|| {
        output.write_all(updated.as_bytes())?;
        output.sync_all()?;
        fs::set_permissions(&temporary, fs::metadata(&path)?.permissions())?;
        fs::rename(&temporary, &path)
    })();
    if let Err(error) = write {
        let _ = fs::remove_file(&temporary);
        return Err(error.to_string());
    }
    Ok(())
}

fn main() {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 1 {
        eprintln!("usage: mios-rdp-source-prepare <upstream-checkout>");
        std::process::exit(2);
    }
    match prepare(Path::new(&args[0])) {
        Ok(changed) => {
            println!("{{\"fix\":\"rdpdr-logon-and-dynamic-extension\",\"changed\":{changed}}}")
        }
        Err(error) => {
            eprintln!("RDP source preparation: {error}");
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Fixture(std::path::PathBuf);
    impl Fixture {
        fn new(label: &str, body: &str) -> Self {
            let root = std::env::temp_dir()
                .join(format!("mios-rdp-source-{label}-{}", std::process::id()));
            fs::create_dir(&root).unwrap();
            fs::create_dir_all(root.join("crates/ironrdp-rdpdr/src")).unwrap();
            fs::create_dir(root.join("crates/ironrdp-rdpdr/src/pdu")).unwrap();
            fs::write(root.join("crates/ironrdp-rdpdr/src/server.rs"), body).unwrap();
            fs::write(
                root.join("crates/ironrdp-rdpdr/src/pdu/efs.rs"),
                "impl CapabilityMessage {\n}\n",
            )
            .unwrap();
            fs::create_dir_all(root.join("crates/ironrdp-server/src")).unwrap();
            fs::write(
                root.join("crates/ironrdp-server/src/server.rs"),
                include_str!("../ironrdp/crates/ironrdp-server/src/server.rs"),
            )
            .unwrap();
            Self(root)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_file(self.0.join("crates/ironrdp-rdpdr/src/server.rs"));
            let _ = fs::remove_file(self.0.join("crates/ironrdp-rdpdr/src/pdu/efs.rs"));
            let _ = fs::remove_dir(self.0.join("crates/ironrdp-rdpdr/src/pdu"));
            let _ = fs::remove_file(self.0.join("crates/ironrdp-server/src/server.rs"));
            let _ = fs::remove_dir(self.0.join("crates/ironrdp-server/src"));
            let _ = fs::remove_dir(self.0.join("crates/ironrdp-server"));
            for directory in [
                "crates/ironrdp-rdpdr/src",
                "crates/ironrdp-rdpdr",
                "crates",
                "",
            ] {
                let _ = fs::remove_dir(self.0.join(directory));
            }
        }
    }
    #[test]
    fn known_handler_is_prepared_once_and_only_inside_its_boundary() {
        let source = format!("prefix\n{START}) {{\n{OLD}\n    }}\n{END}) {{ untouched }}\nsuffix");
        let fixture = Fixture::new("positive", &source);
        assert!(prepare(&fixture.0).unwrap());
        let updated =
            fs::read_to_string(fixture.0.join("crates/ironrdp-rdpdr/src/server.rs")).unwrap();
        assert!(updated.starts_with("prefix\n"));
        assert!(updated.ends_with("suffix"));
        assert!(updated.contains(NEW));
        assert!(updated.contains("CapabilityMessage::mios_supports_user_logged_on"));
        assert!(
            fs::read_to_string(fixture.0.join("crates/ironrdp-rdpdr/src/pdu/efs.rs"))
                .unwrap()
                .contains("general.extended_pdu.contains")
        );
        assert!(!prepare(&fixture.0).unwrap());
    }
    #[test]
    fn unknown_upstream_shape_fails_without_mutation() {
        let source = format!("{START}) {{ unfamiliar(); }}\n{END}) {{}}\n");
        let fixture = Fixture::new("negative", &source);
        assert!(prepare(&fixture.0).unwrap_err().contains("unknown shape"));
        assert_eq!(
            fs::read_to_string(fixture.0.join("crates/ironrdp-rdpdr/src/server.rs")).unwrap(),
            source
        );
    }
    #[test]
    fn a_missing_checkout_fails() {
        assert!(prepare(Path::new("/mios-missing-rdp-upstream-fixture")).is_err());
    }
    #[test]
    fn dynamic_extension_is_idempotent_and_rejects_unknown_shapes() {
        let source = include_str!("../ironrdp/crates/ironrdp-server/src/server.rs");
        let prepared = camera_extension(source)
            .unwrap()
            .unwrap_or_else(|| source.into());
        assert!(prepared.contains("factory.attach_dynamic(&mut dvc, self.ev_sender.clone())"));
        assert!(prepared.contains("ServerEvent::DvcAction(action)"));
        assert!(camera_extension(&prepared).unwrap().is_none());
        assert!(
            camera_extension("unknown upstream API")
                .unwrap_err()
                .contains("boundary changed")
        );
    }
}
