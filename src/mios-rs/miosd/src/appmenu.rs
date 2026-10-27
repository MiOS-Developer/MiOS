// AI-hint: miosd appmenu: follows Hyprland focus and exports the focused app's real menus (GTK org.gtk.Menus, dbusmenu AppMenu registrar) to $XDG_RUNTIME_DIR/mios/appmenu.json for the top-bar pills.
// AI-related: usr/share/mios/quickshell/AppMenu.qml, usr/lib/systemd/user/mios-appmenu.service, usr/share/mios/mios.toml [user_units]
//! Real application menus for the focused Hyprland window (`miosd appmenu`).
//!
//! `serve` follows Hyprland focus and atomically writes `$XDG_RUNTIME_DIR/mios/appmenu.json`
//! with the menus the focused application really exports; it never invents entries.
//! Sources:
//!   gtk      - GApplication menubars exported as `org.gtk.Menus` (+ `org.gtk.Actions`) on the
//!              session bus, found under the well-known name equal to the app_id, or under any
//!              bus connection owned by the focused client's PID.
//!   dbusmenu - `com.canonical.dbusmenu` menus registered with the `com.canonical.AppMenu.Registrar`
//!              service this daemon hosts (Qt/appmenu-gtk-module apps on XWayland). The X window id
//!              is mapped to the focused client via Xwayland's root `_NET_ACTIVE_WINDOW`, falling
//!              back to the registering connection's PID. Wayland-native Qt windows never
//!              register (Qt and Firefox use org_kde_kwin_appmenu, absent in Hyprland); their
//!              `/MenuBar/<n>` (Qt) or `/com/canonical/menu/<n>` (Gecko) dbusmenu export is found on
//!              the focused process's connections (a Flatpak's through its xdg-dbus-proxy, matched
//!              by the app-flatpak-*.scope cgroup) and paired with the window by creation rank.
//!   accessibility - native AT-SPI menu bars and application menu buttons, scoped to the
//!              focused process and active frame, including GTK4/libadwaita applications.
//! `activate <app_id> <item_id>` triggers one item and exits 0 once the action was delivered.
// AI-related: usr/lib/systemd/user/mios-appmenu.service, usr/share/mios/quickshell (topbar app menus)

use std::collections::{BTreeSet, HashMap};
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::net::UnixStream as StdUnixStream;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use clap::Subcommand;
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use zbus::object_server::SignalEmitter;
use zbus::zvariant::{ObjectPath, OwnedObjectPath, OwnedValue, Value};
use zbus::Connection;

const REGISTRAR_NAME: &str = "com.canonical.AppMenu.Registrar";
const REGISTRAR_PATH: &str = "/com/canonical/AppMenu/Registrar";
const MAX_DEPTH: usize = 8;
const NEGATIVE_TTL: Duration = Duration::from_secs(20);
/// Every peer call is bounded: a frozen or misbehaving client must never stall focus tracking.
const CALL_TIMEOUT: Duration = Duration::from_millis(1500);

async fn call<B>(
    conn: &Connection,
    dest: &str,
    path: &str,
    iface: &str,
    method: &str,
    body: &B,
) -> zbus::Result<zbus::Message>
where
    B: serde::Serialize + zbus::zvariant::DynamicType,
{
    match tokio::time::timeout(
        CALL_TIMEOUT,
        conn.call_method(Some(dest), path, Some(iface), method, body),
    )
    .await
    {
        Ok(result) => result,
        Err(_) => {
            let msg = format!("{dest} {path} {iface}.{method}: no reply within {CALL_TIMEOUT:?}");
            eprintln!("[appmenu] {msg}");
            Err(zbus::Error::Failure(msg))
        }
    }
}

#[derive(Subcommand, Debug)]
pub enum AppmenuAction {
    /// Follow Hyprland focus, host the AppMenu registrar and keep appmenu.json current
    Serve,
    /// Print the focused window's menus as JSON once (no registrar; diagnostic)
    Dump,
    /// Trigger one menu item of an application (ids come from appmenu.json)
    Activate {
        app_id: String,
        item_id: String,
        /// Bind a HUD click to the exact window which supplied its menu.
        #[arg(long)]
        window: Option<String>,
    },
}

pub async fn run(action: &AppmenuAction) -> Result<(), String> {
    match action {
        AppmenuAction::Serve => serve().await,
        AppmenuAction::Dump => {
            let conn = Connection::session().await.map_err(|e| e.to_string())?;
            let mut ctx = Ctx::new(conn, Arc::new(Mutex::new(HashMap::new())));
            let snap = ctx.snapshot(&focused_client()).await;
            println!(
                "{}",
                serde_json::to_string_pretty(&snap).map_err(|e| e.to_string())?
            );
            Ok(())
        }
        AppmenuAction::Activate { app_id, item_id, window } => activate(app_id, item_id, window.as_deref()).await,
    }
}

// ---------------------------------------------------------------------------------------------
// Output contract

#[derive(Serialize, Clone, PartialEq, Debug)]
pub struct Item {
    pub id: String,
    pub label: String,
    pub enabled: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub submenu: Option<Vec<Item>>,
    /// The item starts a new section (the app placed a separator before it).
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub separator_before: bool,
}

#[derive(Serialize, Clone, PartialEq, Debug)]
pub struct Menu {
    pub id: String,
    pub label: String,
    pub items: Vec<Item>,
    /// An application's native menu button, when it exposes an accessibility
    /// action rather than an exported menu model. Never invent its contents.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub action: Option<String>,
    pub enabled: bool,
}

#[derive(Serialize, Clone, PartialEq, Debug)]
pub struct Snapshot {
    pub app_id: String,
    pub source: String,
    pub menus: Vec<Menu>,
    /// Hyprland client address the menus belong to ("" when nothing is focused).
    pub address: String,
}

/// Strip GTK/Qt mnemonic underscores: `_File` -> `File`, `__` -> `_`.
pub fn strip_mnemonic(label: &str) -> String {
    let mut out = String::with_capacity(label.len());
    let mut chars = label.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '_' {
            if chars.peek() == Some(&'_') {
                out.push('_');
                chars.next();
            }
            continue;
        }
        out.push(c);
    }
    out
}

fn runtime_json_path() -> PathBuf {
    let base = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(format!("/run/user/{}", unsafe { libc::getuid() })));
    base.join("mios").join("appmenu.json")
}

fn write_atomic(snap: &Snapshot) -> Result<(), String> {
    let path = runtime_json_path();
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    let tmp = path.with_extension("json.new");
    let body = serde_json::to_vec(snap).map_err(|e| e.to_string())?;
    {
        let mut f = std::fs::File::create(&tmp).map_err(|e| format!("{}: {e}", tmp.display()))?;
        f.write_all(&body).map_err(|e| e.to_string())?;
        f.write_all(b"\n").map_err(|e| e.to_string())?;
    }
    std::fs::rename(&tmp, &path).map_err(|e| format!("{}: {e}", path.display()))
}

// ---------------------------------------------------------------------------------------------
// Hyprland

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Client {
    pub address: String,
    pub class: String,
    pub pid: u32,
    pub xwayland: bool,
    pub stable_id: u64,
}

/// The live Hyprland instance directory. The inherited HYPRLAND_INSTANCE_SIGNATURE goes stale
/// when the compositor restarts under a long-running user service, so fall back to the newest
/// instance whose request socket accepts a connection.
fn hypr_dir() -> Option<PathBuf> {
    let rt = PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR")?).join("hypr");
    let alive = |d: &PathBuf| StdUnixStream::connect(d.join(".socket.sock")).is_ok();
    if let Ok(sig) = std::env::var("HYPRLAND_INSTANCE_SIGNATURE") {
        let d = rt.join(sig);
        if alive(&d) {
            return Some(d);
        }
    }
    let mut dirs: Vec<(std::time::SystemTime, PathBuf)> = std::fs::read_dir(&rt)
        .ok()?
        .filter_map(|e| e.ok())
        .filter_map(|e| Some((e.metadata().ok()?.modified().ok()?, e.path())))
        .collect();
    dirs.sort_by_key(|d| std::cmp::Reverse(d.0));
    dirs.into_iter().map(|(_, d)| d).find(alive)
}

fn hypr_request(cmd: &str) -> Option<serde_json::Value> {
    let mut s = StdUnixStream::connect(hypr_dir()?.join(".socket.sock")).ok()?;
    s.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
    s.write_all(cmd.as_bytes()).ok()?;
    let mut buf = Vec::new();
    s.read_to_end(&mut buf).ok()?;
    serde_json::from_slice(&buf).ok()
}

fn client_from(v: &serde_json::Value) -> Option<Client> {
    let address = v.get("address")?.as_str()?.to_string();
    Some(Client {
        address,
        class: v
            .get("class")
            .and_then(|c| c.as_str())
            .unwrap_or("")
            .to_string(),
        pid: v.get("pid").and_then(|p| p.as_i64()).unwrap_or(0).max(0) as u32,
        xwayland: v.get("xwayland").and_then(|x| x.as_bool()).unwrap_or(false),
        stable_id: v
            .get("stableId")
            .and_then(|s| s.as_str())
            .and_then(|s| u64::from_str_radix(s, 16).ok())
            .unwrap_or(0),
    })
}

/// The `app-flatpak-<app>-<instance>.scope` cgroup `pid` runs in, if it is a Flatpak process.
fn flatpak_scope(pid: u32) -> Option<String> {
    let cg = std::fs::read_to_string(format!("/proc/{pid}/cgroup")).ok()?;
    let path = cg.lines().find_map(|l| l.strip_prefix("0::"))?.trim();
    let leaf = path.rsplit('/').next()?;
    (leaf.starts_with("app-flatpak-") && leaf.ends_with(".scope")).then(|| path.to_string())
}

fn focused_client() -> Option<Client> {
    hypr_request("j/activewindow")
        .as_ref()
        .and_then(client_from)
}

fn all_clients() -> Vec<Client> {
    match hypr_request("j/clients") {
        Some(serde_json::Value::Array(list)) => list.iter().filter_map(client_from).collect(),
        _ => Vec::new(),
    }
}

// ---------------------------------------------------------------------------------------------
// Xwayland: _NET_ACTIVE_WINDOW on the root window

struct X11 {
    conn: Option<(x11rb::rust_connection::RustConnection, u32, u32)>,
}

impl X11 {
    fn active_window(&mut self) -> Option<u32> {
        use x11rb::connection::Connection as _;
        use x11rb::protocol::xproto::{AtomEnum, ConnectionExt as _};
        if self.conn.is_none() {
            let display = std::env::var("DISPLAY").unwrap_or_else(|_| ":0".into());
            let (c, screen) = x11rb::connect(Some(&display)).ok()?;
            let root = c.setup().roots.get(screen)?.root;
            let atom = c
                .intern_atom(false, b"_NET_ACTIVE_WINDOW")
                .ok()?
                .reply()
                .ok()?
                .atom;
            self.conn = Some((c, root, atom));
        }
        let (c, root, atom) = self.conn.as_ref()?;
        let reply = c
            .get_property(false, *root, *atom, AtomEnum::WINDOW, 0, 1)
            .ok()
            .and_then(|r| r.reply().ok());
        match reply {
            Some(r) => r.value32().and_then(|mut v| v.next()).filter(|w| *w != 0),
            None => {
                self.conn = None; // reconnect next time (Xwayland restarted)
                None
            }
        }
    }

