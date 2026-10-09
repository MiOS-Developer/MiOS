// AI-hint: Tier and fragment discovery -- builds the figment provider stack in vendor < vendor.d < host < host.d < user < user.d precedence order.
// AI-related: usr/share/mios/mios.toml, /etc/mios/mios.toml
use crate::error::ResolverError;
use crate::merge::deep_merge;
use figment::providers::Serialized;
use figment::value::{Dict, Map};
use figment::{Figment, Metadata, Profile, Provider};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

pub fn normalize_path_str(p: &str) -> String {
    if p.is_empty() {
        return String::new();
    }
    // std::fs::canonicalize uses verbatim Windows prefixes. Normalize them
    // before slash conversion so native child generators can read the same root.
    let plain = if let Some(unc) = p.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{unc}")
    } else {
        p.strip_prefix(r"\\?\").unwrap_or(p).to_string()
    };
    let mut normalized = plain.replace('\\', "/");
    // MSYS drive paths (/c/MiOS) exist only on Windows. On Linux /c or /w is a
    // real directory: translating it emptied every tier of a tree mounted at /w.
    if cfg!(windows) && normalized.starts_with('/') && normalized.len() > 2 {
        let bytes = normalized.as_bytes();
        if bytes[1].is_ascii_alphabetic() && bytes[2] == b'/' {
            let drive = (bytes[1] as char).to_ascii_lowercase();
            normalized = format!("{}:/{}", drive, &normalized[3..]);
        }
    }
    normalized
}

pub fn normalize_path(p: &Path) -> PathBuf {
    PathBuf::from(normalize_path_str(&p.to_string_lossy()))
}

fn _frags(dirpath: &Path) -> Vec<PathBuf> {
    if !dirpath.exists() || !dirpath.is_dir() {
        return Vec::new();
    }
    let mut entries = Vec::new();
    if let Ok(read_dir) = fs::read_dir(dirpath) {
        for entry in read_dir.flatten() {
            let path = entry.path();
            if path.is_file() && path.extension().is_some_and(|ext| ext == "toml") {
                entries.push(normalize_path(&path));
            }
        }
    }
    entries.sort_by(|a, b| {
        let a_base = a.file_name().unwrap_or_default();
        let b_base = b.file_name().unwrap_or_default();
        a_base.cmp(b_base)
    });
    entries
}

const FHS_VENDOR: &str = "/usr/share/mios/mios.toml";
const FHS_HOST: &str = "/etc/mios/mios.toml";
const FHS_VENDOR_D: &str = "/usr/lib/mios/mios.d";

/// (vendor, host, vendor_d) when no root is given, matching mios_toml.py:
/// the installed FHS tiers, and the source-tree vendor file only when MiOS
/// is not installed. Paths relative to the caller's cwd made every unrooted
/// call on a deployed host silently drop the vendor and host tiers.
fn unrooted_defaults(fhs_vendor_installed: bool) -> (String, String, String) {
    let vendor = if fhs_vendor_installed {
        FHS_VENDOR.to_string()
    } else {
        "usr/share/mios/mios.toml".to_string()
    };
    (vendor, FHS_HOST.to_string(), FHS_VENDOR_D.to_string())
}

pub fn resolve_tier_dirs(
    root_dir: Option<&Path>,
) -> (PathBuf, PathBuf, PathBuf, PathBuf, PathBuf, PathBuf) {
    let root_str = root_dir
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|| env::var("MIOS_TOML_ROOT").unwrap_or_default());
    let root = normalize_path_str(&root_str);
    let (unrooted_vendor, unrooted_host, unrooted_vendor_d) =
        unrooted_defaults(Path::new(FHS_VENDOR).is_file());

    let vendor = env::var("MIOS_VENDOR_TOML")
        .or_else(|_| env::var("MIOS_TOML"))
        .unwrap_or_else(|_| {
            if root.is_empty() {
                unrooted_vendor
            } else {
                format!("{}/usr/share/mios/mios.toml", root)
            }
        });

    let host = env::var("MIOS_HOST_TOML").unwrap_or_else(|_| {
        if !root.is_empty() {
            format!("{}/etc/mios/mios.toml", root)
        } else {
            unrooted_host
        }
    });

    let user = env::var("MIOS_USER_TOML").unwrap_or_else(|_| {
        // Mirror userenv.sh and mios_toml.py whatever the root: etc/skel seeds
        // new homes, it is not a user tier. "~" never expands, so resolve HOME.
        let xdg = env::var("XDG_CONFIG_HOME").unwrap_or_else(|_| {
            let home = env::var("HOME")
                .or_else(|_| env::var("USERPROFILE"))
                .unwrap_or_default();
            format!("{}/.config", home)
        });
        format!("{}/mios/mios.toml", xdg)
    });

    let vendor_d = env::var("MIOS_VENDOR_TOML_D").unwrap_or_else(|_| {
        if !root.is_empty() {
            format!("{}/usr/lib/mios/mios.d", root)
        } else {
            unrooted_vendor_d
        }
    });

    let host_p = Path::new(&host);
    let host_dir = host_p.parent().unwrap_or_else(|| Path::new("."));
    let host_d = env::var("MIOS_HOST_TOML_D")
        .unwrap_or_else(|_| host_dir.join("mios.d").to_string_lossy().to_string());

    let user_p = Path::new(&user);
    let user_dir = user_p.parent().unwrap_or_else(|| Path::new("."));
    let user_d = env::var("MIOS_USER_TOML_D")
        .unwrap_or_else(|_| user_dir.join("mios.d").to_string_lossy().to_string());

    (
        normalize_path(Path::new(&vendor)),
        normalize_path(Path::new(&vendor_d)),
        normalize_path(Path::new(&host)),
        normalize_path(Path::new(&host_d)),
        normalize_path(Path::new(&user)),
        normalize_path(Path::new(&user_d)),
    )
}

