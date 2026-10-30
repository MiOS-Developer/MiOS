// AI-hint: Project SSOT global remaps and native compositor shortcut-inhibition escapes.
// AI-related: keybindings.global, mios-inputd/src/vm.rs, render.rs
use mios_resolver::keybindings::normalize_chord;
use toml::Value;
fn at<'a>(v: &'a Value, path: &str) -> Option<&'a Value> { path.split('.').try_fold(v, |v,k|v.get(k)) }
fn canonical(v: &str, sep: &str) -> Result<String,String> {
 let normalized=normalize_chord(v,sep)?;
 Ok(if let Some((mods,key))=normalized.rsplit_once('+') {format!("{mods}+{}",key.to_lowercase())} else {normalized.to_lowercase()})
}
fn q(v: &str) -> String { serde_json::to_string(v).unwrap() }
pub fn key(data: &Value, id: &str, fallback: &str) -> String {
    let selected=at(data,"keybindings.global.bindings").and_then(Value::as_array).into_iter().flatten()
        .find(|b|b.get("id").and_then(Value::as_str)==Some(id))
        .and_then(|b|b.get("chord")).and_then(Value::as_str).unwrap_or(fallback);
    if normalize_chord(selected,"\0").ok()==normalize_chord(fallback,"\0").ok() {fallback.into()} else {selected.into()}

}
/// The modifier mask of [keybindings.global].bypass_mod in canonical order (e.g. "SUPER+CTRL").
fn bypass_mods(data:&Value,sep:&str)->Result<String,String>{
    let m=at(data,"keybindings.global.bypass_mod").and_then(Value::as_str).ok_or("missing bypass_mod")?;
    Ok(canonical(&format!("{m}+x"),sep)?.trim_end_matches("+x").to_owned())
}
/// The keysyms of one modifier, as the compositor names them.
fn modifier_keys(m:&str)->&'static [&'static str]{
    match m {"SUPER"=>&["Super_L","Super_R"],"CTRL"=>&["Control_L","Control_R"],"ALT"=>&["Alt_L","Alt_R"],"SHIFT"=>&["Shift_L","Shift_R"],_=>&[]}
}
/// `trigger = "release"` rows: a modifier-only chord written as its modifiers plus one of their own keys
/// ("SUPER+CTRL+Super_L"). Returns every release handle the chord needs: each modifier key of the set, with the
/// whole set held, so the chord fires on whichever key is released first. None for an ordinary (press) row.
pub fn release_chords(binding:&Value,chord:&str,sep:&str)->Result<Option<Vec<String>>,String>{
    let id=binding.get("id").and_then(Value::as_str).unwrap_or("?");
    match binding.get("trigger").and_then(Value::as_str) {
        None|Some("press")=>return Ok(None),
        Some("release")=>{}
        Some(other)=>return Err(format!("binding {id}: trigger {other:?} must be press or release")),
    }
    if chord.contains(sep) {return Err(format!("binding {id}: a release chord is one step"));}
    let (mods,leaf)=chord.rsplit_once('+').ok_or_else(||format!("binding {id}: a release chord needs its modifiers"))?;
    let mods:Vec<&str>=mods.split('+').collect();
    let owns=mods.iter().any(|m|modifier_keys(m).iter().any(|k|k.eq_ignore_ascii_case(leaf)));
    if !owns || mods.iter().any(|m|modifier_keys(m).is_empty()) {
        return Err(format!("binding {id}: trigger = \"release\" is for modifier-only chords: the key must be one of the chord's own modifier keys (e.g. SUPER+CTRL+Super_L)"));
    }
    let prefix=mods.join("+");
    Ok(Some(mods.iter().flat_map(|m|modifier_keys(m).iter().map(|k|format!("{prefix}+{k}"))).collect()))
}
pub fn prelude(data: &Value) -> Result<String,String> {
    if at(data,"keybindings.global.enabled").and_then(Value::as_bool)!=Some(true) {return Ok(String::new());}
    // A local hl proxy captures only handles created by this projection. No
    // monkey-patching the compositor API or rewriting another client's bindings.
    Ok(r#"
-- MiOS global policy: native handles retain app shortcut inhibition by default.
local mios_native_hl = hl
local mios_input_handles = {}
local hl = setmetatable({}, {__index=mios_native_hl})
hl.bind = function(key, dispatcher, flags)
 local handle = mios_native_hl.bind(key, dispatcher, flags)
 if handle then table.insert(mios_input_handles, {handle=handle,key=key,dispatcher=dispatcher,flags=flags}) end
 return handle
end
"#.into())
}
pub fn finish(data: &Value) -> Result<String,String> {
    if at(data,"keybindings.global.enabled").and_then(Value::as_bool)!=Some(true) {return Ok(String::new());}
    let bindings=at(data,"keybindings.global.bindings").and_then(Value::as_array).ok_or("missing global bindings")?;
    let sep=at(data,"keybindings.global.chord_sep").and_then(Value::as_str).ok_or("missing chord separator")?;
    let mut out=String::from("\nmios_section(\"global-input\", function()\n local policies={}\n local escapes={}\n");
    out+=&format!(" local capture_fullscreen={}\n local capture_tag={}\n",at(data,"keybindings.global.controller.capture_fullscreen").and_then(Value::as_bool).unwrap_or(false),q(at(data,"keybindings.global.controller.capture_tag").and_then(Value::as_str).unwrap_or("")));
    for binding in bindings {
        if binding.get("scope").and_then(Value::as_str)==Some("app") {continue;}
        let chord=binding.get("chord").and_then(Value::as_str).ok_or("binding chord missing")?;
        if chord.starts_with("BTN_") {continue;}
        let chord=canonical(chord,sep)?;
        if chord.contains(sep) {return Err("host multi-step chords require an explicit native submap".into());}
        let contexts=binding.get("contexts").and_then(Value::as_array).cloned().unwrap_or_default();
        let pass=binding.get("passthrough").and_then(Value::as_array).cloned().unwrap_or_default();
        let array=|v:Vec<Value>| -> Result<String,String> { Ok(format!("{{{}}}",v.iter().map(|v|v.as_str().map(q).ok_or("context must be a string")).collect::<Result<Vec<_>,_>>()?.join(","))) };
        out+=&format!(" policies[{}]={{contexts={},passthrough={}}}\n",q(&chord),array(contexts)?,array(pass)?);
        if let Some(bypass)=binding.get("bypass").and_then(Value::as_str) {
            out+=&format!(" escapes[{}]={}\n",q(&chord),q(&canonical(bypass,sep)?));
        }
        if let Some(keys)=release_chords(binding,&chord,sep)? {
            // A modifier-only chord: every modifier key of the chord is a release handle (one fires per chord, in
            // either press order; none fires once another key was pressed, so SUPER+CTRL+<key> binds still work).
            let action=binding.get("action").and_then(Value::as_str).ok_or("binding action missing")?;
            let quoted=format!("'{}'",action.replace('\'', "'\\''"));
            let escape=chord.rsplit_once('+').map(|(m,_)|m)==Some(bypass_mods(data,sep)?.as_str());
            for k in keys {
                if canonical(&k,sep)?!=chord {out+=&format!(" policies[{}]=policies[{}]\n",q(&canonical(&k,sep)?),q(&chord));}
                out+=&format!(" local b=mios_native_hl.bind({},mios_native_hl.dsp.exec_cmd({}),{{release=true,dont_inhibit=true,submap_universal=true}})\n if b then table.insert(mios_input_handles,{{handle=b,key={},escape={escape}}}) end\n",q(&k),q(&format!("/usr/libexec/mios/mios-inputd dispatch {quoted}")),q(&k));
            }
            continue;
        }
        if binding.get("tier").and_then(Value::as_integer)!=Some(2) && !["app-drawer","terminal","close-window","toggle-placement","fullscreen","hypr3d-toggle","keymap"].contains(&binding.get("id").and_then(Value::as_str).unwrap_or("")) {
            let action=binding.get("action").and_then(Value::as_str).ok_or("binding action missing")?;
            let quoted=format!("'{}'",action.replace('\'', "'\\''"));
            out+=&format!(" local b=mios_native_hl.bind({},mios_native_hl.dsp.exec_cmd({}))\n if b then table.insert(mios_input_handles,{{handle=b,key={}}}) end\n",q(&chord),q(&format!("/usr/libexec/mios/mios-inputd dispatch {quoted}")),q(&chord));
        }
        if binding.get("tier").and_then(Value::as_integer)==Some(2) {
            let action=binding.get("action").and_then(Value::as_str).ok_or("VM action missing")?;
            if action=="mios:vm-command-modifier" {
                let classes=at(data,"keybindings.global.vm.looking_glass_classes").and_then(Value::as_array).ok_or("Looking Glass classes missing")?;
                let key=at(data,"keybindings.global.vm.looking_glass_escape_key").and_then(Value::as_str).ok_or("Looking Glass command key missing")?;
                let classes=classes.iter().map(|v|v.as_str().map(q).ok_or("invalid VM class")).collect::<Result<Vec<_>,_>>()?.join(",");
                out+=&format!(" local lg_classes={{{classes}}}\n local lg_key={}\n",q(key));
                out+=r#" local held=nil
 local function release_vm_command()
  local h=held; held=nil
  if not h then return end
  for _,w in ipairs(hl.get_windows() or {}) do
   if w.address==h.address and w.pid==h.pid then
    pcall(function() hl.dispatch(hl.dsp.send_key_state({mods='',key=lg_key,state='up',window='address:'..h.address})) end)
   end
  end
 end
 local function press_vm_command()
  if held then return end
  local w=hl.get_active_window(); if not w then return end
  local allowed=false; for _,class in ipairs(lg_classes) do if w.class==class then allowed=true end end
  if not allowed then return end
  held={address=w.address,pid=w.pid}
  hl.dispatch(hl.dsp.send_key_state({mods='',key=lg_key,state='down',window='address:'..w.address}))
 end
 hl.on('window.active',release_vm_command)
 hl.on('config.reloaded',release_vm_command)
"#;
                out+=&format!(" local down=mios_native_hl.bind({},press_vm_command,{{dont_inhibit=true,submap_universal=true}})\n local up=mios_native_hl.bind({},release_vm_command,{{release=true,dont_inhibit=true,submap_universal=true}})\n if down then table.insert(mios_input_handles,{{handle=down,key={},escape=true}}) end\n",q(&chord),q(&chord),q(&chord));
                continue;
            }
            if !matches!(action,"mios:toggle_vm_passthrough"|"mios:vm-capture-toggle"|"mios:vm-fullscreen") {return Err(format!("unsupported native VM action {action}"));}
            let action=action.strip_prefix("mios:").unwrap();
            out+=&format!(" local b=mios_native_hl.bind({},mios_native_hl.dsp.exec_cmd({}),{{dont_inhibit=true,submap_universal=true}})\n if b then table.insert(mios_input_handles,{{handle=b,key={},escape=true}}) end\n",q(&chord),q(&format!("/usr/libexec/mios/mios-inputd vm-action {action}")),q(&chord));
        }
    }
    for name in ["vm-passthrough","game","terminal"] {
        if let Some(patterns)=at(data,&format!("keybindings.global.contexts.{name}")).and_then(Value::as_array) {
            let patterns=patterns.iter().map(|v|v.as_str().ok_or("class pattern must be a string")).collect::<Result<Vec<_>,_>>()?;
            if !patterns.is_empty() {out+=&format!(" hl.window_rule({{name={},match={{class={}}},tag={}}})\n",q(&format!("mios-input-{name}")),q(&format!("^({})$",patterns.join("|"))),q(&format!("+mios-input-{name}")));}
        }
    }
    out+=r#" local function canonical(key)
  key=key:gsub('%s','')
  local mods,leaf={},nil
  for token in key:gmatch('[^+]+') do
   local u=token:upper()
   if u=='SUPER' or u=='CTRL' or u=='ALT' or u=='SHIFT' then mods[u]=true else leaf=token:lower() end
  end
  local parts={}
  for _,m in ipairs({'SUPER','CTRL','ALT','SHIFT'}) do if mods[m] then table.insert(parts,m) end end
  table.insert(parts,leaf or '')
  return table.concat(parts,'+')
 end
 local function contains(list,value) for _,v in pairs(list or {}) do if type(v)=='string' and v:gsub('%*$','')==value then return true end end return false end
 local function context()
  local w=hl.get_active_window()
  local tags=w and w.tags or {}
  if (w and capture_fullscreen and tonumber(w.fullscreen)==2) or contains(tags,capture_tag) then return 'app-capture' end
  local ok,room=pcall(function() return hl.plugin.hypr3d.active() end)
  if ok and room then return 'desktop-3d' end
  if contains(tags,'mios-input-vm-passthrough') then return 'vm-passthrough' end
  if contains(tags,'mios-input-game') then return 'game' end
  if contains(tags,'mios-input-terminal') then return 'terminal' end
  return 'desktop-2d'
 end
 local function refresh()
  local ctx=context()
  for _,v in ipairs(mios_input_handles) do
   local p=policies[canonical(v.key)]
   local enabled=ctx~='vm-passthrough'
   if p then enabled=(#p.contexts==0 or contains(p.contexts,ctx)) and not contains(p.passthrough,ctx) end
   if ctx=='app-capture' or ctx=='vm-passthrough' then enabled=v.escape==true end
   v.handle:set_enabled(enabled)
  end
 end
 local extra={}
 for _,v in ipairs(mios_input_handles) do
  local escape=escapes[canonical(v.key)]
  if escape then
   local b=mios_native_hl.bind(escape,v.dispatcher,{dont_inhibit=true,submap_universal=true})
   if b then table.insert(extra,{handle=b,key=escape,escape=true}) end
  end
 end
 for _,v in ipairs(extra) do table.insert(mios_input_handles,v) end
 -- Expose read-only status for native integration tests and the global map UI.
 MIOS_INPUT_POLICY={handles=mios_input_handles,context=context,refresh=refresh}
 hl.on('window.active',refresh)
 hl.on('window.class',refresh)
 hl.on('window.fullscreen',refresh)
 hl.on('window.update_rules',refresh)
 refresh()
end)
"#;
    Ok(out)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn disabled_policy_is_inert() {let d:Value=toml::from_str("[keybindings.global]\nenabled=false").unwrap();assert!(prelude(&d).unwrap().is_empty());assert!(finish(&d).unwrap().is_empty());}
    #[test] fn generated_policy_uses_native_handles_and_bypass_flags() {let d:Value=toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap();let lua=finish(&d).unwrap();assert!(lua.contains("dont_inhibit=true,submap_universal=true"));assert!(lua.contains("v.handle:set_enabled(enabled)"));assert!(lua.contains("mios-input-vm-passthrough"));assert!(lua.contains("vm-action toggle_vm_passthrough"));}
    #[test] fn remapping_has_one_source() {let mut d:Value=toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap();let b=d["keybindings"]["global"]["bindings"].as_array_mut().unwrap().iter_mut().find(|b|b["id"].as_str()==Some("fullscreen")).unwrap();b["chord"]=Value::String("SUPER+F10".into());assert_eq!(key(&d,"fullscreen","SUPER+F11"),"SUPER+F10");}    #[test]
    fn native_tag_context_and_hold_release_lifecycle() {
        let data:Value=toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap();
        let fixture=r#"
local window={address='0xabc',pid=42,class='looking-glass-client',tags={'mios-input-vm-passthrough*'}}
local events,hooks,handles={},{},{}
hl={plugin={},dsp={exec_cmd=function() return function() end end,send_key_state=function(t) return t end},get_active_window=function() return window end,get_windows=function() return {window} end,window_rule=function() end}
hl.dispatch=function(t) table.insert(events,t.state) end
hl.bind=function(key,dispatcher,flags)
 local h={enabled=true,dispatcher=dispatcher,flags=flags or {}}
 h.set_enabled=function(self,value) self.enabled=value end
 h.is_enabled=function(self) return self.enabled end
 table.insert(handles,h); return h
end
hl.on=function(event,fn) hooks[event]=hooks[event] or {};table.insert(hooks[event],fn) end
local function mios_section(_,fn) fn() end
"#;
        let checks=r#"
local p=MIOS_INPUT_POLICY
assert(p.context()=='vm-passthrough','native dynamic tag must match')
local ordinary,bypass,hold
for _,v in ipairs(p.handles) do
 if v.key=='SUPER+Space' then ordinary=v.handle end
 if v.key=='SUPER+CTRL+space' then bypass=v.handle end
 if v.key=='SUPER+CTRL+m' then hold=v.handle end
end
assert(ordinary and not ordinary.enabled)
assert(bypass and bypass.enabled and bypass.flags.dont_inhibit and bypass.flags.submap_universal)
hold.dispatcher();hold.dispatcher();assert(#events==1 and events[1]=='down')
for _,fn in ipairs(hooks['window.active']) do fn() end
assert(#events==2 and events[2]=='up','focus loss must release the original target')
for _,fn in ipairs(hooks['window.active']) do fn() end
assert(#events==2,'release must be idempotent')
window.tags={};window.class='Alacritty';p.refresh();assert(ordinary.enabled)
io.write('native policy controls passed')
"#;
        let text=format!("{fixture}\n{}\nhl.bind('SUPER+Space',function() end)\n{}\n{checks}",prelude(&data).unwrap(),finish(&data).unwrap());
        let dir=tempfile::tempdir().unwrap();let file=dir.path().join("policy.lua");std::fs::write(&file,text).unwrap();
        let output=std::process::Command::new("lua").arg(&file).output().expect("native Lua interpreter required");
        assert!(output.status.success(),"{}{}",String::from_utf8_lossy(&output.stdout),String::from_utf8_lossy(&output.stderr));
    }

    #[test]
    fn modifier_only_release_chord_binds_every_modifier_key_as_an_escape() {
        let d:Value=toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap();
        let lua=finish(&d).unwrap();
        for k in ["Super_L","Super_R","Control_L","Control_R"] {
            let line=format!("mios_native_hl.bind(\"SUPER+CTRL+{k}\",mios_native_hl.dsp.exec_cmd(\"/usr/libexec/mios/mios-inputd dispatch 'mios:tmux-os-launcher'\"),{{release=true,dont_inhibit=true,submap_universal=true}})");
            assert!(lua.contains(&line),"missing {line}\n{lua}");
        }
        assert_eq!(lua.matches("dispatch 'mios:tmux-os-launcher'").count(),4,"one handle per modifier key, nothing else");
        assert!(lua.contains("key=\"SUPER+CTRL+Control_R\",escape=true}"),"on bypass_mod: an escape handle");
        assert!(lua.contains("policies[\"SUPER+CTRL+control_l\"]=policies[\"SUPER+CTRL+super_l\"]"));
        // Negative control: the same row without trigger = "release" is one press bind on Super_L (it would fire on
        // the press and swallow every SUPER+CTRL+<key> chord typed after it).
        let mut press=d.clone();
        let row=press["keybindings"]["global"]["bindings"].as_array_mut().unwrap().iter_mut().find(|b|b["id"].as_str()==Some("tmux-os-launcher")).unwrap();
        row.as_table_mut().unwrap().remove("trigger");
        let plain=finish(&press).unwrap();
        assert_eq!(plain.matches("dispatch 'mios:tmux-os-launcher'").count(),1);
        assert!(!plain.contains("SUPER+CTRL+Control_L") && plain.contains("mios_native_hl.bind(\"SUPER+CTRL+super_l\",mios_native_hl.dsp.exec_cmd(\"/usr/libexec/mios/mios-inputd dispatch 'mios:tmux-os-launcher'\"))\n"),"a press row is one press handle: {plain}");
    }

    #[test]
    fn release_trigger_is_only_for_modifier_only_chords() {
        let row=|chord:&str,trigger:&str|->Value{toml::from_str(&format!("id='x'\nchord='{chord}'\ntrigger='{trigger}'")).unwrap()};
        assert_eq!(release_chords(&row("SUPER+CTRL+Super_L","release"),"SUPER+CTRL+super_l"," ").unwrap().unwrap().len(),4);
        assert_eq!(release_chords(&row("SUPER+Super_L","release"),"SUPER+super_l"," ").unwrap().unwrap(),["SUPER+Super_L","SUPER+Super_R"]);
        assert!(release_chords(&row("SUPER+CTRL+p","release"),"SUPER+CTRL+p"," ").is_err(),"a key that is no modifier of the chord");
        assert!(release_chords(&row("SUPER+Control_L","release"),"SUPER+control_l"," ").is_err(),"Control_L without CTRL in the set");
        assert!(release_chords(&row("SUPER+CTRL+Super_L","hold"),"SUPER+CTRL+super_l"," ").is_err());
        assert!(release_chords(&row("SUPER+CTRL+Super_L","press"),"SUPER+CTRL+super_l"," ").unwrap().is_none());
    }

    #[test]
    fn newly_registered_shortcut_is_projected_by_the_native_dispatcher() {
        let mut d:Value=toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap();
        let b:Value=toml::from_str("id='custom-browser'\ntier=1\nchord='SUPER+F8'\naction='mios:launch-browser'\nrealized_by='hyprland'\npassthrough=['vm-passthrough']").unwrap();
        d["keybindings"]["global"]["bindings"].as_array_mut().unwrap().push(b);
        let lua=finish(&d).unwrap();
        assert!(lua.contains("mios-inputd dispatch 'mios:launch-browser'"));
        assert!(lua.contains("mios_native_hl.bind(\"SUPER+f8\""));
    }

}
