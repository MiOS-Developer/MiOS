// AI-hint: Native per-window PiP ownership and reversible state, stored on the window itself, and the native stay-on-top policy (pinned layer; re-pin a lost pin; raise only above overlapping pinned windows).
// AI-related: mios-pip-gravity, Quickshell DockPopup.qml, desktop.pip_gravity
use serde_json::{json,Value};
pub const MANAGED:&str="mios-pip-managed";
pub const OPT_OUT:&str="mios-pip-disabled";
const ORIGIN:&str="mios-pip-origin:";
pub fn has_tag(w:&Value,tag:&str)->bool {w.get("tags").and_then(Value::as_array).is_some_and(|v|v.iter().filter_map(Value::as_str).any(|s|s.trim_end_matches('*')==tag))}
#[derive(Clone,Debug,PartialEq)]
pub struct Identity {pub address:String,pub pid:u64,pub stable:String}
impl Identity {
 pub fn read(w:&Value)->Result<Self,String> {
  let s=Self{address:w["address"].as_str().ok_or("window address missing")?.into(),pid:w["pid"].as_u64().filter(|p|*p>0).ok_or("window process missing")?,stable:w["stableId"].as_str().ok_or("native stable window id missing")?.into()};
  if !s.address.starts_with("0x") || s.address.len()<3 || !s.address[2..].bytes().all(|b|b.is_ascii_hexdigit()) || s.stable.is_empty() || !s.stable.bytes().all(|b|b.is_ascii_hexdigit()) {return Err("invalid native window identity".into());} Ok(s)
 }
 pub fn guard(&self)->String {format!("local w=nil;for _,v in ipairs(hl.get_windows()) do if v.address=={} then w=v;break end end; if not w or w.pid~={} or w.stable_id~=tonumber({},16) then error('PiP window identity changed; no action',0) end;local target={};local function dispatch(d) local r=hl.dispatch(d);if not r or not r.ok then error(r and r.error or 'PiP native action failed',0) end end;",q(&self.address),self.pid,q(&self.stable),q(&format!("address:{}",self.address)))}
 pub fn matches(&self,w:&Value)->bool {Self::read(w).is_ok_and(|v|v==*self)}
}
fn q(s:&str)->String {serde_json::to_string(s).unwrap()}
#[derive(Debug,PartialEq)]
pub struct Origin {pub floating:bool,pub pinned:bool,pub fullscreen:i64,pub rect:[i64;4]}
impl Origin {
 pub fn from_window(w:&Value)->Result<Self,String> {
  let n=|key:&str,i:usize| w.get(key).and_then(|v|v.get(i)).and_then(Value::as_i64).ok_or("native geometry missing");
  Ok(Self{floating:w["floating"].as_bool().ok_or("native float state missing")?,pinned:w["pinned"].as_bool().ok_or("native pin state missing")?,fullscreen:w["fullscreen"].as_i64().unwrap_or(0),rect:[n("at",0)?,n("at",1)?,n("size",0)?,n("size",1)?]})
 }
 pub fn tag(&self)->String {format!("{ORIGIN}{}:{}:{}:{}:{}:{}:{}",u8::from(self.floating),u8::from(self.pinned),self.fullscreen,self.rect[0],self.rect[1],self.rect[2],self.rect[3])}
 pub fn parse(tag:&str)->Result<Self,String> {
  let v=tag.strip_prefix(ORIGIN).ok_or("not a PiP origin tag")?.split(':').map(|v|v.parse::<i64>().map_err(|_|"invalid native PiP origin".to_string())).collect::<Result<Vec<_>,_>>()?;
  if v.len()!=7 || ![0,1].contains(&v[0]) || ![0,1].contains(&v[1]) || !(0..=3).contains(&v[2]) || v[5]<=0 || v[6]<=0 {return Err("invalid native PiP origin".into());}
  Ok(Self{floating:v[0]==1,pinned:v[1]==1,fullscreen:v[2],rect:[v[3],v[4],v[5],v[6]]})
 }
}
fn tag_op(tag:&str,on:bool)->String {format!("dispatch(hl.dsp.window.tag({{window=target,tag={}}}));",q(&format!("{}{tag}",if on{"+"}else{"-"})))}
pub fn plan(w:&Value,mode:&str,pip_width:f64)->Result<Option<String>,String> {
 let id=Identity::read(w)?;let managed=has_tag(w,MANAGED);
 let on=match mode {"on"=>true,"off"=>false,"toggle"=>!managed,_=>return Err("PiP mode must be on, off or toggle".into())};
 if managed==on {return Ok(None);}
 let mut lua=id.guard();
 if on {
  let origin=Origin::from_window(w)?;
  if !pip_width.is_finite() || pip_width<1.0 {return Err("invalid PiP size".into());}
  let width=pip_width.round() as i64;let height=(pip_width*origin.rect[3] as f64/origin.rect[2].max(1) as f64).round().max(1.0) as i64;
  lua+=&tag_op(&origin.tag(),true);lua+=&tag_op(OPT_OUT,false);
  lua+="dispatch(hl.dsp.window.fullscreen({window=target,mode='fullscreen',action='unset'}));dispatch(hl.dsp.window.float({window=target,action='enable'}));dispatch(hl.dsp.window.pin({window=target,action='enable'}));";
  lua+=&format!("dispatch(hl.dsp.window.resize({{window=target,x={width},y={height},exact=true}}));");lua+=&tag_op(MANAGED,true);lua+="dispatch(hl.dsp.window.alter_zorder({window=target,mode='top'}));";
 } else {
  let tags=w["tags"].as_array().ok_or("native window tags missing")?;
  let tag=tags.iter().filter_map(Value::as_str).find(|v|v.starts_with(ORIGIN)).ok_or("PiP original state missing; refusing a guessed restore")?;
  let origin=Origin::parse(tag)?;lua+=&tag_op(MANAGED,false);lua+=&tag_op(OPT_OUT,true);
  lua+="dispatch(hl.dsp.window.pin({window=target,action='disable'}));";
  lua+=&format!("dispatch(hl.dsp.window.resize({{window=target,x={},y={},exact=true}}));dispatch(hl.dsp.window.move({{window=target,x={},y={}}}));",origin.rect[2],origin.rect[3],origin.rect[0],origin.rect[1]);
  if origin.pinned {lua+="dispatch(hl.dsp.window.pin({window=target,action='enable'}));";}
  if !origin.floating {lua+="dispatch(hl.dsp.window.float({window=target,action='disable'}));";}
  if origin.fullscreen!=0 {lua+=&format!("dispatch(hl.dsp.window.fullscreen({{window=target,mode='{}',action='set'}}));",if origin.fullscreen==1{"maximized"}else{"fullscreen"});}
  lua+=&tag_op(tag,false);
 }
 lua+="error('MIOS_PIP_APPLIED',0)";Ok(Some(lua))
}
pub fn status(w:&Value)->Value {json!({"address":w["address"],"pid":w["pid"],"stableId":w["stableId"],"pip":has_tag(w,MANAGED)})}
fn rect(w:&Value)->Option<[f64;4]> {let n=|k:&str,i:usize|w.get(k).and_then(|v|v.get(i)).and_then(Value::as_f64);Some([n("at",0)?,n("at",1)?,n("size",0)?,n("size",1)?])}
fn overlaps(a:[f64;4],b:[f64;4])->bool {a[0]<b[0]+b[2]&&b[0]<a[0]+a[2]&&a[1]<b[1]+b[3]&&b[1]<a[1]+a[3]}
/// Native stay-on-top. Hyprland draws pinned floating windows after, and hit-tests them before, every unpinned window
/// (fullscreen and special workspaces included), so a managed PiP needs only (a) its pin and (b) to sit above the other
/// PINNED windows that overlap it. The client list is the native z-order (bottom first). Returns the indices of
/// floating, non-fullscreen managed PiPs that lost their pin, and of pinned PiPs covered by an overlapping pinned
/// non-PiP window. Unpinned windows never cause a raise (that only churned the z-order and the pointer focus).
pub fn stacking(clients:&Value)->(Vec<usize>,Vec<usize>) {
 let empty=Vec::new();let list=clients.as_array().unwrap_or(&empty);
 let shown=|w:&Value|w["mapped"]==true&&w["hidden"]==false;
 let (mut repin,mut raise)=(Vec::new(),Vec::new());
 for (i,w) in list.iter().enumerate() {
  if !has_tag(w,MANAGED)||!shown(w)||w["floating"]!=true||w["fullscreen"].as_i64().unwrap_or(0)!=0 {continue;}
  if w["pinned"]!=true {repin.push(i);continue;}
  let Some(r)=rect(w) else {continue};
  if list[i+1..].iter().any(|o|!has_tag(o,MANAGED)&&shown(o)&&o["pinned"]==true&&o["floating"]==true&&o["monitor"]==w["monitor"]&&rect(o).is_some_and(|q|overlaps(q,r))) {raise.push(i);}
 }
 (repin,raise)
}
#[cfg(test)]
mod tests {
 use super::*;
 fn win()->Value {json!({"address":"0xabc","pid":42,"stableId":"18000001","at":[120,180],"size":[960,692],"floating":true,"pinned":false,"fullscreen":0,"tags":[]})}
 #[test] fn origin_is_native_and_exactly_reversible(){let o=Origin::from_window(&win()).unwrap();assert_eq!(Origin::parse(&o.tag()).unwrap(),o);for t in ["bad","mios-pip-origin:0:0:0:1:2:0:30"] {assert!(Origin::parse(t).is_err());}}
 #[test] fn recycled_addresses_cannot_inherit_the_operation(){let w=win();let id=Identity::read(&w).unwrap();let mut recycled=w.clone();recycled["stableId"]=json!("18000002");assert!(!id.matches(&recycled));assert!(id.guard().contains("w.stable_id"));}
 fn c(addr:&str,pip:bool,pinned:bool,at:[i64;2],size:[i64;2])->Value {json!({"address":addr,"mapped":true,"hidden":false,"floating":true,"pinned":pinned,"fullscreen":0,"monitor":0,"at":at,"size":size,"tags":if pip {json!([MANAGED])} else {json!([])}})}
 #[test] fn unpinned_windows_never_raise_a_pinned_pip(){
  // A new floating dialog opens above the PiP in the list: the native pinned layer already keeps the PiP above it.
  let l=json!([c("0x1",true,true,[0,0],[400,300]),c("0x2",false,false,[100,100],[400,300])]);assert_eq!(stacking(&l),(vec![],vec![]));
 }
 #[test] fn an_overlapping_pinned_window_above_the_pip_raises_it_and_a_distant_one_does_not(){
  let l=json!([c("0x1",true,true,[0,0],[400,300]),c("0x2",false,true,[100,100],[400,300])]);assert_eq!(stacking(&l),(vec![],vec![0]));
  let l=json!([c("0x1",true,true,[0,0],[400,300]),c("0x2",false,true,[900,100],[400,300])]);assert_eq!(stacking(&l),(vec![],vec![]));
  let l=json!([c("0x2",false,true,[100,100],[400,300]),c("0x1",true,true,[0,0],[400,300])]);assert_eq!(stacking(&l),(vec![],vec![]),"already above");
  let l=json!([c("0x1",true,true,[0,0],[400,300]),c("0x3",true,true,[100,100],[400,300])]);assert_eq!(stacking(&l),(vec![],vec![]),"PiPs never fight each other");
 }
 #[test] fn a_floating_pip_that_lost_its_pin_is_reported_but_fullscreen_or_tiled_ones_are_not(){
  let mut fs=c("0x2",true,false,[0,0],[400,300]);fs["fullscreen"]=json!(2);
  let mut tiled=c("0x3",true,false,[0,0],[400,300]);tiled["floating"]=json!(false);
  let l=json!([c("0x1",true,false,[0,0],[400,300]),fs,tiled,c("0x4",false,false,[0,0],[400,300])]);assert_eq!(stacking(&l),(vec![0],vec![]));
 }
 #[test] fn toggle_keeps_original_flags_and_geometry_on_the_window(){let mut w=win();let on=plan(&w,"on",460.0).unwrap().unwrap();assert!(on.contains("mios-pip-origin:1:0:0:120:180:960:692"));w["tags"]=json!([MANAGED,Origin::from_window(&w).unwrap().tag()]);let off=plan(&w,"off",460.0).unwrap().unwrap();assert!(off.contains("x=960,y=692"));assert!(off.contains("x=120,y=180"));assert!(plan(&w,"on",460.0).unwrap().is_none());}
}
