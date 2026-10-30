// AI-hint: Isolated native MiOS desktop sessions for many uses: a parent PiP/window (default), a whole attached output for a second local user, the parent's remote display output (RDP/VNC), or a Hypr3D room window; per-session seat/user identity, private tmux namespace and assigned evdev nodes bound into its namespace.
// AI-related: desktop.nested, tools/native/mios-gen/src/nested_devices.rs, Quickshell Nested.qml, mios-pip-gravity, global_bindings, remote.rdp.virtual_output, render_hyprland::toggle_3d
use std::{env,fs,io::{Read,Write},os::{unix::{fs::{PermissionsExt,MetadataExt,symlink},net::UnixStream}},path::{Path,PathBuf},process::{Command,Stdio},thread,time::{Duration,Instant}};
use serde_json::{json,Value as Json};
use toml::Value;
use crate::nested_devices::{self as devices,Device};

/// Launch targets. pip/window/side-by-side embed the session in a parent window; output fills an attached parent
/// output (a second local user); remote fills the parent's remote display output (served by the existing RDP/VNC
/// path); hypr3d places the session as a window of the parent's Hypr3D room.
pub const TARGETS:[&str;6]=["pip","window","side-by-side","output","remote","hypr3d"];

/// The fit scale for a viewport: the largest-fitting scale for the logical width x height, clamped, then the nearest
/// multiple of 1/120 (Wayland fractional scale) whose logical size is whole in both axes, so the compositor never
/// substitutes a scale of its own. Shared by the projection and its test.
const NESTED_SCALE_LUA:&str=r#"
local function nested_scale(w,h)
 if nested_fit.mode ~= 'fit' then return 1 end
 local s = math.min(w/nested_fit.w, h/nested_fit.h) * (nested_fit.zoom or 1)
 s = math.max(nested_fit.min, math.min(nested_fit.max, s))
 local k = math.floor(s*120+0.5)
 local lo, hi = math.ceil(nested_fit.min*120), math.floor(nested_fit.max*120)
 for d = 0, 240 do
  for _, c in ipairs({k-d, k+d}) do
   if c >= lo and c <= hi and (w*120) % c == 0 and (h*120) % c == 0 then return c/120 end
  end
 end
 return 1
