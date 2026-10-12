<!-- AI-hint: How MiOS documentation is produced -- AI-hints stay in source and are projected forward, comments are scraped, sanitized and distilled into the manual on a daily pass. -->

# The documentation pipeline

<!-- MIOS-GEN:boilerplate:what-mios-is -->
MiOS is one thing built two ways at once: an immutable, `bootc`/OCI-shaped
Fedora workstation -- the whole OS is a single container image, so `bootc
upgrade` behaves like a `git pull` and `bootc rollback` like a Ctrl-Z -- that
is *also* a local, self-hosted, agentic AI operating system.

<!-- derived from usr/share/mios/mios.toml [docs.boilerplate].what-mios-is -->
<!-- /MIOS-GEN:boilerplate:what-mios-is -->

Documentation here is **produced, not maintained**. Two different kinds of
knowledge live in the source tree, and they are treated differently on purpose.

## AI-hints stay put

Every taggable file carries an `AI-hint:` header — one line saying what the file
is for — plus `AI-related:` cross-links. These are **never scraped out**. They
stay next to the code, are pushed forward and updated in place by
`usr/libexec/mios/mios-ai-tag`, and reach the documentation by *projection*: the
`index:<glob>` and `related:<path>` derivers read them at render time.

That is why `usr/share/doc/mios/reference/tool-index.md` can describe every
shipped tool without anyone maintaining a list. Correct a description by editing
the header; the page follows.

## Comments are scraped, sanitized and distilled

Narrative comments are the opposite case. A long rationale block is written for
whoever is editing that file, but its knowledge belongs to the reader of a
manual. So on the Day-N+1 pass:

1. **Scrape** — `mios-manual distill` takes every block the classifier marked
   `MIGRATE` that has not landed: exactly the set `check_docs_ratchet` counts,
   so a clean distill leaves that ratchet at zero. Generated artifacts never reach
   it, because `[docs].blocklist_globs` classifies them `DROP`.
2. **Sanitize** — `[docs.sanitize]` rewrites developer-box paths to their FHS
   canonicals and redacts secret-shaped values, because a comment is written for
   a contributor's machine and a manual ships inside the image.
3. **Distil** — each passage is appended to the manual page for its area, under
   a heading, carrying a `mios-src:<sha12>` anchor back to the comment it came
   from.

The source file is **not modified**. Removing a scraped comment is a separate,
deliberate act (`mios-manual prune`), permitted only where `landed()` proves the
knowledge is already in a doc — and `check_comment_landing` keeps proving it
afterwards.

## What runs when

| When | What | Writes |
|---|---|---|
| Daily (`mios-doc-distill.timer`, 03:30) | scrape → sanitize → distil → render | the manual pages and derived sections |
| Every `just drift-gate` / CI | `ledger --check`, `render --check`, ratchet + landing gates | nothing; fails on drift |
| `tools/sync-generated.sh` | ledger and ratchet floor, last | the corpus census |

The timer **no-ops on a booted host**: `/usr` is read-only there and an immutable
image has no new comments to scrape, so it degrades open with a log line rather
than failing nightly. It does its work in a source checkout — a dev box or
MiOS-DEV — where comments actually change.

## The gates that keep it honest

| Gate | Asserts |
|---|---|
| `check_manual_ledger` | the corpus census regenerates verbatim from the tracked tree |
| `check_manual_generated` | derived sections match the SSOT **and** authored prose outside a marker is untouched |
| `check_comment_landing` | every pruned comment still lands in a doc |
| `check_docs_ratchet` | the narrative and over-cap-hint counts stay at or below their ceilings |
| `check_docs_ratchet_monotone` | a ceiling never rises, against HEAD *and* the recorded low-water mark |
| `check_hint_coverage` | new taggable files carry an AI-hint |

## Cross-refs

- `usr/share/doc/mios/reference/tool-index.md` — the projection of every AI-hint.
- `usr/share/doc/mios/reference/build-pipeline.md` — derived build phases and root exceptions.
- `usr/share/doc/mios/manual/` — the distilled per-area pages.
- `docs/design/doc-generative-documentation.md` — the programme specification.
