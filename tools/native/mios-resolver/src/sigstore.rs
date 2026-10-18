// AI-hint: Shared containers-policy.json renderer for [security.sigstore] (mios-gen writes, mios-gate checks): valid types, typed scopes, and no policy that accepts a MiOS-namespace image without a MiOS-anchored signature.
// AI-related: usr/share/mios/mios.toml, usr/lib/containers/policy.json, automation/49-cosign-policy.sh, tools/native/mios-gen/src/main.rs, src/mios-rs/mios-gate/src/sigpolicy.rs, tests/test-cosign-trust-anchor.sh

use serde_json::{Map, Value as Json};
use std::collections::BTreeMap;
use std::path::Path;
use toml::Value;

/// The vendor projection, tracked and shipped under /usr. containers/image does
/// not read it; 49-cosign-policy.sh installs it as the image default
/// /etc/containers/policy.json.
pub const ARTIFACT: &str = "usr/lib/containers/policy.json";

/// Where MiOS trust anchors live: vendor content, updated with the image. A
/// MiOS-owned scope that trusted a key under /etc would trust host state.
pub const ANCHOR_DIR: &str = "/usr/share/pki/containers";

/// Requirement types the global default may take. sigstoreSigned needs a scope
/// to be meaningful, and the two insecure spellings are the defect class this
/// module exists for: `insecureAcceptEverything` is not a type at all, and
/// containers/image refuses to load a policy that names it.
const DEFAULT_TYPES: [&str; 2] = ["insecureAcceptAnything", "reject"];
const SCOPE_TYPES: [&str; 3] = ["insecureAcceptAnything", "reject", "sigstoreSigned"];
/// cosign signatures carry only a repository, so only these identities accept them.
const IDENTITIES: [&str; 3] = ["matchRepository", "matchRepoDigestOrExact", "matchExact"];
const TRANSPORTS: [&str; 8] = [
    "docker",
    "docker-daemon",
    "containers-storage",
    "oci",
    "oci-archive",
    "docker-archive",
    "dir",
    "tarball",
];
const SCOPE_KEYS: [&str; 5] = ["transport", "scope", "type", "key_paths", "signed_identity"];

/// One rendered policy and the facts the checker and the bake verify about it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Policy {
    /// Exact bytes of the projection (serde_json pretty form plus a newline).
    pub json: String,
    /// The registry namespace MiOS publishes into ([image].name minus its last component).
    pub mios_namespace: String,
    /// Every key path a requirement over the MiOS namespace trusts, sorted.
    pub anchors: Vec<String>,
}

#[derive(Debug, Clone)]
struct Requirement {
    kind: String,
    key_paths: Vec<String>,
    identity: String,
}

impl Requirement {
    /// Keys inserted in byte order, so the bytes are identical whether or not a
    /// build unifies serde_json's `preserve_order` feature (mios-gen enables it,
    /// mios-gate does not).
    fn to_json(&self) -> Json {
        let mut m = Map::new();
        if self.kind == "sigstoreSigned" {
            if self.key_paths.len() == 1 {
                m.insert("keyPath".into(), Json::String(self.key_paths[0].clone()));
            } else {
                m.insert(
                    "keyPaths".into(),
                    Json::Array(self.key_paths.iter().cloned().map(Json::String).collect()),
                );
            }
            let mut id = Map::new();
            id.insert("type".into(), Json::String(self.identity.clone()));
            m.insert("signedIdentity".into(), Json::Object(id));
        }
        m.insert("type".into(), Json::String(self.kind.clone()));
        Json::Object(m)
    }

    fn describe(&self) -> String {
        if self.kind == "sigstoreSigned" {
            format!("sigstoreSigned by [{}]", self.key_paths.join(", "))
        } else {
            self.kind.clone()
        }
    }
}

fn str_field<'a>(t: &'a toml::map::Map<String, Value>, key: &str, at: &str) -> Result<&'a str, String> {
    t.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("{at}.{key} is absent or not a string"))
}

fn one_of(value: &str, allowed: &[&str], at: &str) -> Result<(), String> {
    if allowed.contains(&value) {
        Ok(())
    } else {
        Err(format!(
            "{at} = \"{value}\" is not a containers-policy.json(5) value here; use one of: {}",
            allowed.join(", ")
        ))
    }
}

