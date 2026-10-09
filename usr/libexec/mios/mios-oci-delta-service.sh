#!/bin/bash
# AI-hint: GAP-5 (task_50) edge distribution wrapper
set -euo pipefail

TOML_PATH="/usr/share/mios/mios.toml"
RECHUNK_ENABLE=$(python3 -c "
try:
    import tomllib
    with open('$TOML_PATH', 'rb') as f:
        cfg = tomllib.load(f)
    print(str(cfg.get('distribution', {}).get('rechunk_enable', False)).lower())
except Exception:
    print('false')
")

if [[ "$RECHUNK_ENABLE" != "true" ]]; then
    echo "Distribution rechunk_enable=false. Skipping edge delta apply"
    exit 0
fi

BUNDLE_URL="${MIOS_DELTA_BUNDLE_URL:-http://localhost:8700/v1/update/delta.tar}"
PUB_KEY_PATH="/etc/mios/ai/v1/keys/mios_audit_pub.pem"
TMP_DIR="/var/tmp/mios-delta-update"
OLD_OCI="/var/tmp/mios-delta-old-oci"
NEW_OCI="/var/tmp/mios-delta-new-oci"

rm -rf "$TMP_DIR" "$OLD_OCI" "$NEW_OCI"
mkdir -p "$TMP_DIR"

echo "Fetching delta bundle from $BUNDLE_URL"
curl -sL --fail "$BUNDLE_URL" -o "$TMP_DIR/delta.tar" || {
    echo "Failed to fetch delta bundle. Exiting"
    exit 1
}

echo "Exporting current OCI layout"
skopeo copy "containers-storage:localhost/mios:latest" "oci:$OLD_OCI" || {
    echo "Could not export old image. Continuing with empty old_dir for bootstrapping"
    mkdir -p "$OLD_OCI"
}

echo "Applying delta"
if [[ -f "$PUB_KEY_PATH" ]]; then
    PUB_KEY_ARG="--key $(cat $PUB_KEY_PATH)"
else
    PUB_KEY_ARG=""
fi

/usr/libexec/mios/mios-oci-delta-apply "$OLD_OCI" "$NEW_OCI" "$TMP_DIR/delta.tar" $PUB_KEY_ARG

echo "Importing reconstructed OCI layout"
skopeo copy "oci:$NEW_OCI" "containers-storage:localhost/mios:edge-update"

echo "Signaling bootc to stage"
bootc switch --transport containers-storage localhost/mios:edge-update

echo "Delta apply complete"
rm -rf "$TMP_DIR" "$OLD_OCI" "$NEW_OCI"
