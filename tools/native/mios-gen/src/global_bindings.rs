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
/// The canonical bypass_mod prefix ("SUPER+CTRL"): a chord whose canonical form starts with it plus "+" is an escape.
fn bypass_prefix(data: &Value) -> Result<String,String> {
    let m=at(data,"keybindings.global.bypass_mod").and_then(Value::as_str).ok_or("missing [keybindings.global].bypass_mod")?;
    let c=canonical(&format!("{m}+x"),"\0")?;
    Ok(c.strip_suffix("+x").ok_or("bypass_mod must be modifiers only")?.to_owned())
}
fn is_bypass(chord: &str, prefix: &str) -> bool { chord.strip_prefix(prefix).is_some_and(|r|r.starts_with('+')) }
/// Evaluate the literal part of a Lua string expression ("a" .. " + " .. "b"); None when any part is dynamic.
fn lua_literal(expr: &str) -> Option<String> {
    expr.split("..").map(|p|p.trim().strip_prefix('"')?.strip_suffix('"').map(str::to_owned)).collect()
}
/// The literal compositor binds of `lua` (`hl.bind("<chord>", <dispatcher>...)` lines): canonical chord -> the
/// dispatcher expressions bound on it. A global row whose chord is here gets no second dispatch bind when one of
/// those dispatchers realizes the row's own action (C-22); any other literal on its chord is a collision.
pub fn literal_binds(lua: &str, sep: &str) -> std::collections::BTreeMap<String,Vec<String>> {
    let mut out:std::collections::BTreeMap<String,Vec<String>>=std::collections::BTreeMap::new();
    for line in lua.lines() {
        let Some(rest)=line.trim_start().strip_prefix("hl.bind(") else {continue};
        let mut quoted=false; let mut escaped=false; let mut end=None;
        for (i,c) in rest.char_indices() {
            if escaped {escaped=false; continue}
            if c=='\\' && quoted {escaped=true; continue}
            if c=='"' {quoted=!quoted}
            if c==',' && !quoted {end=Some(i);break}
        }
        let Some(e)=end else {continue};
        if let Some(chord)=lua_literal(&rest[..e]) {
            // The dispatcher expression, without hl.bind's own closing parenthesis.
            let d=rest[e+1..].trim(); let d=d.strip_suffix(')').unwrap_or(d);
            if let Ok(c)=canonical(&chord,sep) { out.entry(c).or_default().push(d.trim().to_owned()); }
        }
    }
    out
}
/// Canonical chords the compositor already binds literally (the keys of [`literal_binds`]).
#[cfg(test)]
pub fn literal_chords(lua: &str, sep: &str) -> std::collections::BTreeSet<String> {
    literal_binds(lua,sep).into_keys().collect()
}
/// Whether a literal compositor `dispatcher` runs `binding`'s own action, i.e. the literal bind IS the row's
/// realization. The rule is shared with the gate (mios_resolver::keybindings::lua_dispatcher_realizes): exactly the
/// action's [keybindings.global.action_argv], `exec:mios-terminal` = the SSOT terminal action's command, any other
/// `exec:<prog>` = `prog` alone, and the native dispatchers of the hypr: verbs. A literal that merely mentions the
/// program (`alacritty -e ... --action system` for `exec:alacritty`) is another action (V1).
pub fn realizes(data: &Value, binding: &Value, dispatcher: &str) -> bool {
    binding.get("action").and_then(Value::as_str)
        .is_some_and(|action| mios_resolver::keybindings::lua_dispatcher_realizes(data,action,dispatcher))
}
pub fn prelude(data: &Value) -> Result<String,String> {
    if at(data,"keybindings.global.enabled").and_then(Value::as_bool)!=Some(true) {return Ok(String::new());}
    // A local hl proxy captures only handles created by this projection. No
    // monkey-patching the compositor API or rewriting another client's bindings.
    // Every handle on bypass_mod is an escape (C-36): it keeps working, uninhibited, in app-capture and vm-passthrough.
    Ok(format!(r#"
-- MiOS global policy: native handles retain app shortcut inhibition by default; bypass_mod handles are escapes.
local mios_native_hl = hl
local mios_input_handles = {{}}
local mios_input_bypass = {}
local function mios_input_canonical(key)
 key=key:gsub('%s','')
 local mods,leaf={{}},nil
 for token in key:gmatch('[^+]+') do
  local u=token:upper()
  if u=='SUPER' or u=='CTRL' or u=='ALT' or u=='SHIFT' then mods[u]=true else leaf=token:lower() end
 end
 local parts={{}}
 for _,m in ipairs({{'SUPER','CTRL','ALT','SHIFT'}}) do if mods[m] then table.insert(parts,m) end end
 table.insert(parts,leaf or '')
 return table.concat(parts,'+')
end
local function mios_input_is_bypass(key)
 local c=mios_input_canonical(key)
 return c:sub(1,#mios_input_bypass+1)==mios_input_bypass..'+'
end
local hl = setmetatable({{}}, {{__index=mios_native_hl}})
hl.bind = function(key, dispatcher, flags)
 local escape = mios_input_is_bypass(key)
 if escape then
  local f={{}}
  for k,v in pairs(flags or {{}}) do f[k]=v end
  f.dont_inhibit=true; f.submap_universal=true; flags=f
 end
 local handle = mios_native_hl.bind(key, dispatcher, flags)
 if handle then table.insert(mios_input_handles, {{handle=handle,key=key,dispatcher=dispatcher,flags=flags,escape=escape}}) end
 return handle
end
"#,q(&bypass_prefix(data)?)))
}
/// The global-input section. `literal` is the Lua emitted before it: rows whose chord it already binds get no
/// dispatch bind of their own (no double binds, C-22).
pub fn finish(data: &Value, literal: &str) -> Result<String,String> {
    if at(data,"keybindings.global.enabled").and_then(Value::as_bool)!=Some(true) {return Ok(String::new());}
    let bindings=at(data,"keybindings.global.bindings").and_then(Value::as_array).ok_or("missing global bindings")?;
    let sep=at(data,"keybindings.global.chord_sep").and_then(Value::as_str).ok_or("missing chord separator")?;
    let prefix=bypass_prefix(data)?;
    let bound=literal_binds(literal,sep);
    let ctl=|k:&str| at(data,&format!("keybindings.global.controller.{k}"));
    let mut out=String::from("\nmios_section(\"global-input\", function()\n local policies={}\n local escapes={}\n");
    out+=&format!(" local capture_fullscreen={}\n local capture_tag={}\n local release_tag={}\n",
        ctl("capture_fullscreen").and_then(Value::as_bool).unwrap_or(false),
        q(ctl("capture_tag").and_then(Value::as_str).unwrap_or("")),
        q(ctl("release_tag").and_then(Value::as_str).unwrap_or("")));
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
        let escape=is_bypass(&chord,&prefix);
        let flags=if escape {",{dont_inhibit=true,submap_universal=true}"} else {""};
        // C-22: the literal bind on this chord realizes the row -> no second handle. A literal that runs a different
        // action would make the row dead (no handle of its own) and hide the collision, so it is an error (D1).
        // Every literal on the chord must be the row's realization: one foreign literal is a second action on the key.
        let realized=match bound.get(&chord) {
            None => false,
            Some(dispatchers) if dispatchers.iter().all(|d| realizes(data,binding,d)) => true,
            Some(dispatchers) => {
                let id=binding.get("id").and_then(Value::as_str).unwrap_or("?");
                let action=binding.get("action").and_then(Value::as_str).unwrap_or("?");
                if binding.get("tier").and_then(Value::as_integer)!=Some(2) {
                    let foreign=dispatchers.iter().filter(|d| !realizes(data,binding,d)).cloned().collect::<Vec<_>>();
                    return Err(format!("[keybindings.global] {id}: chord {chord} is already bound by the compositor to {} (not {action}); choose a free chord", foreign.join(" and ")));
                }
                false
            }
        };
        if binding.get("tier").and_then(Value::as_integer)!=Some(2) && !realized {
            let action=binding.get("action").and_then(Value::as_str).ok_or("binding action missing")?;

            // The dispatcher is kept with the handle so a `bypass` on this row binds the same action (escapes below).
            // do ... end scopes d and b to this row: the section's closure keeps its Lua local budget (200) for rows.
            out+=&format!(" do local d=mios_native_hl.dsp.exec_cmd({})\n local b=mios_native_hl.bind({},d{flags})\n if b then table.insert(mios_input_handles,{{handle=b,key={},dispatcher=d,escape={escape}}}) end end\n",q(&mios_resolver::keybindings::dispatch_command(action)),q(&chord),q(&chord));
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
            out+=&format!(" do local b=mios_native_hl.bind({},mios_native_hl.dsp.exec_cmd({}),{{dont_inhibit=true,submap_universal=true}})\n if b then table.insert(mios_input_handles,{{handle=b,key={},escape=true}}) end end\n",q(&chord),q(&format!("/usr/libexec/mios/mios-inputd vm-action {action}")),q(&chord));
        }
    }
    for name in ["vm-passthrough","game","terminal"] {
        if let Some(patterns)=at(data,&format!("keybindings.global.contexts.{name}")).and_then(Value::as_array) {
            let patterns=patterns.iter().map(|v|v.as_str().ok_or("class pattern must be a string")).collect::<Result<Vec<_>,_>>()?;
            if !patterns.is_empty() {out+=&format!(" hl.window_rule({{name={},match={{class={}}},tag={}}})\n",q(&format!("mios-input-{name}")),q(&format!("^({})$",patterns.join("|"))),q(&format!("+mios-input-{name}")));}
        }
    }
    out+=r#" local canonical=mios_input_canonical
 local function contains(list,value) for _,v in pairs(list or {}) do if type(v)=='string' and v:gsub('%*$','')==value then return true end end return false end
 -- THE capture predicate (C-35): fullscreen==2, capture_tag or the room's input_owner capture a window; release_tag
 -- cancels all three. Class contexts (vm-passthrough, game) are never demoted by it.
 local function captured(w)
  if not w then return false end
  local tags=w.tags or {}
  if release_tag~='' and contains(tags,release_tag) then return false end
  if capture_fullscreen and tonumber(w.fullscreen)==2 then return true end
  if capture_tag~='' and contains(tags,capture_tag) then return true end
  local p=hl.plugin and hl.plugin.hypr3d
  if p and p.input_owner then
   local ok,id=pcall(p.input_owner)
   if ok and id and id~=0 and tonumber(w.address)==id then return true end
  end
  return false
 end
 local function context()
  local w=hl.get_active_window()
  local tags=w and w.tags or {}
  if captured(w) then return 'app-capture' end
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
 MIOS_INPUT_POLICY={handles=mios_input_handles,context=context,captured=captured,refresh=refresh,bypass=mios_input_bypass}
 hl.on('window.active',refresh)
 hl.on('window.class',refresh)
 hl.on('window.fullscreen',refresh)
 hl.on('window.update_rules',refresh)
 refresh()
end)
"#;
    Ok(out)
}
// ---- Super+Escape overlay (C-25) ----------------------------------------------------------------------------------
// The keymap overlay is generated here, from the same tables render-hyprland binds: the compositor tables (with their
// [keybindings.global] remaps), every global row and its bypass, the toggle chord, North, Start, the half-capture
// chord, the system layer and the nav / room tables. mios-sync-theme writes the result into theme.json (Keymap.qml).

/// The global row a [desktop.hyprland].bindings entry realizes ("" when none): render-hyprland binds its remap.
pub fn bind_global_id(bind: &Value) -> &'static str {
    let args = bind.get("args");
    match (bind.get("dispatcher").and_then(Value::as_str).unwrap_or(""), args.and_then(Value::as_str)) {
        ("window.close", _) => "close-window",
        ("window.fullscreen", _) if args.and_then(|a| a.get("mode")).and_then(Value::as_str) == Some("fullscreen") => "fullscreen",
        ("exec_cmd", Some("/usr/bin/miosd placement toggle")) => "toggle-placement",
        ("exec_cmd", Some(cmd)) if cmd.ends_with("call keymap toggle") => "keymap",
        _ => "",
    }
}