fn parse_scope(t: &toml::map::Map<String, Value>, at: &str) -> Result<(String, String, Requirement), String> {
    if let Some(unknown) = t.keys().find(|k| !SCOPE_KEYS.contains(&k.as_str())) {
        return Err(format!("{at}.{unknown} is not a scope field ({})", SCOPE_KEYS.join(", ")));
    }
    let transport = str_field(t, "transport", at)?.to_string();
    one_of(&transport, &TRANSPORTS, &format!("{at}.transport"))?;
    let scope = str_field(t, "scope", at)?.to_string();
    let kind = str_field(t, "type", at)?.to_string();
    one_of(&kind, &SCOPE_TYPES, &format!("{at}.type"))?;
    let mut key_paths = Vec::new();
    let mut identity = String::new();
    if kind == "sigstoreSigned" {
        let arr = t
            .get("key_paths")
            .and_then(Value::as_array)
            .ok_or_else(|| format!("{at}.key_paths is required for sigstoreSigned"))?;
        for v in arr {
            let p = v
                .as_str()
                .ok_or_else(|| format!("{at}.key_paths holds a non-string"))?;
            if !p.starts_with('/') || p.contains("/../") || p.ends_with('/') {
                return Err(format!("{at}.key_paths entry \"{p}\" is not an absolute file path"));
            }
            if key_paths.iter().any(|k: &String| k == p) {
                return Err(format!("{at}.key_paths lists \"{p}\" twice"));
            }
            key_paths.push(p.to_string());
        }
        if key_paths.is_empty() {
            return Err(format!("{at}.key_paths is empty: a sigstoreSigned scope trusting no key accepts nothing"));
        }
        identity = t
            .get("signed_identity")
            .map(|v| v.as_str().map(str::to_string).ok_or_else(|| format!("{at}.signed_identity is not a string")))
            .transpose()?
            .unwrap_or_else(|| "matchRepository".to_string());
        one_of(&identity, &IDENTITIES, &format!("{at}.signed_identity"))?;
    } else if t.contains_key("key_paths") || t.contains_key("signed_identity") {
        return Err(format!(
            "{at} is type {kind}, which takes no key_paths or signed_identity (they would be silently unused)"
        ));
    }
    Ok((transport, scope, Requirement { kind, key_paths, identity }))
}

/// The requirement containers/image applies to a docker repository: the most
/// specific namespace scope (the repository itself, then each parent), then the
/// most specific `*.domain` wildcard on the host, then the transport's "" scope,
/// then the global default.
fn effective<'a>(
    docker: &'a BTreeMap<String, Requirement>,
    default: &'a Requirement,
    repo: &str,
) -> (&'a Requirement, String) {
    let mut cur = repo.to_string();
    loop {
        if let Some(r) = docker.get(&cur) {
            return (r, format!("docker scope \"{cur}\""));
        }
        match cur.rfind('/') {
            Some(i) => cur.truncate(i),
            None => break,
        }
    }
    let host = repo.split('/').next().unwrap_or_default();
    let mut labels: &str = host;
    while let Some(i) = labels.find('.') {
        labels = &labels[i + 1..];
        let wild = format!("*.{labels}");
        if let Some(r) = docker.get(&wild) {
            return (r, format!("docker scope \"{wild}\""));
        }
    }
    if let Some(r) = docker.get("") {
        return (r, "docker scope \"\"".to_string());
    }
    (default, "the global default".to_string())
}

fn mios_signed(r: &Requirement) -> bool {
    r.kind == "sigstoreSigned"
        && r
            .key_paths
            .iter()
            .all(|k| k.starts_with(&format!("{ANCHOR_DIR}/")) && !k[ANCHOR_DIR.len() + 1..].contains('/'))
}

