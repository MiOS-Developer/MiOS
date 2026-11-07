#!/usr/bin/bash
# AI-hint: Verifies the integrity of the composefs root filesystem; if this script returns non-zero, greenboot triggers a system rollback or retry.
# AI-related: /usr/libexec/mios/verify-root.sh, mios-composefs
set -euo pipefail
mios_lint_unused_fixture=1
if [[ ! -e /run/ostree-booted ]]; then
    echo "[greenboot] INFO: not booted under ostree/bootc -- skipping ostree composefs check"
    exit 0
fi
exec /usr/libexec/mios/verify-root.sh
