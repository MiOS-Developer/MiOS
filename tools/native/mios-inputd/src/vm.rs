// AI-hint: Focus-guarded VM client escape commands; capture state remains client-owned.
// AI-related: keybindings.global.vm, mios-gen/src/global_bindings.rs
use serde::{Deserialize, Serialize};
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct VmConfig {
    pub looking_glass_classes: Vec<String>,
    pub x11_rdp_classes: Vec<String>,
    pub other_classes: Vec<String>,
    pub looking_glass_escape_key: String,
    pub x11_rdp_escape_key: String,
}
impl VmConfig {
    pub fn validate(&self) -> Result<(), String> {
        for key in [&self.looking_glass_escape_key, &self.x11_rdp_escape_key] {
            if key.is_empty() || !key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_') {
                return Err("invalid VM escape keysym".into());
            }
        }
        for class in self.looking_glass_classes.iter().chain(&self.x11_rdp_classes).chain(&self.other_classes) {
            if class.is_empty() || class.chars().any(char::is_control) {
                return Err("invalid VM class".into());
            }
        }
        Ok(())
    }
    pub fn lua(&self, action: &str, address: &str, pid: u64, class: &str) -> Result<String, String> {
        self.validate()?;
        if !address.starts_with("0x") || address.len() < 3 || !address[2..].bytes().all(|b| b.is_ascii_hexdigit()) || pid == 0 {
            return Err("invalid focused VM identity".into());
        }
        let lg = self.looking_glass_classes.iter().any(|v| v == class);
        let rdp = self.x11_rdp_classes.iter().any(|v| v == class);
        if !lg && !rdp && !self.other_classes.iter().any(|v| v == class) {
            return Err("focused window is not a configured VM client".into());
        }
        let q = |v: &str| serde_json::to_string(v).unwrap();
        let target = q(&format!("address:{address}"));
        let guard = format!("local w=hl.get_active_window(); if not w or w.address~={} or w.pid~={} or w.class~={} then error('VM focus changed; no input sent',0) end; ", q(address),pid,q(class));
        let op = match action {
            "vm-fullscreen" => format!("hl.dispatch(hl.dsp.window.fullscreen({{window={target},mode='fullscreen',action='toggle'}}))"),
            "toggle_vm_passthrough" | "vm-capture-toggle" => {
                let key = if lg { &self.looking_glass_escape_key } else if rdp { &self.x11_rdp_escape_key } else { return Err("this VM client has no verified native capture escape; use host fullscreen/escape bindings".into()); };
                format!("hl.dispatch(hl.dsp.send_shortcut({{mods='',key={},window={target}}}))",q(key))
            }
            _ => return Err("unsupported VM action".into()),
        };
        Ok(format!("{guard}{op}; error('MIOS_VM_SENT',0)"))
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn config() -> VmConfig { VmConfig { looking_glass_classes:vec!["looking-glass-client".into()],x11_rdp_classes:vec!["xfreerdp".into()],other_classes:vec!["remote-viewer".into()],looking_glass_escape_key:"Scroll_Lock".into(),x11_rdp_escape_key:"Control_R".into() } }
    #[test] fn escapes_guard_address_pid_and_class() { let s=config().lua("vm-capture-toggle","0xabc",42,"looking-glass-client").unwrap(); assert!(s.contains("w.address~=\"0xabc\"")); assert!(s.contains("w.pid~=42")); assert!(s.contains("key=\"Scroll_Lock\"")); assert!(s.contains("window=\"address:0xabc\"")); }
    #[test] fn x11_rdp_uses_its_native_escape() { assert!(config().lua("vm-capture-toggle","0xabc",42,"xfreerdp").unwrap().contains("Control_R")); }
    #[test] fn unsupported_client_and_injection_fail() { for (a,p,c) in [("0xabc",42,"Alacritty"),("0xabc",0,"xfreerdp"),("0xabc');x()",42,"xfreerdp"),("0xabc",42,"wlfreerdp"),("0xabc",42,"remote-viewer")] {assert!(config().lua("vm-capture-toggle",a,p,c).is_err());} }
    #[test] fn generic_clients_can_fullscreen_without_fake_capture_state() {assert!(config().lua("vm-fullscreen","0xabc",42,"remote-viewer").is_ok());}
}
