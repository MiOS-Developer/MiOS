<!-- AI-hint: Portable snapshot of global MiOS skills for detached transfer and installation.
AI-related: MANIFEST.json, SHA256SUMS, definitions/, catalog/, context/ -->
# Global MiOS Skills

This directory is a portable export snapshot: **16 instruction skills and
12 typed procedure definitions**, including every tracked supporting file within
each instruction skill. Original sources remain authoritative and unchanged.

- `definitions/<name>/SKILL.md`: complete instruction skill folders.
- `catalog/*.json`: the 12 shipped MiOS procedure definitions.
- `context/`: repository instructions and native coordination/MCP contracts.
- `MANIFEST.json`: inventory, provenance, source hashes and scope.
- `SHA256SUMS`: checksums for every payload file.
- `mios-global-skills.tar.gz`: standalone archive; no Git checkout required.
- `mios-global-skills.tar.gz.sha256`: archive checksum.

## Transfer and detached download

Copy the archive and its checksum from this machine; replace SOURCE_HOST with
the existing authorized SSH host. No new server or public endpoint is created.

```sh
scp SOURCE_HOST:/SKILLS/mios-global-skills.tar.gz .
scp SOURCE_HOST:/SKILLS/mios-global-skills.tar.gz.sha256 .
sha256sum -c mios-global-skills.tar.gz.sha256
mkdir mios-skills-download
tar -xzf mios-global-skills.tar.gz -C mios-skills-download
cd mios-skills-download/SKILLS
sha256sum -c SHA256SUMS
```

Direct detached download from the repository (no Git checkout):

```sh
curl -fLO https://raw.githubusercontent.com/MiOS-Developer/MiOS/main/SKILLS/mios-global-skills.tar.gz
curl -fLO https://raw.githubusercontent.com/MiOS-Developer/MiOS/main/SKILLS/mios-global-skills.tar.gz.sha256
sha256sum -c mios-global-skills.tar.gz.sha256
```

Then extract and verify the payload as above. For an immutable download,
replace `main` in both URLs with the same published commit SHA.


## Install instruction skills

Choose the skill discovery directory configured by the receiving agent.
An ordinary user-local installation is:

```sh
skill_destination="$HOME/.agents/skills"
mkdir -p "$skill_destination"
for skill in definitions/*; do
    name="$(basename "$skill")"
    if [ -e "$skill_destination/$name" ]; then
        printf 'Keep existing skill: %s\n' "$name"
    else
        cp -a "$skill" "$skill_destination/$name"
    fi
done
```

Existing names are preserved. Reopen the receiving agent's session or refresh
its skill discovery after copying. On Windows, extract with built-in `tar`,
verify the archive with `Get-FileHash -Algorithm SHA256`, and copy the complete
skill folders into the configured discovery directory.

## MiOS procedure catalog

The JSON procedures are a separate runtime format, not SKILL.md instructions.
They require a working MiOS agent-pipe, typed verbs and database. Optional import
on a receiving MiOS machine uses its existing native command:

```sh
mios-skills import --source "$PWD/catalog"
```

Import preserves declared status, including promoted entries. This package
does not import, promote or execute procedures automatically. Installing the
instruction folders alone does not install MiOS services or native tools.
Read source-specific prerequisites and the included coordination contracts.

## Inventory boundary

Sources: /.agents/skills, the MiOS CI/CD plugin skill, shipped system skill
folders and the vendor procedure catalog. The local catalog is empty; a
successful native database export returned zero rows. Profile discovery found
no additional MiOS-specific skills. Eight notes-vault records labelled
"skills" are private conversation transcripts, not reusable global definitions;
their content is excluded. Generic third-party plugin skills, temporary worker
copies, caches, credentials and application state are outside this export.

Supporting references inside a skill folder are included. References to
MiOS system commands still require the corresponding installed MiOS runtime.
The context folder is reference material; it does not change host policy.
