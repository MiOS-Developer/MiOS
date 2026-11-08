---
name: self-improvement
description: Use whenever a system skill or shim is *almost* right but needs editing for the current task. Fork it with mios-skill-clone (skills) or mios-tool-clone (tools) into the writable overlay and modify there -- DO NOT try to edit /usr/share/mios/hermes/skills/* or /usr/libexec/mios/*; both live on the immutable bootc tree and the writes will EIO. Same-name clones override the vendor copy on the next Hermes load.
metadata:
  hermes:
    requires_tools:
      - run_sandboxed_code
      - create_file
      - read_file
      - replace_file_text
---
<!-- AI-hint: Defines the self-improvement skill for agents to clone and modify immutable vendor skills or shims into writable overlays, ensuring local overrides are used instead of failing on read-only system paths.
     AI-related: /usr/share/mios/hermes/skills/, /usr/share/mios/hermes/, mios-skill-clone, mios-tool-clone, mios-windows, mios-windows-extended -->

# self-improvement -- fork existing skills + tools, edit in writable overlay

The MiOS image ships a baseline of skills (`/usr/share/mios/hermes/
skills/<name>/`) and shims (`/usr/libexec/mios/<name>`). Both live
on the immutable bootc tree -- direct edits return EIO. The
self-improvement workflow uses the writable overlays:

| What | Vendor (read-only)                          | Writable overlay                      | PATH/precedence |
|---|---|---|---|
| Skill | `/usr/share/mios/hermes/skills/<name>/`    | `$HERMES_HOME/skills/<name>/` (= `/var/lib/mios/hermes/skills/<name>/` for the gateway, or `~/.hermes/skills/<name>/` for the operator) | local dir loads FIRST per `get_all_skills_dirs()` |
| Shim | `/usr/libexec/mios/<name>`                  | `/usr/local/bin/<name>`               | `/usr/local/bin` precedes `/usr/libexec/mios` on standard PATH |

## Decision tree

* New behaviour, no existing skill/tool resembles it -> **author from
  scratch**: write a new `SKILL.md` into the writable overlay
  (`$HERMES_HOME/skills/<name>/SKILL.md`) with `create_file` (skills),
  or `create_file` a script to `/usr/libexec/mios/<name>` + chmod +
  symlink via `run_sandboxed_code` (tools).

* Existing skill/tool is *almost* right -> **clone it** with
  `mios-skill-clone <name>` or `mios-tool-clone <name>`, edit the
  copy. Same-name clone overrides the vendor copy on the next load.

* Want both vendor and modified version side-by-side -> clone with
  `--as <new-name>`. Both ship; resolver finds both.

## How

```
# Fork a skill, edit in place
mios-skill-clone parallel-fanout
$EDITOR ~/.hermes/skills/parallel-fanout/SKILL.md   # operator-side
# OR for the gateway:
$EDITOR /var/lib/mios/hermes/skills/parallel-fanout/SKILL.md

# Fork a tool to a sibling name
mios-tool-clone mios-windows --as mios-windows-extended
$EDITOR /usr/local/bin/mios-windows-extended

# Confirm overrides took effect (run via run_sandboxed_code)
which mios-windows                               # /usr/local/bin/...
cat ~/.hermes/skills/parallel-fanout/SKILL.md    # local copy wins on next load
```

Both helpers stamp a marker comment (`mios-skill-clone:` or
`mios-tool-clone:`) into the cloned file so the next reader can tell
at a glance that it's a fork.

## When NOT to use this

* The change belongs in the vendor copy permanently and benefits
  every MiOS host -> edit the source repo (`C:\MiOS\usr\share\mios/
  hermes/skills/<name>/SKILL.md` or `usr/libexec/mios/<name>`),
  commit, push; the next bootc image will ship it.

* The change is host-specific operator preference -> the writable
  overlay (this workflow) is the right place; the vendor copy stays
  pristine.

## Marker convention

Both helpers prepend a marker so re-runs and `git`-style review
catch the fork origin:

* skills: HTML comment after the YAML frontmatter
  `> _mios-skill-clone: forked from <src> at <iso8601>. ..._`
* tools: shell comment as line 2 (preserving the shebang)
  `# mios-tool-clone: forked from <src> at <iso8601>. ...`

If either marker survives unchanged across many edits, the fork is
de-facto a vendor candidate -- consider upstreaming it.
