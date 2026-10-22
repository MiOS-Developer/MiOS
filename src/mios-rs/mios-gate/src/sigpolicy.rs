// AI-hint: Asserts usr/lib/containers/policy.json is the byte-exact, sound projection of [security.sigstore] and that every MiOS trust anchor it names ships under usr/share/pki/containers.
// AI-related: usr/share/mios/mios.toml, usr/lib/containers/policy.json, tools/native/mios-resolver/src/sigstore.rs, tools/native/mios-gen/src/main.rs, automation/49-cosign-policy.sh

use crate::Report;
use mios_resolver::sigstore;
use std::path::Path;

const CHECK: &str = "signature-policy";

fn cannot_run(why: impl Into<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok: false,
        could_not_run: Some(why.into()),
        summary: String::new(),
        findings: Vec::new(),
    }
}

fn violation(findings: Vec<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok: false,
        could_not_run: None,
        summary: String::new(),
        findings,
    }
}

pub fn check(root: &Path) -> Report {
    let artifact = sigstore::ARTIFACT;
    let ssot = root.join("usr/share/mios/mios.toml");
    if !ssot.is_file() {
        return cannot_run(format!("{} is missing", ssot.display()));
    }
    let resolved = match mios_resolver::resolve_vendor_projection(root) {
        Ok(v) => v,
        Err(e) => return cannot_run(format!("the vendor SSOT did not resolve: {e}")),
    };
    if resolved
        .get("security")
        .and_then(|s| s.get("sigstore"))
        .and_then(|s| s.as_table())
        .is_none()
    {
        return cannot_run("mios.toml declares no [security.sigstore] table");
    }

    // Absent is a violation, not a skip: the artifact is tracked, and a policy
    // that is not there is not a permissive policy -- it is an unknown one.
    let artifact_path = root.join(artifact);
    if !artifact_path.is_file() {
        return violation(vec![format!(
            "{artifact} is tracked but missing -- regenerate it: mios-gen cosign-policy"
        )]);
    }

    // An SSOT the renderer refuses is a finding, not a skip: it is exactly the
    // policy (invalid type, or a MiOS image accepted unsigned) this gate exists for.
    let policy = match sigstore::render(&resolved) {
        Ok(p) => p,
        Err(e) => return violation(vec![format!("[security.sigstore] does not project to a sound policy: {e}")]),
    };

    let Ok(current) = std::fs::read_to_string(&artifact_path) else {
        return cannot_run(format!("{artifact} could not be read"));
    };
    let mut findings = Vec::new();
    // Bytes, not parsed JSON: a compact or reordered file that means the same
    // thing is still a hand edit the writer would not produce (Law 8).
    if current != policy.json {
        findings.push(format!(
            "{artifact} is not what [security.sigstore] projects -- regenerate it: mios-gen cosign-policy"
        ));
    }
    // The bake installs these from the tree and refuses a missing one; failing
    // here keeps that from first surfacing an hour into an image build.
    for anchor in &policy.anchors {
        let rel = anchor.trim_start_matches('/');
        let p = root.join(rel);
        let regular = std::fs::symlink_metadata(&p)
            .map(|m| m.file_type().is_file() && m.len() > 0)
            .unwrap_or(false);
        if !regular {
            findings.push(format!(
                "MiOS trust anchor {anchor} is named by {artifact} for {} but the tree ships no {rel} \
                 -- the MiOS signing key is an operator secret (task_3536): create it with \
                 `cosign generate-key-pair`, keep the private key as a publisher secret, and commit \
                 only the public key at {rel}",
                policy.mios_namespace
            ));
        }
    }

    let ok = findings.is_empty();
    Report {
        check: CHECK.to_string(),
        ok,
        could_not_run: None,
        summary: format!(
            "{artifact} matches [security.sigstore]; {} is sigstoreSigned by {} MiOS anchor(s), all shipped",
            policy.mios_namespace,
            policy.anchors.len()
        ),
        findings,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    const SSOT: &str = "[image]\nname = \"ghcr.io/mios-dev/mios\"\n[security.sigstore]\npolicy_mode = \"insecureAcceptAnything\"\n\
        [[security.sigstore.scopes]]\ntransport = \"docker\"\nscope = \"ghcr.io/mios-dev\"\ntype = \"sigstoreSigned\"\n\
        key_paths = [\"/usr/share/pki/containers/mios-cosign.pub\"]\n";

    fn tree(ssot: &str, key: bool) -> tempfile::TempDir {
        let d = tempfile::tempdir().unwrap();
        fs::create_dir_all(d.path().join("usr/share/mios")).unwrap();
        fs::write(d.path().join("usr/share/mios/mios.toml"), ssot).unwrap();
        if let Ok(p) = sigstore::render(&ssot.parse().unwrap()) {
            fs::create_dir_all(d.path().join("usr/lib/containers")).unwrap();
            fs::write(d.path().join(sigstore::ARTIFACT), p.json).unwrap();
        }
        if key {
            fs::create_dir_all(d.path().join("usr/share/pki/containers")).unwrap();
            fs::write(d.path().join("usr/share/pki/containers/mios-cosign.pub"), "-----BEGIN PUBLIC KEY-----\n").unwrap();
        }
        d
    }

    #[test]
    fn a_sound_projection_with_its_anchor_shipped_is_clean() {
        let d = tree(SSOT, true);
        let r = check(d.path());
        assert!(r.ok, "{:?}", r.findings);
    }

    #[test]
    fn a_missing_anchor_is_a_finding() {
        let d = tree(SSOT, false);
        let r = check(d.path());
        assert!(!r.ok && r.could_not_run.is_none());
        assert!(r.findings.iter().any(|f| f.contains("mios-cosign.pub")), "{:?}", r.findings);
    }

    #[test]
    fn an_insecure_ssot_is_a_finding_not_a_skip() {
        let d = tree(&SSOT.replace("\"sigstoreSigned\"", "\"insecureAcceptAnything\"").replace(
            "key_paths = [\"/usr/share/pki/containers/mios-cosign.pub\"]\n", ""), true);
        // The renderer refuses it, so tree() wrote no artifact; plant the old one.
        fs::create_dir_all(d.path().join("usr/lib/containers")).unwrap();
        fs::write(d.path().join(sigstore::ARTIFACT), "{}\n").unwrap();
        let r = check(d.path());
        assert!(!r.ok && r.could_not_run.is_none(), "{:?} {:?}", r.could_not_run, r.findings);
        assert!(r.findings.iter().any(|f| f.contains("MiOS-owned")), "{:?}", r.findings);
    }

    #[test]
    fn same_meaning_other_bytes_is_a_finding() {
        let d = tree(SSOT, true);
        let compact: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(d.path().join(sigstore::ARTIFACT)).unwrap()).unwrap();
        fs::write(d.path().join(sigstore::ARTIFACT), compact.to_string()).unwrap();
        assert!(!check(d.path()).ok);
    }

    #[test]
    fn a_symlinked_anchor_is_not_shipped() {
        let d = tree(SSOT, false);
        fs::create_dir_all(d.path().join("usr/share/pki/containers")).unwrap();
        std::os::unix::fs::symlink("/etc/pki/containers/mios-cosign.pub", d.path().join("usr/share/pki/containers/mios-cosign.pub")).unwrap();
        assert!(!check(d.path()).ok);
    }
}