const PAD_NAMES: &[(&str, &str)] = &[
    ("BTN_SOUTH", "South"), ("BTN_EAST", "East"), ("BTN_WEST", "West"), ("BTN_NORTH", "North"), ("BTN_TL", "LB"),
    ("BTN_TR", "RB"), ("BTN_TL2", "LT"), ("BTN_TR2", "RT"), ("BTN_SELECT", "Select"), ("BTN_START", "Start"),
    ("BTN_MODE", "Guide"), ("BTN_THUMBL", "L3"), ("BTN_THUMBR", "R3"), ("BTN_DPAD_UP", "D-pad up"),
    ("BTN_DPAD_DOWN", "D-pad down"), ("BTN_DPAD_LEFT", "D-pad left"), ("BTN_DPAD_RIGHT", "D-pad right"),
    ("LS_UP", "Left stick up"), ("LS_DOWN", "Left stick down"), ("LS_LEFT", "Left stick left"),
    ("LS_RIGHT", "Left stick right"), ("RS_UP", "Right stick up"), ("RS_DOWN", "Right stick down"),
    ("RS_LEFT", "Right stick left"), ("RS_RIGHT", "Right stick right"),
];

const ACTION_LABELS: &[(&str, &str)] = &[
    ("hypr:togglefloating", "Toggle floating"), ("hypr:killactive", "Close window"), ("hypr:fullscreen", "Fullscreen"),
    ("mios:launch-browser", "Browser"), ("quickshell:monitor-toggle", "System monitor"),
    ("mios:system-monitor", "System monitor"), ("quickshell:lock", "Lock"), ("mios:toggle_vm_passthrough", "VM passthrough latch"),
    ("mios:vm-fullscreen", "VM fullscreen"), ("mios:vm-command-modifier", "Looking Glass command key"),
    ("hypr:workspace e-1", "Previous workspace"), ("hypr:workspace e+1", "Next workspace"),
    ("hypr:movetoworkspace e-1", "Window to previous workspace"), ("hypr:movetoworkspace e+1", "Window to next workspace"),
    ("quickshell:drawer-toggle", "App drawer"), ("quickshell:keymap-toggle", "Bindings overlay"),
    ("mios:screenshot-region", "Screenshot region"), ("mios:placement-toggle", "Placement toggle"),
    ("mios:hyprland-3d-toggle", "Hypr3D room"), ("mios:session-scale-up", "Session scale up"),
    ("mios:session-scale-down", "Session scale down"), ("mios:session-scale-reset", "Session scale reset"),
    ("mios:graphical-vt", "Back to the graphical VT"), ("none", "nothing"),
];

