#!/usr/bin/env bash
# ==============================================================================
# install.sh - Universal Installer for Dev Loop Suite
# Installs /dev-loop, /goal, /research (/rs), /websearch (/s), /audit, /sync,
# /triage (/tr), /review (/rv), /ship (/sh) across all AI harnesses.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEVLOOP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMMANDS_DIR="$DEVLOOP_ROOT/commands"
WORKFLOWS_DIR="$(cd "$DEVLOOP_ROOT/../../workflows" && pwd)"

SCOPE="global"
DRY_RUN=false

usage() {
    cat << 'USAGE'
Usage: ./install.sh [OPTIONS]

Options:
  --global     Install to user home configuration directories (default)
  --project    Install to current repository configuration directories
  --dry-run    Display planned installations without modifying filesystem
  --help       Show this message
USAGE
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --global)   SCOPE="global"; shift ;;
        --project)  SCOPE="project"; shift ;;
        --dry-run)  DRY_RUN=true; shift ;;
        --help)     usage ;;
        *)          echo "Unknown option: $1" >&2; usage ;;
    esac
done

echo -e "\033[0;36m==> Installing Complete Dev Loop Suite (Scope: $SCOPE, DryRun: $DRY_RUN)...\033[0m"

install_file() {
    local src="$1"
    local dest_dir="$2"
    local dest_filename="$3"
    local target="$dest_dir/$dest_filename"

    if [[ ! -f "$src" ]]; then
        echo -e "\033[0;33m[SKIP] Source missing: $src\033[0m"
        return
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        echo -e "[DRY-RUN] Would install: $src -> $target"
        return
    fi

    mkdir -p "$dest_dir"
    cp "$src" "$target"
    echo -e "\033[0;32m[INSTALLED]\033[0m $target"
}

