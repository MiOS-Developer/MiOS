#!/usr/bin/env python3
# AI-hint: Generates a consolidated markdown reference of all build scripts in execution order, used by agents to map the MiOS build pipeline, i...
# AI-doc: usr/share/doc/mios/manual/lib.md

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "usr/share/doc/mios/reference/build-scripts.md"

LAYERS = [
    ("Layer 1 -- User entry points (mios-bootstrap repo)",
     []),  # populated dynamically below if sibling repo exists

    ("Layer 2 -- System-side installers",
     [
         "automation/install.sh",
         "automation/install-bootstrap.sh",
         "automation/build-mios.sh",
     ]),

    ("Layer 3 -- Build orchestrators",
     [
         "Containerfile",
         "Justfile",
         "mios-build-local.ps1",
         "automation/mios-build-builder.ps1",
         "preflight.ps1",
         "preflight.sh",
         "push-to-github.ps1",
         "Get-MiOS.ps1",
     ]),

    ("Layer 4a -- Library (sourced helpers)",
     [
         "automation/lib/common.sh",
         "automation/lib/packages.sh",
         "automation/lib/masking.sh",
         "automation/lib/paths.sh",
     ]),

    ("Layer 4b -- Master orchestrator",
     [
         "automation/build.sh",
     ]),

    ("Layer 4c-j -- Numbered phase scripts (lex order)",
     []),  # populated below

    ("Layer 4k -- Helpers",
     [
         "automation/ai-bootstrap.sh",
         "automation/bcvk-wrapper.sh",
         "automation/bootstrap.sh",
         "automation/enroll-mok.sh",
         "automation/generate-mok-key.sh",
         "automation/overlay-builder.sh",
     ]),

    ("Layer 5 -- Postcheck + system-files overlay",
     [
     ]),
]

BOOTSTRAP_ROOT = ROOT.parent / "mios-bootstrap"
if BOOTSTRAP_ROOT.is_dir():
    for f in ["bootstrap.sh", "bootstrap.ps1", "install.sh", "install.ps1"]:
        p = BOOTSTRAP_ROOT / f
        if p.is_file():
            LAYERS[0][1].append(str(p))

nn_scripts = sorted((ROOT / "automation").glob("[0-9][0-9]-*.sh"))
LAYERS[5] = (LAYERS[5][0], [str(p.relative_to(ROOT)) for p in nn_scripts])

def fence_for(path: Path) -> str:
    """Return the appropriate ``` fence language tag for a file."""
    s = path.suffix.lower()
    name = path.name
    if name == "Containerfile":
        return "dockerfile"
    if name == "Justfile":
        return "makefile"  # closest fit; just-flavored Makefile syntax
    return {
        ".sh":   "bash",
        ".ps1":  "powershell",
        ".py":   "python",
        ".toml": "toml",
        ".md":   "markdown",
    }.get(s, "")

def section(path_str: str, content: str, fence: str) -> str:
    return f"\n### `{path_str}`\n\n```{fence}\n{content}\n```\n"

def main():
    lines = []
    lines.append("# 'MiOS' Build Scripts -- Full Source Bundle\n")
    lines.append("Every script that participates in building the 'MiOS' OCI image, in")
    lines.append("execution order, with complete source and no truncation. Each section")
    lines.append("header carries the file path; each fenced block carries the verbatim")
    lines.append("file contents. Use `Ctrl-F` against a path to find a script.\n")
    ssot = tomllib.loads((ROOT / "usr/share/mios/mios.toml").read_text())
    native = [phase for phase in ssot["build"]["phases"]["list"] if phase.get("native")]
    if native:
        lines.append("\nNative phases retain their registry identities and execution order:")
        for phase in native:
            lines.append(f"- `{phase['script']}`: typed `{phase['native']}` handler, "
                         f"`miosd build-phase --phase {phase['script']}`; "
                         "source: `src/mios-rs/miosd/src/main.rs`.")
        lines.append("")
    lines.append("---\n")

    total_files = 0
    total_lines = 0
    skipped = []

    for label, paths in LAYERS:
        if not paths:
            continue
        lines.append(f"\n## {label}\n")
        for path_str in paths:
            p = Path(path_str)
            if not p.is_absolute():
                p = ROOT / path_str
            if not p.is_file():
                skipped.append(path_str)
                continue
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                skipped.append(f"{path_str} (read error: {e})")
                continue
            try:
                display = str(p.relative_to(ROOT)).replace("\\", "/")
            except ValueError:
                anchor = None
                for ancestor in p.parents:
                    if (ancestor / ".git").exists():
                        anchor = ancestor.parent
                        break
                if anchor is not None:
                    display = str(p.relative_to(anchor)).replace("\\", "/")
                else:
                    display = p.name
            lines.append(section(display, content.rstrip("\n"), fence_for(p)))
            total_files += 1
            total_lines += content.count("\n") + 1

    if skipped:
        lines.append(f"\n## Skipped (not found at expected paths)\n")
        for s in skipped:
            lines.append(f"- `{s}`")

    lines.append(f"\n---\n")
    lines.append(f"\n**Bundle stats:** {total_files} files, "
                 f"{total_lines} source lines aggregated.\n")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"  files: {total_files}")
    print(f"  source lines aggregated: {total_lines}")
    if skipped:
        print(f"  skipped (missing): {len(skipped)}")

if __name__ == "__main__":
    main()