const OVER: &str = " (over captured apps and VMs)";

fn pad_name(button: &str, toggle: &str) -> String {
    if button == "TOGGLE_CHORD" && !toggle.is_empty() {
        return toggle.into();
    }
    PAD_NAMES.iter().find(|(b, _)| *b == button).map(|(_, n)| (*n).to_string()).unwrap_or_else(|| button.into())
}

/// The overlay's chord identity: the resolver's normalizer, falling back to a lowercase spelling for display-only
/// keys ("SUPER (tap)", "SUPER + 1-0").
fn overlay_canon(chord: &str) -> String {
    canonical(chord, "\0").unwrap_or_else(|_| chord.split_whitespace().collect::<String>().to_lowercase())
}

fn spaced(chord: &str) -> String {
    chord.split('+').map(str::trim).filter(|p| !p.is_empty()).collect::<Vec<_>>().join(" + ")
}

fn str_of<'a>(v: &'a Value, path: &str) -> Option<&'a str> { at(v, path).and_then(Value::as_str) }
fn on(v: &Value, path: &str, default: bool) -> bool { at(v, path).and_then(Value::as_bool).unwrap_or(default) }
fn list<'a>(v: &'a Value, path: &str) -> Vec<&'a str> {
    at(v, path).and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).collect()
}

fn action_label(data: &Value, terminal: &str, action: &str) -> String {
    if action == "exec:mios-terminal" {
        return terminal.into();
    }
    if let Some((_, l)) = ACTION_LABELS.iter().find(|(a, _)| *a == action) {
        return (*l).into();
    }
    let (ns, verb) = action.split_once(':').unwrap_or(("", action));
    let first = verb.split(' ').next().unwrap_or("");
    match ns {
        "hypr" if first == "movefocus" || first == "movewindow" => {
            let rest = verb.split_once(' ').map(|x| x.1).unwrap_or(verb);
            format!("{}{rest}", if first == "movefocus" { "Focus " } else { "Move window " })
        }
        "key" => verb.replace('+', " + "),
        "tmux" => {
            let prefix = str_of(data, "keybindings.tmux_prefix").unwrap_or("C-b");
            format!("tmux {}", verb.replace("key ", &format!("{prefix} ")))
        }
        _ => action.into(),
    }
}