if [[ "$SCOPE" == "global" ]]; then
    # Claude Code
    install_file "$WORKFLOWS_DIR/dev-loop.md" "$HOME/.claude/commands" "dev-loop.md"
    install_file "$WORKFLOWS_DIR/goal.md" "$HOME/.claude/commands" "goal.md"
    install_file "$WORKFLOWS_DIR/research.md" "$HOME/.claude/commands" "research.md"
    install_file "$WORKFLOWS_DIR/research.md" "$HOME/.claude/commands" "rs.md"
    install_file "$WORKFLOWS_DIR/websearch.md" "$HOME/.claude/commands" "websearch.md"
    install_file "$WORKFLOWS_DIR/websearch.md" "$HOME/.claude/commands" "s.md"
    install_file "$WORKFLOWS_DIR/audit.md" "$HOME/.claude/commands" "audit.md"
    install_file "$WORKFLOWS_DIR/sync.md" "$HOME/.claude/commands" "sync.md"
    install_file "$WORKFLOWS_DIR/triage.md" "$HOME/.claude/commands" "triage.md"
    install_file "$WORKFLOWS_DIR/triage.md" "$HOME/.claude/commands" "tr.md"
    install_file "$WORKFLOWS_DIR/review.md" "$HOME/.claude/commands" "review.md"
    install_file "$WORKFLOWS_DIR/review.md" "$HOME/.claude/commands" "rv.md"
    install_file "$WORKFLOWS_DIR/ship.md" "$HOME/.claude/commands" "ship.md"
    install_file "$WORKFLOWS_DIR/ship.md" "$HOME/.claude/commands" "sh.md"

    # Gemini CLI / Code Assist
    install_file "$COMMANDS_DIR/gemini/dev-loop.toml" "$HOME/.gemini/commands" "dev-loop.toml"
    install_file "$COMMANDS_DIR/gemini/goal.toml" "$HOME/.gemini/commands" "goal.toml"
    install_file "$COMMANDS_DIR/gemini/research.toml" "$HOME/.gemini/commands" "research.toml"
    install_file "$COMMANDS_DIR/gemini/websearch.toml" "$HOME/.gemini/commands" "websearch.toml"
    install_file "$COMMANDS_DIR/gemini/audit.toml" "$HOME/.gemini/commands" "audit.toml"
    install_file "$COMMANDS_DIR/gemini/sync.toml" "$HOME/.gemini/commands" "sync.toml"
    install_file "$COMMANDS_DIR/gemini/triage.toml" "$HOME/.gemini/commands" "triage.toml"
    install_file "$COMMANDS_DIR/gemini/review.toml" "$HOME/.gemini/commands" "review.toml"
    install_file "$COMMANDS_DIR/gemini/ship.toml" "$HOME/.gemini/commands" "ship.toml"

    # OpenAI Codex
    install_file "$COMMANDS_DIR/codex/dev-loop.md" "$HOME/.codex/prompts" "dev-loop.md"
    install_file "$COMMANDS_DIR/codex/goal.md" "$HOME/.codex/prompts" "goal.md"
    install_file "$COMMANDS_DIR/codex/research.md" "$HOME/.codex/prompts" "research.md"
    install_file "$COMMANDS_DIR/codex/research.md" "$HOME/.codex/prompts" "rs.md"
    install_file "$COMMANDS_DIR/codex/websearch.md" "$HOME/.codex/prompts" "websearch.md"
    install_file "$COMMANDS_DIR/codex/websearch.md" "$HOME/.codex/prompts" "s.md"
    install_file "$COMMANDS_DIR/codex/audit.md" "$HOME/.codex/prompts" "audit.md"
    install_file "$COMMANDS_DIR/codex/sync.md" "$HOME/.codex/prompts" "sync.md"
    install_file "$COMMANDS_DIR/codex/triage.md" "$HOME/.codex/prompts" "triage.md"
    install_file "$COMMANDS_DIR/codex/triage.md" "$HOME/.codex/prompts" "tr.md"
    install_file "$COMMANDS_DIR/codex/review.md" "$HOME/.codex/prompts" "review.md"
    install_file "$COMMANDS_DIR/codex/review.md" "$HOME/.codex/prompts" "rv.md"
    install_file "$COMMANDS_DIR/codex/ship.md" "$HOME/.codex/prompts" "ship.md"
    install_file "$COMMANDS_DIR/codex/ship.md" "$HOME/.codex/prompts" "sh.md"

    # Google Antigravity
    install_file "$COMMANDS_DIR/antigravity/dev-loop.md" "$HOME/.antigravity/workflows" "dev-loop.md"
    install_file "$COMMANDS_DIR/antigravity/goal.md" "$HOME/.antigravity/workflows" "goal.md"
    install_file "$COMMANDS_DIR/antigravity/research.md" "$HOME/.antigravity/workflows" "research.md"
    install_file "$COMMANDS_DIR/antigravity/websearch.md" "$HOME/.antigravity/workflows" "websearch.md"
    install_file "$COMMANDS_DIR/antigravity/audit.md" "$HOME/.antigravity/workflows" "audit.md"
    install_file "$COMMANDS_DIR/antigravity/sync.md" "$HOME/.antigravity/workflows" "sync.md"
    install_file "$COMMANDS_DIR/antigravity/triage.md" "$HOME/.antigravity/workflows" "triage.md"
    install_file "$COMMANDS_DIR/antigravity/review.md" "$HOME/.antigravity/workflows" "review.md"
    install_file "$COMMANDS_DIR/antigravity/ship.md" "$HOME/.antigravity/workflows" "ship.md"

    # OpenCode
    install_file "$COMMANDS_DIR/opencode/dev-loop.md" "$HOME/.opencode/commands" "dev-loop.md"
    install_file "$COMMANDS_DIR/opencode/goal.md" "$HOME/.opencode/commands" "goal.md"
    install_file "$COMMANDS_DIR/opencode/research.md" "$HOME/.opencode/commands" "research.md"
    install_file "$COMMANDS_DIR/opencode/websearch.md" "$HOME/.opencode/commands" "websearch.md"
    install_file "$COMMANDS_DIR/opencode/audit.md" "$HOME/.opencode/commands" "audit.md"
    install_file "$COMMANDS_DIR/opencode/sync.md" "$HOME/.opencode/commands" "sync.md"
    install_file "$COMMANDS_DIR/opencode/triage.md" "$HOME/.opencode/commands" "triage.md"
    install_file "$COMMANDS_DIR/opencode/review.md" "$HOME/.opencode/commands" "review.md"
    install_file "$COMMANDS_DIR/opencode/ship.md" "$HOME/.opencode/commands" "ship.md"
