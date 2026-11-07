#!/usr/bin/bash
# AI-hint: Verifies the composefs root: verify-root.sh integrity when ostree-booted, then, when prepare-root.conf requests verity, the composefs mount and fsverity on a core binary. Non-zero makes greenboot roll back or retry.
# AI-related: /usr/libexec/mios/verify-root.sh, mios-composefs, /usr/lib/ostree/prepare-root.conf
set -euo pipefail
# Both halves always run, so a failed integrity check still reports the verity state.
rc=0
if [[ -e /run/ostree-booted ]]; then
    /usr/libexec/mios/verify-root.sh || rc=$?
else
    echo "[greenboot] INFO: not booted under ostree/bootc -- skipping ostree composefs check"
fi
if ! grep -q "enabled = verity" /usr/lib/ostree/prepare-root.conf 2>/dev/null; then
    echo "[greenboot] INFO: composefs verity not requested - skipping deep check"
    exit "$rc"
fi
echo "[greenboot] composefs verity is enabled in configuration"
if ! mount | grep "type composefs" >/dev/null; then
    echo "[greenboot] ERROR: composefs requested but not active"
    exit 1
fi
echo "[greenboot] SUCCESS: root is mounted as composefs"
if command -v composefs-info >/dev/null; then
    if ! fsverity digest /usr/bin/bash >/dev/null 2>&1; then
        echo "[greenboot] FAILURE: fsverity missing on critical binary"
        exit 1
    fi
    echo "[greenboot] SUCCESS: fsverity is active on /usr/bin/bash"
fi
exit "$rc"