/// The overlay rows [{keys, label}] in display order, without duplicates.
pub fn keymap_overlay(data: &Value) -> Result<Vec<(String, String)>, String> {
    let h = at(data, "desktop.hyprland").ok_or("missing [desktop.hyprland]")?;
    let modifier = str_of(h, "main_modifier").ok_or("missing [desktop.hyprland].main_modifier")?;
    let actions = at(data, "keybindings.actions").and_then(Value::as_array).ok_or("missing [[keybindings.actions]]")?;
    let label_of = |v: &Value| v.get("label").and_then(Value::as_str).unwrap_or("").to_string();
    let terminal = actions.iter().find(|a| a.get("id").and_then(Value::as_str) == Some("terminal")).map(label_of)
        .ok_or("[[keybindings.actions]] has no terminal row")?;
    let desk = str_of(data, "keybindings.desktop_modifier").ok_or("missing [keybindings].desktop_modifier")?
        .split_whitespace().collect::<Vec<_>>().join(" + ");
    let mut rows: Vec<(String, String)> = Vec::new();
    for k in ["Q", "Return"] {
        let fallback = format!("{modifier} + {k}");
        rows.push((if k == "Return" { key(data, "terminal", &fallback) } else { fallback }, terminal.clone()));
    }
    for k in list(h, "launcher_keys") {
        let fallback = format!("{modifier} + {k}");
        rows.push((if k.eq_ignore_ascii_case("space") { key(data, "app-drawer", &fallback) } else { fallback }, "App drawer".into()));
    }
    for a in actions {
        rows.push((format!("{desk} + {}", a.get("key").and_then(Value::as_str).unwrap_or("")), label_of(a)));
    }
    for b in h.get("bindings").and_then(Value::as_array).into_iter().flatten() {
        let k = b.get("key").and_then(Value::as_str).ok_or("[desktop.hyprland].bindings row without key")?;
        let keys = if k == "SUPER + Super_L" || k == "SUPER + Super_R" {
            "SUPER (tap)".to_string()
        } else {
            match bind_global_id(b) { "" => k.to_string(), id => key(data, id, k) }
        };
        let label = b.get("label").or_else(|| b.get("dispatcher")).and_then(Value::as_str).unwrap_or("").to_string();
        rows.push((keys, label));
    }
    if on(data, "desktop.screenshot.enabled", true) {
        for b in at(data, "desktop.screenshot.bindings").and_then(Value::as_array).into_iter().flatten() {
            rows.push((b.get("key").and_then(Value::as_str).unwrap_or("").into(), b.get("label").and_then(Value::as_str).unwrap_or("Screenshot").into()));
        }
    }
    if at(data, "desktop.shaderglass.enabled").and_then(Value::as_bool) == Some(true) {
        for b in at(data, "desktop.shaderglass.bindings").and_then(Value::as_array).into_iter().flatten() {
            rows.push((b.get("key").and_then(Value::as_str).unwrap_or("").into(), b.get("label").and_then(Value::as_str).unwrap_or("ShaderGlass").into()));
        }
    }
    let h3d_on = on(h, "hypr3d.enabled", true);
    if h3d_on {
        let toggle = str_of(h, "hypr3d.toggle_key").unwrap_or("SUPER + F12");
        rows.push((key(data, "hypr3d-toggle", toggle), "Hypr3D: 3D glass room toggle".into()));
    }
    let count = at(h, "workspace_count").and_then(Value::as_integer).ok_or("missing workspace_count")?;
    rows.push((format!("{modifier} + 1-{}", count % 10), "Switch workspace".into()));
    rows.push((format!("{modifier} + SHIFT + 1-{}", count % 10), "Move window to workspace".into()));

    // Global rows: a chord the compositor tables do not list, then every bypass chord. A chord on bypass_mod reaches
    // the host over captured apps and VMs (its handle is an escape), so the overlay says so.
    let g = at(data, "keybindings.global");
    let enabled = g.and_then(|g| g.get("enabled")).and_then(Value::as_bool) == Some(true);
    if enabled {
        let prefix = bypass_prefix(data)?;
        let mut listed: std::collections::BTreeMap<String, String> = rows.iter().map(|(k, l)| (overlay_canon(k), l.clone())).collect();
        for r in at(data, "keybindings.global.bindings").and_then(Value::as_array).into_iter().flatten() {
            let chord = r.get("chord").and_then(Value::as_str).unwrap_or("");
            if chord.is_empty() || chord.starts_with("BTN_") || r.get("scope").and_then(Value::as_str) == Some("app") {
                continue;
            }
            let c = overlay_canon(chord);
            let label = listed.get(&c).cloned()
                .unwrap_or_else(|| action_label(data, &terminal, r.get("action").and_then(Value::as_str).unwrap_or("")));
            if !listed.contains_key(&c) {
                let vm = if r.get("tier").and_then(Value::as_integer) == Some(2) { "VM: " } else { "" };
                let tag = if is_bypass(&c, &prefix) { OVER } else { "" };
                rows.push((spaced(chord), format!("{vm}{label}{tag}")));
                listed.insert(c, label.clone());
            }
            if let Some(bypass) = r.get("bypass").and_then(Value::as_str) {
                rows.push((spaced(bypass), format!("{label}{OVER}")));
            }
        }
    }

    // Controller rows: names, chords and maps from the SSOT (never a hard-coded "L3 + R3").
    let gc = at(data, "keybindings.global.controller");
    let gcv = |k: &str| gc.and_then(|c| c.get(k));
    if h3d_on && on(h, "hypr3d.controller.enabled", true) && on(data, "keybindings.controller.enabled", true)
        && gcv("enabled").and_then(Value::as_bool).unwrap_or(true)
    {
        let hold = gcv("capture_hold_ms").and_then(Value::as_integer).unwrap_or(500);
        let toggle = list(h, "hypr3d.controller.toggle_chord").iter().map(|b| pad_name(b, "")).collect::<Vec<_>>().join(" + ");
        let name = |b: &str| pad_name(b, &toggle);
        let pad = |v: &Value| match v {
            Value::String(a) => action_label(data, &terminal, a),
            Value::Array(a) if a.len() == 2 => format!(
                "{} · hold: {}",
                action_label(data, &terminal, a[0].as_str().unwrap_or("")),
                action_label(data, &terminal, a[1].as_str().unwrap_or(""))
            ),
            other => other.to_string(),
        };
        if !toggle.is_empty() {
            rows.push((format!("Pad {toggle}"), format!("Hypr3D room (tap) · release capture (hold {hold} ms)")));
        }
        rows.push((format!("Pad {}", name(str_of(h, "hypr3d.controller.fullscreen_button").unwrap_or("BTN_NORTH"))),
            format!("Capture the aimed window (tap) · fullscreen + capture (hold {hold} ms)")));
        rows.push((format!("Pad {}", name(gcv("cursor_mode_button").and_then(Value::as_str).unwrap_or("BTN_START"))), "Cursor mode".into()));
        let over = gcv("mode_override_chord").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).map(name).collect::<Vec<_>>();
        if !over.is_empty() {
            rows.push((format!("Pad {}", over.join(" + ")), "Half-capture: host pointer over a captured app".into()));
        }
        let guide = name(gcv("guide_button").and_then(Value::as_str).unwrap_or("BTN_MODE"));
        for (b, a) in gcv("system_layer").and_then(Value::as_table).into_iter().flatten() {
            rows.push((format!("Pad {guide} + {}", name(b)), pad(a)));
        }
        if let Some(nav) = gcv("nav").and_then(Value::as_table).filter(|n| n.get("enabled").and_then(Value::as_bool).unwrap_or(true)) {
            let profile = nav.get("contexts").and_then(|c| c.get("desktop-2d")).and_then(Value::as_str);
            if let Some(t) = profile.filter(|p| *p != "vpad").and_then(|p| nav.get(p)).and_then(Value::as_table) {
                for (b, v) in t.iter().filter(|(b, _)| *b != "inherits") {
                    rows.push((format!("Pad {}", name(b)), pad(v)));
                }
            }
        }
        for (b, v) in gcv("room").and_then(Value::as_table).into_iter().flatten() {
            rows.push((format!("Room {}", name(b)), pad(v)));
        }
    }
    let mut seen = std::collections::BTreeSet::new();
    rows.retain(|r| seen.insert(r.clone()));
    Ok(rows)
}

