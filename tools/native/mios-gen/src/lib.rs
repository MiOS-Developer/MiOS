// AI-hint: mios-gen library: the names-registry generator (check 30) and the bootstrap mirror, shared with generate-names-registry and the mios-gen CLI.
pub mod bootstrap_sync;

// Names registry: referenced_names.txt and names.generated.txt from the SSOT and the tracked consumers.
pub mod names_registry {
    use regex::Regex;
    use std::collections::BTreeSet;
    use std::fs;
    use std::path::Path;

    fn walk_value(val: &toml::Value, prefix: &str, results: &mut Vec<(String, String)>) {
        if let toml::Value::Table(table) = val {
            for (k, v) in table {
                let path = if prefix.is_empty() {
                    k.clone()
                } else {
                    format!("{}.{}", prefix, k)
                };
                if path == "routing.domains" {
                    continue;
                }
                if let toml::Value::Table(_) = v {
                    walk_value(v, &path, results);
                } else {
                    let env_name = mios_resolver::names::canonical_name(&path);
                    results.push((path, env_name));
                }
            }
        }
    }

    /// Tracked paths, slash-separated. An error or an empty listing is fatal.
    ///
    /// The walk this replaced skipped every directory named `build`, so it silently
    /// dropped tracked files and still exited 0. A census that cannot be answered
    /// must refuse, not guess.
    fn tracked(root: &Path) -> Result<Vec<String>, String> {
        let out = std::process::Command::new("git")
            .arg("-C")
            .arg(root)
            .arg("ls-files")
            .arg("-z")
            .output()
            .map_err(|e| format!("git ls-files could not run in {}: {e}", root.display()))?;
        if !out.status.success() {
            return Err(format!(
                "git ls-files failed in {} ({}): {}",
                root.display(),
                out.status,
                String::from_utf8_lossy(&out.stderr).trim()
            ));
        }
        let paths: Vec<String> = String::from_utf8(out.stdout)
            .map_err(|e| format!("tracked paths are not UTF-8: {e}"))?
            .split('\0')
            .map(|l| l.replace('\\', "/"))
            .filter(|l| !l.is_empty())
            .collect();
        if paths.is_empty() {
            return Err(format!(
                "git ls-files listed no tracked file in {}, so nothing would be \
             scanned and the result would be clean for the wrong reason",
                root.display()
            ));
        }
        Ok(paths)
    }

    /// True when `line` ASSIGNS `v`, matching the Python leg's
    /// `\s*(export\s+)?<v>=`. Judging the whole line instead drops every name that
    /// rides on an env-prefix line -- 16 of them in this tree.
    fn assigned_at_start(line: &str, v: &str) -> bool {
        let rest = line.trim_start();
        let is_assign = |s: &str| s.strip_prefix(v).is_some_and(|t| t.starts_with('='));
        if is_assign(rest) {
            return true;
        }
        match rest.strip_prefix("export") {
            Some(after) => {
                let t = after.trim_start();
                after.len() != t.len() && is_assign(t)
            }
            None => false,
        }
    }

    /// Basename globs, matched exactly as the Python leg's fnmatchcase does.
    fn matches_consumer_glob(fname: &str) -> bool {
        const SUFFIXES: [&str; 12] = [
            ".container",
            ".service",
            ".timer",
            ".py",
            ".sh",
            ".toml",
            ".ps1",
            ".psm1",
            ".yaml",
            ".yml",
            ".tmpl",
            ".nft",
        ];
        if fname == "Justfile" || fname == ".env.mios" || fname == "Containerfile" {
            return true;
        }
        if fname.ends_with(".sql") {
            return true;
        }
        // `Containerfile.*` needs the dot: `starts_with("Containerfile")` also took
        // Containerfilexyz, which fnmatchcase never would.
        if fname.starts_with("Containerfile.") {
            return true;
        }
        SUFFIXES.iter().any(|s| fname.ends_with(s))
    }