/// Render the projection from a resolved SSOT. Any policy that would accept an
/// image in the MiOS namespace without a MiOS-anchored signature is an error,
/// never a rendered file.
pub fn render(ssot: &Value) -> Result<Policy, String> {
    let sig = ssot
        .get("security")
        .and_then(|s| s.get("sigstore"))
        .and_then(Value::as_table)
        .ok_or("mios.toml declares no [security.sigstore] table")?;
    let mode = sig
        .get("policy_mode")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or("[security.sigstore].policy_mode is absent or not a string")?;
    one_of(mode, &DEFAULT_TYPES, "[security.sigstore].policy_mode")?;
    let default = Requirement { kind: mode.to_string(), key_paths: Vec::new(), identity: String::new() };

    let mut transports: BTreeMap<String, BTreeMap<String, Requirement>> = BTreeMap::new();
    if let Some(v) = sig.get("scopes") {
        let arr = v.as_array().ok_or("[security.sigstore].scopes is not an array of tables")?;
        for (i, item) in arr.iter().enumerate() {
            let at = format!("[[security.sigstore.scopes]] #{}", i + 1);
            let t = item.as_table().ok_or_else(|| format!("{at} is not a table"))?;
            let (transport, scope, req) = parse_scope(t, &at)?;
            let slot = transports.entry(transport.clone()).or_default();
            if slot.insert(scope.clone(), req).is_some() {
                return Err(format!("{at} repeats transport \"{transport}\" scope \"{scope}\""));
            }
        }
    }

    let name = ssot
        .get("image")
        .and_then(|i| i.get("name"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or("[image].name is absent: the MiOS-owned namespace cannot be derived")?;
    let namespace = match name.rfind('/') {
        Some(i) => name[..i].to_string(),
        None => name.to_string(),
    };
    let empty = BTreeMap::new();
    let docker = transports.get("docker").unwrap_or(&empty);
    let mut anchors: Vec<String> = Vec::new();
    let mut check = |repo: &str, req: &Requirement, via: &str| -> Result<(), String> {
        if !mios_signed(req) {
            return Err(format!(
                "{repo} is MiOS-owned ([image].name = \"{name}\"), but {via} resolves it to {}: every \
                 image in {namespace} must be sigstoreSigned by keys directly under {ANCHOR_DIR} -- add \
                 [[security.sigstore.scopes]] transport = \"docker\", scope = \"{namespace}\", \
                 type = \"sigstoreSigned\", key_paths = [\"{ANCHOR_DIR}/mios-cosign.pub\", ...]",
                req.describe()
            ));
        }
        anchors.extend(req.key_paths.iter().cloned());
        Ok(())
    };
    let (req, via) = effective(docker, &default, &namespace);
    check(&namespace, req, &via)?;
    for (scope, req) in docker {
        if scope.starts_with(&format!("{namespace}/")) {
            check(scope, req, &format!("docker scope \"{scope}\""))?;
        }
    }
    anchors.sort();
    anchors.dedup();

    let mut root = Map::new();
    root.insert("default".into(), Json::Array(vec![default.to_json()]));
    if !transports.is_empty() {
        let mut tr = Map::new();
        for (transport, scopes) in &transports {
            let mut sm = Map::new();
            for (scope, req) in scopes {
                sm.insert(scope.clone(), Json::Array(vec![req.to_json()]));
            }
            tr.insert(transport.clone(), Json::Object(sm));
        }
        root.insert("transports".into(), Json::Object(tr));
    }
    let json = serde_json::to_string_pretty(&Json::Object(root)).map_err(|e| format!("cannot format policy.json: {e}"))? + "\n";
    Ok(Policy { json, mios_namespace: namespace, anchors })
}

/// [`render`] over the vendor tier of `root` (never this host's /etc or $HOME layers).
pub fn render_root(root: &Path) -> Result<Policy, String> {
    let ssot = crate::resolve_vendor_projection(root).map_err(|e| e.to_string())?;
    render(&ssot)
}

#[cfg(test)]
mod tests {
    use super::*;

    const MIOS: &str = "[image]\nname = \"ghcr.io/mios-dev/mios\"\n";
    const SIGNED: &str = "[[security.sigstore.scopes]]\ntransport = \"docker\"\nscope = \"ghcr.io/mios-dev\"\n\
                          type = \"sigstoreSigned\"\nkey_paths = [\"/usr/share/pki/containers/mios-cosign.pub\", \
                          \"/usr/share/pki/containers/mios-cosign-backup.pub\"]\n";

    fn ssot(body: &str) -> Value {
        body.parse::<Value>().expect("fixture parses")
    }

    fn with(mode: &str, scopes: &str) -> Result<Policy, String> {
        render(&ssot(&format!("{MIOS}[security.sigstore]\npolicy_mode = \"{mode}\"\n{scopes}")))
    }

    #[test]
    fn mios_namespace_signed_by_shipped_anchors_renders() {
        let p = with("insecureAcceptAnything", SIGNED).expect("sound policy renders");
        assert_eq!(p.mios_namespace, "ghcr.io/mios-dev");
        assert_eq!(
            p.anchors,
            vec![
                "/usr/share/pki/containers/mios-cosign-backup.pub".to_string(),
                "/usr/share/pki/containers/mios-cosign.pub".to_string()
            ]
        );
        let v: Json = serde_json::from_str(&p.json).unwrap();
        let req = &v["transports"]["docker"]["ghcr.io/mios-dev"][0];
        assert_eq!(req["type"], "sigstoreSigned");
        assert_eq!(req["signedIdentity"]["type"], "matchRepository");
        assert_eq!(req["keyPaths"].as_array().unwrap().len(), 2);
        assert_eq!(v["default"][0]["type"], "insecureAcceptAnything");
        assert!(p.json.ends_with("}\n"));
    }

    #[test]
    fn bytes_are_key_sorted_pretty_json() {
        let one = SIGNED.replace(", \"/usr/share/pki/containers/mios-cosign-backup.pub\"", "");
        let p = with("reject", &one).unwrap();
        assert_eq!(
            p.json,
            "{\n  \"default\": [\n    {\n      \"type\": \"reject\"\n    }\n  ],\n  \"transports\": {\n    \"docker\": {\n      \
             \"ghcr.io/mios-dev\": [\n        {\n          \"keyPath\": \"/usr/share/pki/containers/mios-cosign.pub\",\n          \
             \"signedIdentity\": {\n            \"type\": \"matchRepository\"\n          },\n          \"type\": \"sigstoreSigned\"\n        \
             }\n      ]\n    }\n  }\n}\n"
        );
    }

    #[test]
    fn the_invalid_insecure_spelling_is_refused() {
        let e = with("insecureAcceptEverything", SIGNED).unwrap_err();
        assert!(e.contains("insecureAcceptEverything"), "{e}");
    }

    #[test]
    fn no_mios_scope_means_the_default_accepts_mios_images_and_is_refused() {
        let e = with("insecureAcceptAnything", "").unwrap_err();
        assert!(e.contains("MiOS-owned") && e.contains("the global default"), "{e}");
    }

    #[test]
    fn a_reject_default_without_a_mios_scope_is_still_refused() {
        // reject is safe but un-upgradable; the rule is "signed by MiOS anchors", not "not accepted".
        assert!(with("reject", "").is_err());
    }

    #[test]
    fn insecure_mios_scope_is_refused() {
        let e = with(
            "reject",
            "[[security.sigstore.scopes]]\ntransport = \"docker\"\nscope = \"ghcr.io/mios-dev\"\ntype = \"insecureAcceptAnything\"\n",
        )
        .unwrap_err();
        assert!(e.contains("insecureAcceptAnything"), "{e}");
    }

    #[test]
    fn a_weaker_scope_inside_the_namespace_is_refused() {
        let hole = format!(
            "{SIGNED}[[security.sigstore.scopes]]\ntransport = \"docker\"\nscope = \"ghcr.io/mios-dev/mios-micro\"\ntype = \"insecureAcceptAnything\"\n"
        );
        let e = with("insecureAcceptAnything", &hole).unwrap_err();
        assert!(e.contains("mios-micro"), "{e}");
    }

    #[test]
    fn a_host_etc_key_is_not_a_mios_anchor() {
        let etc = SIGNED.replace("/usr/share/pki/containers/mios-cosign.pub", "/etc/pki/containers/mios-cosign.pub");
        let e = with("insecureAcceptAnything", &etc).unwrap_err();
        assert!(e.contains("/etc/pki/containers"), "{e}");
    }

    #[test]
    fn a_registry_wide_signed_scope_covers_the_namespace() {
        let wide = SIGNED.replace("scope = \"ghcr.io/mios-dev\"", "scope = \"ghcr.io\"");
        assert!(with("insecureAcceptAnything", &wide).is_ok());
        let wild = SIGNED.replace("scope = \"ghcr.io/mios-dev\"", "scope = \"*.io\"");
        assert!(with("insecureAcceptAnything", &wild).is_ok());
    }

    #[test]
    fn typed_fields_are_enforced() {
        assert!(with("insecureAcceptAnything", &SIGNED.replace("\"docker\"", "\"dockr\"")).is_err());
        assert!(with("insecureAcceptAnything", &format!("{SIGNED}signed_identity = \"remapIdentity\"\n")).is_err());
        assert!(with("insecureAcceptAnything", &format!("{SIGNED}keyPath = \"/x\"\n")).is_err());
        assert!(with("insecureAcceptAnything", &SIGNED.replace("/usr/share/pki/containers/mios-cosign.pub", "relative.pub")).is_err());
        let dup = format!("{SIGNED}{SIGNED}");
        assert!(with("insecureAcceptAnything", &dup).unwrap_err().contains("repeats"));
        let empty = "[[security.sigstore.scopes]]\ntransport = \"docker\"\nscope = \"ghcr.io/mios-dev\"\ntype = \"sigstoreSigned\"\nkey_paths = []\n";
        assert!(with("insecureAcceptAnything", empty).unwrap_err().contains("empty"));
    }

    #[test]
    fn unrelated_scopes_are_carried_verbatim() {
        let extra = format!(
            "{SIGNED}[[security.sigstore.scopes]]\ntransport = \"docker\"\nscope = \"ghcr.io/ublue-os\"\ntype = \"sigstoreSigned\"\n\
             key_paths = [\"/etc/pki/containers/ublue-os.pub\"]\n[[security.sigstore.scopes]]\ntransport = \"docker-daemon\"\nscope = \"\"\ntype = \"insecureAcceptAnything\"\n"
        );
        let p = with("insecureAcceptAnything", &extra).unwrap();
        let v: Json = serde_json::from_str(&p.json).unwrap();
        assert_eq!(v["transports"]["docker"]["ghcr.io/ublue-os"][0]["keyPath"], "/etc/pki/containers/ublue-os.pub");
        assert_eq!(v["transports"]["docker-daemon"][""][0]["type"], "insecureAcceptAnything");
        assert!(!p.anchors.iter().any(|a| a.contains("ublue")), "a base-image scope is not a MiOS anchor");
    }
}
