#!/usr/bin/env bash
# AI-hint: bash Composites the MiOS dashboard into /etc/issue.d/30-mios.issue so it AI-related: /usr/libexec/mios/mios-dashboard-render-issu...
# AI-doc: usr/share/doc/mios/manual/mios.md
set -uo pipefail

ISSUE_DIR=/etc/issue.d
ISSUE_FILE="${ISSUE_DIR}/30-mios.issue"
DASHBOARD=/usr/libexec/mios/mios-dashboard

mkdir -p "$ISSUE_DIR" 2>/dev/null || true

if [[ ! -x "$DASHBOARD" ]]; then
    {
        echo ""
        echo "  MiOS"
        echo "  Login to inspect the system state via /etc/profile.d/zz-mios-motd.sh"
        echo ""
    } > "$ISSUE_FILE.new"
    mv -f "$ISSUE_FILE.new" "$ISSUE_FILE"
    chmod 0644 "$ISSUE_FILE"
    exit 0
fi

if TERM=linux timeout -k 3 10 env -i PATH="$PATH" TERM=linux python3 "$DASHBOARD" \
        --no-color --services-only > "$ISSUE_FILE.new" 2>/dev/null \
   && [[ -s "$ISSUE_FILE.new" ]]; then
    chmod 0644 "$ISSUE_FILE.new"
    mv -f "$ISSUE_FILE.new" "$ISSUE_FILE"
else
    rm -f "$ISSUE_FILE.new"
    {
        echo ""
        echo "  MiOS"
        echo "  Login for live system state"
        echo ""
    } > "$ISSUE_FILE.tmp"
    chmod 0644 "$ISSUE_FILE.tmp"
    mv -f "$ISSUE_FILE.tmp" "$ISSUE_FILE"
fi

exit 0
