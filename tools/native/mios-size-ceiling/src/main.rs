// AI-hint: Emits [legibility].max_tracked_mb as round(tracked MiB) + [legibility].tracked_mb_headroom, so the size budget is generated rather than hand-typed.
// AI-related: usr/share/mios/mios.toml, automation/98-drift-checks.sh, src/mios-rs/mios-gate/src/ratchet.rs

#![forbid(unsafe_code)]
#![warn(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

const SSOT: &str = "usr/share/mios/mios.toml";
const KEY: &str = "max_tracked_mb";
const HEADROOM_KEY: &str = "tracked_mb_headroom";
const MIB: f64 = 1_048_576.0;

const USAGE: &str = "usage: mios-size-ceiling [--root DIR] [--check]\n";

fn die(msg: &str) -> ExitCode {
    eprintln!("mios-size-ceiling: {msg}");
    ExitCode::from(2)
}

/// Size the deliverable from the INDEX blobs, not the checkout.
///
/// `.gitattributes` checks `*.ps1` out as CRLF on every platform, so the
/// worktree carries line-ending expansion the commit does not contain. The
/// legibility check measures the same way; measuring differently here would
/// make the generated ceiling disagree with the gate that reads it.
fn tracked_bytes(root: &Path) -> Result<u64, String> {
    let ls = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["ls-files", "-s", "-z"])
        .output()
        .map_err(|e| format!("git ls-files could not run ({e})"))?;
    if !ls.status.success() {
        return Err("git ls-files failed -- no size was measured".into());
    }
    let text = String::from_utf8_lossy(&ls.stdout);
    let (oids, unmerged) = index_oids(&text);
    if let Some(first) = unmerged.first() {
        // Mid-merge, a conflicted path is listed once per stage (base, ours,
        // theirs), so summing them counted it up to three times and this tool
        // then "regenerated" the ceiling up to fit. A merge has no single
        // size: refuse, as usr/lib/mios/mios_comments.py does.
        return Err(format!(
            "the index has {} unmerged path(s) (first: {first}) -- a mid-merge index \
             has no single size; resolve the merge, then measure",
            unmerged.len()
        ));
    }
    if oids.is_empty() {
        return Err("git listed no tracked file -- an empty index is not a measurement".into());
    }

    use std::io::Write;
    let mut child = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["cat-file", "--batch-check=%(objectsize)"])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("git cat-file could not run ({e})"))?;
    {
        let stdin = child.stdin.as_mut().ok_or("git cat-file has no stdin")?;
        stdin
            .write_all(oids.as_bytes())
            .map_err(|e| format!("could not feed git cat-file ({e})"))?;
    }
    let out = child
        .wait_with_output()
        .map_err(|e| format!("git cat-file failed ({e})"))?;
    if !out.status.success() {
        return Err("git cat-file failed -- no size was measured".into());
    }
    let mut total: u64 = 0;
    for line in String::from_utf8_lossy(&out.stdout).split_whitespace() {
        if let Ok(n) = line.parse::<u64>() {
            total += n;
        }
    }
    if total == 0 {
        return Err("tracked size measured as 0 bytes -- that is a broken measurement".into());
    }
    Ok(total)
}

/// The blob ids of a `git ls-files -s -z` listing, one per line, and every
/// path listed at a stage other than 0 (an unmerged entry).
fn index_oids(listing: &str) -> (String, Vec<String>) {
    let mut oids = String::new();
    let mut unmerged = Vec::new();
    for entry in listing.split('\0') {
        if entry.trim().is_empty() {
            continue;
        }
        // "<mode> <oid> <stage>\t<path>"
        let (meta, path) = entry.split_once('\t').unwrap_or((entry, ""));
        let mut fields = meta.split_whitespace();
        let oid = fields.nth(1);
        if fields.next().is_some_and(|stage| stage != "0") {
            if unmerged.last().map(String::as_str) != Some(path) {
                unmerged.push(path.to_string());
            }
            continue;
        }
        if let Some(oid) = oid {
            oids.push_str(oid);
            oids.push('\n');
        }
    }
    (oids, unmerged)
}

/// The band a committed ceiling may sit in: [floor, floor + headroom].
///
/// A ceiling BELOW the floor means the gate that reads it is already red. A
/// ceiling above the band is slack nobody declared -- the headroom is the
/// allowance, and anything past it was not decided, it drifted.
fn band(bytes: u64, headroom: i64) -> (i64, i64) {
    let floor = (bytes as f64 / MIB).round() as i64;
    (floor, floor + headroom)
}

fn int_field(v: &toml::Value, section: &str, key: &str) -> Option<i64> {
    v.get(section)?.get(key)?.as_integer()
}

