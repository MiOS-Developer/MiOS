#!/usr/bin/env bash
# ==============================================================================
# devloop.sh - Universal Multi-Harness Engineering Loop & Worktree Orchestrator
# Compatible with Linux / macOS (bash, tmux, subshells, git worktrees)
# ==============================================================================
set -euo pipefail

log_info()    { echo -e "\033[0;36m[$(date '+%Y-%m-%d %H:%M:%S')] [INFO]    $*\033[0m"; }
log_warn()    { echo -e "\033[0;33m[$(date '+%Y-%m-%d %H:%M:%S')] [WARN]    $*\033[0m"; }
log_error()   { echo -e "\033[0;31m[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR]   $*\033[0m" >&2; }
log_success() { echo -e "\033[0;32m[$(date '+%Y-%m-%d %H:%M:%S')] [SUCCESS] $*\033[0m"; }

usage() {
    cat << 'USAGE'
Usage: devloop.sh --config <lanes.json> [OPTIONS]

Options:
  --config <path>       Path to lanes JSON configuration file (required)
  --reconcile           Perform pre-merge verification, atomic merge, and cleanup
  --layout <layout>     Terminal layout: tmux_grid, detached_sh, headless (default: tmux_grid)
  --base-branch <name>  Base git branch (overrides config if specified)
  --help                Show this message
USAGE
    exit 1
}

CONFIG_FILE=""
DO_RECONCILE=false
CLI_LAYOUT=""
CLI_BASE_BRANCH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)       CONFIG_FILE="$2"; shift 2 ;;
        --reconcile)    DO_RECONCILE=true; shift ;;
        --layout)       CLI_LAYOUT="$2"; shift 2 ;;
        --base-branch)  CLI_BASE_BRANCH="$2"; shift 2 ;;
        --help)         usage ;;
        *)              log_error "Unknown argument: $1"; usage ;;
    esac
done

if [[ -z "$CONFIG_FILE" || ! -f "$CONFIG_FILE" ]]; then
    log_error "Configuration file is required and must exist."
    usage
fi

if ! command -v jq &>/dev/null; then
    log_error "'jq' command is required to parse JSON configurations."
    exit 1
fi

ROOT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT_DIR" ]]; then
    log_error "Must be executed inside a git repository."
    exit 1
fi
cd "$ROOT_DIR"

OBJECTIVE="$(jq -r '.objective // empty' "$CONFIG_FILE")"
BASE_BRANCH="${CLI_BASE_BRANCH:-$(jq -r '.base_branch // "main"' "$CONFIG_FILE")}"
LAYOUT="${CLI_LAYOUT:-$(jq -r '.terminal_layout // "tmux_grid"' "$CONFIG_FILE")}"
WORKTREE_DIR="$(jq -r '.worktree_dir // ".worktrees"' "$CONFIG_FILE")"

if [[ -z "$OBJECTIVE" ]]; then
    log_error "Missing 'objective' in config file."
    exit 1
fi

# Ensure .gitignore includes worktree parent directory
if [[ -f .gitignore ]]; then
    if ! grep -qs "^${WORKTREE_DIR}/" .gitignore && ! grep -qs "^${WORKTREE_DIR}$" .gitignore; then
        log_warn "Adding '${WORKTREE_DIR}/' to .gitignore to avoid repository index pollution."
        echo -e "\n${WORKTREE_DIR}/" >> .gitignore
    fi
fi

NUM_LANES="$(jq '.lanes | length' "$CONFIG_FILE")"
if [[ "$NUM_LANES" -eq 0 ]]; then
    log_error "No lanes defined in configuration."
    exit 1
fi

# Ensure log and pid directories exist
mkdir -p .devloop_logs .devloop_pids "$WORKTREE_DIR"