end
"#;
fn shell_arg(s:&str)->String { format!("'{}'",s.replace('\'',"'\\''")) }
fn command(v:&Value,key:&str)->Result<String,String> {
 let a=v.get(key).and_then(Value::as_array).filter(|v|!v.is_empty()).ok_or(format!("desktop.nested.{key} must be nonempty argv"))?;
 a.iter().map(|s|s.as_str().filter(|s|!s.chars().any(char::is_control)).map(shell_arg).ok_or(format!("invalid desktop.nested.{key} argument"))).collect::<Result<Vec<_>,_>>().map(|v|v.join(" "))
}
/// [desktop.nested] scale_mode ("fit" | "native"), scale_min and scale_max (0.1..=8, min <= max).
fn nested_scaling(n:&Value)->Result<(String,f64,f64),String>{
 let mode=n.get("scale_mode").and_then(Value::as_str).unwrap_or("fit");
 if !["fit","native"].contains(&mode){return Err("desktop.nested.scale_mode must be fit or native".into());}
 let f=|k:&str,d:f64|->Result<f64,String>{let v=match n.get(k){None=>d,Some(Value::Float(f))=>*f,Some(Value::Integer(i))=>*i as f64,_=>return Err(format!("desktop.nested.{k} must be a number"))};if (0.1..=8.0).contains(&v){Ok(v)}else{Err(format!("desktop.nested.{k} must be 0.1..8"))}};
 let (lo,hi)=(f("scale_min",0.25)?,f("scale_max",2.0)?);
 if lo>hi {return Err("desktop.nested.scale_min must not exceed scale_max".into());}
 Ok((mode.to_owned(),lo,hi))
}
/// The scale mode a target uses: [desktop.nested.targets.<target>].scale_mode, else [desktop.nested].scale_mode.
pub fn target_scale_mode(n:&Value,target:&str)->Result<String,String>{
 let base=nested_scaling(n)?.0;
 match n.get("targets").and_then(|t|t.get(target)).and_then(|t|t.get("scale_mode")) {
  None=>Ok(base),
  Some(Value::String(s)) if ["fit","native"].contains(&s.as_str())=>Ok(s.clone()),
  Some(_)=>Err(format!("desktop.nested.targets.{target}.scale_mode must be fit or native")),
 }
}
fn number(v:&Value,key:&str)->Result<i64,String>{v.get(key).and_then(Value::as_integer).filter(|n|(320..=8192).contains(n)).ok_or(format!("desktop.nested.{key} must be 320..8192"))}
/// The nested compositor's Lua configuration; `scale_mode` overrides [desktop.nested].scale_mode (a launch target's own).
pub fn project_for(data:&Value,scale_mode:Option<&str>)->Result<String,String>{
 let n=&data["desktop"]["nested"];let (width,height)=(number(n,"width")?,number(n,"height")?);
 let mut d=data.clone();d["desktop"]["hyprland"]["placement"]["enabled"]=Value::Boolean(false);
 // The session owns its output scale (fit/zoom below); the global automatic scale policy must not re-apply its own.
 if let Some(sc)=d.get_mut("desktop").and_then(|v|v.get_mut("scaling")).and_then(Value::as_table_mut){sc.insert("apply_to".into(),Value::Array(vec![]));}let h=d.get_mut("desktop").and_then(|v|v.get_mut("hyprland")).and_then(Value::as_table_mut).ok_or("desktop.hyprland missing")?;
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
 // scale_mode "fit" keeps the session's logical desktop at width x height whatever the viewport (a PiP shows the
 // whole desktop scaled down, an enlarged window scales it up); "native" maps viewport pixels 1:1.
 let (mut mode,smin,smax)=nested_scaling(n)?;
 if let Some(m)=scale_mode { if !["fit","native"].contains(&m){return Err(format!("nested scale mode {m:?} must be fit or native"));} mode=m.to_owned(); }
 let zstep=data.get("desktop").and_then(|d|d.get("scaling")).and_then(|s|s.get("session_step")).and_then(|v|v.as_float().or_else(||v.as_integer().map(|i|i as f64))).filter(|v|*v>0.0&&*v<=1.0).unwrap_or(0.125);
 out+=&format!("local nested_fit={{mode='{mode}',w={width},h={height},min={smin},max={smax},zoom=1,zstep={zstep}}}\n");
 out+=r#"
local nested_modes = {}
"#;
 out+=NESTED_SCALE_LUA;
 out+=r#"
local function nested_reflow()
 for _,m in ipairs(hl.get_monitors()) do
  local mode = string.format('%dx%d',m.width,m.height)
  if m.width > 0 and m.height > 0 and nested_modes[m.name] ~= mode then
   nested_modes[m.name] = mode
   hl.monitor({output=m.name,mode=mode,position=m.x..'x'..m.y,scale=nested_scale(m.width,m.height),transform=m.transform})
  end
 end
end
hl.on('monitor.layout_changed', nested_reflow)
-- `mios-gen session-scale` in the parent zooms this session when its window is focused (global SUPER+CTRL +/-/0/wheel).
-- Every press reaches the next scale the output accepts in that direction: on a small viewport valid scales are
-- sparse, so the zoom keeps stepping until the effective scale actually changes (or the range ends).
function mios_nested_zoom(dir)
 local m = hl.get_monitors()[1]
 if dir ~= 'up' and dir ~= 'down' then nested_fit.zoom = 1
 elseif m and m.width > 0 and m.height > 0 then
  local cur = nested_scale(m.width, m.height)
  local z = nested_fit.zoom or 1
  for _ = 1, 64 do
   z = z + (dir == 'up' and nested_fit.zstep or -nested_fit.zstep)
   if z < 0.05 or z > 8 then break end
   nested_fit.zoom = z
   local s = nested_scale(m.width, m.height)
   if (dir == 'up' and s > cur + 1e-9) or (dir == 'down' and s < cur - 1e-9) then break end
  end
 end
 nested_modes = {}
 nested_reflow()
end
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
/// A Lua eval that must succeed: Hyprland answers "ok", anything else is its error text.
fn eval(rt:&Path,sig:&str,code:&str)->Result<(),String>{
 let r=ipc(rt,sig,&format!("/eval {code}"))?;if r.trim()=="ok"{Ok(())}else{Err(r.trim().to_owned())}
}
fn lua_str(s:&str)->String{serde_json::to_string(s).unwrap()}
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
/// The nested XWayland's listening socket as a DISPLAY value usable outside the sandbox ("unix:<dir>/X<n>").
fn x11_socket(dir:&Path)->Option<String>{
 let mut n:Vec<(u32,PathBuf)>=fs::read_dir(dir).ok()?.flatten().filter_map(|e|{let name=e.file_name().into_string().ok()?;let d=name.strip_prefix('X')?;d.parse::<u32>().ok().map(|v|(v,e.path()))}).collect();
 n.sort();n.first().map(|(_,p)|format!("unix:{}",p.display()))
}
/// The session runtime and, when that had to move (see session_runtime), the link left at the usual place in the parent.
struct OwnedDir(PathBuf,Option<PathBuf>);
impl Drop for OwnedDir {fn drop(&mut self){
 if let Some(l)=&self.1{let _=fs::remove_file(l);}
 // A session helper still exiting (the shell's state writers run `mkdir -p` + rename) can recreate the runtime just
 // after it is removed: watch it for a bounded grace period and remove it again if it comes back.
 let _=fs::remove_dir_all(&self.0);
 let end=Instant::now()+Duration::from_millis(1500);
 while Instant::now()<end {thread::sleep(Duration::from_millis(100));if self.0.exists(){let _=fs::remove_dir_all(&self.0);}}
}}

/// A UNIX socket path holds at most 107 bytes. The session's compositor listens at <runtime>/hypr/<signature>/.socket2.sock
/// (a signature is under 40 bytes) and its tmux at <runtime>/tmux/tmux-<uid>/<socket>, so a session inside a nested
/// session must not live under that session's own runtime. Returns (runtime, link): the runtime is
/// <parent>/mios-nested-desktop-<pid> when that keeps every socket in budget, else the same name under the outermost
/// runtime (the parent path minus its nested-session components), with a link at <parent>/mios-nested-desktop-<pid> so
/// the parent's tools (session-scale, tests) still find the session there.
pub fn session_runtime(parent:&Path,pid:u32)->(PathBuf,Option<PathBuf>){
 const BUDGET:usize=107-"/hypr/".len()-40-"/.socket2.sock".len();
 let name=format!("mios-nested-desktop-{pid}");
 let near=parent.join(&name);
 if near.as_os_str().len()<=BUDGET {return (near,None);}
 let mut base=parent;
 while base.file_name().is_some_and(|n|n.to_string_lossy().starts_with("mios-nested-desktop-")) { match base.parent() { Some(p)=>base=p, None=>break } }
 (base.join(&name),Some(near))
}

/// Command-line choices; each unset field falls back to the seat preset, then [desktop.nested].
#[derive(Debug,Default,Clone)]
pub struct Options { pub target:Option<String>, pub output:Option<String>, pub seat:Option<String>, pub user:Option<String>, pub devices:Vec<String>, pub no_devices:bool, pub plan:bool, pub list_devices:bool, pub list_sessions:bool }

/// The resolved launch: identity, target and the device specs (resolved to nodes later, against the host).
#[derive(Debug,Clone,PartialEq)]
pub struct Plan { pub target:String, pub output:Option<String>, pub seat:String, pub user:String, pub specs:Vec<String>, pub scale_mode:String, pub tmux_socket:String, pub terminal_session:String }

fn opt_str<'a>(v:Option<&'a Value>,key:&str)->Result<Option<&'a str>,String>{
 match v { None=>Ok(None), Some(Value::String(s)) if !s.chars().any(char::is_control)=>Ok(Some(s.as_str())), Some(_)=>Err(format!("{key} must be a string")) }
}
fn ident(s:&str)->bool{!s.is_empty()&&s.bytes().all(|b|b.is_ascii_alphanumeric()||b==b'-')}
pub fn plan(data:&Value,o:&Options)->Result<Plan,String>{
 let n=&data["desktop"]["nested"];
 let seat=match o.seat.as_deref() { Some(s)=>s.to_owned(), None=>opt_str(n.get("seat"),"desktop.nested.seat")?.unwrap_or("").to_owned() };
 if !seat.is_empty()&&!devices::valid_seat(&seat){return Err(format!("seat {seat:?} must be 1..32 of a-z, 0-9 and '-' (not leading)"));}
 let preset=if seat.is_empty(){None}else{n.get("seats").and_then(|s|s.get(&seat))};
 if let Some(p)=preset { if !p.is_table(){return Err(format!("desktop.nested.seats.{seat} must be a table"));} }
 let pk=|k:&str|format!("desktop.nested.seats.{seat}.{k}");
 let target=match o.target.as_deref() { Some(t)=>t.to_owned(), None=>match opt_str(preset.and_then(|p|p.get("target")),&pk("target"))? { Some(t)=>t.to_owned(), None=>n.get("launch_layout").and_then(Value::as_str).ok_or("nested launch_layout missing")?.to_owned() } };
 if !TARGETS.contains(&target.as_str()){return Err(format!("nested target {target:?} must be one of {}",TARGETS.join(", ")));}
 // A preset's output belongs to the targets that use one: a CLI target change (e.g. the preset's output seat opened
 // in the room for once) drops it instead of refusing; an explicit --output on such a target is still an error.
 let uses_output=["output","remote"].contains(&target.as_str());
 let preset_output=opt_str(preset.and_then(|p|p.get("output")),&pk("output"))?.filter(|_|uses_output).map(str::to_owned);
 let output=match o.output.as_deref() { Some(v)=>Some(v.to_owned()), None=>preset_output.or_else(||if target=="output"{n.get("targets").and_then(|t|t.get("output")).and_then(|t|t.get("output")).and_then(Value::as_str).filter(|s|!s.is_empty()).map(str::to_owned)}else{None}) }.filter(|s|!s.is_empty());
 if let Some(out)=&output { if out.chars().any(|c|c.is_control()||c=='"'||c=='\\'){return Err(format!("output name {out:?} is not valid"));} if !uses_output {return Err(format!("--output applies to the output and remote targets, not {target}"));} }
 if target=="output"&&output.is_none(){return Err("the output target needs an output: --output NAME, desktop.nested.seats.<seat>.output or desktop.nested.targets.output.output".into());}
 let user=match o.user.as_deref() { Some(u)=>u.to_owned(), None=>opt_str(preset.and_then(|p|p.get("user")),&pk("user"))?.map(str::to_owned).or_else(||n.get("user_label").and_then(Value::as_str).filter(|s|!s.is_empty()).map(str::to_owned)).unwrap_or_else(||seat.clone()) };
 if user.len()>64||user.chars().any(char::is_control){return Err("user label must be at most 64 printable characters".into());}
 let pol=devices::Policy::from_ssot(n)?;
 let specs=if o.no_devices{Vec::new()}else if !o.devices.is_empty(){o.devices.clone()}else if let Some(d)=preset.and_then(|p|p.get("devices")){
  d.as_array().ok_or(format!("{} must be an array",pk("devices")))?.iter().map(|s|s.as_str().map(str::to_owned).ok_or(format!("{} entries must be strings",pk("devices")))).collect::<Result<_,_>>()?
 }else{pol.default.clone()};
 if specs.iter().any(|s|s=="virtual-pad")&&seat.is_empty(){return Err("the virtual-pad device needs a seat label (--seat): the input bridge names a session's pad after its seat".into());}
 let base_socket=n.get("tmux_socket_name").and_then(Value::as_str).ok_or("nested tmux_socket_name missing")?;
 let terminal_session=n.get("terminal_session").and_then(Value::as_str).ok_or("nested terminal_session missing")?.to_owned();
 let tmux_socket=if seat.is_empty(){base_socket.to_owned()}else{format!("{base_socket}-{seat}")};
 if ![tmux_socket.as_str(),terminal_session.as_str()].iter().all(|s|ident(s)){return Err("nested terminal identifiers must be sanitized".into());}
 Ok(Plan{scale_mode:target_scale_mode(n,&target)?,target,output,seat,user,specs,tmux_socket,terminal_session})
}