fn main() -> ExitCode {
    let mut root = PathBuf::from(std::env::var("MIOS_ROOT").unwrap_or_else(|_| ".".into()));
    let mut check = false;
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--check" => check = true,
            "--root" => match args.next() {
                Some(v) => root = PathBuf::from(v),
                None => return die("--root needs a directory"),
            },
            "-h" | "--help" => {
                print!("{USAGE}");
                return ExitCode::SUCCESS;
            }
            other => return die(&format!("unrecognised argument {other:?}\n{USAGE}")),
        }
    }

    let ssot = root.join(SSOT);
    let Ok(text) = std::fs::read_to_string(&ssot) else {
        return die(&format!("{} could not be read", ssot.display()));
    };
    let Ok(parsed) = text.parse::<toml::Value>() else {
        return die(&format!("{} did not parse", ssot.display()));
    };
    let Some(headroom) = int_field(&parsed, "legibility", HEADROOM_KEY) else {
        return die(&format!(
            "[legibility].{HEADROOM_KEY} is absent -- the allowance is operator-tunable \
             and this tool will not invent one"
        ));
    };
    if headroom < 0 {
        return die(&format!("[legibility].{HEADROOM_KEY} is negative"));
    }
    let bytes = match tracked_bytes(&root) {
        Ok(b) => b,
        Err(e) => return die(&e),
    };
    let (floor, cap) = band(bytes, headroom);

    let Some(current) = int_field(&parsed, "legibility", KEY) else {
        return die(&format!("[legibility].{KEY} is absent from {SSOT}"));
    };

    if check {
        if current < floor {
            eprintln!(
                "mios-size-ceiling: [legibility].{KEY} = {current} is BELOW the measured \
                 floor of {floor} MiB -- the tree already exceeds its own budget"
            );
            return ExitCode::from(1);
        }
        if current > cap {
            eprintln!(
                "mios-size-ceiling: [legibility].{KEY} = {current} exceeds {floor} + \
                 headroom {headroom} = {cap} -- slack past the declared allowance is not \
                 a budget, regenerate: mios-size-ceiling"
            );
            return ExitCode::from(1);
        }
        println!("[size-ceiling] {KEY} = {current} is within [{floor}, {cap}] ({bytes} tracked bytes, headroom {headroom})");
        return ExitCode::SUCCESS;
    }

    if current >= floor && current <= cap {
        println!("[size-ceiling] {KEY} = {current} already within [{floor}, {cap}]; unchanged");
        return ExitCode::SUCCESS;
    }
    let want = cap;
    let mut wrote = false;
    let mut out = String::with_capacity(text.len() + 8);
    for line in text.split_inclusive('\n') {
        let trimmed = line.trim_start();
        if !wrote && trimmed.starts_with(KEY) && trimmed[KEY.len()..].trim_start().starts_with('=')
        {
            // Preserve the column alignment and any trailing comment: the
            // comment carries this value's provenance, and re-flowing the
            // column would put unrelated churn in every diff that touches it.
            let eq = line.find('=').unwrap_or(KEY.len());
            let head = &line[..=eq];
            let tail = match line.find('#') {
                Some(i) => format!("  {}", line[i..].trim_end()),
                None => String::new(),
            };
            out.push_str(&format!("{head} {want}{tail}\n"));
            wrote = true;
            continue;
        }
        out.push_str(line);
    }
    if !wrote {
        return die(&format!(
            "could not find a `{KEY} =` line to rewrite in {SSOT}"
        ));
    }
    if std::fs::write(&ssot, out).is_err() {
        return die(&format!("{} could not be written", ssot.display()));
    }
    println!("[size-ceiling] {KEY} {current} -> {want} (floor {floor} + headroom {headroom})");
    ExitCode::SUCCESS
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_band_is_the_floor_plus_the_declared_allowance() {
        // 202.54 MiB rounds to 203, so with 1 MiB of headroom a committed
        // ceiling of 203 or 204 is valid and 202 is already red.
        let bytes = (202.54 * MIB) as u64;
        assert_eq!(band(bytes, 1), (203, 204));
        assert_eq!(band(bytes, 0), (203, 203));
    }

    #[test]
    fn rounding_is_half_up_at_the_boundary_both_ways() {
        assert_eq!(band((201.49 * MIB) as u64, 0).0, 201);
        assert_eq!(band((201.51 * MIB) as u64, 0).0, 202);
    }

    #[test]
    fn a_merged_index_lists_every_blob_once() {
        let listing = "100644 aaaa 0\tREADME.md\x00100755 bbbb 0\ttools/x.sh\0";
        let (oids, unmerged) = index_oids(listing);
        assert_eq!(oids, "aaaa\nbbbb\n");
        assert!(unmerged.is_empty());
    }

    #[test]
    fn an_unmerged_path_is_named_once_and_never_summed() {
        // One conflicted path at stages 1, 2 and 3: counting all three is the
        // inflation this refuses.
        let listing = "100644 aaaa 0\tREADME.md\0\
                       100644 b111 1\tTASKS.jsonl\0\
                       100644 b222 2\tTASKS.jsonl\0\
                       100644 b333 3\tTASKS.jsonl\0";
        let (oids, unmerged) = index_oids(listing);
        assert_eq!(oids, "aaaa\n");
        assert_eq!(unmerged, vec!["TASKS.jsonl".to_string()]);
    }

    #[test]
    fn headroom_widens_the_band_by_exactly_itself() {
        let bytes = (10.0 * MIB) as u64;
        let (f, c) = band(bytes, 5);
        assert_eq!(f, 10);
        assert_eq!(c - f, 5);
    }
}
