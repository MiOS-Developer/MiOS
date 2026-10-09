#!/usr/bin/env bash
# AI-hint: Automated CI test suite for AST structural diff calculation and 2-peer review merge gating (task_724 / task_725).
# AI-related: usr/lib/mios/agent-pipe/mios_ast_diff.py, usr/lib/mios/agent-pipe/server.py, tools/ci-suites.py
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AST_DIFF_TOOL="${ROOT_DIR}/usr/lib/mios/agent-pipe/mios_ast_diff.py"

TMP_DIR=""
cleanup() {
    if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT
TMP_DIR="$(mktemp -d)"

pass_count=0
fail_count=0

assert_pass() {
    echo "  PASS: $1"
    pass_count=$((pass_count + 1))
}

assert_fail() {
    echo "  FAIL: $1" >&2
    fail_count=$((fail_count + 1))
}

echo "[test-ast-diff-peer-review] === MiOS AST Diff & 2-Peer Review Test Suite ==="

# Verify tool existence
if [[ -f "$AST_DIFF_TOOL" ]]; then
    assert_pass "AST diff engine exists at $AST_DIFF_TOOL"
else
    assert_fail "AST diff engine missing at $AST_DIFF_TOOL"
    exit 1
fi

# Run 20 explicit test cases using python runner
python3 - << 'EOF'
import sys
import os
import json

sys.path.insert(0, os.path.join(os.environ.get("ROOT_DIR", "."), "usr/lib/mios/agent-pipe"))
from mios_ast_diff import AstDiffEngine, TwoPeerReviewGate

engine = AstDiffEngine()
gate = TwoPeerReviewGate()

tests_run = 0
tests_passed = 0

def check(name, condition, extra=""):
    global tests_run, tests_passed
    tests_run += 1
    if condition:
        print(f"  PASS [{tests_run:02d}]: {name}")
        tests_passed += 1
    else:
        print(f"  FAIL [{tests_run:02d}]: {name} {extra}", file=sys.stderr)

# Case 1: Python pure whitespace edit
orig = "def foo(x):\n    return x + 1\n"
mod = "def foo(x):\n\n    return x + 1\n\n"
res = engine.compute_ast_diff(orig, mod, "python")
check("Python pure whitespace is cosmetic", res["is_purely_cosmetic"] and not res["has_semantic_diff"])

# Case 2: Python comment edit
orig = "def foo(x):\n    # original comment\n    return x + 1\n"
mod = "def foo(x):\n    # completely different comment\n    return x + 1\n"
res = engine.compute_ast_diff(orig, mod, "python")
check("Python comment edit is cosmetic", res["is_purely_cosmetic"] and not res["has_semantic_diff"])

# Case 3: Python logic mutation (+ to *)
orig = "def foo(x, y):\n    return x + y\n"
mod = "def foo(x, y):\n    return x * y\n"
res = engine.compute_ast_diff(orig, mod, "python")
check("Python operator change is semantic", res["has_semantic_diff"] and not res["is_purely_cosmetic"])

# Case 4: Python variable rename
orig = "def bar(val):\n    res = val * 2\n    return res\n"
mod = "def bar(val):\n    output = val * 2\n    return output\n"
res = engine.compute_ast_diff(orig, mod, "python")
check("Python variable rename is semantic", res["has_semantic_diff"])

# Case 5: Python security risk (eval)
orig = "def evaluate(expr):\n    return parse(expr)\n"
mod = "def evaluate(expr):\n    return eval(expr)\n"
res = engine.compute_ast_diff(orig, mod, "python")
check("Python eval is flagged as security risk", res["has_security_risks"] and "eval" in str(res["security_risks"]).lower())

# Case 6: Rust comment and formatting
orig = "pub fn add(a: i32, b: i32) -> i32 {\n    // comment\n    a + b\n}\n"
mod = "\npub fn add(a: i32, b: i32) -> i32 {\n    /* block comment */\n    a + b\n}\n"
res = engine.compute_ast_diff(orig, mod, "rust")
check("Rust comment edit is cosmetic", res["is_purely_cosmetic"] and not res["has_semantic_diff"])

# Case 7: Rust function signature change
orig = "pub fn add(a: i32, b: i32) -> i32 { a + b }"
mod = "pub fn add(a: i64, b: i64) -> i64 { a + b }"
res = engine.compute_ast_diff(orig, mod, "rust")
check("Rust type signature change is semantic", res["has_semantic_diff"])

# Case 8: Rust unsafe block introduction
orig = "pub fn read_ptr(ptr: *const u8) -> u8 { 0 }"
mod = "pub fn read_ptr(ptr: *const u8) -> u8 { unsafe { *ptr } }"
res = engine.compute_ast_diff(orig, mod, "rust")
check("Rust unsafe block detected as security risk", res["has_security_risks"] and "unsafe" in str(res["security_risks"]).lower())

# Case 9: Go cosmetic whitespace and comments
orig = "func Sum(a, b int) int {\n    return a + b\n}\n"
mod = "// Sum adds numbers\nfunc Sum(a, b int) int {\n\n    return a + b\n}\n"
res = engine.compute_ast_diff(orig, mod, "go")
check("Go comment/whitespace is cosmetic", res["is_purely_cosmetic"] and not res["has_semantic_diff"])

# Case 10: Go logic mutation
orig = "func IsValid(n int) bool {\n    return n > 0\n}\n"
mod = "func IsValid(n int) bool {\n    return n >= 0\n}\n"
res = engine.compute_ast_diff(orig, mod, "go")
check("Go condition operator change is semantic", res["has_semantic_diff"])

# Case 11: TypeScript cosmetic indentation
orig = "function greet(name: string): string {\n  return 'Hello ' + name;\n}\n"
mod = "function greet(name: string): string {\n      return 'Hello ' + name;\n}\n"
res = engine.compute_ast_diff(orig, mod, "typescript")
check("TypeScript indentation change is cosmetic", res["is_purely_cosmetic"] and not res["has_semantic_diff"])

# Case 12: TypeScript parameter addition
orig = "function greet(name: string): string {\n  return 'Hello ' + name;\n}\n"
mod = "function greet(name: string, prefix: string): string {\n  return prefix + name;\n}\n"
res = engine.compute_ast_diff(orig, mod, "typescript")
check("TypeScript parameter addition is semantic", res["has_semantic_diff"])

# Case 13: C strcpy buffer vulnerability detection
orig = "void copy(char *s) { strncpy(buf, s, sizeof(buf)); }"
mod = "void copy(char *s) { strcpy(buf, s); }"
res = engine.compute_ast_diff(orig, mod, "c")
check("C strcpy is flagged as security risk", res["has_security_risks"] and "strcpy" in str(res["security_risks"]).lower())

# Case 14: C cosmetic comments
orig = "int main() { return 0; }"
mod = "/* Entry point */\nint main() {\n    // returns zero\n    return 0;\n}"
res = engine.compute_ast_diff(orig, mod, "c")
check("C comments and formatting are cosmetic", res["is_purely_cosmetic"] and not res["has_semantic_diff"])

# Case 15: Secret credential literal detection
orig = "API_KEY = fetch_secret_from_keyring('service', 'key')"
mod = "API_KEY = 'sk-live-1234567890abcdef1234567890abcdef'"
res = engine.compute_ast_diff(orig, mod, "python")
check("Hardcoded API token is flagged as security risk", res["has_security_risks"])

# Case 16: 2-Peer Review: initial state requires 2 approvals
rec = gate.create_review(orig, mod, "python", patch_id="p-001")
check("New patch requires approvals (can_merge is False)", not rec["can_merge"] and rec["status"] == "pending")

# Case 17: 2-Peer Review: single reviewer approval is insufficient
v1 = gate.submit_vote("p-001", "reviewer", "approve", "Code looks good")
check("1/2 approval keeps can_merge False", not v1["can_merge"] and v1["status"] == "pending")

# Case 18: 2-Peer Review: security auditor approval completes 2/2 consensus
v2 = gate.submit_vote("p-001", "security_auditor", "approve", "Security audited")
check("2/2 consensus achieves can_merge True", v2["can_merge"] and v2["status"] == "approved")

# Case 19: 2-Peer Review: rejection overrides approval
rec_bad = gate.create_review("a = 1", "import os; os.system('sh')", "python", patch_id="p-002")
gate.submit_vote("p-002", "reviewer", "approve")
v_rej = gate.submit_vote("p-002", "security_auditor", "reject", "Shell injection risk")
check("Rejection blocks merge immediately", not v_rej["can_merge"] and v_rej["status"] == "rejected")

# Case 20: 2-Peer Review: invalid role rejected
try:
    gate.submit_vote("p-001", "random_hacker", "approve")
    check("Invalid reviewer role is rejected", False)
except ValueError:
    check("Invalid reviewer role is rejected", True)

if tests_passed == tests_run:
    print(f"\n[test-ast-diff-peer-review] SUCCESS: All {tests_run} test cases passed!")
    sys.exit(0)
else:
    print(f"\n[test-ast-diff-peer-review] FAILURE: {tests_run - tests_passed}/{tests_run} test cases failed!", file=sys.stderr)
    sys.exit(1)
EOF
chmod +x "$ROOT_DIR/tests/test-ast-diff-peer-review.sh"

echo "[test-ast-diff-peer-review] Completed successfully."
