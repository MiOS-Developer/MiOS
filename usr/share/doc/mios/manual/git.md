<!-- AI-hint: Manual pages distilled from the source comments of git, sanitized, each passage anchored to the comment it came from. -->

# git

### AST Semantic Merge Resolver for Multi-Agent Git Conflicts...

AST Semantic Merge Resolver for Multi-Agent Git Conflicts (task_499).

Performs 3-way abstract syntax tree (AST) semantic merge resolution across
concurrent agent edits in Python, TOML, JSON, and Shell scripts, avoiding
spurious line-based git conflicts for independent additions, reordered imports,
and disjoint function/class declarations.

<!-- mios-src:96d0cb92f67f from usr/libexec/mios/git/ast_merge.py:4-10 -->

### MiOS Git Pre-Commit Linter & Auto-Formatter Engine....

MiOS Git Pre-Commit Linter & Auto-Formatter Engine.

Performs fast staged-file validation prior to git commit:
1. Python AST parsing and syntax validation (py_compile equivalent).
2. JSON syntax and schema validation.
3. Shell script error-level syntax validation.
4. Conventional commit message format verification.
5. Invariant enforcement: No unencrypted secrets, no vendor-specific AI references.

<!-- mios-src:dc063169a6c4 from usr/libexec/mios/git/pre_commit.py:4-13 -->

### Multi-Master Divergent Git DAG Reconciliation Engine...

Multi-Master Divergent Git DAG Reconciliation Engine (task_505).

Reconciles divergent Git commit graphs and branch histories produced by offline
peer agents or distributed cluster blades. Computes Lowest Common Ancestors (LCA),
replays commits via AST merge resolution, creates multi-agent consensus commits
signed with Ed25519 node identities, and pushes atomically across multiple git forges.

<!-- mios-src:d41728f9c3a7 from usr/libexec/mios/git/reconcile_dag.py:4-10 -->