pub fn resolve_layer_paths(root_dir: Option<&Path>) -> Vec<PathBuf> {
    let (vendor, vendor_d, host, host_d, user, user_d) = resolve_tier_dirs(root_dir);
    let mut paths = Vec::new();

    if vendor.exists() {
        paths.push(vendor);
    }
    paths.extend(_frags(&vendor_d));

    if host.exists() {
        paths.push(host);
    }
    paths.extend(_frags(&host_d));

    if user.exists() {
        paths.push(user);
    }
    paths.extend(_frags(&user_d));

    paths
}

/// The vendor, vendor.d, host and host.d layers -- everything a user-tier
/// write sits on top of.
pub fn resolve_layer_paths_below_user(root_dir: Option<&Path>) -> Vec<PathBuf> {
    let (vendor, vendor_d, host, host_d, _user, _user_d) = resolve_tier_dirs(root_dir);
    let mut paths = Vec::new();
    if vendor.exists() {
        paths.push(vendor);
    }
    paths.extend(_frags(&vendor_d));
    if host.exists() {
        paths.push(host);
    }
    paths.extend(_frags(&host_d));
    paths
}

/// Parse `paths` lowest precedence first and fold them with the MiOS overlay
/// rule (`merge::deep_merge`): an empty string never overrides a non-empty
/// value below it (Law 1). figment's own `merge` has no such rule, so stacking
/// the tiers as figment providers let `endpoint = ""` in /etc/mios erase the
/// vendor endpoint for every native reader while mios_toml.py kept it.
/// A layer that vanished between discovery and reading is skipped, as an
/// absent tier is; one that cannot be read or parsed is an error naming it.
pub fn merge_layer_files<P: AsRef<Path>>(paths: &[P]) -> Result<toml::Value, ResolverError> {
    let mut merged = toml::Value::Table(toml::Table::new());
    for path in paths {
        let path = path.as_ref();
        let body = match fs::read_to_string(path) {
            Ok(body) => body,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
            Err(e) => {
                return Err(ResolverError::TypeShape {
                    msg: format!("{}: {e}", path.display()),
                })
            }
        };
        let layer = body
            .parse::<toml::Table>()
            .map_err(|source| ResolverError::LayerParse {
                path: path.display().to_string(),
                source,
            })?;
        deep_merge(&mut merged, toml::Value::Table(layer));
    }
    Ok(merged)
}

/// The merged layers as one figment provider, so callers that extract a typed
/// model or stack `Env` on top keep their API while the tiers merge by Law 1.
struct Layered(Result<toml::Value, String>);

impl Provider for Layered {
    fn metadata(&self) -> Metadata {
        Metadata::named("mios.toml layers")
    }

    fn data(&self) -> Result<Map<Profile, Dict>, figment::Error> {
        match &self.0 {
            Ok(merged) => Serialized::defaults(merged).data(),
            Err(e) => Err(figment::Error::from(e.clone())),
        }
    }
}

/// A figment over `paths`, merged by `merge_layer_files`.
pub fn figment_of<P: AsRef<Path>>(paths: &[P]) -> Figment {
    Figment::from(Layered(
        merge_layer_files(paths).map_err(|e| e.to_string()),
    ))
}