/// A parent output by exact name, else the first output whose name starts with one of `prefixes`.
pub fn pick_output<'a>(monitors:&'a Json,names:&[String],prefixes:&[String])->Option<&'a Json>{
 let list=monitors.as_array()?;
 let usable=|m:&&Json|m["width"].as_i64().unwrap_or(0)>0&&m["height"].as_i64().unwrap_or(0)>0&&!m["disabled"].as_bool().unwrap_or(false);
 names.iter().find_map(|n|list.iter().filter(usable).find(|m|m["name"].as_str()==Some(n.as_str())))
  .or_else(||prefixes.iter().find_map(|p|list.iter().filter(usable).find(|m|m["name"].as_str().is_some_and(|s|s.starts_with(p.as_str())))))
}
fn strs(v:Option<&Value>)->Vec<String>{v.and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).filter(|s|!s.is_empty()).map(str::to_owned).collect()}
/// The remote display output the existing remote path serves: an explicit name, else remote.rdp.virtual_output, the
/// [desktop.nested.targets.remote] outputs, then its output_prefixes. Waits up to wait_ms for a client to attach.
fn remote_output(data:&Value,rt:&Path,sig:&str,explicit:Option<&str>)->Result<Json,String>{
 let r=&data["desktop"]["nested"]["targets"]["remote"];
 let mut names:Vec<String>=explicit.map(|s|vec![s.to_owned()]).unwrap_or_default();
 if explicit.is_none(){
  names.extend(data["remote"]["rdp"]["virtual_output"].as_str().filter(|s|!s.is_empty()).map(str::to_owned));
  names.extend(strs(r.get("outputs")));
 }
 let prefixes=if explicit.is_none(){strs(r.get("output_prefixes"))}else{Vec::new()};
 let wait=r.get("wait_ms").and_then(Value::as_integer).filter(|v|(0..=600000).contains(v)).ok_or("desktop.nested.targets.remote.wait_ms must be 0..600000")? as u64;
 let end=Instant::now()+Duration::from_millis(wait);
 loop {
  let monitors:Json=serde_json::from_str(&ipc(rt,sig,"j/monitors")?).map_err(|e|e.to_string())?;
  if let Some(m)=pick_output(&monitors,&names,&prefixes){return Ok(m.clone());}
  if Instant::now()>=end {
   let have:Vec<&str>=monitors.as_array().into_iter().flatten().filter_map(|m|m["name"].as_str()).collect();
   return Err(format!("no remote display output is attached (looked for {names:?}, prefixes {prefixes:?}; outputs: {have:?}); connect a remote client first or raise desktop.nested.targets.remote.wait_ms"));
  }
  thread::sleep(Duration::from_millis(250));
 }
}
fn host_output(rt:&Path,sig:&str,name:&str)->Result<Json,String>{
 let monitors:Json=serde_json::from_str(&ipc(rt,sig,"j/monitors")?).map_err(|e|e.to_string())?;
 pick_output(&monitors,&[name.to_owned()],&[]).cloned().ok_or_else(||format!("parent output {name:?} is not attached (outputs: {:?})",monitors.as_array().into_iter().flatten().filter_map(|m|m["name"].as_str()).collect::<Vec<_>>()))
}
/// Room-window geometry: the session's logical width x height (its fit scale is then 1: one texel per parent pixel)
/// clamped into the output's work area minus the shell edge margin, centred in it.
pub fn room_box(m:&Json,width:f64,height:f64,edge:f64)->(f64,f64,f64,f64){
 let num=|k:&str|m[k].as_f64().unwrap_or(0.0);let scale=num("scale").max(0.1);
 let r=|i:usize|m["reserved"][i].as_f64().unwrap_or(0.0);
 let (aw,ah)=((num("width")/scale-r(0)-r(2)-2.0*edge).max(1.0),(num("height")/scale-r(1)-r(3)-2.0*edge).max(1.0));
 let k=(aw/width).min(ah/height).min(1.0);
 let (w,h)=((width*k).floor().max(1.0),(height*k).floor().max(1.0));
 (num("x")+r(0)+edge+((aw-w)/2.0).floor(),num("y")+r(1)+edge+((ah-h)/2.0).floor(),w,h)
}
fn private_files(n:&Value)->Result<Vec<PathBuf>,String>{
 strs(n.get("private_files")).into_iter().map(PathBuf::from).map(|p|if p.is_absolute()&&p.file_name().is_some()&&!p.starts_with("/dev")&&!p.starts_with("/proc")&&p.components().all(|c|!matches!(c,std::path::Component::ParentDir)){Ok(p)}else{Err(format!("desktop.nested.private_files entry {} must be an absolute file path outside /dev and /proc",p.display()))}).collect()
}