else
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

    # Claude Code project
    install_file "$WORKFLOWS_DIR/dev-loop.md" "$REPO_ROOT/.claude/commands" "dev-loop.md"
    install_file "$WORKFLOWS_DIR/goal.md" "$REPO_ROOT/.claude/commands" "goal.md"
    install_file "$WORKFLOWS_DIR/research.md" "$REPO_ROOT/.claude/commands" "research.md"
    install_file "$WORKFLOWS_DIR/research.md" "$REPO_ROOT/.claude/commands" "rs.md"
    install_file "$WORKFLOWS_DIR/websearch.md" "$REPO_ROOT/.claude/commands" "websearch.md"
    install_file "$WORKFLOWS_DIR/websearch.md" "$REPO_ROOT/.claude/commands" "s.md"
    install_file "$WORKFLOWS_DIR/audit.md" "$REPO_ROOT/.claude/commands" "audit.md"
    install_file "$WORKFLOWS_DIR/sync.md" "$REPO_ROOT/.claude/commands" "sync.md"
    install_file "$WORKFLOWS_DIR/triage.md" "$REPO_ROOT/.claude/commands" "triage.md"
    install_file "$WORKFLOWS_DIR/triage.md" "$REPO_ROOT/.claude/commands" "tr.md"
    install_file "$WORKFLOWS_DIR/review.md" "$REPO_ROOT/.claude/commands" "review.md"
    install_file "$WORKFLOWS_DIR/review.md" "$REPO_ROOT/.claude/commands" "rv.md"
    install_file "$WORKFLOWS_DIR/ship.md" "$REPO_ROOT/.claude/commands" "ship.md"
    install_file "$WORKFLOWS_DIR/ship.md" "$REPO_ROOT/.claude/commands" "sh.md"

    # Gemini project
    install_file "$COMMANDS_DIR/gemini/dev-loop.toml" "$REPO_ROOT/.gemini/workflows" "dev-loop.toml"
    install_file "$COMMANDS_DIR/gemini/goal.toml" "$REPO_ROOT/.gemini/workflows" "goal.toml"
    install_file "$COMMANDS_DIR/gemini/research.toml" "$REPO_ROOT/.gemini/workflows" "research.toml"
    install_file "$COMMANDS_DIR/gemini/websearch.toml" "$REPO_ROOT/.gemini/workflows" "websearch.toml"
    install_file "$COMMANDS_DIR/gemini/audit.toml" "$REPO_ROOT/.gemini/workflows" "audit.toml"
    install_file "$COMMANDS_DIR/gemini/sync.toml" "$REPO_ROOT/.gemini/workflows" "sync.toml"
    install_file "$COMMANDS_DIR/gemini/triage.toml" "$REPO_ROOT/.gemini/workflows" "triage.toml"
    install_file "$COMMANDS_DIR/gemini/review.toml" "$REPO_ROOT/.gemini/workflows" "review.toml"
    install_file "$COMMANDS_DIR/gemini/ship.toml" "$REPO_ROOT/.gemini/workflows" "ship.toml"

    # GitHub Copilot prompts & agents
    install_file "$COMMANDS_DIR/copilot/dev-loop.prompt.md" "$REPO_ROOT/.github/prompts" "dev-loop.prompt.md"
    install_file "$COMMANDS_DIR/copilot/dev-loop.agent.md" "$REPO_ROOT/.github/agents" "dev-loop.agent.md"
    install_file "$COMMANDS_DIR/copilot/goal.prompt.md" "$REPO_ROOT/.github/prompts" "goal.prompt.md"
    install_file "$COMMANDS_DIR/copilot/goal.agent.md" "$REPO_ROOT/.github/agents" "goal.agent.md"
    install_file "$COMMANDS_DIR/copilot/research.prompt.md" "$REPO_ROOT/.github/prompts" "research.prompt.md"
    install_file "$COMMANDS_DIR/copilot/websearch.prompt.md" "$REPO_ROOT/.github/prompts" "websearch.prompt.md"
    install_file "$COMMANDS_DIR/copilot/audit.prompt.md" "$REPO_ROOT/.github/prompts" "audit.prompt.md"
    install_file "$COMMANDS_DIR/copilot/sync.prompt.md" "$REPO_ROOT/.github/prompts" "sync.prompt.md"
    install_file "$COMMANDS_DIR/copilot/triage.prompt.md" "$REPO_ROOT/.github/prompts" "triage.prompt.md"
    install_file "$COMMANDS_DIR/copilot/review.prompt.md" "$REPO_ROOT/.github/prompts" "review.prompt.md"
    install_file "$COMMANDS_DIR/copilot/ship.prompt.md" "$REPO_ROOT/.github/prompts" "ship.prompt.md"

    # Cursor rules (.md and .mdc)
    install_file "$COMMANDS_DIR/cursor/dev-loop.mdc" "$REPO_ROOT/.cursor/rules" "dev-loop.mdc"
    install_file "$COMMANDS_DIR/cursor/dev-loop.md" "$REPO_ROOT/.cursor/rules" "dev-loop.md"
    install_file "$COMMANDS_DIR/cursor/goal.mdc" "$REPO_ROOT/.cursor/rules" "goal.mdc"
    install_file "$COMMANDS_DIR/cursor/goal.md" "$REPO_ROOT/.cursor/rules" "goal.md"
    install_file "$COMMANDS_DIR/cursor/research.mdc" "$REPO_ROOT/.cursor/rules" "research.mdc"
    install_file "$COMMANDS_DIR/cursor/websearch.mdc" "$REPO_ROOT/.cursor/rules" "websearch.mdc"
    install_file "$COMMANDS_DIR/cursor/audit.mdc" "$REPO_ROOT/.cursor/rules" "audit.mdc"
    install_file "$COMMANDS_DIR/cursor/sync.mdc" "$REPO_ROOT/.cursor/rules" "sync.mdc"
    install_file "$COMMANDS_DIR/cursor/triage.mdc" "$REPO_ROOT/.cursor/rules" "triage.mdc"
    install_file "$COMMANDS_DIR/cursor/review.mdc" "$REPO_ROOT/.cursor/rules" "review.mdc"
    install_file "$COMMANDS_DIR/cursor/ship.mdc" "$REPO_ROOT/.cursor/rules" "ship.mdc"

    # Antigravity project
    install_file "$COMMANDS_DIR/antigravity/dev-loop.md" "$REPO_ROOT/.antigravity/workflows" "dev-loop.md"
    install_file "$COMMANDS_DIR/antigravity/goal.md" "$REPO_ROOT/.antigravity/workflows" "goal.md"
    install_file "$COMMANDS_DIR/antigravity/research.md" "$REPO_ROOT/.antigravity/workflows" "research.md"
    install_file "$COMMANDS_DIR/antigravity/websearch.md" "$REPO_ROOT/.antigravity/workflows" "websearch.md"
    install_file "$COMMANDS_DIR/antigravity/audit.md" "$REPO_ROOT/.antigravity/workflows" "audit.md"
    install_file "$COMMANDS_DIR/antigravity/sync.md" "$REPO_ROOT/.antigravity/workflows" "sync.md"
    install_file "$COMMANDS_DIR/antigravity/triage.md" "$REPO_ROOT/.antigravity/workflows" "triage.md"
    install_file "$COMMANDS_DIR/antigravity/review.md" "$REPO_ROOT/.antigravity/workflows" "review.md"
    install_file "$COMMANDS_DIR/antigravity/ship.md" "$REPO_ROOT/.antigravity/workflows" "ship.md"

    # OpenCode project
    install_file "$COMMANDS_DIR/opencode/dev-loop.md" "$REPO_ROOT/.opencode/commands" "dev-loop.md"
    install_file "$COMMANDS_DIR/opencode/goal.md" "$REPO_ROOT/.opencode/commands" "goal.md"
    install_file "$COMMANDS_DIR/opencode/research.md" "$REPO_ROOT/.opencode/commands" "research.md"
    install_file "$COMMANDS_DIR/opencode/websearch.md" "$REPO_ROOT/.opencode/commands" "websearch.md"
    install_file "$COMMANDS_DIR/opencode/audit.md" "$REPO_ROOT/.opencode/commands" "audit.md"
    install_file "$COMMANDS_DIR/opencode/sync.md" "$REPO_ROOT/.opencode/commands" "sync.md"
    install_file "$COMMANDS_DIR/opencode/triage.md" "$REPO_ROOT/.opencode/commands" "triage.md"
    install_file "$COMMANDS_DIR/opencode/review.md" "$REPO_ROOT/.opencode/commands" "review.md"
    install_file "$COMMANDS_DIR/opencode/ship.md" "$REPO_ROOT/.opencode/commands" "ship.md"
fi

echo -e "\033[0;32m==> Complete Dev Loop Suite installed successfully!\033[0m"
