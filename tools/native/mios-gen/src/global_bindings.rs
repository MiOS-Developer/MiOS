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
 local function contains(list,value) for _,v in pairs(list or {}) do if v==value then return true end end return false end
 local function context()
  local w=hl.get_active_window()
  local tags=w and w.tags or {}
  if contains(tags,'mios-input-vm-passthrough') then return 'vm-passthrough' end
  if contains(tags,'mios-input-game') then return 'game' end
  local ok,room=pcall(function() return hl.plugin.hypr3d.active() end)
  if ok and room then return 'desktop-3d' end
  if contains(tags,'mios-input-terminal') then return 'terminal' end
  return 'desktop-2d'
 end
 local function refresh()
  local ctx=context()
  for _,v in ipairs(mios_input_handles) do
   local p=policies[canonical(v.key)]
   local enabled=ctx~='vm-passthrough'
   if p then enabled=(#p.contexts==0 or contains(p.contexts,ctx)) and not contains(p.passthrough,ctx) end
   if v.escape and not p then enabled=true end
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
    #[test] fn remapping_has_one_source() {let mut d:Value=toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap();let b=d["keybindings"]["global"]["bindings"].as_array_mut().unwrap().iter_mut().find(|b|b["id"].as_str()==Some("fullscreen")).unwrap();b["chord"]=Value::String("SUPER+F10".into());assert_eq!(key(&d,"fullscreen","SUPER+F11"),"SUPER+F10");}
}