    /// GTK menubar export a window advertises on X11 (`_GTK_*` window properties, set by
    /// GApplication menubars and by unity-gtk-module for plain GtkMenuBar widgets), plus its
    /// `_NET_WM_PID` so the caller can check it belongs to the focused client.
    fn gtk_export(&mut self, window: u32) -> Option<X11GtkExport> {
        use x11rb::protocol::xproto::{AtomEnum, ConnectionExt as _};
        let (c, _, _) = self.conn.as_ref()?;
        let atom = |name: &str| -> Option<u32> {
            Some(c.intern_atom(true, name.as_bytes()).ok()?.reply().ok()?.atom).filter(|a| *a != 0)
        };
        let text = |name: &str| -> Option<String> {
            let a = atom(name)?;
            let r = c
                .get_property(false, window, a, AtomEnum::ANY, 0, 1024)
                .ok()?
                .reply()
                .ok()?;
            let v = String::from_utf8(r.value).ok()?;
            Some(v.trim_end_matches('\0').to_string()).filter(|v| !v.is_empty())
        };
        let pid = atom("_NET_WM_PID").and_then(|a| {
            c.get_property(false, window, a, AtomEnum::CARDINAL, 0, 1)
                .ok()?
                .reply()
                .ok()?
                .value32()?
                .next()
        });
        Some(X11GtkExport {
            bus: text("_GTK_UNIQUE_BUS_NAME")?,
            menubar_path: text("_GTK_MENUBAR_OBJECT_PATH")?,
            app_path: text("_GTK_APPLICATION_OBJECT_PATH"),
            window_path: text("_GTK_WINDOW_OBJECT_PATH"),
            pid,
        })
    }
}

#[derive(Debug, Clone, PartialEq)]
struct X11GtkExport {
    bus: String,
    menubar_path: String,
    app_path: Option<String>,
    window_path: Option<String>,
    pid: Option<u32>,
}

/// Action group an org.gtk.Menus item action lives in. unity-gtk-module exports a GtkMenuBar
/// widget as org.gtk.Menus + org.gtk.Actions on one object path with every action in the
/// `unity.` namespace, so its target uses the app path (which is the menubar path there).
fn action_target(action: &str) -> Option<(ActionScope, &str)> {
    if let Some(n) = action.strip_prefix("app.") {
        return Some((ActionScope::App, n));
    }
    if let Some(n) = action.strip_prefix("unity.") {
        return Some((ActionScope::App, n));
    }
    action.strip_prefix("win.").map(|n| (ActionScope::Win, n))
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum ActionScope {
    App,
    Win,
}

// ---------------------------------------------------------------------------------------------
// D-Bus helpers

type Registrations = Arc<Mutex<HashMap<u32, (String, OwnedObjectPath)>>>;

fn val(v: &Value<'_>) -> Value<'static> {
    v.try_to_owned()
        .map(Value::from)
        .unwrap_or(Value::Bool(false))
}

fn unwrap_variant<'a, 'b>(v: &'b Value<'a>) -> &'b Value<'a> {
    match v {
        Value::Value(inner) => unwrap_variant(inner),
        other => other,
    }
}

fn as_str(v: &Value<'_>) -> Option<String> {
    match unwrap_variant(v) {
        Value::Str(s) => Some(s.as_str().to_string()),
        _ => None,
    }
}

fn as_bool(v: &Value<'_>) -> Option<bool> {
    match unwrap_variant(v) {
        Value::Bool(b) => Some(*b),
        _ => None,
    }
}

fn as_uu(v: &Value<'_>) -> Option<(u32, u32)> {
    match unwrap_variant(v) {
        Value::Structure(s) => match s.fields() {
            [Value::U32(a), Value::U32(b)] => Some((*a, *b)),
            _ => None,
        },
        _ => None,
    }
}

async fn introspect(conn: &Connection, dest: &str, path: &str) -> Option<String> {
    // Some connections (raw libdbus/a11y side connections) never answer Introspect; GTK answers
    // in milliseconds, so probe with a short silent bound instead of CALL_TIMEOUT.
    let fut = conn.call_method(
        Some(dest),
        path,
        Some("org.freedesktop.DBus.Introspectable"),
        "Introspect",
        &(),
    );
    let msg = tokio::time::timeout(Duration::from_millis(400), fut)
        .await
        .ok()?
        .ok()?;
    msg.body().deserialize::<String>().ok()
}

/// (child node names, interface names) from introspection XML.
fn parse_introspection(xml: &str) -> (Vec<String>, Vec<String>) {
    let attr = |tag: &str| -> Vec<String> {
        let needle = format!("<{tag} name=\"");
        xml.match_indices(&needle)
            .filter_map(|(i, _)| {
                let rest = &xml[i + needle.len()..];
                rest.find('"').map(|end| rest[..end].to_string())
            })
            .filter(|n| !n.starts_with('/'))
            .collect()
    };
    (attr("node"), attr("interface"))
}

// ---------------------------------------------------------------------------------------------
// GTK org.gtk.Menus

#[derive(Clone, Debug, PartialEq)]
struct GtkTarget {
    bus: String,
    app_path: String,
    menubar_path: String,
    window_path: Option<String>,
}

type GtkGroups = HashMap<(u32, u32), Vec<HashMap<String, OwnedValue>>>;
type GtkStart = Vec<(u32, u32, Vec<HashMap<String, OwnedValue>>)>;

/// Subscribe to every group reachable from group 0; returns the groups and subscribed ids.
/// `requested` receives each group id before its Start call is sent, so a caller whose future
/// is cancelled (snapshot timeout) or that sees an error still knows every group the app may
/// now hold a subscription for and can End it (End on a group never subscribed is a no-op).
async fn gtk_fetch(
    conn: &Connection,
    bus: &str,
    path: &str,
    requested: &mut Vec<u32>,
) -> Result<(GtkGroups, Vec<u32>), String> {
    let mut groups: GtkGroups = HashMap::new();
    let mut subscribed: BTreeSet<u32> = BTreeSet::new();
    let mut pending: Vec<u32> = vec![0];
    while !pending.is_empty() && subscribed.len() < 256 {
        for g in &pending {
            if !requested.contains(g) {
                requested.push(*g);
            }
        }
        let msg = call(
            conn,
            bus,
            path,
            "org.gtk.Menus",
            "Start",
            &(pending.clone(),),
        )
        .await
        .map_err(|e| format!("org.gtk.Menus.Start {bus}{path}: {e}"))?;
        subscribed.extend(pending.iter().copied());
        let entries: GtkStart = msg.body().deserialize().map_err(|e| e.to_string())?;
        for (g, m, items) in entries {
            groups.insert((g, m), items);
        }
        let mut next = BTreeSet::new();
        for items in groups.values() {
            for attrs in items {
                for key in [":section", ":submenu"] {
                    if let Some((g, _)) = attrs.get(key).and_then(|v| as_uu(v)) {
                        if !subscribed.contains(&g) {
                            next.insert(g);
                        }
                    }
                }
            }
        }
        pending = next.into_iter().collect();
    }
    Ok((groups, subscribed.into_iter().collect()))
}

async fn gtk_end(conn: &Connection, bus: &str, path: &str, groups: &[u32]) {
    if groups.is_empty() {
        return;
    }
    let _ = call(conn, bus, path, "org.gtk.Menus", "End", &(groups.to_vec(),)).await;
}

/// An org.gtk.Menus subscription: (bus, menubar path, subscribed group ids).
type GtkSub = (String, String, Vec<u32>);

/// Take the live subscription and any half-made one (a snapshot cancelled or failed mid
/// `gtk_fetch`), leaving both slots empty; empty group lists need no End.
fn take_subs(live: &mut Option<GtkSub>, inflight: &mut Option<GtkSub>) -> Vec<GtkSub> {
    [live.take(), inflight.take()]
        .into_iter()
        .flatten()
        .filter(|(_, _, groups)| !groups.is_empty())
        .collect()
}

/// Send End on a task of its own: the snapshot future may be cancelled by the serve loop's
/// timeout, and an End awaited inside it would then never be sent.
fn end_detached(conn: &Connection, subs: Vec<GtkSub>) {
    if subs.is_empty() {
        return;
    }
    let conn = conn.clone();
    tokio::spawn(async move {
        for (bus, path, groups) in subs {
            gtk_end(&conn, &bus, &path, &groups).await;
        }
    });
}

/// dbusmenu AboutToShow makes lazily populated apps (Qt, Gecko) rebuild the menu and emit
/// LayoutUpdated, which re-triggers a snapshot. Prime (send AboutToShow) only when the menu
/// source changes; re-snapshots of the same source (signal-driven) read the layout only, so
/// a snapshot never causes the signal that causes the next one.
fn should_prime(primed: &Option<(String, String)>, bus: &str, path: &str) -> bool {
    primed.as_ref().map_or(true, |(b, p)| b != bus || p != path)
}

type ActionStates = HashMap<String, bool>;

async fn gtk_actions(conn: &Connection, bus: &str, path: &str) -> ActionStates {
    let Ok(msg) = call(conn, bus, path, "org.gtk.Actions", "DescribeAll", &()).await else {
        return HashMap::new();
    };
    let body = msg.body();
    type Described = HashMap<String, (bool, zbus::zvariant::Signature, Vec<OwnedValue>)>;
    let Ok(map) = body.deserialize::<Described>() else {
        return HashMap::new();
    };
    map.into_iter()
        .map(|(k, (enabled, _, _))| (k, enabled))
        .collect()
}

fn gtk_action_enabled(action: &str, app: &ActionStates, win: Option<&ActionStates>) -> bool {
    match action_target(action) {
        Some((ActionScope::App, name)) => app.get(name).copied().unwrap_or(false),
        Some((ActionScope::Win, name)) => win.and_then(|w| w.get(name).copied()).unwrap_or(false),
        None => false,
    }
}

/// Build items of menu (g, m), flattening sections and recursing into submenus.
fn gtk_items(
    groups: &GtkGroups,
    key: (u32, u32),
    app: &ActionStates,
    win: Option<&ActionStates>,
    depth: usize,
) -> Vec<Item> {
    let mut out: Vec<Item> = Vec::new();
    let Some(entries) = groups.get(&key) else {
        return out;
    };
    if depth > MAX_DEPTH {
        return out;
    }
    for (idx, attrs) in entries.iter().enumerate() {
        if let Some(section) = attrs.get(":section").and_then(|v| as_uu(v)) {
            let mut sub = gtk_items(groups, section, app, win, depth + 1);
            if let (false, Some(first)) = (out.is_empty(), sub.first_mut()) {
                first.separator_before = true;
            }
            out.append(&mut sub);
            continue;
        }
        let label = attrs
            .get("label")
            .and_then(|v| as_str(v))
            .map(|l| strip_mnemonic(&l))
            .unwrap_or_default();
        let id = format!("gtk:{}.{}.{}", key.0, key.1, idx);
        if let Some(sub) = attrs.get(":submenu").and_then(|v| as_uu(v)) {
            out.push(Item {
                id,
                label,
                enabled: true,
                submenu: Some(gtk_items(groups, sub, app, win, depth + 1)),
                separator_before: false,
            });
            continue;
        }
        let enabled = attrs
            .get("action")
            .and_then(|v| as_str(v))
            .map(|a| gtk_action_enabled(&a, app, win))
            .unwrap_or(false);
        out.push(Item {
            id,
            label,
            enabled,
            submenu: None,
            separator_before: false,
        });
    }
    out
}

/// Top-level menus are the submenu items of the menubar (sections flattened).
fn gtk_menus(groups: &GtkGroups, app: &ActionStates, win: Option<&ActionStates>) -> Vec<Menu> {
    gtk_items(groups, (0, 0), app, win, 0)
        .into_iter()
        .filter_map(|item| {
            item.submenu.map(|items| Menu {
                id: item.id,
                label: item.label,
                items,
                action: None,
                enabled: item.enabled,
            })
        })
        .collect()
}

/// Locate the raw attributes of an item id `gtk:g.m.idx`.
fn gtk_lookup<'a>(groups: &'a GtkGroups, id: &str) -> Option<&'a HashMap<String, OwnedValue>> {
    let mut parts = id
        .strip_prefix("gtk:")?
        .split('.')
        .map(|p| p.parse::<u32>().ok());
    let (g, m, i) = (parts.next()??, parts.next()??, parts.next()??);
    groups.get(&(g, m))?.get(i as usize)
}