    fn render_referenced_vars(root: &Path) -> Result<String, String> {
        // globals.{sh,ps1} are rendered in full and DEFINE the namespace; the
        // renderers' prose carries example placeholders that are not variables.
        let emitter_suffixes = [
            "usr/lib/mios/userenv.sh",
            "tools/lib/userenv.sh",
            "usr/libexec/mios/system-sync-env.sh",
            "usr/share/mios/names.generated.txt",
            "usr/share/doc/mios/reference/naming-unification.md",
            "automation/lib/globals.sh",
            "automation/lib/globals.ps1",
            "tools/render-globals.py",
            "tools/render-ports.py",
        ];

        let var_re = Regex::new(r"MIOS_[A-Z0-9_]+").map_err(|e| e.to_string())?;
        let mut refs: BTreeSet<String> = BTreeSet::new();

        for rel in tracked(root)? {
            let path = root.join(&rel);
            if emitter_suffixes.iter().any(|s| rel.ends_with(s)) {
                continue;
            }
            let fname = match path.file_name().and_then(|n| n.to_str()) {
                Some(f) => f,
                None => continue,
            };
            if !matches_consumer_glob(fname) {
                continue;
            }
            let content = fs::read_to_string(&path)
                .map_err(|error| format!("cannot read tracked consumer {rel}: {error}"))?;
            for line in content.lines() {
                for m in var_re.find_iter(line) {
                    let v = m.as_str().trim_end_matches('_');
                    if v.is_empty() || v == "MIOS" {
                        continue;
                    }
                    if assigned_at_start(line, v) {
                        continue;
                    }
                    refs.insert(v.to_string());
                }
            }
        }

        // The file also carries names that are not MIOS_*; a rewrite that forgets
        // them drops 19 rows the registry is the only record of.
        let ref_file = root.join("usr/share/mios/referenced_names.txt");
        let existing = match fs::read_to_string(&ref_file) {
            Ok(existing) => Some(existing),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
            Err(error) => return Err(format!("cannot read {}: {error}", ref_file.display())),
        };
        if let Some(existing) = existing {
            for line in existing.lines() {
                let s = line.trim();
                if !s.is_empty() && !s.starts_with("MIOS_") {
                    refs.insert(s.to_string());
                }
            }
        }

        let mut out = String::new();
        for r in &refs {
            out.push_str(r);
            out.push('\n');
        }
        Ok(out)
    }

