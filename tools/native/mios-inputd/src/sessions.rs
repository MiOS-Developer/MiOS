// AI-hint: Validated controller ownership of explicit local or remote terminal panes; argv planning never falls back to host-global input.
// AI-related: usr/share/mios/mios.toml [keybindings.global.controller.sessions], config.rs, runtime.rs, nav.rs
// AI-functions: validate, route_for_device, plan

use crate::config::GlobalConfig;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Component, Path, PathBuf};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SessionRoute {
    pub name: String,
    pub controllers: Vec<PathBuf>,
    pub socket: PathBuf,
    pub session: String,
    pub pane: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ssh_host: Option<String>,
}

fn printable_path(path: &Path) -> bool {
    path.to_str()
        .is_some_and(|text| !text.chars().any(char::is_control))
        && path.is_absolute()
        && path
            .components()
            .all(|part| matches!(part, Component::RootDir | Component::Normal(_)))
}

fn ssh_host_valid(host: &str) -> bool {
    let mut parts = host.split('@');
    let first = parts.next().unwrap_or_default();
    let second = parts.next();
    if parts.next().is_some() {
        return false;
    }
    let (user, hostname) = second.map_or((None, first), |name| (Some(first), name));
    let safe = |text: &str| {
        !text.is_empty()
            && !text.starts_with('-')
            && text
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"._-:[]%".contains(&c))
    };
    safe(hostname)
        && user.is_none_or(|name| {
            !name.is_empty()
                && !name.starts_with('-')
                && name
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b"._-".contains(&c))
        })
}

/// Structural validation does not depend on device availability: a disconnected
/// by-id path retains its owner and becomes matchable again when it reconnects.
pub fn validate(routes: &[SessionRoute]) -> Result<(), String> {
    let mut names = BTreeMap::new();
    let mut devices = BTreeMap::new();
    for route in routes {
        if route.name.is_empty()
            || route.name.len() > 64
            || route.name.trim() != route.name
            || route.name.chars().any(char::is_control)
            || names.insert(route.name.as_str(), ()).is_some()
        {
            return Err("controller.sessions: names must be unique, printable, nonempty and at most 64 bytes".into());
        }
        if route.controllers.is_empty() {
            return Err(format!(
                "controller.sessions.{}: controllers must not be empty",
                route.name
            ));
        }
        for path in &route.controllers {
            if !printable_path(path)
                || !path.starts_with("/dev/input")
                || path == Path::new("/dev/input")
            {
                return Err(format!("controller.sessions.{}: controller paths must be absolute beneath /dev/input without traversal", route.name));
            }
            if let Some(owner) = devices.insert(path, route.name.as_str()) {
                return Err(format!(
                    "controller.sessions: controller {} belongs to both {owner} and {}",
                    path.display(),
                    route.name
                ));
            }
        }
        if !printable_path(&route.socket) || route.socket == Path::new("/") {
            return Err(format!("controller.sessions.{}: socket must be an absolute printable file path without traversal", route.name));
        }
        if route.session.trim().is_empty() || route.session.chars().any(char::is_control) {
            return Err(format!(
                "controller.sessions.{}: session must be a nonempty printable exact session name",
                route.name
            ));
        }
        if !route
            .pane
            .strip_prefix('%')
            .is_some_and(|id| !id.is_empty() && id.bytes().all(|c| c.is_ascii_digit()))
        {
            return Err(format!(
                "controller.sessions.{}: pane must be a stable %digits pane ID",
                route.name
            ));
        }
        if route
            .ssh_host
            .as_deref()
            .is_some_and(|host| !ssh_host_valid(host))
        {
            return Err(format!("controller.sessions.{}: ssh_host must be a safe host alias or user@host without options or shell syntax", route.name));
        }
    }
    Ok(())
}