# 1. Worktree Provisioning
log_info "Validating worktrees for objective: $OBJECTIVE"
for ((i=0; i<NUM_LANES; i++)); do
    LANE_JSON="$(jq -c ".lanes[$i]" "$CONFIG_FILE")"
    WORKER_ID="$(echo "$LANE_JSON" | jq -r '.worker_id')"
    WT_PATH="$(echo "$LANE_JSON" | jq -r '.worktree')"
    
    # Path traversal validation
    if [[ "$WT_PATH" =~ \.\. || "$WT_PATH" = /* ]]; then
        log_error "Invalid worktree path '$WT_PATH'. Path traversal prohibited."
        exit 1
    fi

    if [[ ! -d "$WT_PATH" ]]; then
        log_info "Creating worktree for '$WORKER_ID' at '$WT_PATH'..."
        if git show-ref --verify --quiet "refs/heads/$WORKER_ID"; then
            log_info "Branch '$WORKER_ID' already exists. Adding worktree attached to existing branch."
            git worktree add "$WT_PATH" "$WORKER_ID"
        else
            log_info "Branch '$WORKER_ID' not found. Creating new branch off '$BASE_BRANCH'."
            git worktree add "$WT_PATH" -b "$WORKER_ID" "$BASE_BRANCH"
        fi
    else
        log_info "Worktree at '$WT_PATH' already exists. Reusing."
    fi
done

# Build Runner Command per Harness
get_harness_cmd() {
    local harness="$1"
    local wt_path="$2"
    local cmd="$3"
    local test_cmd="$4"

    local base_cmd=""
    case "$harness" in
        claude)     base_cmd="claude -w '$wt_path' -p $(printf '%q' "$cmd")" ;;
        cloudcode)  base_cmd="cloudcode cli -w '$wt_path' --exec $(printf '%q' "$cmd")" ;;
        gemini)     base_cmd="gemini code -w '$wt_path' -p $(printf '%q' "$cmd")" ;;
        openai)     base_cmd="openai-agent-cli -w '$wt_path' -p $(printf '%q' "$cmd")" ;;
        codex)       base_cmd="codex exec -C '$wt_path' --prompt $(printf '%q' "$cmd")" ;;
        copilot)     base_cmd="gh copilot run -w '$wt_path' -p $(printf '%q' "$cmd")" ;;
        antigravity) base_cmd="antigravity run -w '$wt_path' --task $(printf '%q' "$cmd")" ;;
        opencode)    base_cmd="opencode run -d '$wt_path' -p $(printf '%q' "$cmd")" ;;
        cursor)      base_cmd="cursor-cli -w '$wt_path' -p $(printf '%q' "$cmd")" ;;
        custom)     base_cmd="$cmd" ;;
        *)          log_error "Unsupported harness: $harness"; exit 1 ;;
    esac

    if [[ -n "$test_cmd" ]]; then
        echo "$base_cmd && { echo -e '\033[0;32m==> DevLoop: Running test verification...\033[0m'; cd '$wt_path' && eval $(printf '%q' "$test_cmd"); }"
    else
        echo "$base_cmd"
    fi
}

# 2. Execution Launcher
if [[ "$DO_RECONCILE" == "false" ]]; then
    if [[ "$LAYOUT" == "tmux_grid" ]]; then
        if ! command -v tmux &>/dev/null; then
            log_warn "'tmux' not found. Falling back to detached subshells layout."
            LAYOUT="detached_sh"
        fi
    fi

    if [[ "$LAYOUT" == "tmux_grid" ]]; then
        SESSION_NAME="devloop-$(date +%s)"
        log_info "Starting tmux session: $SESSION_NAME"

        FIRST_LANE="$(jq -c ".lanes[0]" "$CONFIG_FILE")"
        FIRST_ID="$(echo "$FIRST_LANE" | jq -r '.worker_id')"
        FIRST_WT="$(echo "$FIRST_LANE" | jq -r '.worktree')"
        FIRST_HARNESS="$(echo "$FIRST_LANE" | jq -r '.harness')"
        FIRST_CMD="$(echo "$FIRST_LANE" | jq -r '.command')"
        FIRST_TEST="$(echo "$FIRST_LANE" | jq -r '.test_cmd // empty')"
        FIRST_EXEC="$(get_harness_cmd "$FIRST_HARNESS" "$FIRST_WT" "$FIRST_CMD" "$FIRST_TEST")"

        tmux new-session -d -s "$SESSION_NAME" -n "devloop" -c "$ROOT_DIR/$FIRST_WT" "$FIRST_EXEC; exec bash"

        for ((i=1; i<NUM_LANES; i++)); do
            LANE_JSON="$(jq -c ".lanes[$i]" "$CONFIG_FILE")"
            L_ID="$(echo "$LANE_JSON" | jq -r '.worker_id')"
            L_WT="$(echo "$LANE_JSON" | jq -r '.worktree')"
            L_HARNESS="$(echo "$LANE_JSON" | jq -r '.harness')"
            L_CMD="$(echo "$LANE_JSON" | jq -r '.command')"
            L_TEST="$(echo "$LANE_JSON" | jq -r '.test_cmd // empty')"
            L_EXEC="$(get_harness_cmd "$L_HARNESS" "$L_WT" "$L_CMD" "$L_TEST")"

            tmux split-window -t "$SESSION_NAME:devloop" -c "$ROOT_DIR/$L_WT" "$L_EXEC; exec bash"
            tmux select-layout -t "$SESSION_NAME:devloop" tiled
        done

        log_success "All lanes launched in tmux session '$SESSION_NAME'. Attach with: tmux attach -t $SESSION_NAME"
    else
        log_info "Launching detached background processes..."
        for ((i=0; i<NUM_LANES; i++)); do
            LANE_JSON="$(jq -c ".lanes[$i]" "$CONFIG_FILE")"
            L_ID="$(echo "$LANE_JSON" | jq -r '.worker_id')"
            L_WT="$(echo "$LANE_JSON" | jq -r '.worktree')"
            L_HARNESS="$(echo "$LANE_JSON" | jq -r '.harness')"
            L_CMD="$(echo "$LANE_JSON" | jq -r '.command')"
            L_TEST="$(echo "$LANE_JSON" | jq -r '.test_cmd // empty')"
            L_EXEC="$(get_harness_cmd "$L_HARNESS" "$L_WT" "$L_CMD" "$L_TEST")"

            LOG_FILE="$ROOT_DIR/.devloop_logs/${L_ID}.log"
            PID_FILE="$ROOT_DIR/.devloop_pids/${L_ID}.pid"

            (
                cd "$ROOT_DIR/$L_WT"
                echo "=== Starting Lane: $L_ID at $(date) ===" > "$LOG_FILE"
                eval "$L_EXEC" >> "$LOG_FILE" 2>&1
                echo "=== Finished Lane: $L_ID (rc=$?) at $(date) ===" >> "$LOG_FILE"
            ) &
            echo "$!" > "$PID_FILE"
            log_info "Lane '$L_ID' started (PID $!, logs at '$LOG_FILE')"
        done
        log_success "All worker lanes launched in background."
    fi
    exit 0
fi

# 3. Hardened Reconciliation and Merge Gate Phase
log_info "Starting reconciliation into base branch '$BASE_BRANCH'..."

# Verify base branch is clean
git checkout "$BASE_BRANCH"
if [[ -n "$(git status --porcelain)" ]]; then
    log_error "Base branch '$BASE_BRANCH' has uncommitted changes. Please clean or stash first."
    exit 1
fi

MERGE_SUCCESS=()
MERGE_FAILURES=()

for ((i=0; i<NUM_LANES; i++)); do
    LANE_JSON="$(jq -c ".lanes[$i]" "$CONFIG_FILE")"
    WORKER_ID="$(echo "$LANE_JSON" | jq -r '.worker_id')"
    WT_PATH="$(echo "$LANE_JSON" | jq -r '.worktree')"
    TEST_CMD="$(echo "$LANE_JSON" | jq -r '.test_cmd // empty')"

    log_info "Inspecting lane '$WORKER_ID'..."

    if [[ ! -d "$WT_PATH" ]]; then
        log_warn "Worktree '$WT_PATH' does not exist. Skipping."
        continue
    fi

    # Check for uncommitted worktree files
    if [[ -n "$(git -C "$WT_PATH" status --porcelain)" ]]; then
        log_error "Lane '$WORKER_ID' has uncommitted changes in worktree! Aborting merge for this lane."
        MERGE_FAILURES+=("$WORKER_ID: uncommitted worktree changes")
        continue
    fi

    # Run pre-merge test gate inside the worktree
    if [[ -n "$TEST_CMD" ]]; then
        log_info "Running pre-merge test gate in '$WT_PATH'..."
        if ! (cd "$WT_PATH" && eval "$TEST_CMD"); then
            log_error "Pre-merge test gate FAILED for lane '$WORKER_ID'. Aborting merge."
            MERGE_FAILURES+=("$WORKER_ID: test gate failed")
            continue
        fi
        log_success "Pre-merge test gate passed for lane '$WORKER_ID'."
    fi

    # Attempt atomic merge
    log_info "Merging branch '$WORKER_ID' into '$BASE_BRANCH'..."
    git checkout "$BASE_BRANCH"
    if git merge --no-ff "$WORKER_ID" -m "Merge lane $WORKER_ID for: $OBJECTIVE"; then
        log_success "Merged lane '$WORKER_ID' successfully."
        git worktree remove "$WT_PATH"
        git branch -d "$WORKER_ID"
        MERGE_SUCCESS+=("$WORKER_ID")
    else
        log_error "Merge CONFLICT encountered while merging '$WORKER_ID'. Aborting merge."
        git merge --abort
        MERGE_FAILURES+=("$WORKER_ID: merge conflict")
        continue
    fi
done

echo ""
log_info "=========================================="
log_info "DevLoop Reconciliation Summary"
log_info "=========================================="
log_success "Merged successfully (${#MERGE_SUCCESS[@]}): ${MERGE_SUCCESS[*]:-None}"

if [[ ${#MERGE_FAILURES[@]} -gt 0 ]]; then
    log_error "Failed merges (${#MERGE_FAILURES[@]}):"
    for fail in "${MERGE_FAILURES[@]}"; do
        log_error "  - $fail"
    done
    exit 1
else
    log_success "All active lanes merged and cleaned up cleanly!"
    exit 0
fi
