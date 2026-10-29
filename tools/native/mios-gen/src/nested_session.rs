// AI-hint: Isolated native MiOS development desktop, embedded in a guarded parent PiP window.
// AI-related: desktop.nested, Quickshell Nested.qml, mios-pip-gravity, global_bindings
use std::{env,fs,io::{Read,Write},os::{unix::{fs::{PermissionsExt,MetadataExt,symlink},net::UnixStream}},path::{Path,PathBuf},process::{Command,Stdio},thread,time::Duration};
use serde_json::Value as Json;
use toml::Value;

fn shell_arg(s:&str)->String { format!("'{}'",s.replace('\'',"'\\''")) }
fn command(v:&Value,key:&str)->Result<String,String> {
 let a=v.get(key).and_then(Value::as_array).filter(|v|!v.is_empty()).ok_or(format!("desktop.nested.{key} must be nonempty argv"))?;
 a.iter().map(|s|s.as_str().filter(|s|!s.chars().any(char::is_control)).map(shell_arg).ok_or(format!("invalid desktop.nested.{key} argument"))).collect::<Result<Vec<_>,_>>().map(|v|v.join(" "))
}
fn number(v:&Value,key:&str)->Result<i64,String>{v.get(key).and_then(Value::as_integer).filter(|n|(320..=8192).contains(n)).ok_or(format!("desktop.nested.{key} must be 320..8192"))}
pub fn project(data:&Value)->Result<String,String>{
 let n=&data["desktop"]["nested"];let (width,height)=(number(n,"width")?,number(n,"height")?);
 let mut d=data.clone();d["desktop"]["hyprland"]["placement"]["enabled"]=Value::Boolean(false);let h=d.get_mut("desktop").and_then(|v|v.get_mut("hyprland")).and_then(Value::as_table_mut).ok_or("desktop.hyprland missing")?;
 h.insert("startup".into(),Value::Array(vec![]));h.insert("shutdown".into(),Value::Array(vec![]));h.insert("monitors".into(),Value::Table(Default::default()));
 h.insert("monitor".into(),toml::toml!{output="" mode="preferred" position="auto" scale=1}.into());
 h["config"]["cursor"]["no_hardware_cursors"]=Value::Integer(1);
 if let Some(c)=h["config"].get_mut("cursor").and_then(Value::as_table_mut){c.remove("default_monitor");}
 let mut out=crate::render::render_hyprland::project(&d)?;
 // An independent QML entry point prevents --any-display from addressing the operator's shell.
 let shell=n.get("shell_entry").and_then(Value::as_str).filter(|s|Path::new(s).is_absolute()&&!s.chars().any(char::is_control)).ok_or("desktop.nested.shell_entry must be an absolute path")?;
 out=out.replace("/usr/share/mios/quickshell/Config.qml",shell);
 // A Wayland backend configure changes the output size without rebuilding its
 // workspace workarea. Keep the native monitor rule on the actual host viewport;
 // its prop refresh then recalculates tiled clients, without an initial-mode reset.
 out+=r#"
local nested_modes = {}
local function nested_reflow()
 for _,m in ipairs(hl.get_monitors()) do
  local mode = string.format('%dx%d',m.width,m.height)
  if m.width > 0 and m.height > 0 and nested_modes[m.name] ~= mode then
   nested_modes[m.name] = mode
   hl.monitor({output=m.name,mode=mode,position=m.x..'x'..m.y,scale=m.scale,transform=m.transform})
  end
 end
end
hl.on('monitor.layout_changed', nested_reflow)
"#;
 let resize_poll=n.get("resize_poll_ms").and_then(Value::as_integer).filter(|v|(20..=5000).contains(v)).ok_or("desktop.nested.resize_poll_ms must be 20..5000")?;
 out+=&format!("hl.timer(nested_reflow,{{timeout={resize_poll},type='repeat'}})\n");
 let terminal=command(n,"terminal_argv")?;
 // The parent's placement service must never execute inside this compositor.
 out=out.replace("/usr/bin/miosd placement toggle","/usr/bin/true");

 out+=&format!("\n-- Native nested output and child processes; no user-manager imports or unit starts.\nhl.on('hyprland.start',function()\n for _,m in ipairs(hl.get_monitors()) do hl.monitor({{output=m.name,mode='{width}x{height}@60',position='0x0',scale=1}}) end\n hl.exec_cmd({})\nend)\n",serde_json::to_string(&terminal).unwrap());
 Ok(out)
}
fn ipc(rt:&Path,sig:&str,cmd:&str)->Result<String,String>{
 let mut s=UnixStream::connect(rt.join("hypr").join(sig).join(".socket.sock")).map_err(|e|e.to_string())?;
 s.set_read_timeout(Some(Duration::from_secs(2))).map_err(|e|e.to_string())?;s.write_all(cmd.as_bytes()).map_err(|e|e.to_string())?;
 let mut out=String::new();s.read_to_string(&mut out).map_err(|e|e.to_string())?;Ok(out)
}
// A window-list snapshot is not a close request: temporary IPC failures,
// workspace changes or compositor reconfiguration must not kill a desktop.
// Subscribe after proving the new parent's identity, so old events from a
// recycled address cannot become a close request for this launch.
#[derive(Default)]
struct ParentEvents { stream:Option<UnixStream>, pending:Vec<u8> }
impl ParentEvents {
 fn connect(rt:&Path,sig:&str)->Self {
  let stream=UnixStream::connect(rt.join("hypr").join(sig).join(".socket2.sock")).ok().and_then(|s|s.set_nonblocking(true).ok().map(|_|s));
  Self{stream,pending:Vec::new()}
 }
 fn feed(&mut self,bytes:&[u8],address:&str)->bool {
  self.pending.extend_from_slice(bytes);let expected=address.strip_prefix("0x").unwrap_or(address);let mut closed=false;
  while let Some(end)=self.pending.iter().position(|b|*b==b'\n') {
   closed|=std::str::from_utf8(&self.pending[..end]).ok().and_then(|line|line.strip_prefix("closewindow>>"))
    .is_some_and(|value|value.strip_prefix("0x").unwrap_or(value)==expected);
   self.pending.drain(..=end);
  }
  if self.pending.len()>65536 {self.pending.clear();} // bounded malformed event, never evidence of a close
  closed
 }
 fn closed(&mut self,address:&str)->bool {
  let mut buf=[0u8;4096];
  loop {
   let Some(stream)=self.stream.as_mut() else{return false;};
   match stream.read(&mut buf) {
    Ok(0)=>{self.stream=None;return false;},
    Ok(n)=>{if self.feed(&buf[..n],address){return true;}},
    Err(e) if e.kind()==std::io::ErrorKind::WouldBlock=>return false,
    Err(e) if e.kind()==std::io::ErrorKind::Interrupted=>continue,
    Err(_)=>{self.stream=None;return false;}
   }
  }
 }
}
struct OwnedDir(PathBuf);
impl Drop for OwnedDir {fn drop(&mut self){let _=fs::remove_dir_all(&self.0);}}
pub fn run(root:&Path,dry_run:bool,layout:Option<&str>)->Result<(),String>{
 let data=mios_resolver::resolve_merged(Some(root),false).map_err(|e|e.to_string())?;let config=project(&data)?;
 if dry_run {print!("{config}");return Ok(());}
 let parent=PathBuf::from(env::var_os("XDG_RUNTIME_DIR").ok_or("XDG_RUNTIME_DIR missing")?);
 let signature=env::var("HYPRLAND_INSTANCE_SIGNATURE").map_err(|_|"parent compositor signature missing")?;
 let display=env::var("WAYLAND_DISPLAY").map_err(|_|"parent Wayland display missing")?;
 let display=if Path::new(&display).is_absolute(){PathBuf::from(display)}else{parent.join(display)};
 let owned=OwnedDir(parent.join(format!("mios-nested-desktop-{}",std::process::id())));
 fs::create_dir(&owned.0).map_err(|e|e.to_string())?;fs::set_permissions(&owned.0,fs::Permissions::from_mode(0o700)).map_err(|e|e.to_string())?;
 let user=mios_resolver::user_toml_path(Some(root));
 let old_fragments=env::var_os("MIOS_USER_TOML_D").map(PathBuf::from).unwrap_or_else(||user.parent().unwrap_or(Path::new(".")).join("mios.d"));
 let fragments=owned.0.join("mios.d");fs::create_dir(&fragments).map_err(|e|e.to_string())?;
 let mut names=Vec::new();
 if old_fragments.is_dir(){for entry in fs::read_dir(&old_fragments).map_err(|e|e.to_string())? {
  let entry=entry.map_err(|e|e.to_string())?;if entry.path().is_file()&&entry.path().extension().is_some_and(|v|v=="toml"){let name=entry.file_name();symlink(fs::canonicalize(entry.path()).map_err(|e|e.to_string())?,fragments.join(&name)).map_err(|e|e.to_string())?;names.push(name);}
 }}
 names.sort();let last=names.last().map(|n|n.to_string_lossy().into_owned()).unwrap_or_else(||"mios".into());
 let socket_name=data["desktop"]["nested"]["tmux_socket_name"].as_str().ok_or("nested tmux_socket_name missing")?;
 let terminal_session=data["desktop"]["nested"]["terminal_session"].as_str().ok_or("nested terminal_session missing")?;
 if [socket_name,terminal_session].iter().any(|s|s.is_empty()||!s.bytes().all(|b|b.is_ascii_alphanumeric()||b==b'-')){return Err("nested terminal identifiers must be sanitized".into());}
 let tmux_root=owned.0.join("tmux");fs::create_dir(&tmux_root).map_err(|e|e.to_string())?;
 let socket_root_text=tmux_root.to_string_lossy();
 let patch=toml::toml!{ [keybindings] socket_name=socket_name terminal_session=terminal_session [terminal] socket_root=(socket_root_text.as_ref()) default_action="terminal" [terminal.sessions] selector=false new_session_name=terminal_session };
 fs::write(fragments.join(format!("{last}-mios-nested.toml")),toml::to_string(&patch).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
 let focused:Json=serde_json::from_str(&ipc(&parent,&signature,"j/activewindow")?).map_err(|e|e.to_string())?;
 let input=owned.0.join("input");fs::create_dir(&input).map_err(|e|e.to_string())?;
 let path=owned.0.join("hyprland.lua");fs::write(&path,config).map_err(|e|e.to_string())?;
 let log=fs::File::create(owned.0.join("desktop.log")).map_err(|e|e.to_string())?;
 // Mask real evdev nodes and the legacy room packet in this process tree only.
 let mut child=Command::new("bwrap").args(["--die-with-parent","--bind","/","/","--dev-bind","/dev","/dev","--ro-bind","/dev/null","/tmp/mios-hypr3d-pad.bin","--ro-bind"]).arg(&input).args(["/dev/input","--","/usr/bin/Hyprland","--config"]).arg(&path)
 .env("MIOS_USER_TOML",&user).env("MIOS_USER_TOML_D",&fragments).env("XDG_RUNTIME_DIR",&owned.0).env("WAYLAND_DISPLAY",display).env("AQ_DRM_DEVICES","/dev/null").env("WLR_DRM_DEVICES","/dev/null").env_remove("HYPRLAND_INSTANCE_SIGNATURE").env_remove("DISPLAY").env_remove("TMUX").env_remove("TMUX_PANE")
 .stdout(Stdio::from(log.try_clone().map_err(|e|e.to_string())?)).stderr(Stdio::from(log)).spawn().map_err(|e|e.to_string())?;
 let mut shell_child=None;
 let result=(||{
  let mut selected=None;
  for _ in 0..150 {
   if let Some(status)=child.try_wait().map_err(|e|e.to_string())? {return Err(format!("nested compositor exited {status}; inspect {}",owned.0.join("desktop.log").display()));}
   let out=Command::new("hyprctl").args(["-j","instances"]).env("XDG_RUNTIME_DIR",&owned.0).output().map_err(|e|e.to_string())?;
   if let Ok(v)=serde_json::from_slice::<Json>(&out.stdout) {if let Some(inst)=v.as_array().and_then(|v|v.first()){selected=Some(inst.clone());break;}}
   thread::sleep(Duration::from_millis(100));
  }
  let inst=selected.ok_or("nested compositor did not become ready")?;let pid=inst["pid"].as_u64().ok_or("nested PID missing")?;
  let shell_log=fs::File::create(owned.0.join("shell.log")).map_err(|e|e.to_string())?;
  let shell_path=data["desktop"]["nested"]["shell_entry"].as_str().ok_or("nested shell entry missing")?;
  shell_child=Some(Command::new("bwrap").args(["--die-with-parent","--bind","/","/","--dev-bind","/dev","/dev","--ro-bind","/dev/null","/tmp/mios-hypr3d-pad.bin","--ro-bind"]).arg(&input).args(["/dev/input","--","quickshell","--no-duplicate","--path",shell_path])
   .env("MIOS_USER_TOML",&user).env("MIOS_USER_TOML_D",&fragments).env("XDG_RUNTIME_DIR",&owned.0).env("HYPRLAND_INSTANCE_SIGNATURE",inst["instance"].as_str().ok_or("nested signature missing")?).env("WAYLAND_DISPLAY",inst["wl_socket"].as_str().ok_or("nested display missing")?).env("QT_QPA_PLATFORM","wayland").env("MIOS_NESTED_RESIZE_POLL_MS",data["desktop"]["nested"]["resize_poll_ms"].as_integer().unwrap().to_string()).env("PIPEWIRE_RUNTIME_DIR",&parent).env_remove("DISPLAY").env_remove("TMUX").env_remove("TMUX_PANE")
   .stdout(Stdio::from(shell_log.try_clone().map_err(|e|e.to_string())?)).stderr(Stdio::from(shell_log)).spawn().map_err(|e|e.to_string())?);

  let mut target=None;
  for _ in 0..100 {
   let v:Json=serde_json::from_str(&ipc(&parent,&signature,"j/clients")?).map_err(|e|e.to_string())?;
   if let Some(w)=v.as_array().and_then(|v|v.iter().find(|w|w["pid"].as_u64()==Some(pid))){target=Some(w.clone());break;}
   thread::sleep(Duration::from_millis(100));
  }
  let w=target.ok_or("parent could not identify the owned nested output")?;
  let address=w["address"].as_str().ok_or("nested address missing")?;let stable=w["stableId"].as_str().ok_or("nested stable id missing")?;
  let layout=layout.unwrap_or(data["desktop"]["nested"]["launch_layout"].as_str().ok_or("nested launch_layout missing")?);
  if !["pip","window","side-by-side"].contains(&layout){return Err("nested layout must be pip, window, or side-by-side".into());}
  if layout=="pip" {
   let pip=Command::new("/usr/bin/mios-pip-gravity").args(["on","--window",address,"--pid",&pid.to_string(),"--stable-id",stable]).status().map_err(|e|e.to_string())?;if !pip.success(){return Err("nested PiP adoption failed".into());}
  }else{
   let mut code=format!("local w=nil;for _,v in ipairs(hl.get_windows()) do if v.address=={} then w=v end end;if not w or w.pid~={pid} or w.stable_id~=tonumber({},16) then error('nested identity changed',0) end;local target={};hl.dispatch(hl.dsp.window.pin({{window=target,action='disable'}}));hl.dispatch(hl.dsp.window.tag({{window=target,tag='+mios-pip-disabled'}}));",serde_json::to_string(address).unwrap(),serde_json::to_string(stable).unwrap(),serde_json::to_string(&format!("address:{address}")).unwrap());
   if layout=="side-by-side" {
    let monitors:Json=serde_json::from_str(&ipc(&parent,&signature,"j/monitors")?).map_err(|e|e.to_string())?;
    let m=monitors.as_array().and_then(|v|v.iter().find(|m|m["id"]==w["monitor"])).ok_or("nested parent monitor missing")?;
    let num=|k:&str|m[k].as_f64().unwrap_or(0.0);let scale=num("scale").max(0.1);
    let edge=data["theme"]["shell"]["edge_margin_px"].as_float().or_else(||data["theme"]["shell"]["edge_margin_px"].as_integer().map(|n|n as f64)).ok_or("global edge margin missing")?;
    let reserved=|i:usize|m["reserved"][i].as_f64().unwrap_or(0.0);
    let width=(num("width")/scale/2.0-2.0*edge).max(1.0);let height=(num("height")/scale-reserved(1)-reserved(3)-2.0*edge).max(1.0);
    code+=&format!("hl.dispatch(hl.dsp.window.resize({{window=target,x={width},y={height},exact=true}}));hl.dispatch(hl.dsp.window.move({{window=target,x={},y={}}}));",num("x")+num("width")/scale/2.0+edge,num("y")+reserved(1)+edge);
   }
   let _=ipc(&parent,&signature,&format!("/eval {code}"))?;
  }
  // Only return focus if this launch itself took it. A later operator focus change wins.
  if let (Some(a),Some(p),Some(stable))=(focused["address"].as_str(),focused["pid"].as_u64(),focused["stableId"].as_str()) {
   let restore=format!("local current=hl.get_active_window();if current and current.pid=={pid} then for _,v in ipairs(hl.get_windows()) do if v.address=={} and v.pid=={p} and v.stable_id==tonumber({},16) then hl.dispatch(hl.dsp.focus({{window={}}}));break end end end",serde_json::to_string(a).unwrap(),serde_json::to_string(stable).unwrap(),serde_json::to_string(&format!("address:{a}")).unwrap());
   let _=ipc(&parent,&signature,&format!("/eval {restore}"))?;
  }
  let descriptor=serde_json::json!({"runtime":owned.0,"instance":inst,"parent_window":address,"layout":layout,"isolated_input":true,"physical_controllers_assigned":false,"tmux_socket":tmux_root.join(format!("tmux-{}/{}",fs::metadata(&owned.0).map_err(|e|e.to_string())?.uid(),socket_name)),"terminal_session":terminal_session});
  fs::write(owned.0.join("session.json"),format!("{descriptor}\n")).map_err(|e|e.to_string())?;
  println!("{descriptor}");
  let mut parent_events=ParentEvents::connect(&parent,&signature);
  loop {
   if let Some(status)=child.try_wait().map_err(|e|e.to_string())? {return if status.success(){Ok(())}else{Err(format!("nested compositor exited {status}"))};}
   if parent_events.closed(address) {let _=child.kill();let _=child.wait();return Ok(());}
   thread::sleep(Duration::from_millis(250));
  }
 })();
 let _=Command::new("tmux").args(["-L",socket_name,"kill-server"]).env("TMUX_TMPDIR",&tmux_root).env_remove("TMUX").env_remove("TMUX_PANE").status();
 if let Some(mut shell)=shell_child {let _=shell.kill();let _=shell.wait();}
 if result.is_err(){let _=child.kill();let _=child.wait();
 if let Ok(log)=fs::read_to_string(owned.0.join("desktop.log")){let tail=log.lines().rev().take(24).collect::<Vec<_>>().into_iter().rev().collect::<Vec<_>>().join("\n");eprintln!("Nested desktop log tail:\n{tail}");}
}
 result
}
#[cfg(test)]
mod tests {
 use super::*;
 #[test] fn nested_projection_keeps_shell_and_system_manager_separate(){
  let data=toml::from_str::<Value>(&fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).ancestors().nth(3).unwrap().join("usr/share/mios/mios.toml")).unwrap()).unwrap();
  let out=project(&data).unwrap();assert!(!out.contains("name\"] = \"mios-placement"));assert!(!out.contains("systemctl --user restart mios-desktop.target"));assert!(!out.contains("dbus-update-activation-environment"));assert!(!out.contains("/usr/share/mios/quickshell/Config.qml"));assert!(out.contains("Nested.qml"));assert!(out.contains("no_hardware_cursors\"] = 1"));
 }
 #[test] fn nested_resize_interval_is_validated(){
  let mut data=toml::from_str::<Value>(&fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).ancestors().nth(3).unwrap().join("usr/share/mios/mios.toml")).unwrap()).unwrap();
  for value in [20,100,5000] { data["desktop"]["nested"]["resize_poll_ms"]=Value::Integer(value);assert!(project(&data).is_ok()); }
  for value in [0,19,5001] { data["desktop"]["nested"]["resize_poll_ms"]=Value::Integer(value);assert!(project(&data).unwrap_err().contains("resize_poll_ms")); }
 }
 #[test] fn parent_close_requires_one_complete_matching_native_event(){
  let mut events=ParentEvents::default();
  assert!(!events.feed(b"closewindow>>0xabc1\nactivewindowv2>>0xabc\n", "0xabc"));
  assert!(!events.feed(b"closewindow>>0xa", "0xabc"));
  assert!(events.feed(b"bc\n", "0xabc"));
  assert!(events.feed(b"closewindow>>abc\n", "0xabc")); // native event hex has no prefix
  assert!(!events.feed(b"movewindow>>0xabc,2\n", "0xabc"));
 }
 #[test] fn parent_event_disconnect_preserves_a_running_session(){
  let (client,mut server)=UnixStream::pair().unwrap();client.set_nonblocking(true).unwrap();
  let mut events=ParentEvents{stream:Some(client),pending:Vec::new()};
  assert!(!events.closed("0xabc")); // unavailable snapshot/event is not a close
  server.write_all(b"closewindow>>0xother\n").unwrap();assert!(!events.closed("0xabc"));
  drop(server);assert!(!events.closed("0xabc"));
 }
 #[test] fn argv_is_data_not_shell_source(){let v:Value=toml::from_str("terminal_argv=['alacritty', 'a; touch /bad', \"x'y\"]").unwrap();assert_eq!(command(&v,"terminal_argv").unwrap(),"'alacritty' 'a; touch /bad' 'x'\\''y'");}
}
