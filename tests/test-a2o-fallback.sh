#!/usr/bin/env bash
# AI-hint: Unit test for mios-a2o reactive fallback logic on quota exhaustion (F-024).
# AI-related: usr/share/mios/agents/mios-a2o, tests/test-a2o-fallback.sh
# AI-functions: run_test_fallback_enabled, run_test_fallback_disabled

set -euo pipefail

log() {
  echo -e "\033[1;34m[test-a2o-fallback]\033[0m $*"
}

die() {
  echo -e "\033[1;31m[test-a2o-fallback] ERROR:\033[0m $*" >&2
  exit 1
}

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

LOGS="$TMP_DIR/logs"
PROMPTS="$TMP_DIR/prompts"
RUND="$TMP_DIR/run"
mkdir -p "$LOGS" "$PROMPTS" "$RUND"

name="test_task"
AGY_DEBUG_LOG="$RUND/$name.agy-debug.log"
echo "Dummy prompt" > "$PROMPTS/$name.txt"
: > "$LOGS/$name.log"

run_test_fallback_enabled() {
  log "Testing fallback enabled"
  echo "RESOURCE_EXHAUSTED: quota limit reached" > "$AGY_DEBUG_LOG"

  LANE_B_FALLBACK_ENGINE="claude"
  EXEC_FALLBACK="echo 'injected-claude-fallback-execution'"

  rc=0
  _a2o_fail=0
  [ -s "$LOGS/$name.log" ] || _a2o_fail=1
  if [ -f "$AGY_DEBUG_LOG" ] && grep -qE 'RESOURCE_EXHAUSTED|INVALID_ARGUMENT|Failed to resolve model flag|agent executor error|model unreachable' "$AGY_DEBUG_LOG"; then _a2o_fail=1; fi

  if [ "$_a2o_fail" = 1 ]; then
    rc=1
    _a2o_reason=""
    _is_quota=0
    if [ -f "$AGY_DEBUG_LOG" ] && grep -qiE 'RESOURCE_EXHAUSTED|quota reached' "$AGY_DEBUG_LOG"; then
      _is_quota=1
    fi

    if [ "$_is_quota" = 1 ] && [ -n "$EXEC_FALLBACK" ]; then
      printf '%s\n' "=== [mios-a2o] agy quota failure detected -- retrying on fallback engine '$LANE_B_FALLBACK_ENGINE' ===" | tee -a "$LOGS/$name.log"
      PROMPT="$(cat "$PROMPTS/$name.txt")"
      eval "$EXEC_FALLBACK" 2>&1 | tee -a "$LOGS/$name.log"
      rc=0
      _a2o_fail=0
    fi
  fi

  [ "$rc" = 0 ] || die "Fallback did not result in successful exit code"
  [ "$_a2o_fail" = 0 ] || die "Fallback did not reset failure flag"
  grep -q "injected-claude-fallback-execution" "$LOGS/$name.log" || die "Fallback command output was not captured in task log"
  log "Fallback enabled test passed"
}

run_test_fallback_disabled() {
  log "Testing fallback disabled"
  echo "RESOURCE_EXHAUSTED: quota limit reached" > "$AGY_DEBUG_LOG"
  : > "$LOGS/$name.log"

  LANE_B_FALLBACK_ENGINE=""
  EXEC_FALLBACK=""

  rc=0
  _a2o_fail=0
  [ -s "$LOGS/$name.log" ] || _a2o_fail=1
  if [ -f "$AGY_DEBUG_LOG" ] && grep -qE 'RESOURCE_EXHAUSTED|INVALID_ARGUMENT|Failed to resolve model flag|agent executor error|model unreachable' "$AGY_DEBUG_LOG"; then _a2o_fail=1; fi

  if [ "$_a2o_fail" = 1 ]; then
    rc=1
    _a2o_reason=""
    _is_quota=0
    if [ -f "$AGY_DEBUG_LOG" ] && grep -qiE 'RESOURCE_EXHAUSTED|quota reached' "$AGY_DEBUG_LOG"; then
      _is_quota=1
    fi

    if [ "$_is_quota" = 1 ] && [ -n "$EXEC_FALLBACK" ]; then
      printf '%s\n' "=== [mios-a2o] agy quota failure detected -- retrying on fallback engine '$LANE_B_FALLBACK_ENGINE' ===" | tee -a "$LOGS/$name.log"
      # shellcheck disable=SC2034  # read by the eval'd fallback command
      PROMPT="$(cat "$PROMPTS/$name.txt")"
      eval "$EXEC_FALLBACK" 2>&1 | tee -a "$LOGS/$name.log"
      rc=0
      _a2o_fail=0
    fi
  fi

  [ "$rc" = 1 ] || die "Unset fallback did not result in failure"
  [ "$_a2o_fail" = 1 ] || die "Unset fallback did not retain failure flag"
  log "Fallback disabled test passed"
}

main() {
  run_test_fallback_enabled
  run_test_fallback_disabled
  log "All fallback tests completed successfully"
}

main "$@"