pub fn create_figment(root_dir: Option<&Path>) -> Figment {
    figment_of(&resolve_layer_paths(root_dir))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::{self, File};
    use tempfile::tempdir;

    #[test]
    fn canonical_windows_roots_keep_native_drive_and_unc_identity() {
        assert_eq!(normalize_path_str(r"\\?\M:\MiOS\source"), "M:/MiOS/source");
        assert_eq!(
            normalize_path_str(r"\\?\UNC\server\share\MiOS"),
            "//server/share/MiOS"
        );
        assert_eq!(normalize_path_str("/mnt/m/MiOS"), "/mnt/m/MiOS");
    }

    #[test]
    fn test_unrooted_defaults_are_fhs_tiers() {
        // A deployed host: every tier is absolute, whatever the cwd.
        let (vendor, host, vendor_d) = unrooted_defaults(true);
        assert_eq!(vendor, "/usr/share/mios/mios.toml");
        assert_eq!(host, "/etc/mios/mios.toml");
        assert_eq!(vendor_d, "/usr/lib/mios/mios.d");
        // A source checkout without MiOS installed reads the tree's vendor file,
        // but the host and drop-in tiers stay absolute (mios_toml.py parity).
        let (vendor, host, vendor_d) = unrooted_defaults(false);
        assert_eq!(vendor, "usr/share/mios/mios.toml");
        assert!(Path::new(&host).is_absolute() && Path::new(&vendor_d).is_absolute());
    }

    #[test]
    fn test_path_normalization() {
        if cfg!(windows) {
            assert_eq!(normalize_path_str("/c/MiOS/usr/share"), "c:/MiOS/usr/share");
        } else {
            // A single-letter top directory is a real path off Windows.
            assert_eq!(normalize_path_str("/c/MiOS/usr/share"), "/c/MiOS/usr/share");
            assert_eq!(normalize_path_str("/w/usr/share/mios"), "/w/usr/share/mios");
        }
        assert_eq!(
            normalize_path_str("C:\\MiOS\\usr\\share"),
            "C:/MiOS/usr/share"
        );
    }

    #[test]
    fn test_resolve_layer_paths_order() {
        let dir = tempdir().unwrap();
        let root = dir.path();

        let vendor_dir = root.join("usr/share/mios");
        let vendor_d_dir = root.join("usr/lib/mios/mios.d");
        let host_dir = root.join("etc/mios");
        let host_d_dir = root.join("etc/mios/mios.d");

        fs::create_dir_all(&vendor_dir).unwrap();
        fs::create_dir_all(&vendor_d_dir).unwrap();
        fs::create_dir_all(&host_dir).unwrap();
        fs::create_dir_all(&host_d_dir).unwrap();

        let v_file = vendor_dir.join("mios.toml");
        File::create(&v_file).unwrap();

        let frag2 = vendor_d_dir.join("20-override.toml");
        let frag1 = vendor_d_dir.join("10-base.toml");
        File::create(&frag2).unwrap();
        File::create(&frag1).unwrap();

        let h_file = host_dir.join("mios.toml");
        File::create(&h_file).unwrap();

        let paths = resolve_layer_paths(Some(root));
        assert_eq!(paths.len(), 4);
        assert_eq!(paths[0], normalize_path(&v_file));
        assert_eq!(paths[1], normalize_path(&frag1));
        assert_eq!(paths[2], normalize_path(&frag2));
        assert_eq!(paths[3], normalize_path(&h_file));
    }

    fn layer_files(bodies: &[&str]) -> (tempfile::TempDir, Vec<PathBuf>) {
        let dir = tempdir().unwrap();
        let paths = bodies
            .iter()
            .enumerate()
            .map(|(i, body)| {
                let path = dir.path().join(format!("{i}.toml"));
                fs::write(&path, body).unwrap();
                path
            })
            .collect();
        (dir, paths)
    }

    #[test]
    fn an_empty_higher_tier_never_erases_a_lower_value() {
        // Law 1, end to end through the figment every native reader extracts.
        let (_dir, paths) = layer_files(&[
            "[ai]\nendpoint = \"http://localhost:8642/v1\"\nmodel = \"a\"\n",
            "[ai]\nendpoint = \"\"\nmodel = \"b\"\n",
        ]);
        let merged: toml::Value = figment_of(&paths).extract().unwrap();
        assert_eq!(merged["ai"]["endpoint"].as_str(), Some("http://localhost:8642/v1"));
        assert_eq!(merged["ai"]["model"].as_str(), Some("b"));
        // A non-empty higher tier still wins, and an empty value with nothing
        // below it is kept rather than dropped.
        let (_dir, paths) = layer_files(&[
            "[ai]\nendpoint = \"http://localhost:8642/v1\"\n",
            "[ai]\nendpoint = \"http://blade:8700/v1\"\nextra = \"\"\n",
        ]);
        let merged = merge_layer_files(&paths).unwrap();
        assert_eq!(merged["ai"]["endpoint"].as_str(), Some("http://blade:8700/v1"));
        assert_eq!(merged["ai"]["extra"].as_str(), Some(""));
    }

    #[test]
    fn a_malformed_layer_is_named_and_an_absent_one_is_skipped() {
        let (dir, mut paths) = layer_files(&["[ai]\nendpoint = \"x\"\n", "[ai\n"]);
        let err = merge_layer_files(&paths).unwrap_err().to_string();
        assert!(err.contains("1.toml"), "{err}");
        assert!(figment_of(&paths).extract::<toml::Value>().is_err());
        paths[1] = dir.path().join("absent.toml");
        let merged = merge_layer_files(&paths).unwrap();
        assert_eq!(merged["ai"]["endpoint"].as_str(), Some("x"));
    }
}