/// `mios-gen keymap-overlay`: the overlay rows as a JSON array of {keys, label}.
pub fn keymap_overlay_json(data: &Value) -> Result<String, String> {
    let rows = keymap_overlay(data)?;
    let v: Vec<serde_json::Value> = rows.into_iter().map(|(k, l)| serde_json::json!({"keys": k, "label": l})).collect();
    serde_json::to_string(&v).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn disabled_policy_is_inert() {let d:Value=toml::from_str("[keybindings.global]\nenabled=false").unwrap();assert!(prelude(&d).unwrap().is_empty());assert!(finish(&d,"").unwrap().is_empty());}
    #[test] fn generated_policy_uses_native_handles_and_bypass_flags() {let d:Value=toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap();let lua=finish(&d,"").unwrap();assert!(lua.contains("dont_inhibit=true,submap_universal=true"));assert!(lua.contains("v.handle:set_enabled(enabled)"));assert!(lua.contains("mios-input-vm-passthrough"));assert!(lua.contains("vm-action toggle_vm_passthrough"));}
    #[test] fn remapping_has_one_source() {let mut d:Value=toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap();let b=d["keybindings"]["global"]["bindings"].as_array_mut().unwrap().iter_mut().find(|b|b["id"].as_str()==Some("fullscreen")).unwrap();b["chord"]=Value::String("SUPER+F10".into());assert_eq!(key(&d,"fullscreen","SUPER+F11"),"SUPER+F10");}
    #[test]
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
        let text=format!("{fixture}\n{}\nhl.bind('SUPER+Space',function() end)\n{}\n{checks}",prelude(&data).unwrap(),finish(&data,"").unwrap());
        let dir=tempfile::tempdir().unwrap();let file=dir.path().join("policy.lua");std::fs::write(&file,text).unwrap();
        let output=std::process::Command::new("lua").arg(&file).output().expect("native Lua interpreter required");
        assert!(output.status.success(),"{}{}",String::from_utf8_lossy(&output.stdout),String::from_utf8_lossy(&output.stderr));
    }

    #[test]
    fn newly_registered_shortcut_is_projected_by_the_native_dispatcher() {
        let mut d:Value=toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap();
        let b:Value=toml::from_str("id='custom-browser'\ntier=1\nchord='SUPER+F8'\naction='mios:launch-browser'\nrealized_by='hyprland'\npassthrough=['vm-passthrough']").unwrap();
        d["keybindings"]["global"]["bindings"].as_array_mut().unwrap().push(b);
        let lua=finish(&d,"").unwrap();
        assert!(lua.contains("mios-inputd dispatch 'mios:launch-browser'"));
        assert!(lua.contains("mios_native_hl.bind(\"SUPER+f8\""));
    }


    fn vendor() -> Value { toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap() }

    /// A mocked compositor: hl.bind handles with enable state, a focused window, an optional Hypr3D input owner.
    const MOCK: &str = r#"
local window={address='0xabc',pid=42,class='Alacritty',tags={},fullscreen=0}
local owner=0
local hooks,handles={},{}
hl={plugin={hypr3d={active=function() return false end,input_owner=function() return owner end}},dsp={exec_cmd=function(c) return c end,window={close=function() return 'close' end}},get_active_window=function() return window end,get_windows=function() return {window} end,window_rule=function() end}
hl.bind=function(key,dispatcher,flags)
 local h={key=key,enabled=true,dispatcher=dispatcher,flags=flags or {}}
 h.set_enabled=function(self,value) self.enabled=value end
 table.insert(handles,h); return h