fn canonical(path: &Path) -> Result<Option<PathBuf>, String> {
    match std::fs::canonicalize(path) {
        Ok(path) => Ok(Some(path)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(format!(
            "controller session device {} cannot be resolved: {error}",
            path.display()
        )),
    }
}

fn resolve_routes(
    routes: &[SessionRoute],
    path: &Path,
    mut canonicalize: impl FnMut(&Path) -> Result<Option<PathBuf>, String>,
) -> Result<Option<SessionRoute>, String> {
    let actual = canonicalize(path)?;
    let mut matched = None;
    for (index, route) in routes.iter().enumerate() {
        for device in &route.controllers {
            let resolved = canonicalize(device)?;
            if device == path
                || actual
                    .as_ref()
                    .zip(resolved.as_ref())
                    .is_some_and(|(a, b)| a == b)
            {
                if matched.is_some_and(|owner| owner != index) {
                    return Err(
                        "controller.sessions: device has ambiguous session ownership".into(),
                    );
                }
                matched = Some(index);
            }
        }
    }
    Ok(matched.map(|index| routes[index].clone()))
}

/// Match the literal path or its canonical aliases, rejecting cross-session
/// alias collisions for this device without blocking unrelated controllers. Resolution errors
/// fail closed; no caller should turn an error into host-global routing.
pub fn route_for_device(
    routes: &[SessionRoute],
    path: &Path,
) -> Result<Option<SessionRoute>, String> {
    validate(routes)?;
    resolve_routes(routes, path, canonical)
}

fn tmux_key(text: &str) -> Result<String, String> {
    let chord = crate::nav::parse_key_chord(text)?;
    let (symbol, _) = crate::nav::keysym(&chord);
    let shifted_tab = symbol == "Tab" && chord.mods.contains(&42);
    let key = match symbol.as_str() {
        "Return" => "Enter",
        "Tab" if shifted_tab => "BTab",
        "BackSpace" => "BSpace",
        "Delete" => "DC",
        "Insert" => "IC",
        "Prior" => "PPage",
        "Next" => "NPage",
        "space" => "Space",
        "minus" => "-",
        "equal" => "=",
        "Menu" => {
            return Err("controller session: Compose/Menu has no portable terminal key".into())
        }
        other => other,
    };
    let mut modifiers = String::new();
    for code in &chord.mods {
        modifiers.push_str(match code {
            29 => "C-",
            56 => "M-",
            42 if shifted_tab => "",
            42 => "S-",
            _ => {
                return Err(
                    "controller session: Super has no portable terminal key modifier".into(),
                )
            }
        });
    }
    Ok(format!("{modifiers}{key}"))
}

fn quote(argument: &str) -> String {
    format!("'{}'", argument.replace('\'', "'\\''"))
}

fn transport(route: &SessionRoute, rest: Vec<String>) -> Result<Vec<String>, String> {
    validate(std::slice::from_ref(route))?;
    let mut command = vec![
        "/usr/bin/tmux".into(),
        "-S".into(),
        route.socket.to_string_lossy().into_owned(),
    ];
    command.extend(rest);
    if let Some(host) = route.ssh_host.as_ref() {
        let remote = command
            .iter()
            .map(|part| quote(part))
            .collect::<Vec<_>>()
            .join(" ");
        Ok(vec![
            "/usr/bin/ssh".into(),
            "-o".into(),
            "BatchMode=yes".into(),
            "--".into(),
            host.clone(),
            remote,
        ])
    } else {
        Ok(command)
    }
}

/// Printable separators survive tmux display-message sanitization. The untrusted
/// session name is last, so it can contain the delimiter without changing fields.
pub const IDENTITY_FORMAT: &str = "#{pid}|#{window_id}|#{pane_id}|#{session_name}";

pub struct Identity<'a> {
    pub pid: u32,
    pub window: &'a str,
    pub pane: &'a str,
    pub session: &'a str,
}