    /// Write via a sibling temp file and rename, as the Python leg does: a reader
    /// racing the generator must never see a half-written registry.
    fn write_atomic(path: &Path, body: &[u8]) -> Result<(), String> {
        use std::io::Write;
        let mut temporary =
            tempfile::NamedTempFile::new_in(path.parent().ok_or("output has no parent")?)
                .map_err(|e| e.to_string())?;
        temporary.write_all(body).map_err(|e| e.to_string())?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let permissions = fs::metadata(path)
                .map(|m| m.permissions())
                .unwrap_or_else(|_| fs::Permissions::from_mode(0o644));
            temporary
                .as_file()
                .set_permissions(permissions)
                .map_err(|e| e.to_string())?;
        }
        temporary
            .persist(path)
            .map_err(|e| format!("{}: {}", path.display(), e.error))?;
        Ok(())
    }

    fn previous_artifact(path: &Path) -> Result<Option<Vec<u8>>, String> {
        match fs::symlink_metadata(path) {
            Ok(metadata) if metadata.is_file() => {
                fs::read(path).map(Some).map_err(|e| e.to_string())
            }
            Ok(_) => Err(format!(
                "registry output must be a regular file: {}",
                path.display()
            )),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(error.to_string()),
        }
    }

    fn write_registry_pair(
        names: &Path,
        names_content: &str,
        references: &Path,
        referenced_content: &str,
    ) -> Result<(), String> {
        let previous_names = previous_artifact(names)?;
        previous_artifact(references)?;
        write_atomic(names, names_content.as_bytes())?;
        if let Err(error) = write_atomic(references, referenced_content.as_bytes()) {
            let restored = match previous_names {
                Some(bytes) => write_atomic(names, &bytes),
                None => fs::remove_file(names).map_err(|e| e.to_string()),
            };
            return Err(match restored {
                Ok(()) => format!("{error}; previous name registry restored"),
                Err(restore) => format!(
                    "{error}; restoring {} also failed: {restore}",
                    names.display()
                ),
            });
        }
        Ok(())
    }

    /// Document order for each dotted path, from one scan of the SSOT text.
    ///
    /// toml::Value::Table is a BTreeMap, so walking it is alphabetical while the
    /// Python leg follows the file, and check_names_registry compares line order.
    fn document_order(text: &str) -> std::collections::HashMap<String, usize> {
        let mut order = std::collections::HashMap::new();
        let mut cur = String::new();
        let mut seq = 0usize;
        for line in text.lines() {
            let t = line.trim();
            if t.is_empty() || t.starts_with('#') {
                continue;
            }
            // A header DECLARES its own path too. tomllib inserts `catalog` into
            // the `ai` dict when it first meets [[ai.catalog]], and the walk emits
            // that array as one leaf -- without this it had no position at all.
            // The bracket is matched rather than the line end: six channel keys
            // sorted wrong because their header carries a trailing comment.
            if t.starts_with('[') {
                let (open, close) = if t.starts_with("[[") {
                    (2, "]]")
                } else {
                    (1, "]")
                };
                if let Some(i) = t.find(close) {
                    if i >= open {
                        cur = t[open..i].trim().trim_matches('"').to_string();
                        order.entry(cur.clone()).or_insert(seq);
                        seq += 1;
                        continue;
                    }
                }
            }
            let Some(eq) = t.find('=') else { continue };
            let key = t[..eq].trim().trim_matches('"');
            if key.is_empty() || key.contains(' ') {
                continue;
            }
            let path = if cur.is_empty() {
                key.to_string()
            } else {
                format!("{cur}.{key}")
            };
            order.entry(path).or_insert(seq);
            seq += 1;
        }
        order
    }

    /// A path the scan never saw (an inline table member) sorts with its
    /// nearest declared ancestor, so siblings stay together.
    fn order_of(order: &std::collections::HashMap<String, usize>, path: &str) -> usize {
        let mut probe = path;
        loop {
            if let Some(n) = order.get(probe) {
                return *n;
            }
            match probe.rfind('.') {
                Some(i) => probe = &probe[..i],
                None => return usize::MAX,
            }
        }
    }

    fn project(root: &Path) -> Result<(String, String), Box<dyn std::error::Error>> {
        let toml_path = root.join("usr/share/mios/mios.toml");
        if !toml_path.exists() {
            return Err(format!("mios.toml not found at {}", toml_path.display()).into());
        }

        let content = fs::read_to_string(&toml_path)?;
        let data: toml::Value = toml::from_str(&content)?;

        let order = document_order(&content);
        let mut all_pairs = Vec::new();
        // Validate all keys, including sections previously omitted by a fixed list.
        mios_resolver::names::registry(&data)
            .map_err(|e| -> Box<dyn std::error::Error> { e.into() })?;
        walk_value(&data, "", &mut all_pairs);
        all_pairs.sort_by_key(|(path, _)| order_of(&order, path));

        // Census and parse failures must preserve BOTH existing projections.
        let referenced_content = render_referenced_vars(root)?;

        let mut names_content = String::new();
        for (path, env_name) in &all_pairs {
            names_content.push_str(&format!("{}  {}\n", path, env_name));
        }
        Ok((names_content, referenced_content))
    }

    /// Compare both projections in memory without writing or repairing either file.
    pub fn check(root: &Path) -> Result<(), Box<dyn std::error::Error>> {
        let (names, references) = project(root)?;
        let mut stale = Vec::new();
        for (relative, expected) in [
            ("usr/share/mios/names.generated.txt", names),
            ("usr/share/mios/referenced_names.txt", references),
        ] {
            let actual = fs::read(root.join(relative))
                .map_err(|error| format!("cannot read {relative}: {error}"))?;
            if actual != expected.as_bytes() {
                stale.push(relative);
            }
        }
        if !stale.is_empty() {
            return Err(format!("stale names-registry projection: {}", stale.join(", ")).into());
        }
        println!(
            "Names-registry projections verified read-only against SSOT and tracked consumers"
        );
        Ok(())
    }

    pub fn run(root: &Path) -> Result<(), Box<dyn std::error::Error>> {
        let (names_content, referenced_content) = project(root)?;
        let names_file = root.join("usr/share/mios/names.generated.txt");
        if let Some(parent) = names_file.parent() {
            fs::create_dir_all(parent)?;
        }
        write_registry_pair(
            &names_file,
            &names_content,
            &root.join("usr/share/mios/referenced_names.txt"),
            &referenced_content,
        )?;
        print!("{names_content}");
        Ok(())
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        fn fixture() -> tempfile::TempDir {
            let root = tempfile::tempdir().unwrap();
            let directory = root.path().join("usr/share/mios");
            fs::create_dir_all(&directory).unwrap();
            fs::write(directory.join("mios.toml"), "[ports]\nhttp=80\n").unwrap();
            fs::write(directory.join("referenced_names.txt"), "EXTERNAL_NAME\n").unwrap();
            fs::write(root.path().join("consumer.sh"), "echo ${MIOS_PORTS_HTTP}\n").unwrap();
            for args in [vec!["init", "--quiet"], vec!["add", "."]] {
                assert!(std::process::Command::new("git")
                    .arg("-C")
                    .arg(root.path())
                    .args(args)
                    .status()
                    .unwrap()
                    .success());
            }
            run(root.path()).unwrap();
            assert_eq!(
                fs::read_to_string(directory.join("names.generated.txt")).unwrap(),
                "ports.http  MIOS_PORTS_HTTP\n"
            );
            assert_eq!(
                fs::read_to_string(directory.join("referenced_names.txt")).unwrap(),
                "EXTERNAL_NAME\nMIOS_PORTS_HTTP\n"
            );
            root
        }

        #[test]
        fn check_rejects_each_stale_projection_without_repairing_it() {
            let root = fixture();
            let names = root.path().join("usr/share/mios/names.generated.txt");
            let references = root.path().join("usr/share/mios/referenced_names.txt");
            let original_names = fs::read(&names).unwrap();
            let original_references = fs::read(&references).unwrap();
            check(root.path()).unwrap();
            for (path, label) in [
                (&names, "names.generated.txt"),
                (&references, "referenced_names.txt"),
            ] {
                let mut planted = fs::read(path).unwrap();
                planted.extend_from_slice(b"MIOS_READONLY_PROBE\n");
                fs::write(path, &planted).unwrap();
                assert!(check(root.path()).unwrap_err().to_string().contains(label));
                assert_eq!(fs::read(path).unwrap(), planted);
                fs::write(&names, &original_names).unwrap();
                fs::write(&references, &original_references).unwrap();
                check(root.path()).unwrap();
            }
        }

        #[test]
        fn check_requires_complete_readable_inputs_and_both_outputs() {
            let root = fixture();
            let consumer = root.path().join("consumer.sh");
            let names = root.path().join("usr/share/mios/names.generated.txt");
            let references = root.path().join("usr/share/mios/referenced_names.txt");
            let original_names = fs::read(&names).unwrap();
            let original_references = fs::read(&references).unwrap();
            fs::remove_file(&consumer).unwrap();
            assert!(check(root.path())
                .unwrap_err()
                .to_string()
                .contains("consumer.sh"));
            fs::write(&consumer, b"invalid\xff").unwrap();
            assert!(check(root.path())
                .unwrap_err()
                .to_string()
                .contains("consumer.sh"));
            fs::write(&consumer, "echo ${MIOS_PORTS_HTTP}\n").unwrap();
            fs::remove_file(&references).unwrap();
            assert!(check(root.path())
                .unwrap_err()
                .to_string()
                .contains("referenced_names.txt"));
            assert!(!references.exists());
            assert_eq!(fs::read(&names).unwrap(), original_names);
            fs::write(&references, original_references).unwrap();
            check(root.path()).unwrap();
        }

        #[test]
        fn changed_consumer_and_unavailable_git_census_never_rewrite_outputs() {
            let root = fixture();
            let names = root.path().join("usr/share/mios/names.generated.txt");
            let references = root.path().join("usr/share/mios/referenced_names.txt");
            let before_names = fs::read(&names).unwrap();
            let before_references = fs::read(&references).unwrap();
            fs::write(
                root.path().join("consumer.sh"),
                "echo ${MIOS_NEW_CONSUMER}\n",
            )
            .unwrap();
            assert!(check(root.path())
                .unwrap_err()
                .to_string()
                .contains("referenced_names.txt"));
            fs::rename(root.path().join(".git"), root.path().join("git.saved")).unwrap();
            assert!(check(root.path())
                .unwrap_err()
                .to_string()
                .contains("git ls-files failed"));
            assert_eq!(fs::read(names).unwrap(), before_names);
            assert_eq!(fs::read(references).unwrap(), before_references);
        }

        #[test]
        fn invalid_census_and_colliding_names_preserve_both_projections() {
            let root = tempfile::tempdir().unwrap();
            let directory = root.path().join("usr/share/mios");
            fs::create_dir_all(&directory).unwrap();
            let names = directory.join("names.generated.txt");
            let references = directory.join("referenced_names.txt");
            fs::write(&names, b"previous names\xff").unwrap();
            fs::write(&references, "previous references\n").unwrap();
            for source in ["[ports]\nhttp=80\n", "[a]\n'b-c'=1\nb_c=2\n"] {
                fs::write(directory.join("mios.toml"), source).unwrap();
                assert!(run(root.path()).is_err());
                assert_eq!(fs::read(&names).unwrap(), b"previous names\xff");
                assert_eq!(
                    fs::read_to_string(&references).unwrap(),
                    "previous references\n"
                );
            }
        }

        #[test]
        fn invalid_second_output_preserves_the_first_projection() {
            let root = tempfile::tempdir().unwrap();
            let names = root.path().join("names.txt");
            let references = root.path().join("references.txt");
            fs::write(&names, "previous\n").unwrap();
            fs::create_dir(&references).unwrap();
            assert!(
                write_registry_pair(&names, "replacement\n", &references, "references\n").is_err()
            );
            assert_eq!(fs::read_to_string(&names).unwrap(), "previous\n");
            fs::remove_dir(&references).unwrap();
            write_registry_pair(&names, "replacement\n", &references, "references\n").unwrap();
            assert_eq!(fs::read_to_string(&names).unwrap(), "replacement\n");
            assert_eq!(fs::read_to_string(&references).unwrap(), "references\n");
        }

        /// A whole-line skip loses every name riding on an env-prefix line.
        #[test]
        fn only_the_name_actually_assigned_is_skipped() {
            assert!(assigned_at_start("MIOS_FOO=1", "MIOS_FOO"));
            assert!(assigned_at_start("  export MIOS_FOO=1", "MIOS_FOO"));
            assert!(!assigned_at_start(
                "MIOS_FOO=1 cmd --flag ${MIOS_BAR}",
                "MIOS_BAR"
            ));
            assert!(!assigned_at_start("echo ${MIOS_FOO}", "MIOS_FOO"));
            // export must be followed by whitespace, not glued to the name.
            assert!(!assigned_at_start("exportMIOS_FOO=1", "MIOS_FOO"));
            // the rstripped name must not match a longer assignment.
            assert!(!assigned_at_start("MIOS_FOO_=1", "MIOS_FOO"));
        }

        #[test]
        fn containerfile_glob_needs_the_dot() {
            assert!(matches_consumer_glob("Containerfile"));
            assert!(matches_consumer_glob("Containerfile.builder"));
            assert!(!matches_consumer_glob("Containerfilexyz"));
            assert!(matches_consumer_glob("x.sql"));
            assert!(matches_consumer_glob("Justfile"));
            assert!(!matches_consumer_glob("README.md"));
        }

        /// A header declares its own path, and a trailing comment must not hide it.
        #[test]
        fn headers_declare_their_own_path_and_survive_a_comment() {
            let text =
                "[a]\nx = 1\n[[a.list]]\nk = 2\n[a.chan]   # trailing\nthinking = 3\nplan = 4\n";
            let o = document_order(text);
            assert!(
                o.contains_key("a.list"),
                "an array-of-tables header declares its key"
            );
            assert!(
                o.contains_key("a.chan.thinking"),
                "a commented header still sets the prefix"
            );
            assert!(
                o["a.chan.thinking"] < o["a.chan.plan"],
                "keys keep document order, not alphabetical"
            );
            assert!(o["a.x"] < o["a.list"]);
        }

        /// An inline table's member sorts with its nearest declared ancestor rather
        /// than jumping to the end of the section.
        #[test]
        fn an_undeclared_path_inherits_its_ancestors_position() {
            let text = "[a]\nfirst = 1\ninline = { deep = 2 }\nlast = 3\n";
            let o = document_order(text);
            let d = order_of(&o, "a.inline.deep");
            assert_eq!(d, o["a.inline"]);
            assert!(o["a.first"] < d && d < o["a.last"]);
            assert_eq!(order_of(&o, "nothing.like.this"), usize::MAX);
        }

        #[test]
        fn test_walk_value() {
            let toml_str = r#"
        [ports]
        http = 80
        https = 443
        "#;
            let val: toml::Value = toml::from_str(toml_str).unwrap();
            let mut results = Vec::new();
            walk_value(&val, "", &mut results);
            assert_eq!(
                results,
                vec![
                    ("ports.http".to_string(), "MIOS_PORTS_HTTP".to_string()),
                    ("ports.https".to_string(), "MIOS_PORTS_HTTPS".to_string()),
                ]
            );
        }
    }
}
