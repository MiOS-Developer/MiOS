#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Configures and enables systemd units for NVIDIA CDI (Container Device Interface) auto-refresh, removes legacy...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do
    if [[ -r "$_mlog" ]]; then
        # shellcheck source=usr/lib/mios/log.sh
        . "$_mlog" && break
    fi
done
# shellcheck source=automation/lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
# shellcheck source=automation/lib/packages.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/packages.sh"

if ! _in_build_profile "gpu-nvidia-runtime" || ! _is_section_enabled "gpu-nvidia-runtime"; then
    mios_skip "NVIDIA refresh is outside the selected runtime package profile"
    exit 0
fi
for _unit in nvidia-cdi-refresh.path nvidia-cdi-refresh.service; do
    [[ -f "/usr/lib/systemd/system/$_unit" ]] ||
        die "Required official NVIDIA runtime unit is missing: $_unit"
done

OCI_HOOK=/usr/share/containers/oci/hooks.d/oci-nvidia-hook.json
if [[ -f "$OCI_HOOK" ]]; then
    mios_log "Removing legacy OCI nvidia hook"
    rm -f "$OCI_HOOK"
fi

WANTS=/usr/lib/systemd/system/multi-user.target.wants
install -d -m 0755 "${WANTS}"

mios_log "Symlinking nvidia-cdi-refresh.path, nvidia-cdi-refresh.service, nvidia-persistenced.service into multi-user.target.wants"
for unit in \
    nvidia-cdi-refresh.path \
    nvidia-cdi-refresh.service \
    nvidia-persistenced.service
do
    if [[ -f "/usr/lib/systemd/system/${unit}" ]]; then
        ln -sf "../${unit}" "${WANTS}/${unit}"
        mios_ok "Enabled ${unit}"
    else
        mios_warn "${unit} not found, skipping enablement"
    fi
done

mios_ok "CDI refresh pipeline configured"
