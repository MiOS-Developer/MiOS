# System-wide MiOS AI CLI convenience aliases
alias zai="zai-cli" 2>/dev/null || true
if ! command -v code >/dev/null 2>&1 && command -v codium >/dev/null 2>&1; then
    alias code="codium" 2>/dev/null || true
fi