// ---------------------------------------------------------------------------------------------
// dbusmenu

fn dbm_items(children: &[Value<'_>], depth: usize) -> Vec<Item> {
    let mut out = Vec::new();
    let mut sep = false;
    if depth > MAX_DEPTH {
        return out;
    }
    for child in children {
        let Value::Structure(s) = unwrap_variant(child) else {
            continue;
        };
        let [Value::I32(id), Value::Dict(props), Value::Array(kids)] = s.fields() else {
            continue;
        };
        let prop = |k: &str| -> Option<Value<'static>> {
            props
                .iter()
                .find(|(key, _)| as_str(key).as_deref() == Some(k))
                .map(|(_, v)| val(v))
        };
        if prop("visible").as_ref().and_then(as_bool) == Some(false) {
            continue;
        }
        if prop("type").as_ref().and_then(as_str).as_deref() == Some("separator") {
            sep = !out.is_empty();
            continue;
        }
        let label = prop("label")
            .as_ref()
            .and_then(as_str)
            .map(|l| strip_mnemonic(&l))
            .unwrap_or_default();
        let enabled = prop("enabled").as_ref().and_then(as_bool).unwrap_or(true);
        let has_sub = !kids.inner().is_empty()
            || prop("children-display")
                .as_ref()
                .and_then(as_str)
                .as_deref()
                == Some("submenu");
        out.push(Item {
            id: format!("dbm:{id}"),
            label,
            enabled,
            submenu: has_sub.then(|| dbm_items(kids.inner(), depth + 1)),
            separator_before: std::mem::take(&mut sep),
        });
    }
    out
}

async fn dbm_layout(
    conn: &Connection,
    bus: &str,
    path: &str,
    prime: bool,
) -> Result<Vec<Menu>, String> {
    type Layout = (u32, (i32, HashMap<String, OwnedValue>, Vec<OwnedValue>));
    let get = || async {
        let msg = call(
            conn,
            bus,
            path,
            "com.canonical.dbusmenu",
            "GetLayout",
            &(0i32, -1i32, Vec::<String>::new()),
        )
        .await
        .map_err(|e| format!("dbusmenu GetLayout {bus}{path}: {e}"))?;
        let (_rev, (_root_id, _props, kids)): Layout = msg
            .body()
            .deserialize()
            .map_err(|e| format!("dbusmenu GetLayout {bus}{path}: {e}"))?;
        Ok::<Vec<Value<'static>>, String>(kids.iter().map(|k| val(k)).collect())
    };
    if !prime {
        return Ok(dbm_menus(&get().await?));
    }
    let _ = call(
        conn,
        bus,
        path,
        "com.canonical.dbusmenu",
        "AboutToShow",
        &(0i32,),
    )
    .await;
    let mut kids = get().await?;
    // Lazily populated toolkits (Qt) fill submenus on AboutToShow; ask once per top-level menu.
    let mut refetch = false;
    for kid in &kids {
        if let Value::Structure(k) = unwrap_variant(kid) {
            if let Some(Value::I32(id)) = k.fields().first() {
                if let Ok(m) = call(
                    conn,
                    bus,
                    path,
                    "com.canonical.dbusmenu",
                    "AboutToShow",
                    &(*id,),
                )
                .await
                {
                    refetch |= m.body().deserialize::<bool>().unwrap_or(false);
                }
            }
        }
    }
    if refetch {
        kids = get().await?;
    }
    Ok(dbm_menus(&kids))
}

