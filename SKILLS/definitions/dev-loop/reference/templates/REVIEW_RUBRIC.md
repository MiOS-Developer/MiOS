# SCOPE Staged Code Review Rubric & Assurance Gate

**Review Target:** `[Branch name, commit SHA, or PR ID]`  
**Reviewer / Engine:** `reference/review.py` (SCOPE Protocol)  
**Date:** [YYYY-MM-DD]  
**Result:** [PASSED | BLOCKED | REVISE]  
**Telemetry:** `.devloop_artifacts/review_[timestamp].json`  

---

## 1. SCOPE Assurance Model

The review moves through three sequential assurance gates:
1. **Agent Review:** Automated static analysis, secret scanning, and syntax linting.
2. **Developer Review:** Invariant verification, contract adherence, and edge-case testing.
3. **Peer / Operator Gate:** Systemic risk assessment and architectural sign-off.

---

## 2. Evaluation Dimensions

### 2.1 Security & Hygiene Gate (Zero Tolerance)
- [ ] **Secrets & Credentials:** Zero hardcoded API tokens, private keys, passwords, or session tokens.
- [ ] **Injection Safety:** Zero unsanitized `shell=True`, `os.system()`, or raw SQL concatenations.
- [ ] **Input Sanitization:** All untrusted parameters are type-checked and bounds-validated.
- [ ] **Debug Artifacts:** Zero leftover `console.log`, `print()`, or temporary breakpoints in production code.

### 2.2 Contract & Invariant Preservation
- [ ] **API Schemas:** Request/response schemas match `reference/templates/GOALS.md`.
- [ ] **Backwards Compatibility:** No unversioned breaking changes to public interfaces.
- [ ] **Error Handling:** All error paths return structured, predictable error envelopes.

### 2.3 Verification & Test Integrity
- [ ] **Two-Sided Verification:** Both functional assertion and regression checks pass.
- [ ] **Deterministic Behavior:** Zero flaky assertions or reliance on arbitrary `sleep()` durations.
- [ ] **Edge Cases Covered:** Null values, empty arrays, timeout boundaries, and unicode characters tested.

### 2.4 Code Simplification & Elegance
- [ ] **Minimal Abstraction:** Solves the stated problem without premature micro-frameworks.
- [ ] **Dead Code Elimination:** No commented-out blocks or orphaned helper routines.
- [ ] **Documentation:** Public methods include clear docstrings and usage examples.

---

## 3. Findings Ledger

| Finding ID | Severity | File & Line | Rule Violated | Description & Fix Required |
| :--- | :--- | :--- | :--- | :--- |
| **F1** | `[BLOCKER]` | `src/auth.py:42` | `security/secret-leak` | Hardcoded test token in default parameter |
| **F2** | `[WARNING]` | `src/worker.py:118`| `hygiene/debug-print` | Leftover print debugging in retry loop |
| **F3** | `[SUGGESTION]` | `src/utils.py:15` | `style/simplification`| Can be simplified using built-in standard library |

---

## 4. Final Verdict & Sign-Off
- **Status:** `[APPROVED FOR MERGE / CHANGES REQUESTED]`
- **Sign-off By:** [Operator or Lead Agent]
