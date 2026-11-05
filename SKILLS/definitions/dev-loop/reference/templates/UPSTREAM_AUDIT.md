# Upstream Dependency & Release Diff Audit Brief

**Audit ID:** `UPSTREAM-[Package]-[NewVersion]`  
**Package / Component:** `[e.g., @vendor/package or github.com/org/repo]`  
**Upstream Repository:** `https://github.com/[owner]/[repo]`  
**Base Reference:** `[e.g., v1.4.2 or commit-sha-1]`  
**Target Reference:** `[e.g., v2.0.0 or commit-sha-2]`  
**Audit Date:** [YYYY-MM-DD]  
**Auditor:** [Operator or Agent ID]  
**DevLoop Telemetry:** `.devloop_artifacts/research_[timestamp].json`  

---

## 1. Executive Summary & Upgrade Recommendation
- **Verdict:** [SAFE_TO_UPGRADE | UPGRADE_WITH_MIGRATION | BLOCK_UPGRADE]
- **Risk Level:** [LOW | MEDIUM | HIGH | CRITICAL]
- **Summary:** [1-2 sentences summarizing the magnitude of upstream changes and breaking risk.]

---

## 2. Upstream Diff Breakdown

```text
Files Changed: [N] | Additions: [+N] | Deletions: [-N]
```

### 2.1 Breaking Changes & API Removals
List symbols, modules, function signatures, or CLI flags removed or modified upstream.

| Upstream Symbol / File | Nature of Change | Impact on Our Codebase | Action Required |
| :--- | :--- | :--- | :--- |
| `pkg.service.connect()` | Renamed to `.initialize()` | High (Used in 14 files) | Refactor call sites |
| `CONFIG_TIMEOUT_MS` | Constant deleted; uses dict | Medium (Used in settings) | Update config schema |
| `legacy_adapter.py` | Removed completely | None (Not used internally) | None |

### 2.2 New Capabilities & Features
List features or performance improvements available in this upstream release.
- **Feature 1:** [Description and potential benefit]
- **Feature 2:** [Description and potential benefit]

### 2.3 Security Vulnerabilities (CVEs) & Bug Fixes
- **Resolved CVEs:** [e.g., CVE-2026-XXXXX - Remediated in v2.0.0]
- **Bug Fixes:** [Fixes upstream race condition in connection pooling]

---

## 3. Invariant & Contract Verification
Check our existing repository invariants against upstream behavior:

- [ ] **Contract Invariant 1:** Zero unhandled promise rejections on network timeout.
- [ ] **Contract Invariant 2:** JSON schema outputs conform to internal parser specification.
- [ ] **Contract Invariant 3:** Thread-safety maintained under concurrent worker execution.

---

## 4. Local Remediation Plan
Concrete steps required to bump the dependency cleanly:

1. **Phase 1 (Preparation):** Update local test mocks to reflect new upstream signatures.
2. **Phase 2 (Implementation):** Refactor call sites across affected modules.
3. **Phase 3 (Verification):** Run two-sided verification test suite (`pytest tests/integration`).
4. **Phase 4 (Shipping):** Bump version in lockfile and commit with message `deps: bump [package] from [old] to [new]`.