pub fn parse_identity(reply: &str) -> Result<Identity<'_>, String> {
    let parts: Vec<_> = reply
        .trim_end_matches(['\r', '\n'])
        .splitn(4, '|')
        .collect();
    if parts.len() != 4 || parts[3].trim().is_empty() || parts[3].chars().any(char::is_control) {
        return Err("tmux did not report an exact session/pane identity".into());
    }
    let pid = parts[0]
        .parse::<u32>()
        .ok()
        .filter(|p| *p > 0)
        .ok_or("invalid tmux server identity")?;
    for (value, prefix, label) in [(parts[1], '@', "window"), (parts[2], '%', "pane")] {
        if !value
            .strip_prefix(prefix)
            .is_some_and(|n| !n.is_empty() && n.bytes().all(|c| c.is_ascii_digit()))
        {
            return Err(format!("invalid tmux {label} identity"));
        }
    }
    Ok(Identity {
        pid,
        window: parts[1],
        pane: parts[2],
        session: parts[3],
    })
}

/// Query identity before dispatch: callers compare the exact session name and
/// pane, and pin the server PID for the worker lifetime. No configured value is
/// interpolated into a tmux format expression.
pub fn inspect_argv(route: &SessionRoute) -> Result<Vec<String>, String> {
    transport(
        route,
        vec![
            "display-message".into(),
            "-p".into(),
            "-t".into(),
            route.pane.clone(),
            IDENTITY_FORMAT.into(),
        ],
    )
}

/// P3: the pane's process (its tty's foreground group is the verified app). Local routes only: an ssh route's pane
/// runs on another host, whose /proc this bridge cannot verify, so it never selects an app.
pub fn pane_pid_argv(route: &SessionRoute) -> Result<Vec<String>, String> {
    if route.ssh_host.is_some() {
        return Err("controller session: an ssh route never selects a foreground app".into());
    }
    transport(
        route,
        vec![
            "display-message".into(),
            "-p".into(),
            "-t".into(),
            route.pane.clone(),
            "#{pane_pid}".into(),
        ],
    )
}

/// Compatibility query for callers which only need an exact session-name
/// guard. Workers should also use inspect_argv to pin the server lifetime.
pub fn verify_argv(route: &SessionRoute) -> Result<Vec<String>, String> {
    transport(
        route,
        vec![
            "display-message".into(),
            "-p".into(),
            "-t".into(),
            route.pane.clone(),
            "#{session_name}".into(),
        ],
    )
}

/// Select an already resolved and identity-verified pane by its exact ID.
pub fn select_argv(route: &SessionRoute) -> Result<Vec<String>, String> {
    transport(
        route,
        vec!["select-pane".into(), "-t".into(), route.pane.clone()],
    )
}

/// Resolve focus from the explicit source pane's layout rather than a client's
/// active pane. The parent verifies and selects the result before replacing its
/// private controller cursor. A missing neighbor leaves the cursor unchanged.
pub fn focus_target(
    route: &SessionRoute,
    action: &str,
    output: &str,
) -> Result<SessionRoute, String> {
    validate(std::slice::from_ref(route))?;
    let direction = action
        .strip_prefix("tmux:focus ")
        .ok_or("controller session: not a focus action")?;
    if !["up", "down", "left", "right"].contains(&direction) {
        return Err("controller session: invalid focus direction".into());
    }
    let mut panes = BTreeMap::new();
    for line in output.lines() {
        let parts = line.split('|').collect::<Vec<_>>();
        if parts.len() != 5
            || !parts[0]
                .strip_prefix('%')
                .is_some_and(|id| !id.is_empty() && id.bytes().all(|c| c.is_ascii_digit()))
        {
            return Err("controller session: malformed pane layout".into());
        }
        let values = parts[1..]
            .iter()
            .map(|v| {
                v.parse::<i64>()
                    .map_err(|_| "controller session: invalid pane geometry".to_string())
            })
            .collect::<Result<Vec<_>, _>>()?;
        if values.iter().any(|v| !(0..=1_000_000).contains(v))
            || values[2] == 0
            || values[3] == 0
            || panes.insert(parts[0], values).is_some()
            || panes.len() > 512
        {
            return Err("controller session: invalid or duplicate pane geometry".into());
        }
    }
    let source = panes
        .get(route.pane.as_str())
        .ok_or("controller session: source pane disappeared")?;
    let horizontal = matches!(direction, "left" | "right");
    let axis = if horizontal { 0 } else { 1 };
    let cross = 1 - axis;
    let mut candidates = Vec::new();
    for (pane, values) in &panes {
        if *pane == route.pane
            || values[cross] >= source[cross] + source[cross + 2]
            || source[cross] >= values[cross] + values[cross + 2]
        {
            continue;
        }
        let gap = if matches!(direction, "right" | "down") {
            values[axis] - source[axis] - source[axis + 2]
        } else {
            source[axis] - values[axis] - values[axis + 2]
        };
        if gap >= 0 {
            let offset =
                (2 * source[cross] + source[cross + 2] - 2 * values[cross] - values[cross + 2])
                    .abs();
            candidates.push((gap, offset, *pane));
        }
    }
    candidates.sort_unstable();
    let mut target = route.clone();
    if let Some((_, _, pane)) = candidates.first() {
        target.pane = (*pane).into();
    }
    Ok(target)
}