end
hl.on=function(event,fn) hooks[event]=hooks[event] or {};table.insert(hooks[event],fn) end
local function mios_section(_,fn) fn() end
local function handle(key) for _,v in ipairs(MIOS_INPUT_POLICY.handles) do if v.key==key then return v end end end
"#;

    fn run_lua(data: &Value, literal: &str, checks: &str) {
        let text=format!("{MOCK}\n{}\n{literal}\n{}\n{checks}\nio.write('ok')",prelude(data).unwrap(),finish(data,literal).unwrap());
        let dir=tempfile::tempdir().unwrap();let file=dir.path().join("policy.lua");std::fs::write(&file,text).unwrap();
        let output=std::process::Command::new("lua").arg(&file).output().expect("native Lua interpreter required");
        assert!(output.status.success()&&output.stdout.ends_with(b"ok"),"{}{}",String::from_utf8_lossy(&output.stdout),String::from_utf8_lossy(&output.stderr));
    }

    /// C-36: every handle on bypass_mod is an escape (uninhibited, alive in app-capture and vm-passthrough); a chord
    /// off bypass_mod stays disabled there, including SUPER+ALT (not a prefix of SUPER+CTRL).
    #[test]
    fn bypass_mod_handles_are_escapes_and_nothing_else_is() {
        // SUPER+C is close-window's own realization (window.close = hypr:killactive), so it carries no second handle.
        let literal="hl.bind(\"SUPER + C\", hl.dsp.window.close())\nhl.bind(\"SUPER + ALT + G\", hl.dsp.exec_cmd(\"setup\"))\nhl.bind(\"SUPER + CTRL + SHIFT + k\", hl.dsp.exec_cmd(\"lit\"))";
        run_lua(&vendor(),literal,r#"
local p=MIOS_INPUT_POLICY
local scale,close,alt,lit=handle('SUPER+CTRL+equal'),handle('SUPER + C'),handle('SUPER + ALT + G'),handle('SUPER + CTRL + SHIFT + k')
assert(scale and scale.escape==true,'session-scale handle must be an escape')
assert(scale.handle.flags.dont_inhibit and scale.handle.flags.submap_universal,'escape must be uninhibited')
assert(lit and lit.escape==true and lit.handle.flags.dont_inhibit,'a literal bypass_mod bind is an escape too')
assert(close and not close.escape and alt and not alt.escape,'off-bypass handles are not escapes')
window.fullscreen=2;p.refresh();assert(p.context()=='app-capture')
assert(scale.handle.enabled and lit.handle.enabled,'bypass chords reach the host in app-capture')
assert(not close.handle.enabled and not alt.handle.enabled,'Super+C / Super+Alt stay with the captured app')
window.fullscreen=0;window.class='looking-glass-client';window.tags={'mios-input-vm-passthrough*'};p.refresh()
assert(p.context()=='vm-passthrough' and scale.handle.enabled and not close.handle.enabled)
window.tags={};p.refresh();assert(p.context()=='desktop-2d' and close.handle.enabled and alt.handle.enabled)
"#);
    }

    /// Negative control for C-36: with bypass_mod moved off SUPER+CTRL, the same session-scale handle is no longer
    /// an escape, so the property follows the SSOT and is not hard-coded.
    #[test]
    fn escape_marking_follows_bypass_mod() {
        let mut d=vendor();
        d["keybindings"]["global"]["bypass_mod"]=Value::String("SUPER+ALT".into());
        run_lua(&d,"",r#"
local scale=handle('SUPER+CTRL+equal')
assert(scale and scale.escape==false and not scale.handle.flags.dont_inhibit)
window.fullscreen=2;MIOS_INPUT_POLICY.refresh();assert(not scale.handle.enabled)
"#);
    }

    /// C-35: one capture predicate. The room input_owner captures; release_tag cancels fullscreen==2, capture_tag and
    /// input_owner; a vm-class window with release_tag stays vm-passthrough (INV-4).
    #[test]
    fn capture_predicate_covers_input_owner_and_release_tag() {
        let mut d=vendor();
        d["keybindings"]["global"]["controller"].as_table_mut().unwrap().insert("release_tag".into(),Value::String("mios-input-released".into()));
        run_lua(&d,"",r#"
local p=MIOS_INPUT_POLICY
assert(p.context()=='terminal' or p.context()=='desktop-2d')
owner=0xabc;assert(p.captured(window) and p.context()=='app-capture','room input_owner must capture')
window.tags={'mios-input-released*'};assert(not p.captured(window) and p.context()~='app-capture','release_tag cancels input_owner')
owner=0;window.fullscreen=2;assert(not p.captured(window),'release_tag cancels fullscreen==2')
window.tags={'mios-input-released*','mios-input-captured'};window.fullscreen=0;assert(not p.captured(window),'release_tag cancels capture_tag')
window.class='looking-glass-client';window.tags={'mios-input-released*','mios-input-vm-passthrough*'}
assert(p.context()=='vm-passthrough','release_tag never demotes a vm-passthrough class')
window.tags={'mios-input-captured'};assert(p.context()=='app-capture')
"#);
    }

    /// GT parity (C-35): the Lua predicate and mios-inputd's context template agree on every combination of
    /// {fullscreen, capture_tag, input_owner}. release_tag joins the parity set when the inputd template reads it
    /// (step I1); until then the Lua side alone is checked for it (capture_predicate_covers_input_owner_and_release_tag).
    #[test]
    fn capture_predicate_matches_the_inputd_template() {
        let runtime=include_str!("../../mios-inputd/src/runtime.rs");
        let start=runtime.find("let template=r#\"").expect("inputd context template")+"let template=r#\"".len();
        let template=&runtime[start..start+runtime[start..].find("\"#;").unwrap()];
        let template=template.replace("CAPTURE_FULLSCREEN","true").replace("CAPTURE_TAG","\"mios-input-captured\"");
        let with_release=template.contains("RELEASE_TAG");
        let template=template.replace("RELEASE_TAG","\"mios-input-released\"");
        let mut d=vendor();
        if with_release {d["keybindings"]["global"]["controller"].as_table_mut().unwrap().insert("release_tag".into(),Value::String("mios-input-released".into()));}
        let n=if with_release {16} else {8};
        let checks=format!(r#"
-- inputd's own copy: the template's fallback, with the generated policy hidden (a template that delegates to
-- MIOS_INPUT_POLICY.captured when present would otherwise compare the predicate with itself).
local function inputd(w)
 local f=load({tpl})
 local saved=MIOS_INPUT_POLICY; MIOS_INPUT_POLICY=nil
 local ok,err=pcall(f)
 MIOS_INPUT_POLICY=saved
 local s=tostring(err)
 assert(s:find('MIOS_INPUT_CONTEXT:'),s)
 return s:find('"captured":true',1,true)~=nil
end
for i=0,{last} do
 local fs,tag,own,rel=i%2==1,math.floor(i/2)%2==1,math.floor(i/4)%2==1,math.floor(i/8)%2==1
 window.fullscreen=fs and 2 or 0; owner=own and 0xabc or 0
 window.tags={{}}; if tag then table.insert(window.tags,'mios-input-captured') end; if rel then table.insert(window.tags,'mios-input-released') end
 local a,b=MIOS_INPUT_POLICY.captured(window),inputd(window)
 assert(a==b,string.format('combination %d: lua=%s inputd=%s',i,tostring(a),tostring(b)))
end
"#,tpl=serde_json::to_string(&template).unwrap(),last=n-1);
        run_lua(&d,"",&checks);
    }

    /// C-22: a row whose chord the compositor binds literally gets no second dispatch bind, and its bypass becomes the
    /// literal handle's escape. Negative control: without the literal bind the same row does get a dispatch bind, and
    /// the session-scale rows (no literal bind) keep theirs.
    #[test]
    fn no_double_bind_for_literal_chords() {
        let d=vendor();
        assert!(d["keybindings"]["global"]["bindings"].as_array().unwrap().iter().any(|b|b["id"].as_str()==Some("screenshot-region")));
        let literal=" hl.bind(\"Print\", hl.dsp.exec_cmd(\"/usr/bin/mios-shot region\"))\n";
        let lua=finish(&d,literal).unwrap();
        assert!(!lua.contains("mios_native_hl.bind(\"print\""),"{lua}");
        assert!(lua.contains("escapes[\"print\"]=\"SUPER+CTRL+print\""));
        assert!(lua.contains("mios_native_hl.bind(\"SUPER+CTRL+equal\""));
        let without=finish(&d,"").unwrap();
        assert!(without.contains("mios_native_hl.bind(\"print\""),"control: no literal bind means a dispatch bind");
        run_lua(&d,literal,r#"
local n,esc=0,0
for _,v in ipairs(MIOS_INPUT_POLICY.handles) do
 local c=MIOS_INPUT_POLICY.bypass and v.key
 if v.key=='Print' or v.key=='print' then n=n+1 end
 if v.key=='SUPER+CTRL+print' then esc=esc+1 end
end
assert(n==1,'exactly one Print handle, got '..n)
assert(esc==1,'exactly one SUPER+CTRL+Print escape, got '..esc)
window.fullscreen=2;MIOS_INPUT_POLICY.refresh()
for _,v in ipairs(MIOS_INPUT_POLICY.handles) do
 if v.key=='SUPER+CTRL+print' then assert(v.handle.enabled,'screenshot bypass works over a captured app') end
 if v.key=='Print' then assert(not v.handle.enabled,'plain Print belongs to the captured app') end
end
"#);
    }

    /// D1: a realized_by row moved onto a chord the compositor binds literally for ANOTHER action is an error (it used
    /// to be silently dropped: no dispatch bind, no finding, a dead row). Controls: the vendor projection renders, the
    /// literal that IS the row's realization (same argv) still suppresses the second handle, and an exec: row is
    /// realized by a literal running its program.
    #[test]
    fn remapped_row_on_a_foreign_literal_chord_is_rejected() {
        let mut d=vendor();
        let literal=" hl.bind(\"Print\", hl.dsp.exec_cmd(\"/usr/bin/mios-shot region\"))\nhl.bind(\"SUPER + J\", hl.dsp.layout(\"togglesplit\"))\n";
        assert!(finish(&d,literal).is_ok(),"control: the vendor rows render");
        row_mut(&mut d,"session-scale-up")["chord"]=Value::String("SUPER+J".into());
        let err=finish(&d,literal).unwrap_err();
        assert!(err.contains("session-scale-up: chord SUPER+j is already bound by the compositor to hl.dsp.layout(\"togglesplit\") (not mios:session-scale-up)"),"{err}");
        let mut d=vendor();
        row_mut(&mut d,"screenshot-region")["chord"]=Value::String("SUPER+J".into());
        assert!(finish(&d,literal).unwrap_err().contains("screenshot-region: chord SUPER+j"));
        // realization rules
        let v=vendor();
        let b=|t:&str| -> Value { toml::from_str(t).unwrap() };
        assert!(realizes(&v,&b("action='mios:screenshot-region'"),"hl.dsp.exec_cmd(\"/usr/bin/mios-shot region\"))"));
        assert!(!realizes(&v,&b("action='mios:screenshot-region'"),"hl.dsp.exec_cmd(\"/usr/bin/mios-shot window\"))"));
        assert!(realizes(&v,&b("action='exec:mios-terminal'"),"hl.dsp.exec_cmd(\"alacritty -e env -u TMUX -u TMUX_PANE -u TMUX_TMPDIR /usr/libexec/mios/mios-terminal\"))"));
        assert!(!realizes(&v,&b("action='exec:mios-terminal'"),"hl.dsp.exec_cmd(\"alacritty\"))"));
        assert!(!realizes(&v,&b("action='exec:mios-terminal'"),"hl.dsp.exec_cmd(\"alacritty -e env -u TMUX -u TMUX_PANE -u TMUX_TMPDIR /usr/libexec/mios/mios-terminal --action ai\"))"));
        assert!(!realizes(&v,&b("action='exec:alacritty'"),"hl.dsp.exec_cmd(\"alacritty -e env -u TMUX -u TMUX_PANE -u TMUX_TMPDIR /usr/libexec/mios/mios-terminal --action system\"))"));
        assert!(realizes(&v,&b("action='hypr:killactive'"),"hl.dsp.window.close())"));
        assert!(!realizes(&v,&b("action='hypr:killactive'"),"hl.dsp.layout(\"togglesplit\"))"));
        assert!(realizes(&v,&b("action='hypr:fullscreen'"),"hl.dsp.window.fullscreen({[\"mode\"] = \"fullscreen\", [\"action\"] = \"toggle\"}))"));
        assert!(!realizes(&v,&b("action='hypr:fullscreen'"),"hl.dsp.window.fullscreen({[\"mode\"] = \"maximized\"}))"));
    }

    /// V1 (round-2 verifier): an `exec:<prog>` row moved onto a chord whose literal merely mentions the program among
    /// its words was taken as realized -> no dispatch handle, a dead row, and a key that runs another action. Every
    /// probe the verifier ran goes through the FULL projection (the literals come from [keybindings.actions], the
    /// terminal and launcher keys, not the compositor tables). Positive control: the same row on a free chord gets its
    /// own dispatch handle, and the shipped terminal row stays realized by its literal (no second handle).
    #[test]
    fn exec_row_on_an_unrelated_literal_chord_is_rejected() {
        use crate::render::render_hyprland::project;
        let shipped=project(&vendor()).unwrap();
        assert!(!shipped.contains("mios-inputd dispatch 'exec:mios-terminal'"),"control: the terminal row is realized by SUPER+Return's literal");
        for (action,chord,literal) in [
            ("exec:alacritty","CTRL+ALT+SHIFT+m","--action system"),
            ("exec:mios-terminal","CTRL+ALT+SHIFT+a","--action ai"),
            ("exec:quickshell","SUPER+R","call drawer toggle"),
            ("exec:env","SUPER+Q","mios-terminal\")"),
        ] {
            let mut d=vendor();
            row_mut(&mut d,"session-scale-up")["action"]=Value::String(action.into());
            row_mut(&mut d,"session-scale-up")["chord"]=Value::String(chord.into());
            let err=project(&d).unwrap_err();
            assert!(err.contains("session-scale-up: chord") && err.contains(&format!("(not {action})")) && err.contains(literal),"{action} on {chord}: {err}");
            // positive control: a free chord renders, with the row's own dispatch handle
            row_mut(&mut d,"session-scale-up")["chord"]=Value::String("CTRL+ALT+SHIFT+F9".into());
            let lua=project(&d).unwrap();
            assert!(lua.contains(&format!("mios-inputd dispatch '{action}'")) && lua.contains("mios_native_hl.bind(\"CTRL+ALT+SHIFT+f9\",d"),"{action}: no dispatch handle on the free chord");
        }
    }

    /// Verifier observation 2: each dispatch row used to declare two section-level locals (d, b), halving the 200-local
    /// budget of the global-input closure. Rows are scoped in do ... end now: 250 rows load in real Lua. Negative
    /// control: the same rows without the scope exceed the limit.
    #[test]
    fn many_dispatch_rows_fit_the_lua_local_limit() {
        let mut d=vendor();
        let rows=d["keybindings"]["global"]["bindings"].as_array_mut().unwrap();
        for i in 0..250 { rows.push(toml::from_str(&format!("id='bulk-{i}'\ntier=1\nchord='SUPER+CTRL+ALT+SHIFT+F{}'\naction='mios:session-scale-up'\nrealized_by='hyprland'", i%24+1)).unwrap()); }
        run_lua(&d,"","");
        let unscoped=finish(&d,"").unwrap().replace(" do local d="," local d=").replace(" do local b="," local b=").replace(" end end\n"," end\n");
        let text=format!("{MOCK}\n{}\n{unscoped}\nio.write('ok')",prelude(&d).unwrap());
        let dir=tempfile::tempdir().unwrap();let file=dir.path().join("p.lua");std::fs::write(&file,text).unwrap();
        let out=std::process::Command::new("lua").arg(&file).output().unwrap();
        assert!(!out.status.success() && String::from_utf8_lossy(&out.stderr).contains("too many local variables"),"control: {}",String::from_utf8_lossy(&out.stderr));
    }

    #[test]
    fn literal_chord_scan_is_exact() {
        let set=literal_chords("hl.bind(\"SUPER + F11\", x)\n hl.bind(\"SUPER\" .. \" + \" .. key, y)\nlocal b=mios_native_hl.bind(\"SUPER+CTRL+0\",z)\nhl.bind(\"SUPER\" .. \" + \" .. \"1\", w)"," ");
        assert_eq!(set.into_iter().collect::<Vec<_>>(),vec!["SUPER+1".to_string(),"SUPER+f11".to_string()]);
    }

    // ---- Super+Escape overlay (C-25) ----
    fn rows(d: &Value) -> Vec<(String, String)> { keymap_overlay(d).unwrap() }
    fn has(r: &[(String, String)], keys: &str, label: &str) -> bool {
        let n = |s: &str| s.replace(' ', "").to_uppercase();
        r.iter().any(|(k, l)| n(k) == n(keys) && l.contains(label))
    }
    fn row_mut<'a>(d: &'a mut Value, id: &str) -> &'a mut Value {
        d["keybindings"]["global"]["bindings"].as_array_mut().unwrap().iter_mut().find(|b| b["id"].as_str() == Some(id)).unwrap()
    }

    #[test]
    fn overlay_lists_every_bypass_and_bypass_mod_chord_as_reaching_the_host() {
        let r = rows(&vendor());
        for c in ["SUPER+CTRL+Space", "SUPER+CTRL+Escape", "SUPER+CTRL+F11", "SUPER+CTRL+Print", "SUPER+CTRL+Return"] {
            assert!(has(&r, c, OVER), "{c}: {r:#?}");
        }
        assert!(has(&r, "SUPER+CTRL+equal", "Session scale up (over captured"), "{r:#?}");
        // negative control: a chord off bypass_mod is listed without the claim
        assert!(r.iter().any(|(k, l)| k == "SUPER + Escape" && l == "Keybind map"), "{r:#?}");
        assert!(!r.iter().any(|(k, l)| k == "SUPER + Escape" && l.contains(OVER)));
    }

    #[test]
    fn overlay_controller_rows_come_from_the_ssot() {
        let r = rows(&vendor());
        assert!(has(&r, "Pad L3 + R3", "release capture"), "{r:#?}");
        assert!(has(&r, "Pad Guide + D-pad left", "Focus left") && has(&r, "Pad Guide + East", "Close window"));
        assert!(has(&r, "Pad North", "Capture the aimed window") && has(&r, "Pad Start", "Cursor mode"));
        let mut d = vendor();
        d["desktop"]["hyprland"]["hypr3d"]["controller"]["toggle_chord"] = Value::Array(vec!["BTN_TL".into(), "BTN_TR".into()]);
        let r = rows(&d);
        assert!(has(&r, "Pad LB + RB", "release capture"), "{r:#?}");
        assert!(!r.iter().any(|(k, _)| k.contains("L3 + R3")), "no hard-coded L3 + R3");
        // controller rows follow the master toggle
        let mut off = vendor();
        off["keybindings"]["controller"]["enabled"] = Value::Boolean(false);
        assert!(!rows(&off).iter().any(|(k, _)| k.starts_with("Pad ")));
    }

    #[test]
    fn overlay_follows_global_remaps_and_lists_nav_profiles() {
        let mut d = vendor();
        row_mut(&mut d, "keymap")["chord"] = Value::String("SUPER+F1".into());
        let r = rows(&d);
        let labels: Vec<_> = r.iter().filter(|(_, l)| l == "Keybind map").collect();
        assert_eq!(labels, vec![&("SUPER+F1".to_string(), "Keybind map".to_string())], "remap shown, stale chord gone");
        let mut n = vendor();
        let nav: Value = toml::from_str("enabled = true\n[contexts]\ndesktop-2d = \"desktop\"\n[desktop]\nBTN_SOUTH = [\"key:ENTER\", \"key:SHIFT+ENTER\"]\nRS_LEFT = \"hypr:movefocus left\"\n").unwrap();
        n["keybindings"]["global"]["controller"].as_table_mut().unwrap().insert("nav".into(), nav.clone());
        let r = rows(&n);
        assert!(has(&r, "Pad South", "ENTER · hold: SHIFT + ENTER") && has(&r, "Pad Right stick left", "Focus left"), "{r:#?}");
        // negative control: desktop-2d = "vpad" lists no nav rows
        let mut v = nav;
        v["contexts"]["desktop-2d"] = Value::String("vpad".into());
        n["keybindings"]["global"]["controller"]["nav"] = v;
        assert!(!has(&rows(&n), "Pad South", "ENTER"));
    }

    #[test]
    fn overlay_has_no_duplicate_rows_and_the_json_is_well_formed() {
        let r = rows(&vendor());
        let set: std::collections::BTreeSet<_> = r.iter().collect();
        assert_eq!(set.len(), r.len());
        // the shipped Print row is a compositor bind: the global row adds only its bypass, not a second "Print"
        assert_eq!(r.iter().filter(|(k, _)| k.eq_ignore_ascii_case("print")).count(), 1, "{r:#?}");
        let j: serde_json::Value = serde_json::from_str(&keymap_overlay_json(&vendor()).unwrap()).unwrap();
        assert!(j.as_array().unwrap().iter().all(|x| x["keys"].is_string() && x["label"].is_string()));
    }

    #[test]
    fn bind_global_id_matches_the_rows_render_remaps() {
        let b = |t: &str| -> Value { toml::from_str(t).unwrap() };
        assert_eq!(bind_global_id(&b("dispatcher='window.close'")), "close-window");
        assert_eq!(bind_global_id(&b("dispatcher='window.fullscreen'\nargs={mode='fullscreen'}")), "fullscreen");
        assert_eq!(bind_global_id(&b("dispatcher='window.fullscreen'\nargs={mode='maximized'}")), "");
        assert_eq!(bind_global_id(&b("dispatcher='exec_cmd'\nargs='quickshell ipc call keymap toggle'")), "keymap");
        assert_eq!(bind_global_id(&b("dispatcher='exec_cmd'\nargs='/usr/bin/miosd placement toggle'")), "toggle-placement");
        assert_eq!(bind_global_id(&b("dispatcher='exec_cmd'\nargs='/usr/bin/other'")), "");
    }

}