/// The host's legacy Hypr3D controller packet (mios-inputd legacy_pad_state_path); it is replaced by rename several times
/// a second, so only a private /tmp isolates a session from it for good.
const LEGACY_PAD:&str="/tmp/mios-hypr3d-pad.bin";
/// [desktop.nested].private_tmp: "always" | "seat" (a labelled seat gets a private /tmp, the unlabelled development
/// session keeps the shared one) | "never".
pub fn private_tmp(n:&Value,seat:&str)->Result<bool,String>{
 match n.get("private_tmp").and_then(Value::as_str) {
  Some("always")=>Ok(true), Some("seat")|None=>Ok(!seat.is_empty()), Some("never")=>Ok(false),
  _=>Err("desktop.nested.private_tmp must be always, seat or never".into()),
 }
}
/// bwrap's `Unable to remount destination` race: a bind over a file the host replaces by rename between bwrap's
/// bind and its read-only remount. Only that early failure is retried (bounded); any other exit is the caller's.
pub fn bwrap_bind_race(log:&str)->bool{log.contains("bwrap: Can't bind mount")&&log.contains("Unable to remount destination")}
fn spawn_sandboxed(log:&Path,mut make:impl FnMut()->Command)->Result<std::process::Child,String>{
 for attempt in 1..=4 {
  let f=fs::File::create(log).map_err(|e|e.to_string())?;
  let mut child=make().stdout(Stdio::from(f.try_clone().map_err(|e|e.to_string())?)).stderr(Stdio::from(f)).spawn().map_err(|e|e.to_string())?;
  let end=Instant::now()+Duration::from_millis(300);
  while Instant::now()<end {
   if child.try_wait().map_err(|e|e.to_string())?.is_some() {break;}
   thread::sleep(Duration::from_millis(25));
  }
  if attempt<4&&child.try_wait().map_err(|e|e.to_string())?.is_some()&&bwrap_bind_race(&fs::read_to_string(log).unwrap_or_default()) {continue;}
  return Ok(child);
 }
 unreachable!()
}

fn list_devices(data:&Value,rt:&Path)->Result<Json,String>{
 let pol=devices::Policy::from_ssot(&data["desktop"]["nested"])?;
 let sessions=devices::live_sessions(&rt.join(&pol.registry_dir),false);
 let mut out=Vec::new();
 let host=devices::host();
 let mut nodes:Vec<String>=fs::read_dir(host.dev).into_iter().flatten().flatten().map(|e|e.file_name().to_string_lossy().into_owned()).filter(|n|n.starts_with("event")).collect();
 nodes.sort_by_key(|n|n.trim_start_matches("event").parse::<u64>().unwrap_or(u64::MAX));
 let open=devices::Policy{allow_kinds:devices::KINDS.iter().map(|s|s.to_string()).collect(),allow_virtual:true,..pol};
 for n in nodes {
  let path=host.dev.join(&n);
  let owner=sessions.iter().find(|(_,s)|s["devices"].as_array().into_iter().flatten().any(|d|d["path"].as_str()==Some(&path.to_string_lossy()))).map(|(_,s)|s["seat"].clone());
  match devices::resolve_one(&host,&path.to_string_lossy(),"",&open) {
   Ok(d)=>{let mut j=d.to_json();j["assigned_to"]=owner.unwrap_or(Json::Null);out.push(j);},
   Err(e)=>out.push(json!({"path":path,"error":e})),
  }
 }
 Ok(Json::Array(out))
}