/// Plan operations scoped to the configured stable pane. Attached-client
/// operations are explicit errors, never host fallbacks. The global config
/// parameter is reserved for future attached-client policy.
pub fn plan(
    route: &SessionRoute,
    action: &str,
    _cfg: &GlobalConfig,
) -> Result<Vec<String>, String> {
    validate(std::slice::from_ref(route))?;
    let rest: Vec<String> = if let Some(key) = action.strip_prefix("key:") {
        vec![
            "send-keys".into(),
            "-t".into(),
            route.pane.clone(),
            "--".into(),
            tmux_key(key)?,
        ]
    } else if let Some(verb) = action.strip_prefix("tmux:") {
        let words = verb.split_whitespace().collect::<Vec<_>>();
        match words.as_slice() {
            ["focus", direction] => {
                match *direction {
                    "up" | "down" | "left" | "right" => {}
                    _ => {
                        return Err(
                            "controller session: focus direction must be up/down/left/right".into(),
                        )
                    }
                }
                vec![
                    "list-panes".into(),
                    "-t".into(),
                    route.pane.clone(),
                    "-F".into(),
                    "#{pane_id}|#{pane_left}|#{pane_top}|#{pane_width}|#{pane_height}".into(),
                ]
            }
            ["page-up"] => vec![
                "copy-mode".into(),
                "-eu".into(),
                "-t".into(),
                route.pane.clone(),
            ],
            ["page-down"] => vec![
                "send-keys".into(),
                "-t".into(),
                route.pane.clone(),
                "-X".into(),
                "page-down".into(),
            ],
            ["key", _]
            | ["prefix"]
            | ["menu"]
            | ["session-prev"]
            | ["session-next"]
            | ["keymap"] => {
                return Err("controller session: this action requires an attached client; no host fallback is permitted".into());
            }
            _ => {
                return Err(format!(
                    "controller session: unsupported pane action {action:?}"
                ))
            }
        }
    } else {
        return Err(format!(
            "controller session: unsupported action {action:?}; no host fallback is permitted"
        ));
    };
    transport(route, rest)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn route(name: &str, device: &str, pane: &str) -> SessionRoute {
        SessionRoute {
            name: name.into(),
            controllers: vec![device.into()],
            socket: "/run/user/1000/tmux-1000/mios-human".into(),
            session: name.into(),
            pane: pane.into(),
            ssh_host: None,
        }
    }

    #[test]
    fn independent_sessions_have_disjoint_devices_and_panes() {
        let routes = [
            route("one", "/dev/input/by-id/pad-one", "%11"),
            route("two", "/dev/input/event2", "%22"),
        ];
        assert!(validate(&routes).is_ok());
        let resolved = resolve_routes(&routes, Path::new("/dev/input/event1"), |path| {
            Ok(Some(if path == Path::new("/dev/input/by-id/pad-one") {
                "/dev/input/event1".into()
            } else {
                path.to_owned()
            }))
        })
        .unwrap()
        .unwrap();
        assert_eq!(resolved.name, "one");
        let (cfg, _) = crate::config::tests::fixture_nav();
        let first = plan(&routes[0], "key:ENTER", &cfg).unwrap();
        let second = plan(&routes[1], "key:ENTER", &cfg).unwrap();
        assert!(first.contains(&"%11".into()) && !first.contains(&"%22".into()));
        assert!(second.contains(&"%22".into()) && !second.contains(&"%11".into()));
        assert!(route_for_device(&routes, Path::new("/dev/input/event99"))
            .unwrap()
            .is_none());
    }

    #[test]
    fn canonical_alias_collision_is_ambiguous_and_disconnection_retains_reservation() {
        let routes = [
            route("one", "/dev/input/by-id/pad-one", "%11"),
            route("two", "/dev/input/event1", "%22"),
        ];
        let error = resolve_routes(&routes, Path::new("/dev/input/event1"), |_| {
            Ok(Some("/dev/input/event1".into()))
        })
        .unwrap_err();
        assert!(
            error.contains("ambiguous") || error.contains("multiple"),
            "{error}"
        );
        let reserved = route_for_device(&routes[..1], Path::new("/dev/input/by-id/pad-one"))
            .unwrap()
            .unwrap();
        assert_eq!(reserved.name, "one");
        assert!(
            resolve_routes(&routes[..1], Path::new("/dev/input/event1"), |_| Err(
                "permission denied".into()
            ))
            .is_err()
        );
    }

    #[test]
    fn alias_ambiguity_reserves_only_its_device_and_preserves_unrelated_routes() {
        let routes = [
            route("one", "/dev/input/by-id/pad-one", "%11"),
            route("two", "/dev/input/by-id/pad-two", "%22"),
            route("three", "/dev/input/by-id/pad-three", "%33"),
        ];
        let canonical = |path: &Path| -> Result<Option<PathBuf>, String> {
            Ok(Some(match path.to_str().unwrap() {
                "/dev/input/by-id/pad-one" | "/dev/input/by-id/pad-two" => {
                    "/dev/input/event1".into()
                }
                "/dev/input/by-id/pad-three" => "/dev/input/event3".into(),
                _ => path.to_owned(),
            }))
        };
        assert!(validate(&routes).is_ok());
        for affected in [
            "/dev/input/event1",
            "/dev/input/by-id/pad-one",
            "/dev/input/by-id/pad-two",
        ] {
            let error = resolve_routes(&routes, Path::new(affected), canonical).unwrap_err();
            assert!(error.contains("ambiguous"), "{error}");
        }
        for unrelated in ["/dev/input/event3", "/dev/input/by-id/pad-three"] {
            let resolved = resolve_routes(&routes, Path::new(unrelated), canonical)
                .unwrap()
                .unwrap();
            assert_eq!(resolved, routes[2]);
        }
        assert!(
            resolve_routes(&routes, Path::new("/dev/input/event4"), canonical)
                .unwrap()
                .is_none()
        );
    }

    #[cfg(unix)]
    #[test]
    fn real_filesystem_canonicalization_finds_symlink_aliases() {
        let temp = tempfile::tempdir().unwrap();
        let device = temp.path().join("event1");
        std::fs::write(&device, b"fixture").unwrap();
        let alias = temp.path().join("pad-one");
        std::os::unix::fs::symlink(&device, &alias).unwrap();
        let routes = [SessionRoute {
            controllers: vec![alias],
            ..route("one", "/dev/input/event1", "%11")
        }];
        assert_eq!(
            resolve_routes(&routes, &device, canonical)
                .unwrap()
                .unwrap()
                .name,
            "one"
        );
    }

    #[test]
    fn invalid_config_never_acquires_an_implicit_host_target() {
        let valid = route("one", "/dev/input/event1", "%11");
        for controllers in [
            vec![],
            vec!["relative".into()],
            vec!["/dev/input/../event1".into()],
            vec!["/dev/input".into()],
        ] {
            let mut invalid = valid.clone();
            invalid.controllers = controllers;
            assert!(validate(&[invalid]).is_err());
        }
        for pane in ["", "mios", "0", "%", "%1;kill-server", "%1\n"] {
            let mut invalid = valid.clone();
            invalid.pane = pane.into();
            assert!(validate(&[invalid]).is_err(), "{pane:?}");
        }
        for session in ["", " ", "one\ntwo", "one\ttwo"] {
            let mut invalid = valid.clone();
            invalid.session = session.into();
            assert!(validate(&[invalid]).is_err(), "{session:?}");
        }
        let mut duplicate = valid.clone();
        duplicate.name = "two".into();
        assert!(validate(&[valid.clone(), duplicate]).is_err());
        let mut duplicate = valid.clone();
        duplicate.controllers = vec!["/dev/input/event2".into()];
        assert!(validate(&[valid.clone(), duplicate]).is_err());
        for host in [
            "",
            "-oProxyCommand=bad",
            "host;touch /tmp/bad",
            "user@host name",
            "user@@host",
            "@host",
            "user@-host",
            "host\n",
        ] {
            let mut invalid = valid.clone();
            invalid.ssh_host = Some(host.into());
            assert!(validate(&[invalid]).is_err(), "{host:?}");
        }
        for host in ["blade-one", "operator@blade-one", "user@[fd00::1]"] {
            let mut valid = valid.clone();
            valid.ssh_host = Some(host.into());
            assert!(validate(&[valid]).is_ok(), "{host}");
        }
    }

    #[test]
    fn terminal_keys_and_detached_operations_target_only_the_bound_pane() {
        let route = route("one", "/dev/input/event1", "%11");
        let (cfg, _) = crate::config::tests::fixture_nav();
        for (action, expected) in [
            ("key:TAB", "Tab"),
            ("key:SHIFT+TAB", "BTab"),
            ("key:CTRL+SHIFT+TAB", "C-BTab"),
            ("key:ENTER", "Enter"),
            ("key:UP", "Up"),
            ("key:CTRL+ALT+LEFT", "C-M-Left"),
            ("key:PAGEUP", "PPage"),
            ("key:BACKSPACE", "BSpace"),
            ("key:MINUS", "-"),
        ] {
            let argv = plan(&route, action, &cfg).unwrap();
            assert_eq!(argv.last().unwrap(), expected);
            assert_eq!(&argv[3..7], ["send-keys", "-t", "%11", "--"]);
        }
        assert_eq!(
            &plan(&route, "tmux:focus left", &cfg).unwrap()[3..7],
            ["list-panes", "-t", "%11", "-F"]
        );
        assert_eq!(
            &plan(&route, "tmux:page-up", &cfg).unwrap()[3..],
            ["copy-mode", "-eu", "-t", "%11"]
        );
        assert_eq!(
            &plan(&route, "tmux:page-down", &cfg).unwrap()[3..],
            ["send-keys", "-t", "%11", "-X", "page-down"]
        );
        for action in [
            "tmux:key c",
            "tmux:prefix",
            "tmux:menu",
            "tmux:session-next",
            "tmux:session-prev",
            "tmux:keymap",
            "hypr:movefocus left",
            "key:SUPER+a",
            "key:COMPOSE",
            "key:foo;touch",
        ] {
            assert!(plan(&route, action, &cfg).is_err(), "{action}");
        }
    }

    #[test]
    fn ssh_command_quotes_every_argument_without_interpolation() {
        let mut route = route("remote", "/dev/input/event1", "%11");
        route.socket = "/tmp/a socket'$(touch bad);x".into();
        route.ssh_host = Some("operator@blade-two".into());
        let (cfg, _) = crate::config::tests::fixture_nav();
        let remote = plan(&route, "key:SHIFT+TAB", &cfg).unwrap();
        assert_eq!(
            &remote[..5],
            [
                "/usr/bin/ssh",
                "-o",
                "BatchMode=yes",
                "--",
                "operator@blade-two"
            ]
        );
        let decoded = shlex::split(&remote[5]).unwrap();
        route.ssh_host = None;
        assert_eq!(decoded, plan(&route, "key:SHIFT+TAB", &cfg).unwrap());
    }

    #[test]
    fn session_identity_is_data_and_never_an_interpolated_format() {
        let mut route = route("one", "/dev/input/event1", "%11");
        route.session = "work'#{danger};$(ignored)".into();
        assert_eq!(
            verify_argv(&route).unwrap().last().unwrap(),
            "#{session_name}"
        );
        assert_eq!(
            inspect_argv(&route).unwrap().last().unwrap(),
            IDENTITY_FORMAT
        );
        assert_eq!(
            &select_argv(&route).unwrap()[3..],
            ["select-pane", "-t", "%11"]
        );
    }

    #[test]
    fn printable_identity_keeps_delimiters_and_format_tokens_as_session_data() {
        let session = "work|#{danger};$(ignored)";
        let reply = format!("123|@4|%11|{session}\n");
        let parsed = parse_identity(&reply).unwrap();
        assert_eq!(
            (parsed.pid, parsed.window, parsed.pane, parsed.session),
            (123, "@4", "%11", session)
        );
        for bad in [
            "123_work_@4_%11\n",
            "0|@4|%11|work",
            "123|@x|%11|work",
            "123|@4|%x|work",
            "123|@4|%11|",
            "123|@4|%11|wo\trk",
        ] {
            assert!(parse_identity(bad).is_err(), "{bad:?}");
        }
    }

    #[test]
    fn focus_follows_each_controller_cursor_without_reading_active_pane() {
        let first = route("one", "/dev/input/event1", "%11");
        let second = route("two", "/dev/input/event2", "%22");
        let layout = "%11|0|0|40|20\n%22|41|0|40|20\n%33|41|21|40|20\n";
        let moved = focus_target(&first, "tmux:focus right", layout).unwrap();
        assert_eq!(moved.pane, "%22");
        assert_eq!(moved.name, "one");
        assert_eq!(second.pane, "%22");
        assert_eq!(
            focus_target(&moved, "tmux:focus down", layout)
                .unwrap()
                .pane,
            "%33"
        );
        assert_eq!(
            focus_target(&first, "tmux:focus left", layout)
                .unwrap()
                .pane,
            "%11"
        );
        assert_eq!(
            focus_target(&second, "tmux:focus left", layout)
                .unwrap()
                .pane,
            "%11"
        );
        for invalid in [
            "",
            "%22|0|0|10|10",
            "%11|0|0|0|10",
            "%11|0|0|10|10\n%11|0|0|10|10",
            "%11;bad|0|0|10|10",
            "%11|0|0|99999999|10",
        ] {
            assert!(
                focus_target(&first, "tmux:focus right", invalid).is_err(),
                "{invalid:?}"
            );
        }
    }

    #[test]
    fn full_configuration_deserializes_routes_and_validates_them() {
        let expected = route("one", "/dev/input/event1", "%11");
        let (cfg, _) = crate::config::tests::fixture_nav_with(|value| {
            value["keybindings"]["global"]["controller"]["sessions"] =
                toml::Value::try_from(vec![expected.clone()]).unwrap();
        });
        assert_eq!(
            cfg.controller.sessions.as_slice(),
            std::slice::from_ref(&expected)
        );
        assert!(crate::config::tests::fixture_nav()
            .0
            .controller
            .sessions
            .is_empty());
        assert!(crate::config::tests::try_fixture_nav_with(|value| {
            let mut invalid = expected.clone();
            invalid.pane = "current".into();
            value["keybindings"]["global"]["controller"]["sessions"] =
                toml::Value::try_from(vec![invalid]).unwrap();
        })
        .is_err());
        let mut serialized = serde_json::to_value(&expected).unwrap();
        serialized.as_object_mut().unwrap().remove("session");
        assert!(serde_json::from_value::<SessionRoute>(serialized).is_err());
    }
}