fn dbm_menus(kids: &[Value<'_>]) -> Vec<Menu> {
    dbm_items(kids, 0)
        .into_iter()
        .filter_map(|item| {
            item.submenu.map(|items| Menu {
                id: item.id,
                label: item.label,
                items,
                action: None,
                enabled: item.enabled,
            })
        })
        .collect()
}

// ---------------------------------------------------------------------------------------------
// Resolution context

// AT-SPI is the toolkit-neutral fallback for native menu buttons and menu
// bars which do not publish GMenu/dbusmenu. Object refs and roles come from
// the application's own accessibility cache; no per-application labels,
// shortcuts or command lists are fabricated here.
type A11yRef = (String, OwnedObjectPath);
type A11yLegacyCacheRow = (A11yRef, A11yRef, A11yRef, Vec<A11yRef>, Vec<String>, String, u32, String, Vec<u32>);
type A11yCacheRow = (A11yRef, A11yRef, A11yRef, i32, i32, Vec<String>, String, u32, String, Vec<u32>);

#[derive(Clone, Debug)]
struct A11yNode {
    parent: String,
    index: i32,
    label: String,
    role: u32,
    interfaces: Vec<String>,
    states: Vec<u32>,
}

#[derive(Serialize, Deserialize, Debug)]
struct A11yAction {
    bus: String,
    path: String,
    frame: String,
    address: String,
    index: i32,
}

fn a11y_state(states: &[u32], bit: usize) -> bool {
    states.get(bit / 32).is_some_and(|word| word & (1 << (bit % 32)) != 0)
}

fn a11y_in_frame(nodes: &HashMap<String, A11yNode>, path: &str, frame: &str) -> bool {
    let mut current = path;
    for _ in 0..64 {
        if current == frame { return true; }
        let Some(node) = nodes.get(current) else { return false; };
        // Never turn webpage controls, document content or file-list
        // context menus into application-wide menu pills.
        if matches!(node.role, 31 | 55 | 66 | 82 | 95) { return false; }
        if node.parent == current { return false; }
        current = &node.parent;
    }
    false
}

fn a11y_enabled(node: &A11yNode) -> bool {
    !a11y_state(&node.states, 6) &&
        (a11y_state(&node.states, 8) || a11y_state(&node.states, 24))
}

fn a11y_native_button(node: &A11yNode) -> bool {
    matches!(node.role, 43 | 62 | 129) && a11y_state(&node.states, 42) &&
        !node.label.trim().is_empty() &&
        node.interfaces.iter().any(|iface| iface == "org.a11y.atspi.Action")
}

fn a11y_menubar_entry(node: &A11yNode, parent: Option<&A11yNode>) -> bool {
    // Qt Quick labels its MenuBarItem as MENU_BAR as well. Only a
    // direct child of the real menu bar qualifies, never arbitrary buttons.
    matches!(node.role, 8 | 33 | 34 | 35 | 45) && parent.is_some_and(|p| p.role == 34)
}

fn a11y_press_index(actions: &[(String, String, String)]) -> Option<i32> {
    actions.iter().position(|(name, _, _)| matches!(name.to_ascii_lowercase().as_str(),
        "click" | "press" | "activate" | "toggle" | "showmenu" | "show-menu" | "open"))
        .map(|i| i as i32)
        .or_else(|| (actions.len() == 1 && !actions[0].0.to_ascii_lowercase().contains("focus")).then_some(0))
}

async fn a11y_connection(session: &Connection) -> Result<Connection, String> {
    let message = call(session, "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress", &())
        .await.map_err(|e| e.to_string())?;
    let address: String = message.body().deserialize().map_err(|e| e.to_string())?;
    tokio::time::timeout(CALL_TIMEOUT,
        zbus::connection::Builder::address(address.as_str()).map_err(|e| e.to_string())?.build())
        .await.map_err(|_| "accessibility bus connection timed out".to_string())?
        .map_err(|e| e.to_string())
}

async fn a11y_owner_matches(bus: &Connection, name: &str, client: &Client) -> bool {
    let Ok(reply) = call(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus", "GetConnectionUnixProcessID", &(name,)).await else { return false; };
    let Ok(pid) = reply.body().deserialize::<u32>() else { return false; };
    pid == client.pid || (flatpak_scope(client.pid).is_some() && flatpak_scope(pid) == flatpak_scope(client.pid))
}

async fn a11y_cache(bus: &Connection, name: &str) -> Result<HashMap<String, A11yNode>, String> {
    let reply = call(bus, name, "/org/a11y/atspi/cache", "org.a11y.atspi.Cache", "GetItems", &())
        .await.map_err(|e| e.to_string())?;
    let body = reply.body();
    let rows = match body.deserialize::<Vec<A11yCacheRow>>() {
        Ok(rows) => rows,
        Err(_) => {
            // Qt still exports the pre-2015 cache signature. Reconstruct
            // sibling order from its real child-reference arrays.
            let legacy: Vec<A11yLegacyCacheRow> = body.deserialize().map_err(|e| e.to_string())?;
            if legacy.len() > 16384 { return Err("application accessibility cache exceeds menu discovery bound".into()); }
            let order: HashMap<_, _> = legacy.iter().filter(|r| r.0.0 == name)
                .flat_map(|r| r.3.iter().enumerate().filter(|(_, child)| child.0 == name)
                    .map(|(i, child)| (child.1.to_string(), i as i32))).collect();
            legacy.into_iter().map(|r| {
                let index = order.get(r.0.1.as_str()).copied().unwrap_or(-1);
                (r.0, r.1, r.2, index, r.3.len() as i32, r.4, r.5, r.6, r.7, r.8)
            }).collect()
        }
    };
    if rows.len() > 16384 { return Err("application accessibility cache exceeds menu discovery bound".into()); }
    Ok(rows.into_iter().filter(|r| r.0.0 == name).map(|r| (r.0.1.to_string(), A11yNode {
        parent: r.2.1.to_string(), index: r.3, interfaces: r.5,
        label: r.6, role: r.7, states: r.9,
    })).collect())
}

async fn a11y_tree(bus: &Connection, name: &str, root: &str) -> HashMap<String, A11yNode> {
    // Some Qt bridges publish an empty cache. Walk the standard accessible
    // interface in bounded batches instead; never traverse document content.
    let mut pending = std::collections::VecDeque::from([(root.to_string(), String::new(), 0, 0usize)]);
    let mut visited = BTreeSet::new();
    let mut nodes = HashMap::new();
    while !pending.is_empty() && visited.len() < 2048 {
        let mut batch = Vec::new();
        while batch.len() < 8 && visited.len() < 2048 {
            let Some(entry) = pending.pop_front() else { break; };
            if visited.insert(entry.0.clone()) { batch.push(entry); }
        }
        let fetched = futures_util::future::join_all(batch.into_iter().map(|(path, parent, index, depth)| async move {
            let interface = "org.a11y.atspi.Accessible";
            let property_args = (interface, "Name");
            let (properties, role, states, interfaces) = tokio::join!(
                call(bus, name, &path, "org.freedesktop.DBus.Properties", "Get", &property_args),
                call(bus, name, &path, interface, "GetRole", &()),
                call(bus, name, &path, interface, "GetState", &()),
                call(bus, name, &path, interface, "GetInterfaces", &()));
            let role: u32 = role.ok()?.body().deserialize().ok()?;
            let states = states.ok()?.body().deserialize().ok()?;
            let interfaces = interfaces.ok()?.body().deserialize().ok()?;
            // Qt's accessibility adaptor implements Properties.Get but
            // rejects GetAll on this virtual interface. Missing names must
            // not hide the descendants of otherwise valid containers.
            let label = properties.ok().and_then(|r| r.body().deserialize::<OwnedValue>().ok())
                .and_then(|v| as_str(&v)).unwrap_or_default();
            let children = if depth < 64 && !matches!(role, 31 | 55 | 66 | 82 | 95) {
                call(bus, name, &path, interface, "GetChildren", &()).await.ok()
                    .and_then(|r| r.body().deserialize::<Vec<A11yRef>>().ok()).unwrap_or_default()
            } else { Vec::new() };
            Some((path, A11yNode { parent, index, label, role, states, interfaces }, children, depth))
        })).await;
        for (path, node, children, depth) in fetched.into_iter().flatten() {
            for (index, (owner, child)) in children.into_iter().take(256).enumerate() {
                if owner == name && pending.len() < 2048 {
                    pending.push_back((child.to_string(), path.clone(), index as i32, depth + 1));
                }
            }
            nodes.insert(path, node);
        }
    }
    nodes
}

async fn a11y_nodes(bus: &Connection, name: &str, root: &str) -> HashMap<String, A11yNode> {
    match a11y_cache(bus, name).await {
        Ok(nodes) if !nodes.is_empty() => nodes,
        // Includes cache-less bridges and older empty Qt cache signatures.
        _ => a11y_tree(bus, name, root).await,
    }
}

fn a11y_frame(nodes: &HashMap<String, A11yNode>) -> Option<String> {
    let frames: Vec<_> = nodes.iter().filter(|(_, n)| matches!(n.role, 16 | 23) && !a11y_state(&n.states, 6)).collect();
    let active: Vec<_> = frames.iter().filter(|(_, n)| a11y_state(&n.states, 1)).collect();
    if active.len() == 1 { Some(active[0].0.clone()) }
    else if frames.len() == 1 { Some(frames[0].0.clone()) }
    else { None } // Multiple inactive windows are never paired by guesswork.
}

fn a11y_id(bus: &str, path: &str, frame: &str, client: &Client, index: i32) -> String {
    format!("a11y:{}", serde_json::to_string(&A11yAction {
        bus: bus.into(), path: path.into(), frame: frame.into(),
        address: client.address.clone(), index,
    }).expect("serializable accessibility reference"))
}

fn a11y_order(nodes: &HashMap<String, A11yNode>, path: &str) -> Vec<i32> {
    let mut order = Vec::new();
    let mut current = path;
    for _ in 0..64 {
        let Some(node) = nodes.get(current) else { break; };
        order.push(node.index);
        if node.parent == current { break; }
        current = &node.parent;
    }
    order.reverse();
    order
}

fn a11y_items(nodes: &HashMap<String, A11yNode>, bus: &str, parent: &str, frame: &str, client: &Client, depth: usize) -> Vec<Item> {
    if depth > MAX_DEPTH { return Vec::new(); }
    let mut children: Vec<_> = nodes.iter().filter(|(_, n)| n.parent == parent).collect();
    children.sort_by_key(|(_, n)| n.index);
    let mut result = Vec::new();
    let mut separator = false;
    for (path, node) in children {
        if node.role == 50 { separator = true; continue; }
        if matches!(node.role, 8 | 33 | 35 | 45) && !node.label.trim().is_empty() {
            let sub = a11y_items(nodes, bus, path, frame, client, depth + 1);
            result.push(Item { id: a11y_id(bus, path, frame, client, 0), label: strip_mnemonic(&node.label),
                enabled: a11y_enabled(node), submenu: (!sub.is_empty()).then_some(sub), separator_before: separator });
            separator = false;
        } else if matches!(node.role, 20 | 30 | 39 | 46 | 33) {
            let mut nested = a11y_items(nodes, bus, path, frame, client, depth + 1);
            if separator && !nested.is_empty() { nested[0].separator_before = true; separator = false; }
            result.extend(nested);
        }
    }
    result
}

async fn a11y_menus(session: &Connection, client: &Client, provider: &Arc<Mutex<Option<String>>>) -> Vec<Menu> {
    if let Ok(mut current) = provider.lock() { *current = None; }
    let Ok(bus) = a11y_connection(session).await else { return Vec::new(); };
    let Ok(reply) = call(&bus, "org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root",
        "org.a11y.atspi.Accessible", "GetChildren", &()).await else { return Vec::new(); };
    let Ok(apps) = reply.body().deserialize::<Vec<A11yRef>>() else { return Vec::new(); };
    for (name, root) in apps.into_iter().take(256) {
        if !a11y_owner_matches(&bus, &name, client).await { continue; }
        if let Ok(mut current) = provider.lock() { *current = Some(name.clone()); }
        let nodes = a11y_nodes(&bus, &name, root.as_str()).await;
        let Some(frame) = a11y_frame(&nodes) else { continue; };
        let mut candidates: Vec<_> = nodes.iter().filter(|(path, node)|
            a11y_in_frame(&nodes, path, &frame) &&
            (a11y_native_button(node) || a11y_menubar_entry(node, nodes.get(&node.parent))))
            .collect();
        // Menu buttons commonly all have sibling index zero inside separate
        // wrappers. Compare their complete ancestry, then path, so refreshes
        // never shuffle the pills with HashMap iteration order.
        candidates.sort_by_key(|(path, _)| (a11y_order(&nodes, path), (*path).clone()));
        let mut result = Vec::new();
        for (path, node) in candidates {
            if node.label.trim().is_empty() { continue; }
            let items = a11y_items(&nodes, &name, path, &frame, client, 0);
            let action = if items.is_empty() {
                let Ok(actions) = call(&bus, &name, path, "org.a11y.atspi.Action", "GetActions", &()).await else { continue; };
                let Ok(actions) = actions.body().deserialize::<Vec<(String, String, String)>>() else { continue; };
                let Some(index) = a11y_press_index(&actions) else { continue; };
                Some(a11y_id(&name, path, &frame, client, index))
            } else { None };
            result.push(Menu { id: a11y_id(&name, path, &frame, client, 0), label: strip_mnemonic(&node.label),
                items, action, enabled: a11y_enabled(node) });
        }
        return result;
    }
    Vec::new()
}

async fn a11y_activate(session: &Connection, client: &Client, id: &str) -> Result<(), String> {
    let action: A11yAction = serde_json::from_str(id.strip_prefix("a11y:").ok_or("invalid accessibility menu reference")?)
        .map_err(|e| e.to_string())?;
    if action.address != client.address || action.index < 0 { return Err("menu belongs to a different window".into()); }
    let bus = a11y_connection(session).await?;
    if !a11y_owner_matches(&bus, &action.bus, client).await { return Err("menu provider does not belong to the focused application".into()); }
    let nodes = a11y_nodes(&bus, &action.bus, &action.frame).await;
    let node = nodes.get(&action.path).ok_or("application menu is no longer available")?;
    if !a11y_in_frame(&nodes, &action.path, &action.frame) || a11y_frame(&nodes).as_deref() != Some(action.frame.as_str()) {
        return Err("application menu window changed".into());
    }
    if !a11y_enabled(node) { return Err("application menu action is disabled".into()); }
    let actions = call(&bus, &action.bus, &action.path, "org.a11y.atspi.Action", "GetActions", &()).await
        .map_err(|e| e.to_string())?;
    let actions = actions.body().deserialize::<Vec<(String, String, String)>>().map_err(|e| e.to_string())?;
    let index = a11y_press_index(&actions).ok_or("application menu has no activation action")?;
    let reply = call(&bus, &action.bus, &action.path, "org.a11y.atspi.Action", "DoAction", &(index,))
        .await.map_err(|e| e.to_string())?;
    if reply.body().deserialize::<bool>().unwrap_or(false) { Ok(()) }
    else { Err("application rejected the menu action".into()) }
}

struct Ctx {
    conn: Connection,
    regs: Registrations,
    pid_cache: HashMap<String, u32>,
    /// Connections recently probed without a menubar (skipped for NEGATIVE_TTL).
    no_menubar: HashMap<String, std::time::Instant>,
    x11: X11,
    /// Live org.gtk.Menus subscription kept so the app sends Changed signals to us.
    gtk_sub: Option<GtkSub>,
    /// Subscription being built by an unfinished `gtk_fetch` (Ended if the snapshot is cancelled).
    gtk_inflight: Option<GtkSub>,
    /// dbusmenu source last primed with AboutToShow (see `should_prime`).
    dbm_primed: Option<(String, String)>,
    /// Unique bus name of the current menu source (for signal filtering).
    current_bus: Arc<Mutex<Option<String>>>,
    current_a11y: Arc<Mutex<Option<String>>>,
}

#[derive(Debug)]
enum Resolved {
    Gtk(GtkTarget),
    Dbm { bus: String, path: String },
    None,
}

impl Ctx {
    fn new(conn: Connection, regs: Registrations) -> Self {
        Ctx {
            conn,
            regs,
            pid_cache: HashMap::new(),
            no_menubar: HashMap::new(),
            x11: X11 { conn: None },
            gtk_sub: None,
            gtk_inflight: None,
            dbm_primed: None,
            current_bus: Arc::new(Mutex::new(None)),
            current_a11y: Arc::new(Mutex::new(None)),
        }
    }

    async fn pid_of(&mut self, name: &str) -> Option<u32> {
        if let Some(p) = self.pid_cache.get(name) {
            return Some(*p);
        }
        let msg = call(
            &self.conn,
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetConnectionUnixProcessID",
            &(name,),
        )
        .await
        .ok()?;
        let pid: u32 = msg.body().deserialize().ok()?;
        self.pid_cache.insert(name.to_string(), pid);
        Some(pid)
    }

    async fn owner_of(&self, name: &str) -> Option<String> {
        let msg = call(
            &self.conn,
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetNameOwner",
            &(name,),
        )
        .await
        .ok()?;
        msg.body().deserialize::<String>().ok()
    }

    /// Unique names owned by `pid`, plus the owner of the well-known name `app_id`.
    async fn candidate_buses(&mut self, client: &Client) -> Vec<String> {
        let mut out = Vec::new();
        if !client.class.is_empty() && client.class.contains('.') {
            if let Some(owner) = self.owner_of(&client.class).await {
                out.push(owner);
            }
        }
        if client.pid > 0 {
            // A Flatpak app talks to the bus through its own xdg-dbus-proxy, so the bus reports the
            // proxy's PID, never the app's; both live in the same app-flatpak-<id>-<n>.scope.
            let scope = flatpak_scope(client.pid);
            let names: Vec<String> = match call(
                &self.conn,
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ListNames",
                &(),
            )
            .await
            {
                Ok(m) => m.body().deserialize().unwrap_or_default(),
                Err(_) => Vec::new(),
            };
            for n in names.into_iter().filter(|n| n.starts_with(':')) {
                let Some(pid) = self.pid_of(&n).await else { continue };
                let mine = pid == client.pid
                    || (scope.is_some() && flatpak_scope(pid).as_ref() == scope.as_ref());
                if mine && !out.contains(&n) {
                    out.push(n);
                }
            }
        }
        out
    }

    /// Breadth-first search for a `/menus/menubar` org.gtk.Menus object on `bus`.
    async fn find_menubar(&self, bus: &str) -> Option<String> {
        let mut queue = vec![("/".to_string(), 0usize)];
        let mut visited = 0;
        while let Some((path, depth)) = queue.pop() {
            visited += 1;
            if visited > 200 {
                break;
            }
            let Some(xml) = introspect(&self.conn, bus, &path).await else {
                continue;
            };
            let (children, ifaces) = parse_introspection(&xml);
            if path.ends_with("/menus/menubar") && ifaces.iter().any(|i| i == "org.gtk.Menus") {
                return Some(path);
            }
            if depth >= 8 {
                continue;
            }
            for c in children {
                // Window and action subtrees never hold the menubar; skip them.
                if c == "window" {
                    continue;
                }
                let child = if path == "/" {
                    format!("/{c}")
                } else {
                    format!("{path}/{c}")
                };
                queue.insert(0, (child, depth + 1));
            }
        }
        None
    }

    /// Map the focused client to its GtkApplicationWindow object path.
    async fn window_path(&self, bus: &str, app_path: &str, client: &Client) -> Option<String> {
        let base = format!("{app_path}/window");
        let xml = introspect(&self.conn, bus, &base).await?;
        let mut ids: Vec<u32> = parse_introspection(&xml)
            .0
            .iter()
            .filter_map(|c| c.parse().ok())
            .collect();
        ids.sort_unstable();
        if ids.len() == 1 {
            return Some(format!("{base}/{}", ids[0]));
        }
        // Several windows: GTK numbers them in creation order and Hyprland stableIds grow in
        // creation order, so pair them by rank only when the counts agree exactly.
        let mut mine: Vec<Client> = all_clients()
            .into_iter()
            .filter(|c| c.pid == client.pid)
            .collect();
        mine.sort_by_key(|c| c.stable_id);
        if mine.len() != ids.len() {
            return None;
        }
        let rank = mine.iter().position(|c| c.address == client.address)?;
        Some(format!("{base}/{}", ids[rank]))
    }

    async fn resolve(&mut self, client: &Client, skip_gtk: bool) -> Resolved {
        let now = std::time::Instant::now();
        self.no_menubar
            .retain(|_, t| now.duration_since(*t) < NEGATIVE_TTL);
        let all_buses = self.candidate_buses(client).await;
        let buses: Vec<String> = all_buses
            .iter()
            .filter(|b| !self.no_menubar.contains_key(*b))
            .cloned()
            .collect();
        // First connection of the process that answers with a menubar wins; silent side
        // connections only cost the introspection bound when nothing exports a menubar.
        // Xwayland: the window itself names its export (_GTK_* properties); trust it when the
        // properties belong to the focused client's process.
        if !skip_gtk && client.xwayland {
            if let Some(xid) = self.x11.active_window() {
                if let Some(x) = self.x11.gtk_export(xid) {
                    if x.pid.is_none() || x.pid == Some(client.pid) {
                        let app_path = x.app_path.unwrap_or_else(|| x.menubar_path.clone());
                        return Resolved::Gtk(GtkTarget {
                            bus: x.bus,
                            app_path,
                            menubar_path: x.menubar_path,
                            window_path: x.window_path,
                        });
                    }
                }
            }
        }
        let mut hit = None;
        if !skip_gtk && !buses.is_empty() {
            let outcome = {
                let this: &Ctx = self;
                let probes = buses.iter().map(|b| {
                    Box::pin(async move {
                        match this.find_menubar(b).await {
                            Some(p) => Ok((b.clone(), p)),
                            None => Err(()),
                        }
                    })
                });
                futures_util::future::select_ok(probes)
                    .await
                    .map(|(found, _)| found)
            };
            match outcome {
                Ok(found) => hit = Some(found),
                Err(()) => {
                    for b in buses {
                        self.no_menubar.insert(b, now);
                    }
                }
            }
        }
        if let Some((bus, menubar_path)) = hit {
            let app_path = menubar_path.trim_end_matches("/menus/menubar").to_string();
            let window_path = self.window_path(&bus, &app_path, client).await;
            return Resolved::Gtk(GtkTarget {
                bus,
                app_path,
                menubar_path,
                window_path,
            });
        }
        let regs: Vec<(u32, (String, OwnedObjectPath))> = self
            .regs
            .lock()
            .map(|r| r.iter().map(|(k, v)| (*k, v.clone())).collect())
            .unwrap_or_default();
        if regs.is_empty() {
            return self.wayland_dbusmenu(client, &all_buses).await;
        }
        if client.xwayland {
            if let Some(xid) = self.x11.active_window() {
                if let Some((_, (bus, path))) = regs.iter().find(|(id, _)| *id == xid) {
                    return Resolved::Dbm {
                        bus: bus.clone(),
                        path: path.to_string(),
                    };
                }
            }
        }
        let mut by_pid: Vec<&(u32, (String, OwnedObjectPath))> = Vec::new();
        for r in &regs {
            if self.pid_of(&r.1 .0).await == Some(client.pid) {
                by_pid.push(r);
            }
        }
        // Only an unambiguous PID match is trusted (one registered window for that process).
        if let [(_, (bus, path))] = by_pid.as_slice() {
            return Resolved::Dbm {
                bus: bus.clone(),
                path: path.to_string(),
            };
        }
        self.wayland_dbusmenu(client, &all_buses).await
    }

    /// Wayland-native Qt windows export their QMenuBar as `com.canonical.dbusmenu` at
    /// `/MenuBar/<n>` but announce it only through `org_kde_kwin_appmenu`, which Hyprland does
    /// not implement, so the registrar never hears of it. Find the export on the focused
    /// process's own connections; trusted only when that process exports exactly one menubar.
    async fn wayland_dbusmenu(&mut self, client: &Client, buses: &[String]) -> Resolved {
        if client.xwayland || client.pid == 0 {
            return Resolved::None;
        }
        let now = std::time::Instant::now();
        let probe: Vec<String> = buses
            .iter()
            .filter(|b| !self.no_menubar.contains_key(&format!("qt:{b}")))
            .cloned()
            .collect();
        let found: Vec<Vec<(String, String)>> = {
            let this: &Ctx = self;
            futures_util::future::join_all(probe.iter().map(|b| this.qt_menubars(b))).await
        };
        let mut hits = Vec::new();
        for (bus, list) in probe.iter().zip(found) {
            if list.is_empty() {
                self.no_menubar.insert(format!("qt:{bus}"), now);
            }
            hits.extend(list);
        }
        if hits.len() == 1 {
            let (bus, path) = hits.remove(0);
            return Resolved::Dbm { bus, path };
        }
        // One menubar per window (Firefox numbers them /com/canonical/menu/<n> in creation order,
        // Qt /MenuBar/<n>): pair them with the process's windows by creation rank, only when the
        // counts agree exactly and every export sits on one connection.
        if hits.len() < 2 || hits.iter().any(|(b, _)| *b != hits[0].0) {
            return Resolved::None;
        }
        hits.sort_by_key(|(_, p)| {
            p.rsplit('/')
                .next()
                .and_then(|n| n.parse::<u64>().ok())
                .unwrap_or(u64::MAX)
        });
        let mut mine: Vec<Client> = all_clients()
            .into_iter()
            .filter(|c| c.pid == client.pid)
            .collect();
        mine.sort_by_key(|c| c.stable_id);
        if mine.len() != hits.len() {
            return Resolved::None;
        }
        match mine.iter().position(|c| c.address == client.address) {
            Some(rank) => {
                let (bus, path) = hits.swap_remove(rank);
                Resolved::Dbm { bus, path }
            }
            None => Resolved::None,
        }
    }

    /// `(bus, path)` of every `com.canonical.dbusmenu` menubar on `bus`: Qt's `/MenuBar/<n>` and
    /// Firefox/Gecko's `/com/canonical/menu/<n>` (widget.gtk.global-menu*, libdbusmenu).
    async fn qt_menubars(&self, bus: &str) -> Vec<(String, String)> {
        let mut out = Vec::new();
        for base in ["/MenuBar", "/com/canonical/menu"] {
            let Some(xml) = introspect(&self.conn, bus, base).await else {
                continue;
            };
            for c in parse_introspection(&xml).0 {
                if c.parse::<u32>().is_err() {
                    continue;
                }
                let path = format!("{base}/{c}");
                if let Some(x) = introspect(&self.conn, bus, &path).await {
                    if parse_introspection(&x).1.iter().any(|i| i == "com.canonical.dbusmenu") {
                        out.push((bus.to_string(), path));
                    }
                }
            }
        }
        out
    }

    async fn snapshot(&mut self, client: &Option<Client>) -> Snapshot {
        let empty = |c: &Option<Client>| Snapshot {
            app_id: c.as_ref().map(|c| c.class.clone()).unwrap_or_default(),
            source: "none".into(),
            menus: Vec::new(),
            address: c.as_ref().map(|c| c.address.clone()).unwrap_or_default(),
        };
        let Some(c) = client else {
            self.drop_gtk_sub();
            self.dbm_primed = None;
            self.set_current(None);
            return empty(client);
        };
        if let Ok(mut provider) = self.current_a11y.lock() { *provider = None; }
        let resolved = self.resolve(c, false).await;
        if !matches!(resolved, Resolved::Dbm { .. }) {
            self.dbm_primed = None;
        }
        match resolved {
            Resolved::Gtk(t) => match {
                let conn = self.conn.clone();
                let inflight = self
                    .gtk_inflight
                    .insert((t.bus.clone(), t.menubar_path.clone(), Vec::new()));
                gtk_fetch(&conn, &t.bus, &t.menubar_path, &mut inflight.2).await
            } {
                Ok((groups, subscribed)) => {
                    self.gtk_inflight = None;
                    // Subscribe-new-then-End-old: GTK reference-counts per group, so a
                    // re-snapshot of the same menubar keeps exactly one subscription.
                    let old = self
                        .gtk_sub
                        .replace((t.bus.clone(), t.menubar_path.clone(), subscribed));
                    end_detached(&self.conn, old.into_iter().collect());
                    let app = gtk_actions(&self.conn, &t.bus, &t.app_path).await;
                    let win = match &t.window_path {
                        Some(p) => Some(gtk_actions(&self.conn, &t.bus, p).await),
                        None => None,
                    };
                    self.set_current(Some(t.bus.clone()));
                    Snapshot {
                        app_id: c.class.clone(),
                        source: "gtk".into(),
                        menus: gtk_menus(&groups, &app, win.as_ref()),
                        address: c.address.clone(),
                    }
                }
                Err(e) => {
                    eprintln!("[appmenu] {e}");
                    self.drop_gtk_sub();
                    self.set_current(None);
                    empty(client)
                }
            },
            Resolved::Dbm { bus, path } => {
                self.drop_gtk_sub();
                let prime = should_prime(&self.dbm_primed, &bus, &path);
                match dbm_layout(&self.conn, &bus, &path, prime).await {
                    Ok(menus) => {
                        self.dbm_primed = Some((bus.clone(), path));
                        self.set_current(Some(bus));
                        Snapshot {
                            app_id: c.class.clone(),
                            source: "dbusmenu".into(),
                            menus,
                            address: c.address.clone(),
                        }
                    }
                    Err(e) => {
                        eprintln!("[appmenu] {e}");
                        self.dbm_primed = None;
                        self.set_current(None);
                        empty(client)
                    }
                }
            }
            Resolved::None => {
                self.drop_gtk_sub();
                self.set_current(None);
                let menus = a11y_menus(&self.conn, c, &self.current_a11y).await;
                if menus.is_empty() { empty(client) }
                else { Snapshot { app_id: c.class.clone(), source: "accessibility".into(), menus, address: c.address.clone() } }
            }
        }
    }

    fn set_current(&self, bus: Option<String>) {
        if let Ok(mut cur) = self.current_bus.lock() {
            *cur = bus;
        }
    }

    /// End the live subscription and any half-made one (detached; see `end_detached`).
    fn drop_gtk_sub(&mut self) {
        let subs = take_subs(&mut self.gtk_sub, &mut self.gtk_inflight);
        end_detached(&self.conn, subs);
    }
}

// ---------------------------------------------------------------------------------------------
// com.canonical.AppMenu.Registrar

struct Registrar {
    regs: Registrations,
    tx: tokio::sync::mpsc::UnboundedSender<()>,
}

/// Table bounds: any session-bus peer can call RegisterWindow, so the table is capped overall
/// and per sender (one misbehaving client cannot crowd out the others). Real clients register
/// one entry per toplevel window; these are orders of magnitude above that.
const MAX_REGISTRATIONS: usize = 4096;
const MAX_REGISTRATIONS_PER_SENDER: usize = 256;

/// RegisterWindow policy: a window id is bound to the unique bus name that first registered
/// it. Only that sender may re-register (update the path) or unregister it; the binding ends
/// when the sender unregisters or its bus name vanishes (NameOwnerChanged, spawn_owner_watch).
fn reg_insert(
    map: &mut HashMap<u32, (String, OwnedObjectPath)>,
    window_id: u32,
    sender: &str,
    path: OwnedObjectPath,
) -> zbus::fdo::Result<()> {
    match map.get(&window_id) {
        Some((owner, _)) if owner != sender => {
            return Err(zbus::fdo::Error::AccessDenied(format!(
                "window {window_id} is registered by another connection"
            )))
        }
        Some(_) => {}
        None => {
            if map.len() >= MAX_REGISTRATIONS {
                return Err(zbus::fdo::Error::LimitsExceeded("registrar table is full".into()));
            }
            if map.values().filter(|(o, _)| o == sender).count() >= MAX_REGISTRATIONS_PER_SENDER {
                return Err(zbus::fdo::Error::LimitsExceeded(format!(
                    "{sender} holds {MAX_REGISTRATIONS_PER_SENDER} registrations"
                )));
            }
        }
    }
    map.insert(window_id, (sender.to_owned(), path));
    Ok(())
}

/// UnregisterWindow policy: Ok(true) removed, Ok(false) unknown id, Err when another sender owns it.
fn reg_remove(
    map: &mut HashMap<u32, (String, OwnedObjectPath)>,
    window_id: u32,
    sender: &str,
) -> zbus::fdo::Result<bool> {
    match map.get(&window_id) {
        None => Ok(false),
        Some((owner, _)) if owner != sender => Err(zbus::fdo::Error::AccessDenied(format!(
            "window {window_id} is registered by another connection"
        ))),
        Some(_) => Ok(map.remove(&window_id).is_some()),
    }
}

/// NameOwnerChanged(name, old, "") for a registrant: drop every window it registered.
fn reg_drop_owner(map: &mut HashMap<u32, (String, OwnedObjectPath)>, gone: &str) {
    map.retain(|_, (bus, _)| bus != gone);
}

#[zbus::interface(name = "com.canonical.AppMenu.Registrar")]
impl Registrar {
    async fn register_window(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] conn: &Connection,
        #[zbus(signal_emitter)] emitter: SignalEmitter<'_>,
        window_id: u32,
        menu_object_path: OwnedObjectPath,
    ) -> zbus::fdo::Result<()> {
        let Some(sender) = hdr.sender().map(|s| s.to_string()) else {
            return Err(zbus::fdo::Error::AccessDenied("no sender".into()));
        };
        {
            let mut r = self
                .regs
                .lock()
                .map_err(|_| zbus::fdo::Error::Failed("registrar poisoned".into()))?;
            reg_insert(&mut r, window_id, &sender, menu_object_path.clone())?;
        }
        // A sender that disconnected before this call was dispatched already had its
        // NameOwnerChanged processed; drop what it just registered so nothing leaks.
        if let Ok(dbus) = zbus::fdo::DBusProxy::new(conn).await {
            if let Ok(name) = zbus::names::BusName::try_from(sender.as_str()) {
                if dbus.name_has_owner(name).await == Ok(false) {
                    if let Ok(mut r) = self.regs.lock() {
                        reg_drop_owner(&mut r, &sender);
                    }
                    return Err(zbus::fdo::Error::NameHasNoOwner(sender));
                }
            }
        }
        let _ =
            Self::window_registered(&emitter, window_id, &sender, menu_object_path.as_ref()).await;
        let _ = self.tx.send(());
        Ok(())
    }

    async fn unregister_window(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(signal_emitter)] emitter: SignalEmitter<'_>,
        window_id: u32,
    ) -> zbus::fdo::Result<()> {
        let Some(sender) = hdr.sender().map(|s| s.to_string()) else {
            return Err(zbus::fdo::Error::AccessDenied("no sender".into()));
        };
        let removed = {
            let mut r = self
                .regs
                .lock()
                .map_err(|_| zbus::fdo::Error::Failed("registrar poisoned".into()))?;
            reg_remove(&mut r, window_id, &sender)?
        };
        if removed {
            let _ = Self::window_unregistered(&emitter, window_id).await;
            let _ = self.tx.send(());
        }
        Ok(())
    }

    fn get_menu_for_window(&self, window_id: u32) -> zbus::fdo::Result<(String, OwnedObjectPath)> {
        self.regs
            .lock()
            .ok()
            .and_then(|r| r.get(&window_id).cloned())
            .ok_or_else(|| {
                zbus::fdo::Error::InvalidArgs(format!("window {window_id} has no registered menu"))
            })
    }

    fn get_menus(&self) -> Vec<(u32, String, OwnedObjectPath)> {
        self.regs
            .lock()
            .map(|r| {
                r.iter()
                    .map(|(k, (s, p))| (*k, s.clone(), p.clone()))
                    .collect()
            })
            .unwrap_or_default()
    }

    #[zbus(signal)]
    async fn window_registered(
        emitter: &SignalEmitter<'_>,
        window_id: u32,
        service: &str,
        menu_object_path: ObjectPath<'_>,
    ) -> zbus::Result<()>;

    #[zbus(signal)]
    async fn window_unregistered(emitter: &SignalEmitter<'_>, window_id: u32) -> zbus::Result<()>;
}

// ---------------------------------------------------------------------------------------------
// serve

fn spawn_hypr_events(tx: tokio::sync::mpsc::UnboundedSender<()>) {
    std::thread::spawn(move || loop {
        if let Some(dir) = hypr_dir() {
            if let Ok(s) = StdUnixStream::connect(dir.join(".socket2.sock")) {
                for line in BufReader::new(s).lines() {
                    let Ok(line) = line else { break };
                    let relevant =
                        line.starts_with("activewindowv2>>") || line.starts_with("closewindow>>");
                    if relevant && tx.send(()).is_err() {
                        return;
                    }
                }
            }
        }
        std::thread::sleep(Duration::from_secs(2));
    });
}

async fn spawn_signal_watch(
    conn: &Connection,
    current: Arc<Mutex<Option<String>>>,
    tx: tokio::sync::mpsc::UnboundedSender<()>,
) -> Result<(), String> {
    for iface in ["org.gtk.Menus", "org.gtk.Actions", "com.canonical.dbusmenu"] {
        let rule = zbus::MatchRule::builder()
            .msg_type(zbus::message::Type::Signal)
            .interface(iface)
            .map_err(|e| e.to_string())?
            .build();
        let mut stream = zbus::MessageStream::for_match_rule(rule, conn, Some(256))
            .await
            .map_err(|e| e.to_string())?;
        let current = current.clone();
        let tx = tx.clone();
        tokio::spawn(async move {
            while let Some(Ok(msg)) = stream.next().await {
                let sender = msg.header().sender().map(|s| s.to_string());
                let relevant = current
                    .lock()
                    .map(|c| c.is_some() && *c == sender)
                    .unwrap_or(false);
                if relevant && tx.send(()).is_err() {
                    return;
                }
            }
        });
    }
    Ok(())
}

async fn spawn_owner_watch(
    conn: &Connection,
    regs: Registrations,
    tx: tokio::sync::mpsc::UnboundedSender<String>,
) -> Result<(), String> {
    let proxy = zbus::fdo::DBusProxy::new(conn)
        .await
        .map_err(|e| e.to_string())?;
    let mut stream = proxy
        .receive_name_owner_changed()
        .await
        .map_err(|e| e.to_string())?;
    tokio::spawn(async move {
        while let Some(sig) = stream.next().await {
            let Ok(args) = sig.args() else { continue };
            if args.new_owner().is_none() {
                let gone = args.name().to_string();
                if let Ok(mut r) = regs.lock() {
                    reg_drop_owner(&mut r, &gone);
                }
                if tx.send(gone).is_err() {
                    return;
                }
            }
        }
    });
    Ok(())
}

async fn spawn_a11y_watch(session: &Connection, current: Arc<Mutex<Option<String>>>,
    tx: tokio::sync::mpsc::UnboundedSender<()>) -> Result<(), String> {
    let conn = a11y_connection(session).await?;
    for interface in ["org.a11y.atspi.Cache", "org.a11y.atspi.Event.Object"] {
        let rule = zbus::MatchRule::builder().msg_type(zbus::message::Type::Signal)
            .interface(interface).map_err(|e| e.to_string())?.build();
        let mut stream = zbus::MessageStream::for_match_rule(rule, &conn, Some(64))
            .await.map_err(|e| e.to_string())?;
        let current = current.clone();
        let tx = tx.clone();
        tokio::spawn(async move {
            while let Some(Ok(msg)) = stream.next().await {
                let hdr = msg.header();
                let mine = current.lock().map(|p| p.as_deref().is_some_and(|p|
                    hdr.sender().is_some_and(|s| s.as_str() == p))).unwrap_or(false);
                let registry = hdr.path().is_some_and(|p| p.as_str() == "/org/a11y/atspi/accessible/root");
                if (mine || registry) && tx.send(()).is_err() { return; }
            }
        });
    }
    // Register only menu-relevant structural/state changes, never key or
    // pointer capture. These registrations vanish with this bus connection.
    for event in ["object:children-changed", "object:state-changed", "object:property-change:accessible-name"] {
        call(&conn, "org.a11y.atspi.Registry", "/org/a11y/atspi/registry", "org.a11y.atspi.Registry",
            "RegisterEvent", &(event, Vec::<String>::new(), "")).await.map_err(|e| e.to_string())?;
    }
    Ok(())
}

async fn serve() -> Result<(), String> {
    let regs: Registrations = Arc::new(Mutex::new(HashMap::new()));
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<()>();
    let registrar = Registrar {
        regs: regs.clone(),
        tx: tx.clone(),
    };
    let conn = zbus::connection::Builder::session()
        .map_err(|e| e.to_string())?
        .serve_at(REGISTRAR_PATH, registrar)
        .map_err(|e| e.to_string())?
        .build()
        .await
        .map_err(|e| format!("session bus: {e}"))?;
    match conn
        .request_name_with_flags(
            REGISTRAR_NAME,
            zbus::fdo::RequestNameFlags::DoNotQueue.into(),
        )
        .await
    {
        Ok(reply) => eprintln!("[appmenu] {REGISTRAR_NAME}: {reply:?}"),
        Err(e) => {
            eprintln!("[appmenu] {REGISTRAR_NAME} unavailable ({e}); dbusmenu source disabled")
        }
    }
    // This desktop consumes native application menus through AT-SPI too.
    // Advertising an accessibility consumer makes running toolkits expose
    // their trees; it does not enable screen-reader behavior or alter speech.
    if let Err(e) = call(&conn, "org.a11y.Bus", "/org/a11y/bus", "org.freedesktop.DBus.Properties",
        "Set", &("org.a11y.Status", "IsEnabled", Value::from(true))).await {
        eprintln!("[appmenu] accessibility integration unavailable: {e}");
    }
    let mut ctx = Ctx::new(conn.clone(), regs.clone());
    if let Err(e) = spawn_a11y_watch(&conn, ctx.current_a11y.clone(), tx.clone()).await {
        eprintln!("[appmenu] accessibility updates unavailable: {e}");
    }
    spawn_hypr_events(tx.clone());
    spawn_signal_watch(&conn, ctx.current_bus.clone(), tx.clone()).await?;
    let (gone_tx, mut gone_rx) = tokio::sync::mpsc::unbounded_channel::<String>();
    spawn_owner_watch(&conn, regs.clone(), gone_tx).await?;

    let mut last: Option<Snapshot> = None;
    let mut dirty = true;
    loop {
        if dirty {
            // Debounce bursts (focus + title + menu signals arrive together).
            tokio::time::sleep(Duration::from_millis(60)).await;
            while rx.try_recv().is_ok() {}
            let focused = focused_client();
            let snap =
                match tokio::time::timeout(Duration::from_secs(8), ctx.snapshot(&focused)).await {
                    Ok(snap) => snap,
                    Err(_) => {
                        eprintln!("[appmenu] menu resolution timed out; exporting no menus");
                        // End the live subscription and whatever the cancelled gtk_fetch
                        // already started, so the app stops streaming Changed to us.
                        ctx.drop_gtk_sub();
                        ctx.dbm_primed = None;
                        ctx.set_current(None);
                        Snapshot {
                            app_id: focused
                                .as_ref()
                                .map(|c| c.class.clone())
                                .unwrap_or_default(),
                            source: "none".into(),
                            menus: Vec::new(),
                            address: focused
                                .as_ref()
                                .map(|c| c.address.clone())
                                .unwrap_or_default(),
                        }
                    }
                };
            if last.as_ref() != Some(&snap) {
                if let Err(e) = write_atomic(&snap) {
                    eprintln!("[appmenu] write: {e}");
                }
                last = Some(snap);
            }
            dirty = false;
        }
        tokio::select! {
            got = rx.recv() => {
                if got.is_none() { return Err("event channel closed".into()); }
                dirty = true;
            }
            gone = gone_rx.recv() => {
                if let Some(name) = gone {
                    ctx.pid_cache.remove(&name);
                    let was_current = ctx.current_bus.lock().map(|c| c.as_deref() == Some(name.as_str())).unwrap_or(false);
                    if was_current {
                        ctx.gtk_sub = None; // the peer is gone; nothing to End
                        ctx.gtk_inflight = None;
                        ctx.dbm_primed = None;
                        dirty = true;
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------------------------
// activate

async fn activate(app_id: &str, item_id: &str, window: Option<&str>) -> Result<(), String> {
    let conn = Connection::session().await.map_err(|e| e.to_string())?;
    let client = focused_client().filter(|c| c.class == app_id)
        .ok_or("the focused application changed before menu activation")?;
    if window.is_some_and(|address| address != client.address) {
        return Err("menu belongs to a different window".into());
    }
    let regs: Registrations = Arc::new(Mutex::new(HashMap::new()));
    if item_id.starts_with("a11y:") {
        return a11y_activate(&conn, &client, item_id).await;
    }
    if item_id.starts_with("dbm:") {
        // The registrar lives in `serve`; read its registrations over the bus.
        let msg = call(
            &conn,
            REGISTRAR_NAME,
            REGISTRAR_PATH,
            REGISTRAR_NAME,
            "GetMenus",
            &(),
        )
        .await
        .map_err(|e| format!("registrar: {e}"))?;
        let list: Vec<(u32, String, OwnedObjectPath)> =
            msg.body().deserialize().map_err(|e| e.to_string())?;
        if let Ok(mut r) = regs.lock() {
            for (id, bus, path) in list {
                r.insert(id, (bus, path));
            }
        }
    }
    let mut ctx = Ctx::new(conn.clone(), regs);
    match ctx.resolve(&client, item_id.starts_with("dbm:")).await {
        Resolved::Gtk(t) if item_id.starts_with("gtk:") => {
            let mut requested = Vec::new();
            let fetched = gtk_fetch(&conn, &t.bus, &t.menubar_path, &mut requested).await;
            let (groups, subscribed) = match fetched {
                Ok(v) => v,
                Err(e) => {
                    gtk_end(&conn, &t.bus, &t.menubar_path, &requested).await;
                    return Err(e);
                }
            };
            let result = async {
                let attrs = gtk_lookup(&groups, item_id)
                    .ok_or_else(|| format!("{item_id}: no such item"))?;
                let action = attrs
                    .get("action")
                    .and_then(|v| as_str(v))
                    .ok_or_else(|| format!("{item_id}: item has no action"))?;
                let (path, name) = match action_target(&action) {
                    Some((ActionScope::App, n)) => (t.app_path.clone(), n.to_string()),
                    Some((ActionScope::Win, n)) => (
                        t.window_path.clone().ok_or(
                            "window action but the focused GTK window could not be mapped",
                        )?,
                        n.to_string(),
                    ),
                    None => return Err(format!("{action}: unsupported action namespace")),
                };
                if gtk_actions(&conn, &t.bus, &path).await.get(&name) != Some(&true) {
                    return Err(format!("{action}: action is disabled or not exported"));
                }
                let params: Vec<Value<'_>> = match attrs.get("target") {
                    Some(t) => vec![val(t)],
                    None => Vec::new(),
                };
                let platform: HashMap<&str, Value<'_>> = HashMap::new();
                call(
                    &conn,
                    t.bus.as_str(),
                    path.as_str(),
                    "org.gtk.Actions",
                    "Activate",
                    &(name.as_str(), params, platform),
                )
                .await
                .map_err(|e| format!("org.gtk.Actions.Activate {action}: {e}"))?;
                Ok(())
            }
            .await;
            gtk_end(&conn, &t.bus, &t.menubar_path, &subscribed).await;
            result
        }
        Resolved::Dbm { bus, path } if item_id.starts_with("dbm:") => {
            let id: i32 = item_id[4..]
                .parse()
                .map_err(|_| format!("{item_id}: bad dbusmenu id"))?;
            fn find(items: &[Item], id: &str) -> Option<bool> {
                items.iter().find_map(|i| {
                    if i.id == id {
                        Some(i.enabled && i.submenu.is_none())
                    } else {
                        i.submenu.as_deref().and_then(|s| find(s, id))
                    }
                })
            }
            let menus = dbm_layout(&conn, &bus, &path, true).await?;
            match menus.iter().find_map(|m| find(&m.items, item_id)) {
                Some(true) => {}
                Some(false) => return Err(format!("{item_id}: item is disabled or a submenu")),
                None => return Err(format!("{item_id}: no such item")),
            }
            call(
                &conn,
                bus.as_str(),
                path.as_str(),
                "com.canonical.dbusmenu",
                "Event",
                &(id, "clicked", Value::I32(0), 0u32),
            )
            .await
            .map_err(|e| format!("dbusmenu Event: {e}"))?;
            Ok(())
        }
        other => Err(format!(
            "{app_id}: item {item_id} does not match the exported menu source ({other:?})"
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn accessible(role: u32, label: &str, parent: &str, flags: &[usize]) -> A11yNode {
        let mut states = vec![0, 0];
        for &bit in flags { states[bit / 32] |= 1 << (bit % 32); }
        A11yNode { parent: parent.into(), index: 0, label: label.into(), role,
            interfaces: vec!["org.a11y.atspi.Action".into()], states }
    }

    #[test]
    fn accessibility_menu_activation_selects_press_instead_of_focus() {
        let action = |name: &str| (name.into(), String::new(), String::new());
        assert_eq!(a11y_press_index(&[action("SetFocus"), action("Press")]), Some(1));
        assert_eq!(a11y_press_index(&[action("click")]), Some(0));
        assert_eq!(a11y_press_index(&[action("ShowMenu")]), Some(0));
        assert_eq!(a11y_press_index(&[action("SetFocus")]), None);
        assert_eq!(a11y_press_index(&[]), None);
    }

    #[test]
    fn accessibility_menubar_entries_support_native_qt_quick_roles() {
        let mut bar = A11yNode { parent: String::new(), index: 0, label: String::new(),
            role: 34, interfaces: Vec::new(), states: Vec::new() };
        let mut item = bar.clone();
        item.label = "File".into();
        for role in [33, 34, 35] {
            item.role = role;
            assert!(a11y_menubar_entry(&item, Some(&bar)));
        }
        item.role = 29; // labels inside bars are not menu entries
        assert!(!a11y_menubar_entry(&item, Some(&bar)));
        item.role = 34;bar.role = 20;
        assert!(!a11y_menubar_entry(&item, Some(&bar)));
        assert!(!a11y_menubar_entry(&item, None));
    }

    #[test]
    fn accessibility_nested_menu_buttons_follow_application_order() {
        let node = |parent: &str, index| A11yNode { parent: parent.into(), index,
            label: String::new(), role: 43, interfaces: Vec::new(), states: Vec::new() };
        let nodes: HashMap<String, A11yNode> = [
            ("/frame".into(), node("/", 0)),
            ("/first".into(), node("/frame", 2)),
            ("/second".into(), node("/frame", 4)),
            ("/z_button".into(), node("/first", 0)),
            ("/a_button".into(), node("/second", 0)),
        ].into();
        assert!(a11y_order(&nodes, "/z_button") < a11y_order(&nodes, "/a_button"));
    }

    #[test]
    fn accessibility_state_words_are_bitsets_and_support_gtk4_sensitive() {
        let node = accessible(62, "Menu", "/frame", &[24, 42]);
        assert!(a11y_state(&node.states, 42));
        assert!(a11y_enabled(&node)); // GTK4 reports Sensitive without Enabled.
        assert!(!a11y_state(&[24, 42], 42));
        assert!(!a11y_state(&[], 42));
        assert!(!a11y_enabled(&accessible(62, "Menu", "/frame", &[6, 24, 42])));
    }

    #[test]
    fn accessibility_menu_buttons_use_semantics_not_app_names_or_labels() {
        assert!(a11y_native_button(&accessible(62, "Menu principal", "/frame", &[24, 42])));
        assert!(a11y_native_button(&accessible(129, "Options", "/frame", &[24, 42])));
        assert!(!a11y_native_button(&accessible(62, "Menu", "/frame", &[24])));
        assert!(!a11y_native_button(&accessible(62, "", "/frame", &[24, 42])));
        assert!(!a11y_native_button(&accessible(43, "Save", "/frame", &[24])));
    }

    #[test]
    fn accessibility_chooses_the_active_window_and_refuses_ambiguous_windows() {
        let mut nodes = HashMap::from([
            ("/first".into(), accessible(23, "First", "/app", &[])),
            ("/second".into(), accessible(23, "Second", "/app", &[])),
        ]);
        assert!(a11y_frame(&nodes).is_none());
        nodes.get_mut("/second").unwrap().states[0] |= 1 << 1;
        assert_eq!(a11y_frame(&nodes).as_deref(), Some("/second"));
        nodes.get_mut("/first").unwrap().states[0] |= 1 << 1;
        assert!(a11y_frame(&nodes).is_none());
    }

    #[test]
    fn accessibility_never_promotes_webpage_and_file_list_menus() {
        let mut nodes = HashMap::from([
            ("/frame".into(), accessible(23, "Application", "/app", &[1])),
            ("/toolbar".into(), accessible(39, "", "/frame", &[])),
            ("/menu".into(), accessible(62, "Main Menu", "/toolbar", &[24, 42])),
            ("/document".into(), accessible(95, "Web content", "/frame", &[])),
            ("/webmenu".into(), accessible(62, "Page menu", "/document", &[24, 42])),
            ("/files".into(), accessible(55, "Files", "/frame", &[])),
            ("/filemenu".into(), accessible(62, "Context menu", "/files", &[24, 42])),
        ]);
        assert!(a11y_in_frame(&nodes, "/menu", "/frame"));
        assert!(!a11y_in_frame(&nodes, "/webmenu", "/frame"));
        assert!(!a11y_in_frame(&nodes, "/filemenu", "/frame"));
        assert!(!a11y_in_frame(&nodes, "/menu", "/other-frame"));
        nodes.get_mut("/toolbar").unwrap().parent = "/menu".into();
        assert!(!a11y_in_frame(&nodes, "/menu", "/frame"));
    }

    #[test]
    fn accessibility_references_bind_the_original_window_and_exact_object() {
        let client = Client { address: "0x1234".into(), class: "app.example.Editor".into(), ..Client::default() };
        let id = a11y_id(":1.42", "/app/menu/3", "/app/window/2", &client, 0);
        let decoded: A11yAction = serde_json::from_str(id.strip_prefix("a11y:").unwrap()).unwrap();
        assert_eq!(decoded.address, "0x1234");
        assert_eq!(decoded.path, "/app/menu/3");
        assert_eq!(decoded.frame, "/app/window/2");
        assert_eq!(decoded.bus, ":1.42");
    }

    fn ov(v: Value<'_>) -> OwnedValue {
        OwnedValue::try_from(v).expect("owned value")
    }

    fn path(p: &str) -> OwnedObjectPath {
        OwnedObjectPath::try_from(p).unwrap()
    }

    #[test]
    fn registrar_owner_may_register_update_and_unregister() {
        let mut m = HashMap::new();
        reg_insert(&mut m, 7, ":1.10", path("/MenuBar/1")).unwrap();
        reg_insert(&mut m, 7, ":1.10", path("/MenuBar/2")).unwrap();
        assert_eq!(m[&7].1.as_str(), "/MenuBar/2");
        assert_eq!(reg_remove(&mut m, 8, ":1.10").unwrap(), false);
        assert_eq!(reg_remove(&mut m, 7, ":1.10").unwrap(), true);
        assert!(m.is_empty());
    }

    #[test]
    fn registrar_rejects_other_peers_hijacking_a_window() {
        let mut m = HashMap::new();
        reg_insert(&mut m, 7, ":1.10", path("/MenuBar/1")).unwrap();
        assert!(matches!(
            reg_insert(&mut m, 7, ":1.66", path("/evil")),
            Err(zbus::fdo::Error::AccessDenied(_))
        ));
        assert!(matches!(reg_remove(&mut m, 7, ":1.66"), Err(zbus::fdo::Error::AccessDenied(_))));
        assert_eq!(m[&7], (":1.10".to_owned(), path("/MenuBar/1")));
    }

    #[test]
    fn registrar_drops_a_vanished_senders_windows_only() {
        let mut m = HashMap::new();
        reg_insert(&mut m, 1, ":1.10", path("/a")).unwrap();
        reg_insert(&mut m, 2, ":1.10", path("/b")).unwrap();
        reg_insert(&mut m, 3, ":1.11", path("/c")).unwrap();
        reg_drop_owner(&mut m, ":1.10");
        assert_eq!(m.keys().copied().collect::<Vec<_>>(), vec![3]);
        // After the owner is gone, the id is free for a new client.
        reg_insert(&mut m, 1, ":1.12", path("/d")).unwrap();
    }

    #[test]
    fn registrar_table_is_capped_per_sender_and_overall() {
        let mut m = HashMap::new();
        for id in 0..MAX_REGISTRATIONS_PER_SENDER as u32 {
            reg_insert(&mut m, id, ":1.10", path("/m")).unwrap();
        }
        assert!(matches!(
            reg_insert(&mut m, 1_000_000, ":1.10", path("/m")),
            Err(zbus::fdo::Error::LimitsExceeded(_))
        ));
        // Updating an existing entry is not growth and stays allowed at the cap.
        reg_insert(&mut m, 0, ":1.10", path("/m2")).unwrap();
        // Other senders still get in until the global cap.
        let mut sender = 0;
        let mut id = 10_000u32;
        while m.len() < MAX_REGISTRATIONS {
            let name = format!(":2.{}", sender / MAX_REGISTRATIONS_PER_SENDER);
            reg_insert(&mut m, id, &name, path("/m")).unwrap();
            sender += 1;
            id += 1;
        }
        assert!(matches!(
            reg_insert(&mut m, u32::MAX, ":9.9", path("/m")),
            Err(zbus::fdo::Error::LimitsExceeded(_))
        ));
        assert_eq!(m.len(), MAX_REGISTRATIONS);
    }

    fn sub(bus: &str, groups: &[u32]) -> Option<GtkSub> {
        Some((bus.to_owned(), "/menus/menubar".to_owned(), groups.to_vec()))
    }

    #[test]
    fn timeout_ends_live_and_half_made_subscriptions() {
        // Defect: the timeout path did `gtk_sub = None`, so the app kept streaming Changed.
        let (mut live, mut inflight) = (sub(":1.5", &[0, 1, 2]), sub(":1.9", &[0]));
        let ended = take_subs(&mut live, &mut inflight);
        assert_eq!(ended, vec![sub(":1.5", &[0, 1, 2]).unwrap(), sub(":1.9", &[0]).unwrap()]);
        assert!(live.is_none() && inflight.is_none(), "slots must be cleared");
    }

    #[test]
    fn nothing_to_end_without_subscriptions() {
        let (mut live, mut inflight) = (None, sub(":1.9", &[]));
        assert!(take_subs(&mut live, &mut inflight).is_empty());
        assert!(inflight.is_none());
    }

    #[test]
    fn about_to_show_only_when_the_menu_source_changes() {
        let mut primed = None;
        assert!(should_prime(&primed, ":1.4", "/MenuBar/1"), "first sight primes");
        primed = Some((":1.4".to_owned(), "/MenuBar/1".to_owned()));
        // Negative control: the signal-driven re-snapshot of the same source must not send
        // AboutToShow again (that LayoutUpdated -> snapshot -> AboutToShow loop never ended).
        assert!(!should_prime(&primed, ":1.4", "/MenuBar/1"));
        assert!(should_prime(&primed, ":1.4", "/MenuBar/2"), "other window primes");
        assert!(should_prime(&primed, ":1.7", "/MenuBar/1"), "other app primes");
    }

    #[test]
    fn mnemonics_keep_literal_text_and_edge_underscores() {
        assert_eq!(strip_mnemonic("_<b>Bold</b>"), "<b>Bold</b>"); // markup survives as text; QML renders PlainText
        assert_eq!(strip_mnemonic("Snake__case__name"), "Snake_case_name");
        assert_eq!(strip_mnemonic("Trailing_"), "Trailing");
        assert_eq!(strip_mnemonic("___x"), "_x");
    }

    #[test]
    fn mnemonics() {
        assert_eq!(strip_mnemonic("_File"), "File");
        assert_eq!(strip_mnemonic("Save __As"), "Save _As");
        assert_eq!(strip_mnemonic("Open…"), "Open…");
    }

    #[test]
    fn gtk_tree_flattens_sections_and_marks_separators() {
        let mut groups: GtkGroups = HashMap::new();
        let item = |pairs: Vec<(&str, Value<'static>)>| {
            pairs
                .into_iter()
                .map(|(k, v)| (k.to_string(), ov(v)))
                .collect::<HashMap<_, _>>()
        };
        groups.insert(
            (0, 0),
            vec![item(vec![
                ("label", Value::from("_File")),
                (":submenu", Value::from((0u32, 1u32))),
            ])],
        );
        groups.insert(
            (0, 1),
            vec![
                item(vec![(":section", Value::from((1u32, 0u32)))]),
                item(vec![(":section", Value::from((1u32, 1u32)))]),
            ],
        );
        groups.insert(
            (1, 0),
            vec![item(vec![
                ("label", Value::from("_Open")),
                ("action", Value::from("app.open")),
            ])],
        );
        groups.insert(
            (1, 1),
            vec![item(vec![
                ("label", Value::from("Close")),
                ("action", Value::from("win.close")),
            ])],
        );
        let app: ActionStates = [("open".to_string(), true)].into();
        let menus = gtk_menus(&groups, &app, None);
        assert_eq!(menus.len(), 1);
        assert_eq!(menus[0].label, "File");
        assert_eq!(menus[0].items.len(), 2);
        assert!(menus[0].items[0].enabled && !menus[0].items[0].separator_before);
        assert!(!menus[0].items[1].enabled && menus[0].items[1].separator_before);
        assert_eq!(menus[0].items[0].id, "gtk:1.0.0");
        assert!(gtk_lookup(&groups, "gtk:1.1.0").is_some());
    }

    #[test]
    fn unity_actions_resolve_to_the_menubar_action_group() {
        assert_eq!(action_target("unity.-Open---"), Some((ActionScope::App, "-Open---")));
        assert_eq!(action_target("app.quit"), Some((ActionScope::App, "quit")));
        assert_eq!(action_target("win.close"), Some((ActionScope::Win, "close")));
        assert_eq!(action_target("doc.save"), None);
        let app: ActionStates =
            [("-Copy".to_string(), true), ("Disabled-Item".to_string(), false)].into();
        assert!(gtk_action_enabled("unity.-Copy", &app, None));
        assert!(!gtk_action_enabled("unity.Disabled-Item", &app, None));
        assert!(!gtk_action_enabled("unity.missing", &app, None));
    }

    #[test]
    fn introspection_parse() {
        let xml = r#"<node><interface name="org.gtk.Menus"><method name="Start"/></interface><node name="window"/><node name="menus"/></node>"#;
        let (kids, ifaces) = parse_introspection(xml);
        assert_eq!(kids, vec!["window", "menus"]);
        assert_eq!(ifaces, vec!["org.gtk.Menus"]);
    }
}