pub fn run(root:&Path,dry_run:bool,o:&Options)->Result<(),String>{
 let data=mios_resolver::resolve_merged(Some(root),false).map_err(|e|e.to_string())?;
 let n=&data["desktop"]["nested"];
 let parent=PathBuf::from(env::var_os("XDG_RUNTIME_DIR").ok_or("XDG_RUNTIME_DIR missing")?);
 let policy=devices::Policy::from_ssot(n)?;
 let registry=parent.join(&policy.registry_dir);
 if o.list_devices {println!("{}",list_devices(&data,&parent)?);return Ok(());}
 if o.list_sessions {println!("{}",Json::Array(devices::live_sessions(&registry,false).into_iter().map(|(_,j)|j).collect()));return Ok(());}
 let plan=plan(&data,o)?;
 let config=project_for(&data,Some(&plan.scale_mode))?;
 if dry_run {print!("{config}");return Ok(());}
 let assigned:Vec<Device>=devices::resolve(&devices::host(),&plan.specs,&plan.seat,&policy)?;
 if o.plan {
  println!("{}",json!({"target":plan.target,"output":plan.output,"seat":plan.seat,"user":plan.user,"scale_mode":plan.scale_mode,"tmux_socket_name":plan.tmux_socket,"terminal_session":plan.terminal_session,"devices":assigned.iter().map(Device::to_json).collect::<Vec<_>>()}));
  return Ok(());
 }
 let signature=env::var("HYPRLAND_INSTANCE_SIGNATURE").map_err(|_|"parent compositor signature missing")?;
 let display=env::var("WAYLAND_DISPLAY").map_err(|_|"parent Wayland display missing")?;
 let display=if Path::new(&display).is_absolute(){PathBuf::from(display)}else{parent.join(display)};
 // Preflight in the parent before anything starts: a missing output or room leaves nothing behind.
 let target_output:Option<Json>=match plan.target.as_str() {
  "output"=>Some(host_output(&parent,&signature,plan.output.as_deref().unwrap_or_default())?),
  "remote"=>Some(remote_output(&data,&parent,&signature,plan.output.as_deref())?),
  _=>None,
 };
 let room=&n["targets"]["hypr3d"];
 let open_room=room.get("open_room").and_then(Value::as_bool).ok_or("desktop.nested.targets.hypr3d.open_room must be a boolean")?;
 if plan.target=="hypr3d"&&!open_room {
  eval(&parent,&signature,"assert(hl.plugin.hypr3d and hl.plugin.hypr3d.active(), 'the Hypr3D room is not open in the parent (desktop.nested.targets.hypr3d.open_room is false)')")?;
 }
 let me=std::process::id();
 let mut claim=devices::Claim::take(&registry,&plan.seat,&assigned,policy.exclusive,json!({"state":"starting","target":plan.target,"user":plan.user,"parent_runtime":parent,"parent_instance":signature}))?;
 let (runtime,link)=session_runtime(&parent,me);
 fs::create_dir(&runtime).map_err(|e|e.to_string())?;
 let mut owned=OwnedDir(runtime,None);
 fs::set_permissions(&owned.0,fs::Permissions::from_mode(0o700)).map_err(|e|e.to_string())?;
 if let Some(l)=link {symlink(&owned.0,&l).map_err(|e|format!("nested runtime link {}: {e}",l.display()))?;owned.1=Some(l);}
 let user=mios_resolver::user_toml_path(Some(root));
 let old_fragments=env::var_os("MIOS_USER_TOML_D").map(PathBuf::from).unwrap_or_else(||user.parent().unwrap_or(Path::new(".")).join("mios.d"));
 let fragments=owned.0.join("mios.d");fs::create_dir(&fragments).map_err(|e|e.to_string())?;
 let mut names=Vec::new();
 if old_fragments.is_dir(){for entry in fs::read_dir(&old_fragments).map_err(|e|e.to_string())? {
  let entry=entry.map_err(|e|e.to_string())?;if entry.path().is_file()&&entry.path().extension().is_some_and(|v|v=="toml"){let name=entry.file_name();symlink(fs::canonicalize(entry.path()).map_err(|e|e.to_string())?,fragments.join(&name)).map_err(|e|e.to_string())?;names.push(name);}
 }}
 names.sort();let last=names.last().map(|n|n.to_string_lossy().into_owned()).unwrap_or_else(||"mios".into());
 let (socket_name,terminal_session)=(plan.tmux_socket.as_str(),plan.terminal_session.as_str());
 let tmux_root=owned.0.join("tmux");fs::create_dir(&tmux_root).map_err(|e|e.to_string())?;
 let socket_root_text=tmux_root.to_string_lossy();
 let patch=toml::toml!{ [keybindings] socket_name=socket_name terminal_session=terminal_session [terminal] socket_root=(socket_root_text.as_ref()) default_action="terminal" [terminal.sessions] selector=false new_session_name=terminal_session };
 fs::write(fragments.join(format!("{last}-mios-nested.toml")),toml::to_string(&patch).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
 let focused:Json=serde_json::from_str(&ipc(&parent,&signature,"j/activewindow")?).map_err(|e|e.to_string())?;
 let input=owned.0.join("input");fs::create_dir(&input).map_err(|e|e.to_string())?;
 // Mask real evdev nodes in this process tree and expose only the assigned nodes. A private /tmp (labelled seats by
 // default) isolates every plugin state file and the legacy room packet as a whole; with the shared /tmp, those get a
 // per-file mask instead (a mask over a file the host replaces by rename is detached by that rename, see private_tmp).
 let mut sandbox:Vec<String>=["--die-with-parent","--bind","/","/","--dev-bind","/dev","/dev"].map(String::from).to_vec();
 // XWayland: inside bwrap's user namespace the host's root-owned /tmp/.X11-unix reads as uid 65534 and Hyprland refuses
 // it ("Failed to find a suitable socket for XWayland"), so a nested session had no X11 at all (no Steam/Proton/Wine/
 // SDL-X11 game). With the shared /tmp an owned 0700 directory takes its place in this process tree only (display numbers
 // stay unique through the shared /tmp/.X<n>-lock files); a private /tmp carries its own owned .X11-unix. Clients outside
 // the tree use session.json "x11_display" (DISPLAY=unix:<socket>).
 let (tmp,x11)=if private_tmp(n,&plan.seat)? {
  let t=owned.0.join("tmp");fs::create_dir(&t).map_err(|e|e.to_string())?;fs::set_permissions(&t,fs::Permissions::from_mode(0o700)).map_err(|e|e.to_string())?;
  let x=t.join(".X11-unix");fs::create_dir(&x).map_err(|e|e.to_string())?;fs::set_permissions(&x,fs::Permissions::from_mode(0o1777)).map_err(|e|e.to_string())?;
  sandbox.extend(["--bind".into(),t.to_string_lossy().into_owned(),"/tmp".into()]);
  (Some(t),x)
 }else{
  let x=owned.0.join("x11");fs::create_dir(&x).map_err(|e|e.to_string())?;fs::set_permissions(&x,fs::Permissions::from_mode(0o700)).map_err(|e|e.to_string())?;
  sandbox.extend(["--ro-bind","/dev/null",LEGACY_PAD].map(String::from));
  sandbox.extend(["--bind".into(),x.to_string_lossy().into_owned(),"/tmp/.X11-unix".into()]);
  (None,x)
 };
 let private=owned.0.join("private");fs::create_dir(&private).map_err(|e|e.to_string())?;
 for (i,p) in private_files(n)?.iter().enumerate() {
  if tmp.is_some()&&p.starts_with("/tmp") {continue;}
  let copy=private.join(format!("{i}-{}",p.file_name().unwrap().to_string_lossy()));fs::write(&copy,b"").map_err(|e|e.to_string())?;
  sandbox.extend(["--bind".into(),copy.to_string_lossy().into_owned(),p.to_string_lossy().into_owned()]);
 }
 sandbox.extend(devices::bind_args(&input,&assigned)?);
 let device_list=assigned.iter().flat_map(|d|d.nodes()).map(|p|p.to_string_lossy().into_owned()).collect::<Vec<_>>().join(" ");
 let identity=[("MIOS_NESTED_SEAT",plan.seat.as_str()),("MIOS_NESTED_USER",plan.user.as_str()),("MIOS_NESTED_TARGET",plan.target.as_str()),("MIOS_NESTED_DEVICES",device_list.as_str())];
 let path=owned.0.join("hyprland.lua");fs::write(&path,config).map_err(|e|e.to_string())?;
 let mut child=spawn_sandboxed(&owned.0.join("desktop.log"),||{let mut c=Command::new("bwrap");c.args(&sandbox).args(["--","/usr/bin/Hyprland","--config"]).arg(&path)
 .envs(identity).env("MIOS_USER_TOML",&user).env("MIOS_USER_TOML_D",&fragments).env("XDG_RUNTIME_DIR",&owned.0).env("WAYLAND_DISPLAY",&display).env("AQ_DRM_DEVICES","/dev/null").env("WLR_DRM_DEVICES","/dev/null").env_remove("HYPRLAND_INSTANCE_SIGNATURE").env_remove("DISPLAY").env_remove("TMUX").env_remove("TMUX_PANE");c})?;
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
  let shell_path=n["shell_entry"].as_str().ok_or("nested shell entry missing")?;
  let (isig,iwl)=(inst["instance"].as_str().ok_or("nested signature missing")?,inst["wl_socket"].as_str().ok_or("nested display missing")?);
  shell_child=Some(spawn_sandboxed(&owned.0.join("shell.log"),||{let mut c=Command::new("bwrap");c.args(&sandbox).args(["--","quickshell","--no-duplicate","--path",shell_path])
   .envs(identity).env("MIOS_USER_TOML",&user).env("MIOS_USER_TOML_D",&fragments).env("XDG_RUNTIME_DIR",&owned.0).env("HYPRLAND_INSTANCE_SIGNATURE",isig).env("WAYLAND_DISPLAY",iwl).env("QT_QPA_PLATFORM","wayland").env("MIOS_NESTED_RESIZE_POLL_MS",n["resize_poll_ms"].as_integer().unwrap_or(100).to_string()).env("PIPEWIRE_RUNTIME_DIR",&parent).env_remove("DISPLAY").env_remove("TMUX").env_remove("TMUX_PANE");c})?);

  let mut target=None;
  for _ in 0..100 {
   let v:Json=serde_json::from_str(&ipc(&parent,&signature,"j/clients")?).map_err(|e|e.to_string())?;
   if let Some(w)=v.as_array().and_then(|v|v.iter().find(|w|w["pid"].as_u64()==Some(pid))){target=Some(w.clone());break;}
   thread::sleep(Duration::from_millis(100));
  }
  let w=target.ok_or("parent could not identify the owned nested output")?;
  let address=w["address"].as_str().ok_or("nested address missing")?;let stable=w["stableId"].as_str().ok_or("nested stable id missing")?;
  let layout=plan.target.as_str();
  let edge=data["theme"]["shell"]["edge_margin_px"].as_float().or_else(||data["theme"]["shell"]["edge_margin_px"].as_integer().map(|n|n as f64)).ok_or("global edge margin missing")?;
  let mut room_window=false;
  if layout=="pip" {
   let pip=Command::new("/usr/bin/mios-pip-gravity").args(["on","--window",address,"--pid",&pid.to_string(),"--stable-id",stable]).status().map_err(|e|e.to_string())?;if !pip.success(){return Err("nested PiP adoption failed".into());}
  }else{
   let mut code=format!("local w=nil;for _,v in ipairs(hl.get_windows()) do if v.address=={} then w=v end end;if not w or w.pid~={pid} or w.stable_id~=tonumber({},16) then error('nested identity changed',0) end;local target={};{}hl.dispatch(hl.dsp.window.tag({{window=target,tag='+mios-pip-disabled'}}));",lua_str(address),lua_str(stable),lua_str(&format!("address:{address}")),
    // Unpin only a pinned window: Hyprland answers a pin change on a window that cannot be pinned with a warning.
    if w["pinned"].as_bool().unwrap_or(false){"hl.dispatch(hl.dsp.window.pin({window=target,action='disable'}));"}else{""});
   let monitors:Json=serde_json::from_str(&ipc(&parent,&signature,"j/monitors")?).map_err(|e|e.to_string())?;
   let own=monitors.as_array().and_then(|v|v.iter().find(|m|m["id"]==w["monitor"])).ok_or("nested parent monitor missing")?;
   match layout {
    "side-by-side"=>{
     let m=own;let num=|k:&str|m[k].as_f64().unwrap_or(0.0);let scale=num("scale").max(0.1);
     let reserved=|i:usize|m["reserved"][i].as_f64().unwrap_or(0.0);
     let width=(num("width")/scale/2.0-2.0*edge).max(1.0);let height=(num("height")/scale-reserved(1)-reserved(3)-2.0*edge).max(1.0);
     code+=&format!("hl.dispatch(hl.dsp.window.resize({{window=target,x={width},y={height},exact=true}}));hl.dispatch(hl.dsp.window.move({{window=target,x={},y={}}}));",num("x")+num("width")/scale/2.0+edge,num("y")+reserved(1)+edge);
    }
    "output"|"remote"=>{
     let name=target_output.as_ref().and_then(|m|m["name"].as_str()).ok_or("target output missing")?;
     code+=&format!("hl.dispatch(hl.dsp.window.move({{monitor={},window=target}}));hl.dispatch(hl.dsp.window.fullscreen({{window=target,mode='fullscreen',action='set'}}));",lua_str(name));
    }
    "hypr3d"=>{
     let (x,y,bw,bh)=room_box(own,number(n,"width")? as f64,number(n,"height")? as f64,edge);
     code+=&format!("hl.dispatch(hl.dsp.window.resize({{window=target,x={bw},y={bh},exact=true}}));hl.dispatch(hl.dsp.window.move({{window=target,x={x},y={y}}}));");
    }
    _=>{}
   }
   eval(&parent,&signature,&code)?;
   if let Some(m)=&target_output {
    // The session is placed only when the parent reports it fullscreen on that output.
    let mut ok=false;
    for _ in 0..40 {
     let v:Json=serde_json::from_str(&ipc(&parent,&signature,"j/clients")?).map_err(|e|e.to_string())?;
     if v.as_array().into_iter().flatten().any(|c|c["address"].as_str()==Some(address)&&c["monitor"]==m["id"]&&c["fullscreen"].as_i64().unwrap_or(0)>0){ok=true;break;}
     thread::sleep(Duration::from_millis(100));
    }
    if !ok {return Err(format!("the nested window did not become fullscreen on {}",m["name"]));}
   }
   if layout=="hypr3d" {
    let active=eval(&parent,&signature,"assert(hl.plugin.hypr3d and hl.plugin.hypr3d.active(), 'room closed')").is_ok();
    if !active {
     if !open_room {return Err("the Hypr3D room closed before the session joined it".into());}
     crate::render::render_hyprland::toggle_3d(root)?;
    }
    let probe=format!("local j=hl.plugin.hypr3d.export_layout();assert(string.find(j,{},1,true),'nested window is not a room window')",lua_str(&format!("\"id\":\"{address}\"")));
    for _ in 0..50 { if eval(&parent,&signature,&probe).is_ok(){room_window=true;break;} thread::sleep(Duration::from_millis(100)); }
    if !room_window {return Err("the nested window did not join the Hypr3D room".into());}
   }
  }
  // Only return focus if this launch itself took it. A later operator focus change wins.
  if !["output","remote","hypr3d"].contains(&layout) {
  if let (Some(a),Some(p),Some(stable))=(focused["address"].as_str(),focused["pid"].as_u64(),focused["stableId"].as_str()) {
   let restore=format!("local current=hl.get_active_window();if current and current.pid=={pid} then for _,v in ipairs(hl.get_windows()) do if v.address=={} and v.pid=={p} and v.stable_id==tonumber({},16) then hl.dispatch(hl.dsp.focus({{window={}}}));break end end end",lua_str(a),lua_str(stable),lua_str(&format!("address:{a}")));
   let _=ipc(&parent,&signature,&format!("/eval {restore}"))?;
  }
  }
  let device_json:Vec<Json>=assigned.iter().map(Device::to_json).collect();
  let controllers=assigned.iter().any(|d|d.kind=="controller");
  let tmux_socket=tmux_root.join(format!("tmux-{}/{}",fs::metadata(&owned.0).map_err(|e|e.to_string())?.uid(),socket_name));
  let mut x11_display=None;
  for _ in 0..40 {x11_display=x11_socket(&x11);if x11_display.is_some(){break;}thread::sleep(Duration::from_millis(50));}
  let mut descriptor=json!({"runtime":owned.0,"instance":inst,"x11_display":x11_display,"parent_window":address,"layout":layout,"target":layout,"seat":plan.seat,"user":plan.user,"scale_mode":plan.scale_mode,"isolated_input":true,"devices":device_json,"physical_controllers_assigned":controllers,"registry":claim.path,"tmux_socket":tmux_socket,"terminal_session":terminal_session,
   // /tmp: "shared" or the session's private /tmp on the host; pad_state is where an input bridge writes this session's
   // own Hypr3D controller packet (the room inside the session reads /tmp/mios-hypr3d-pad.bin there).
   "tmp":tmp.as_ref().map(|t|json!(t)).unwrap_or(json!("shared")),"pad_state":tmp.as_ref().map(|t|json!(t.join("mios-hypr3d-pad.bin"))).unwrap_or(Json::Null)});
  if let Some(m)=&target_output {descriptor["output"]=m["name"].clone();}
  // What a remote server needs to share this session itself (mirror its only output), with no parent output at all:
  // the native RDP server's nested mode reads runtime + instance from this descriptor.
  let session_output=ipc(&owned.0,inst["instance"].as_str().unwrap_or_default(),"j/monitors").ok().and_then(|t|serde_json::from_str::<Json>(&t).ok()).and_then(|m|m.as_array().and_then(|v|v.first()).and_then(|m|m["name"].as_str().map(str::to_owned)));
  descriptor["serve"]=json!({"mode":"mirror","runtime":owned.0,"hypr_instance":inst["instance"],"wayland_display":inst["wl_socket"],"output":session_output});
  if layout=="remote" {descriptor["remote"]=json!({"output":target_output.as_ref().map(|m|m["name"].clone()),"server":data["remote"]["rdp"]["server"].clone()});}
  if layout=="hypr3d" {descriptor["room_window"]=json!(room_window);}
  fs::write(owned.0.join("session.json"),format!("{descriptor}\n")).map_err(|e|e.to_string())?;
  claim.update(json!({"state":"running","runtime":owned.0,"instance":inst["instance"],"wayland_display":inst["wl_socket"],"compositor_pid":pid,"parent_window":address,"output":descriptor.get("output").cloned().unwrap_or(Json::Null),"tmp":descriptor["tmp"].clone(),"pad_state":descriptor["pad_state"].clone(),"descriptor":owned.0.join("session.json")}))?;
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
 drop(claim);
 result
}
#[cfg(test)]
mod tests {
 use super::*;
 fn ssot()->Value{toml::from_str::<Value>(&fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).ancestors().nth(3).unwrap().join("usr/share/mios/mios.toml")).unwrap()).unwrap()}
 #[test] fn nested_projection_keeps_shell_and_system_manager_separate(){
  let data=ssot();
  let out=project_for(&data,None).unwrap();assert!(!out.contains("name\"] = \"mios-placement"));assert!(!out.contains("systemctl --user restart mios-desktop.target"));assert!(!out.contains("dbus-update-activation-environment"));assert!(!out.contains("/usr/share/mios/quickshell/Config.qml"));assert!(out.contains("Nested.qml"));assert!(out.contains("no_hardware_cursors\"] = 1"));
 }
 #[test] fn nested_projection_turns_off_the_automatic_scale_policy(){
  let mut d:Value=toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap();
  d["desktop"]["scaling"]["apply_to"]=Value::Array(vec![Value::String("local".into())]);
  let lua=project_for(&d,None).unwrap();
  assert!(lua.contains("local nested_fit={mode='fit'"),"fit table projected");
  assert!(lua.contains("function mios_nested_zoom("),"zoom hook projected");
  assert!(!lua.contains("apply_to = {\"local\"}")&&!lua.contains("applies = {local = true"),"automatic policy off in the nested session");
 }
 #[test] fn nested_fit_scaling_is_projected_and_validated(){
  let ok:Value=toml::from_str("scale_mode='fit'\nscale_min=0.25\nscale_max=2.0").unwrap();
  assert_eq!(nested_scaling(&ok).unwrap(),("fit".into(),0.25,2.0));
  assert_eq!(nested_scaling(&toml::from_str::<Value>("").unwrap()).unwrap().0,"fit","fit is the default");
  for bad in ["scale_mode='stretch'","scale_min=0","scale_max=9","scale_min=2.0\nscale_max=1.0","scale_min='x'"] {
   assert!(nested_scaling(&toml::from_str::<Value>(bad).unwrap()).is_err(),"{bad}");
  }
 }
 #[test] fn nested_fit_scale_keeps_the_logical_desktop_in_lua(){
  let lua=["/usr/bin/luajit","/usr/bin/lua"].into_iter().map(std::path::Path::new).find(|p|p.is_file()).expect("a Lua interpreter");
  let src=format!("local nested_fit={{mode='fit',w=1280,h=800,min=0.25,max=2}}\n{NESTED_SCALE_LUA}\nfor _,v in ipairs({{{{464,334}},{{1280,800}},{{2560,1600}},{{100,60}},{{9000,9000}},{{1100,2080}}}}) do local s=nested_scale(v[1],v[2]) io.write(s,' ',v[1]/s,' ',v[2]/s,'\\n') end\nnested_fit.mode='native' io.write(nested_scale(464,334),' 0 0\\n')");
  let out=std::process::Command::new(lua).arg("-e").arg(&src).output().unwrap();
  assert!(out.status.success(),"{}",String::from_utf8_lossy(&out.stderr));
  let rows:Vec<Vec<f64>>=String::from_utf8_lossy(&out.stdout).lines().map(|l|l.split_whitespace().map(|v|v.parse().unwrap()).collect()).collect();
  assert_eq!(rows.len(),7);
  let whole=|x:f64|(x-x.round()).abs()<1e-6;
  for r in &rows[..6] { assert!(whole(r[0]*120.0) && whole(r[1]) && whole(r[2]),"1/120 step with whole logical size: {r:?}"); }
  assert!((rows[0][0]-0.36).abs()<0.05 && rows[0][1]>1200.0,"a 464x334 PiP shows the ~1280-wide desktop scaled down: {:?}",rows[0]);
  assert_eq!(rows[1],vec![1.0,1280.0,800.0]);
  assert_eq!(rows[2],vec![2.0,1280.0,800.0]);
  assert!(rows[3][0]>=0.25,"clamped low: {:?}",rows[3]);
  assert!(rows[4][0]<=2.0,"clamped high: {:?}",rows[4]);
  assert_eq!(rows[6][0],1.0,"native maps pixels 1:1");
 }
 #[test] fn nested_resize_interval_is_validated(){
  let mut data=ssot();
  for value in [20,100,5000] { data["desktop"]["nested"]["resize_poll_ms"]=Value::Integer(value);assert!(project_for(&data,None).is_ok()); }
  for value in [0,19,5001] { data["desktop"]["nested"]["resize_poll_ms"]=Value::Integer(value);assert!(project_for(&data,None).unwrap_err().contains("resize_poll_ms")); }
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
 #[test] fn nested_x11_display_is_the_numbered_socket_not_the_fallback(){
  let d=std::env::temp_dir().join(format!("mios-nested-x11-{}",std::process::id()));let _=fs::remove_dir_all(&d);fs::create_dir(&d).unwrap();
  assert_eq!(x11_socket(&d),None,"no XWayland yet");
  for f in ["X12_","X12","notes","X3x"] {fs::write(d.join(f),"").unwrap();}
  assert_eq!(x11_socket(&d),Some(format!("unix:{}",d.join("X12").display())));
  let _=fs::remove_dir_all(&d);
 }
 #[test] fn argv_is_data_not_shell_source(){let v:Value=toml::from_str("terminal_argv=['alacritty', 'a; touch /bad', \"x'y\"]").unwrap();assert_eq!(command(&v,"terminal_argv").unwrap(),"'alacritty' 'a; touch /bad' 'x'\\''y'");}

 #[test] fn targets_resolve_from_cli_then_seat_preset_then_ssot(){
  let mut d=ssot();
  let p=plan(&d,&Options::default()).unwrap();
  assert_eq!((p.target.as_str(),p.seat.as_str(),p.tmux_socket.as_str(),p.scale_mode.as_str()),("pip","","mios-nested","fit"),"the unlabelled default session is unchanged");
  assert!(p.specs.is_empty(),"no device is assigned by default");
  d["desktop"]["nested"]["seats"]=toml::from_str::<Value>("[p2]\nuser='Player Two'\ntarget='output'\noutput='HDMI-A-1'\ndevices=['virtual-pad']").unwrap();
  let p=plan(&d,&Options{seat:Some("p2".into()),..Default::default()}).unwrap();
  assert_eq!((p.target.as_str(),p.output.as_deref(),p.user.as_str(),p.tmux_socket.as_str(),p.scale_mode.as_str()),("output",Some("HDMI-A-1"),"Player Two","mios-nested-p2","native"));
  assert_eq!(p.specs,vec!["virtual-pad".to_owned()]);
  let p=plan(&d,&Options{seat:Some("p2".into()),target:Some("hypr3d".into()),devices:vec!["/dev/input/event4".into()],..Default::default()}).unwrap();
  assert_eq!((p.target.as_str(),p.scale_mode.as_str(),p.specs.len(),p.output.as_deref()),("hypr3d","fit",1,None),"CLI wins over the preset; the preset's output is dropped for a target without one");
  assert!(plan(&d,&Options{seat:Some("p2".into()),no_devices:true,..Default::default()}).unwrap().specs.is_empty());
  assert_eq!(plan(&d,&Options{seat:Some("p3".into()),..Default::default()}).unwrap().user,"p3","the seat labels its user when none is named");
  assert_eq!(plan(&d,&Options{target:Some("remote".into()),..Default::default()}).unwrap().scale_mode,"native");
 }
 #[test] fn invalid_plans_are_refused_before_anything_starts(){
  let d=ssot();
  let e=|o:Options|plan(&d,&o).unwrap_err();
  assert!(e(Options{target:Some("output".into()),..Default::default()}).contains("needs an output"));
  assert!(e(Options{target:Some("pip".into()),output:Some("DP-1".into()),..Default::default()}).contains("applies to the output and remote"));
  assert!(e(Options{target:Some("tv".into()),..Default::default()}).contains("must be one of"));
  assert!(e(Options{seat:Some("Bad Seat".into()),..Default::default()}).contains("seat"));
  assert!(e(Options{devices:vec!["virtual-pad".into()],..Default::default()}).contains("needs a seat"));
  assert!(e(Options{target:Some("output".into()),output:Some("DP\"1".into()),..Default::default()}).contains("not valid"));
  assert!(e(Options{user:Some("a\nb".into()),..Default::default()}).contains("user label"));
  let mut bad=d.clone();bad["desktop"]["nested"]["targets"]["remote"]["scale_mode"]=Value::String("stretch".into());
  assert!(plan(&bad,&Options{target:Some("remote".into()),..Default::default()}).unwrap_err().contains("targets.remote.scale_mode"));
 }
 #[test] fn target_scale_mode_reaches_the_projection(){
  let d=ssot();
  assert!(project_for(&d,Some("native")).unwrap().contains("local nested_fit={mode='native'"));
  assert!(project_for(&d,None).unwrap().contains("local nested_fit={mode='fit'"));
  assert!(project_for(&d,Some("zoom")).is_err());
 }
 #[test] fn outputs_are_picked_by_name_then_prefix_and_never_unusable(){
  let m:Json=json!([{"id":0,"name":"DP-3","width":1920,"height":1080},{"id":1,"name":"HEADLESS-2","width":0,"height":0},{"id":2,"name":"HEADLESS-MIOS-RDP-1","width":1100,"height":2080},{"id":3,"name":"HEADLESS-MIOS-RDP","width":1100,"height":2080,"disabled":true}]);
  let s=|v:&[&str]|v.iter().map(|s|s.to_string()).collect::<Vec<_>>();
  assert_eq!(pick_output(&m,&s(&["HEADLESS-MIOS-RDP"]),&s(&["HEADLESS-"])).unwrap()["id"],2,"a disabled exact name loses to a usable prefix match; a 0x0 output is skipped");
  assert_eq!(pick_output(&m,&s(&["DP-3"]),&[]).unwrap()["id"],0);
  assert!(pick_output(&m,&s(&["HDMI-A-1"]),&[]).is_none());
  assert!(pick_output(&m,&[],&s(&["WAYLAND-"])).is_none());
 }
 #[test] fn room_box_fits_the_work_area_at_one_texel_per_pixel(){
  let m=json!({"x":1920,"y":0,"width":1920,"height":1080,"scale":1.0,"reserved":[0,44,0,72]});
  assert_eq!(room_box(&m,1280.0,800.0,8.0),(1920.0+8.0+312.0,44.0+8.0+((1080.0-116.0-16.0-800.0)/2.0f64).floor(),1280.0,800.0),"fits unscaled, centred");
  let small=json!({"x":0,"y":0,"width":1280,"height":800,"scale":1.0,"reserved":[0,44,0,72]});
  let (_,_,w,h)=room_box(&small,1280.0,800.0,8.0);
  assert!(w<=1280.0-16.0&&h<=800.0-116.0-16.0&&((w/h)-1.6).abs()<0.01,"shrunk into the work area, aspect kept: {w}x{h}");
  let hidpi=json!({"x":0,"y":0,"width":3840,"height":2160,"scale":2.0,"reserved":[0,0,0,0]});
  assert_eq!(room_box(&hidpi,1280.0,800.0,8.0).2,1280.0,"logical size on a scaled output");
 }
 #[test] fn a_session_inside_a_session_keeps_its_sockets_in_budget(){
  let top=Path::new("/run/user/1000");
  assert_eq!(session_runtime(top,4242),(top.join("mios-nested-desktop-4242"),None),"a session of the desktop lives in its runtime");
  let p1=top.join("mios-nested-desktop-3073595");
  let (rt,link)=session_runtime(&p1,3079838);
  assert_eq!((rt.clone(),link),(top.join("mios-nested-desktop-3079838"),Some(p1.join("mios-nested-desktop-3079838"))),"a session inside a session moves to the outermost runtime, linked from its parent");
  let sock=rt.join("hypr").join("release-v0.56.2_1793345060_1976310160").join(".socket2.sock");
  assert!(sock.as_os_str().len()<=107,"{} bytes",sock.as_os_str().len());
  // negative control: the naive placement overflows sun_path
  assert!(p1.join("mios-nested-desktop-3079838/hypr/release-v0.56.2_1793345060_1976310160/.socket2.sock").as_os_str().len()>107);
 }
 #[test] fn private_tmp_follows_the_seat_policy(){
  let t=|v:&str|toml::from_str::<Value>(v).unwrap();
  assert!(!private_tmp(&t(""),"").unwrap()&&private_tmp(&t(""),"p2").unwrap(),"default seat: labelled seats only");
  assert!(private_tmp(&t("private_tmp='always'"),"").unwrap()&&!private_tmp(&t("private_tmp='never'"),"p2").unwrap());
  assert!(private_tmp(&t("private_tmp='yes'"),"p2").is_err());
  assert_eq!(ssot()["desktop"]["nested"]["private_tmp"].as_str(),Some("seat"));
 }
 #[test] fn only_the_bwrap_rename_race_is_retried(){
  assert!(bwrap_bind_race("bwrap: Can't bind mount /dev/null on /tmp/mios-hypr3d-pad.bin: Unable to remount destination \"/newroot/tmp/mios-hypr3d-pad.bin\" with correct flags: Invalid argument"));
  assert!(!bwrap_bind_race("bwrap: Can't find source path /x: No such file or directory"));
  assert!(!bwrap_bind_race("[ERR] Hyprland exited"));
 }
 #[test] fn private_files_are_absolute_files_outside_dev_and_proc(){
  let ok:Value=toml::from_str("private_files=['/tmp/hypr3d-status.txt']").unwrap();
  assert_eq!(private_files(&ok).unwrap(),vec![PathBuf::from("/tmp/hypr3d-status.txt")]);
  for bad in ["private_files=['tmp/x']","private_files=['/dev/input/event1']","private_files=['/proc/self/x']","private_files=['/tmp/../etc/passwd']"] {
   assert!(private_files(&toml::from_str::<Value>(bad).unwrap()).is_err(),"{bad}");
  }
  assert!(!private_files(&ssot()["desktop"]["nested"]).unwrap().is_empty(),"the SSOT privatizes the room plugin's state files");
 }
}
