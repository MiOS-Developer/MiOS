# AI-hint: The Justfile defines the primary build, deployment, and lifecycle automation for MiOS, providing targets for preflight checks, overlay initialization, ISO creation, and OCI image building via podman.
# AI-related: ./tools/lib/userenv.sh, /usr/libexec/mios/flight-control.sh, /usr/share/mios/build-logs/latest-build.log, /etc/mios/install.env, /etc/mios/forge/admin-password, /usr/lib/mios/agent-pipe, /usr/lib/mios/agents/.venv/bin/python3, /usr/libexec/mios/user-setup.sh, mios-overlay, mios-qcow2-XXXXXX

_load_env := `bash -c 'source ./tools/lib/userenv.sh 2>/dev/null || true'`
_agreement_banner := `bash -c '
case "${MIOS_AGREEMENT_BANNER:-}" in
    quiet|silent|off|0|false|FALSE) ;;
    *)
        cat >&2 <<__EOF__
[mios] just build target invoked. AGREEMENTS.md acknowledged
       (Apache-2.0 + LICENSES.md + CREDITS.md). Research project
       (pronounced MyOS; generative, seed-script-derived).
__EOF__
        ;;
esac
true'`

MIOS_REGISTRY_DEFAULT := "ghcr.io/mios-dev/mios" # @verb:GET_REGISTRY
IMAGE_NAME := env_var_or_default("MIOS_IMAGE_NAME", MIOS_REGISTRY_DEFAULT) # @verb:GET_IMAGE
MIOS_VAR_VERSION := "v0.3.0" # @verb:GET_VERSION
VERSION := `cat VERSION 2>/dev/null || echo {{MIOS_VAR_VERSION}}`
LOCAL := env_var_or_default("MIOS_LOCAL_TAG", "localhost/mios:latest") # @verb:SET_LOCAL
MIOS_IMG_BIB := "quay.io/centos-bootc/bootc-image-builder:latest" # @verb:GET_BIB
BIB := env_var_or_default("MIOS_BIB_IMAGE", MIOS_IMG_BIB)
# miosd by ABSOLUTE path, never `command -v` (task_962): this tree's build, else the installed one.
MIOSD := `for c in "${MIOS_MIOSD_BIN:-}" ./src/mios-rs/target/release/miosd ./src/mios-rs/target/debug/miosd /usr/libexec/mios/miosd; do [ -n "$c" ] && [ -x "$c" ] && { printf '%s' "$c"; exit 0; }; done; printf '%s' miosd-is-not-built`

# Resolved by ABSOLUTE path, never `command -v` (task_962).
preflight:
    @bin=""; for c in "${MIOS_PROBE_BIN:-}" \
        ./src/mios-rs/target/release/mios-probe \
        ./src/mios-rs/target/debug/mios-probe \
        /usr/libexec/mios/mios-probe; do \
        [ -n "$c" ] && [ -x "$c" ] && { bin="$c"; break; }; \
    done; \
    if [ -z "$bin" ]; then \
        if command -v cargo >/dev/null 2>&1 && [ -f "./src/mios-rs/Cargo.toml" ]; then \
            echo "[preflight] mios-probe not found; attempting on-demand build via cargo..." >&2; \
            (cd ./src/mios-rs && cargo build --release -p mios-probe >&2) || \
            (cd ./src/mios-rs && cargo build -p mios-probe >&2) || true; \
            for c in ./src/mios-rs/target/release/mios-probe ./src/mios-rs/target/debug/mios-probe; do \
                [ -x "$c" ] && { bin="$c"; break; }; \
            done; \
        fi; \
    fi; \
    if [ -z "$bin" ]; then \
        if [ "${MIOS_BOOTSTRAP:-0}" = "1" ] || [ "${BOOTSTRAP:-0}" = "1" ] || [ "${MIOS_PREFLIGHT_FALLBACK:-0}" = "1" ] || [ -f "/etc/mios/install.env" ]; then \
            echo "[preflight] WARNING: mios-probe is not available; continuing in fallback mode" >&2; \
        else \
            echo "[preflight] mios-probe is not built -- run: cd src/mios-rs && cargo build --release -p mios-probe" >&2; \
            exit 2; \
        fi; \
    else \
        "$bin" build --root .; \
    fi

check-build-urls:
    @./tools/check-build-urls.sh

audit-provisioning:
    @python3 ./tools/audit-image-provisioning.py

flight-status:
    @bash ./usr/libexec/mios/flight-control.sh || true   # canonical path (was ./tools/, missing -> broke build/iso); status display is non-fatal

init:
    sudo ./tools/mios-overlay.sh

deploy:
    sudo ./tools/mios-overlay.sh

live-init:
    sudo ./tools/mios-overlay.sh

lint:
    podman run --rm --entrypoint /usr/bin/bootc {{LOCAL}} container lint

lint-shell:
    @echo "[lint-shell] Running shellcheck"
    bash ./automation/lint-shell.sh

drift-parity:
    @echo "[drift-parity] Running differential parity harness"
    bash ./tests/drift-parity.sh

ps-gate:
    @echo "[ps-gate] Running PowerShell AST parse, PSScriptAnalyzer, Pester, and Signature gates"
    bash ./automation/lint-powershell.sh
    bash ./automation/lint-ps-analyzer.sh
    bash ./tests/powershell/run-pester.sh

# Regenerate every SSOT projection in dependency order. Run this before
# committing any change under automation/ or tools/ -- several drift gates
# compare a committed artefact against a fresh render, and the AI manifests
# embed file CONTENT, so a stale one turns the gate red for the wrong reason.
sync:
    mios-gen sync --root .

# Run the CI/CD pipeline automation suite (01-ingest, 02-distill, 03-build, 04-deploy, 05-run-agy-pipeline-agent).
cicd:
    @echo "[cicd] Executing full MiOS CI/CD pipeline automation suite..."
    bash ./automation/cicd/01-ingest-daily-telemetry.sh
    python3 ./automation/cicd/02-distill-agent-weights.py
    bash ./automation/cicd/03-build-bootc-oci.sh
    bash ./automation/cicd/04-deploy-atomic-switch.sh
    bash ./automation/cicd/05-run-agy-pipeline-agent.sh

drift-gate:
    @echo "[drift-gate] 97-ssot-lint.sh"
    bash ./automation/97-ssot-lint.sh
    @echo "[drift-gate] lint-shell"
    bash ./automation/lint-shell.sh
    @echo "[drift-gate] agent-pipe unit tests"
    @cd ./usr/lib/mios/agent-pipe && fails=0; \
        py_exec="python3"; [ -x /usr/lib/mios/agents/.venv/bin/python3 ] && py_exec="/usr/lib/mios/agents/.venv/bin/python3"; \
        for t in test_mios_*.py; do \
            if "$py_exec" "$t" >/dev/null 2>&1; then echo "  [ OK ] $t"; \
            else echo "  [FAIL] $t"; fails=$((fails + 1)); fi; \
        done; \
        if [ "$fails" -gt 0 ]; then echo "[drift-gate] $fails test script failed" >&2; exit 1; fi; \
        echo "[drift-gate] all agent-pipe unit tests passed"
    @echo "[drift-gate] libexec unit tests"
    @cd ./usr/libexec/mios && fails=0; \
        py_exec="python3"; [ -x /usr/lib/mios/agents/.venv/bin/python3 ] && py_exec="/usr/lib/mios/agents/.venv/bin/python3"; \
        for t in test_mios_*.py; do \
            if "$py_exec" "$t" >/dev/null 2>&1; then echo "  [ OK ] $t"; \
            else echo "  [FAIL] $t"; fails=$((fails + 1)); fi; \
        done; \
        if [ "$fails" -gt 0 ]; then echo "[drift-gate] $fails libexec test(s) failed" >&2; exit 1; fi
    @echo "[drift-gate] native golden-master (systemd unit snapshots)"
    @# A new unit file with no golden snapshot only failed in CI, six minutes in.
    @if command -v cargo >/dev/null 2>&1; then \
        cd ./tools/native && cargo test -p mios-unit-gen --test projection -q; \
    elif [ "${MIOS_DRIFT_REQUIRE_TOOLS:-0}" = "1" ]; then \
        echo "[drift-gate] cargo absent and MIOS_DRIFT_REQUIRE_TOOLS=1" >&2; exit 1; \
    else \
        echo "  SKIP: cargo absent -- a unit without a golden snapshot will only fail in CI"; \
    fi
    @echo "[drift-gate] tools/ sibling unit tests"
    @cd ./tools && fails=0; \
        py_exec="python3"; [ -x /usr/lib/mios/agents/.venv/bin/python3 ] && py_exec="/usr/lib/mios/agents/.venv/bin/python3"; \
        for t in test_*.py; do \
            if "$py_exec" "$t" >/dev/null 2>&1; then echo "  [ OK ] $t"; \
            else echo "  [FAIL] $t"; fails=$((fails + 1)); fi; \
        done; \
        if [ "$fails" -gt 0 ]; then echo "[drift-gate] $fails tools test(s) failed" >&2; exit 1; fi
    @echo "[drift-gate] 98-drift-checks.sh"
    bash ./automation/98-drift-checks.sh
    @echo "[drift-gate] tests/drift-gate-negatives.sh"
    bash ./tests/drift-gate-negatives.sh
    @echo "[drift-gate] tests/drift-gate-readonly.sh"
    bash ./tests/drift-gate-readonly.sh
    @echo "[drift-gate] tests/test-bake-group.sh"
    bash ./tests/test-bake-group.sh
    @echo "[drift-gate] tests/test-firstboot-prestage.sh"
    bash ./tests/test-firstboot-prestage.sh
    @echo "[drift-gate] tests/test-pgvector-major-upgrade.sh"
    bash ./tests/test-pgvector-major-upgrade.sh
    @echo "[drift-gate] tests/test-powershell-flatten.sh"
    bash ./tests/test-powershell-flatten.sh
    @echo "[drift-gate] tests/test-sandbox-seccomp.sh"
    bash ./tests/test-sandbox-seccomp.sh
    @echo "[drift-gate] usr/lib/mios/test_mios_comments.py"
    python3 ./usr/lib/mios/test_mios_comments.py
    @echo "[drift-gate] tests/test-mios-manual-harvest.sh"
    bash ./tests/test-mios-manual-harvest.sh
    @echo "[drift-gate] tests/doc-production-evidence.sh"
    bash ./tests/doc-production-evidence.sh
    @echo "[drift-gate] automation/lint-python.sh"
    bash ./automation/lint-python.sh
    @echo "[drift-gate] tests/test-lint-coverage.sh"
    bash ./tests/test-lint-coverage.sh
    @echo "[drift-gate] tests/test-theme-merge.py"
    @py_exec="python3"; [ -x /usr/lib/mios/agents/.venv/bin/python3 ] && py_exec="/usr/lib/mios/agents/.venv/bin/python3"; \
        "$py_exec" ./tests/test-theme-merge.py
    @echo "[drift-gate] tests/test-owui-pipe-endpoints.py"
    @py_exec="python3"; [ -x /usr/lib/mios/agents/.venv/bin/python3 ] && py_exec="/usr/lib/mios/agents/.venv/bin/python3"; \
        "$py_exec" ./tests/test-owui-pipe-endpoints.py



# The operator credential is resolved on the BUILD HOST from its layered SSOT (this tree's vendor
# usr/share/mios/mios.toml < /etc/mios/mios.toml + mios.d < ~/.config/mios/mios.toml + mios.d) and
# passed as a sha512-crypt hash; an unresolved credential fails the build (no literal default).
build: preflight flight-status
    _MIOS_CRED="python3 ./usr/libexec/mios/mios-credential"; _MIOS_VENDOR="{{justfile_directory()}}/usr/share/mios/mios.toml"; \
    MIOS_USER_PASSWORD_HASH="${MIOS_USER_PASSWORD_HASH:-$(MIOS_VENDOR_TOML="$_MIOS_VENDOR" $_MIOS_CRED hash)}" || { echo "[FAIL] SSOT credential unresolved on the build host (vendor < /etc/mios < ~/.config/mios); see the mios-credential error above" >&2; exit 3; }; \
    MIOS_IDENTITY_USERNAME="${MIOS_IDENTITY_USERNAME:-$(MIOS_VENDOR_TOML="$_MIOS_VENDOR" $_MIOS_CRED username)}" || { echo "[FAIL] SSOT [identity].username unresolved on the build host" >&2; exit 3; }; \
    rm -rf .staged-portal && mkdir -p .staged-portal; \
    _portal_run="$(find /var/lib/mios-build/artifacts/mios-portal -mindepth 1 -maxdepth 2 -name manifest.json -printf '%h\n' 2>/dev/null | sort | tail -n1 || true)"; \
    if [ -n "$_portal_run" ] && [ -d "$_portal_run" ]; then \
        cp -a "$_portal_run"/* .staged-portal/; \
    fi; \
    export MIOS_USER_PASSWORD_HASH; \
    podman build --retry 5 --retry-delay 3s --no-cache --network=host \
        --secret id=mios_user_password_hash,env=MIOS_USER_PASSWORD_HASH \
        --build-arg BASE_IMAGE="${MIOS_BASE_IMAGE:-$(usr/libexec/mios/mios-toml-get image base)}" \
        --build-arg MIOS_DESKTOP_FLATPAKS={{env_var_or_default("MIOS_DESKTOP_FLATPAKS", "")}} \
        --build-arg MIOS_IDENTITY_USERNAME="$MIOS_IDENTITY_USERNAME" \
        --build-arg MIOS_IDENTITY_HOSTNAME={{env_var_or_default("MIOS_IDENTITY_HOSTNAME", "")}} \
        -t {{LOCAL}} .; _rc=$?; rm -rf .staged-portal; exit $_rc
    @echo "[OK] Built: {{LOCAL}}"

build-logged: artifact
    @mkdir -p logs
    @LOG_FILE="logs/build-$(date -u +%Y%m%dT%H%M%SZ).log"
    @echo "" | tee -a "${LOG_FILE}"
    @echo "[START] CHECKPOINT: Starting 'MiOS' build" | tee -a "${LOG_FILE}"
    @echo "Unified log will be available at: ${LOG_FILE}" | tee -a "${LOG_FILE}"
    @echo "" | tee -a "${LOG_FILE}"
    @set -o pipefail; _MIOS_CRED="python3 ./usr/libexec/mios/mios-credential"; _MIOS_VENDOR="{{justfile_directory()}}/usr/share/mios/mios.toml"; \
    MIOS_USER_PASSWORD_HASH="${MIOS_USER_PASSWORD_HASH:-$(MIOS_VENDOR_TOML="$_MIOS_VENDOR" $_MIOS_CRED hash)}" || { echo "[FAIL] SSOT credential unresolved on the build host (vendor < /etc/mios < ~/.config/mios); see the mios-credential error above" >&2; exit 3; }; \
    MIOS_IDENTITY_USERNAME="${MIOS_IDENTITY_USERNAME:-$(MIOS_VENDOR_TOML="$_MIOS_VENDOR" $_MIOS_CRED username)}" || { echo "[FAIL] SSOT [identity].username unresolved on the build host" >&2; exit 3; }; \
    rm -rf .staged-portal && mkdir -p .staged-portal; \
    _portal_run="$(find /var/lib/mios-build/artifacts/mios-portal -mindepth 1 -maxdepth 2 -name manifest.json -printf '%h\n' 2>/dev/null | sort | tail -n1 || true)"; \
    if [ -n "$_portal_run" ] && [ -d "$_portal_run" ]; then \
        cp -a "$_portal_run"/* .staged-portal/; \
    fi; \
    export MIOS_USER_PASSWORD_HASH; \
    podman build --retry 5 --retry-delay 3s --no-cache --network=host \
        --secret id=mios_user_password_hash,env=MIOS_USER_PASSWORD_HASH \
        --build-arg BASE_IMAGE="${MIOS_BASE_IMAGE:-$(usr/libexec/mios/mios-toml-get image base)}" \
        --build-arg MIOS_DESKTOP_FLATPAKS={{env_var_or_default("MIOS_DESKTOP_FLATPAKS", "")}} \
        --build-arg MIOS_IDENTITY_USERNAME="$MIOS_IDENTITY_USERNAME" \
        --build-arg MIOS_IDENTITY_HOSTNAME={{env_var_or_default("MIOS_IDENTITY_HOSTNAME", "")}} \
        -t {{LOCAL}} . 2>&1 | tee -a "${LOG_FILE}"; _rc=${PIPESTATUS[0]}; rm -rf .staged-portal; [ $_rc -eq 0 ] || exit $_rc
    @echo "" | tee -a "${LOG_FILE}"
    @echo "[OK] CHECKPOINT: 'MiOS' build complete" | tee -a "${LOG_FILE}"
    @echo "Unified log available at: ${LOG_FILE}" | tee -a "${LOG_FILE}"
    @echo ""

build-verbose: artifact
    _MIOS_CRED="python3 ./usr/libexec/mios/mios-credential"; _MIOS_VENDOR="{{justfile_directory()}}/usr/share/mios/mios.toml"; \
    MIOS_USER_PASSWORD_HASH="${MIOS_USER_PASSWORD_HASH:-$(MIOS_VENDOR_TOML="$_MIOS_VENDOR" $_MIOS_CRED hash)}" || { echo "[FAIL] SSOT credential unresolved on the build host (vendor < /etc/mios < ~/.config/mios); see the mios-credential error above" >&2; exit 3; }; \
    MIOS_IDENTITY_USERNAME="${MIOS_IDENTITY_USERNAME:-$(MIOS_VENDOR_TOML="$_MIOS_VENDOR" $_MIOS_CRED username)}" || { echo "[FAIL] SSOT [identity].username unresolved on the build host" >&2; exit 3; }; \
    rm -rf .staged-portal && mkdir -p .staged-portal; \
    _portal_run="$(find /var/lib/mios-build/artifacts/mios-portal -mindepth 1 -maxdepth 2 -name manifest.json -printf '%h\n' 2>/dev/null | sort | tail -n1 || true)"; \
    if [ -n "$_portal_run" ] && [ -d "$_portal_run" ]; then \
        cp -a "$_portal_run"/* .staged-portal/; \
    fi; \
    export MIOS_USER_PASSWORD_HASH; \
    podman build --retry 5 --retry-delay 3s --no-cache --network=host \
        --secret id=mios_user_password_hash,env=MIOS_USER_PASSWORD_HASH \
        --build-arg BASE_IMAGE="${MIOS_BASE_IMAGE:-$(usr/libexec/mios/mios-toml-get image base)}" \
        --build-arg MIOS_DESKTOP_FLATPAKS={{env_var_or_default("MIOS_DESKTOP_FLATPAKS", "")}} \
        --build-arg MIOS_IDENTITY_USERNAME="$MIOS_IDENTITY_USERNAME" \
        --build-arg MIOS_IDENTITY_HOSTNAME={{env_var_or_default("MIOS_IDENTITY_HOSTNAME", "")}} \
        -t {{LOCAL}} .; _rc=$?; rm -rf .staged-portal; exit $_rc

embed-log:
    @echo "[START] Finding most recent build log"
    @LOG_FILE=$$(ls -t logs/build-*.log 2>/dev/null | head -n 1)
    @if [ -z "$${LOG_FILE}" ]; then \
        echo "[FAIL] No build logs found in logs/. Run 'just build-logged' first"; \
        exit 1; \
    fi
    @echo "  Found: $${LOG_FILE}"
    @echo "[START] Creating temporary Containerfile to embed log"
    @echo "FROM {{LOCAL}}" > /tmp/Containerfile.embed
    @echo "COPY" >> /tmp/Containerfile.embed
    @echo "[START] Building image with embedded log"
    @set -o pipefail; podman build --no-cache -f /tmp/Containerfile.embed -t localhost/mios:latest-with-log .
    @rm /tmp/Containerfile.embed
    @echo ""
    @echo "[OK] Success! New image created: localhost/mios:latest-with-log"
    @echo "   Embedded log is at: /usr/share/mios/build-logs/latest-build.log"
    @echo ""

artifact:
    ./automation/ai-bootstrap.sh
    @echo "[OK] Artifacts, UKB, and Wiki refreshed"

# MiOS-Portal Gecko build, off-image (Law 12): heavy.lock + systemd-run scope (MemoryMax, no swap)
# + podman caps, all from [desktop.portal.builder]. Output: <artifact_root>/<ts>/ with the
# tarball, manifest.json (resolved Firefox/Surfer/revision/toolchain), metrics.json and logs.
# Pass extra flags through, e.g. `just portal-build --lane full` or `--no-smoke`.
portal-build *ARGS:
    ./usr/libexec/mios/mios-portal-build build {{ARGS}}

# Install a MiOS-Portal artifact on THIS host exactly as the image does: phase 67 proves the run's
# stage equals the current src/mios-portal (verify-stage), lays the tarball and the run's own
# install-overlay into [desktop.portal.gecko].install_dir root-owned, and proves the result equals
# the artifact (verify-install --root-owned). `just portal-install <artifact_root>/<ts>`; no argument
# = the newest run (which must still pass every check).
portal-install RUN="":
    sudo env {{ if RUN != "" { "MIOS_PORTAL_ARTIFACT_DIR=" + RUN } else { "" } }} bash ./automation/67-bake-portal.sh

# `manual` is gone on purpose: tools/generate-manual.py OWNED the whole file, so
# running it dropped manual.md's H1, its table of contents and now the MIOS-GEN
# markers too. The manual is authored prose plus derived marker interiors --
# `mios-manual render` (below, and in drift-gate) is the only writer.
manual:
    @echo "[manual] retired -- run: python3 ./usr/libexec/mios/mios-manual render"
    @exit 1


cloud-build:
    @echo "Configure cloud-build with your cloud provider CLI"
    @echo "Example: podman build"
    @echo "[OK] Cloud Build target"

rechunk: build
    podman run --rm \
        --security-opt label=type:unconfined_t \
        -v /var/lib/containers/storage:/var/lib/containers/storage \
        {{LOCAL}} \
        /usr/libexec/bootc-base-imagectl rechunk --max-layers 67 containers-storage:{{LOCAL}} containers-storage:{{IMAGE_NAME}}:{{VERSION}}
    podman tag {{IMAGE_NAME}}:{{VERSION}} {{IMAGE_NAME}}:latest
    @echo "[OK] Rechunked: {{IMAGE_NAME}}:{{VERSION}}"

raw: build
    mkdir -p build/raw
    sudo podman run --rm -it --privileged \
        --security-opt label=type:unconfined_t \
        -v ./build/raw:/output \
        -v /var/lib/containers/storage:/var/lib/containers/storage \
        -v ./config/artifacts/bib.toml:/config.toml:ro \
        {{BIB}} build --type raw --rootfs ext4 {{LOCAL}}
    @echo "[OK] RAW image in build/raw"

# iso, qcow2, vhdx: `miosd artifact-build <format>` renders the builder config
# from the SSOT and the credential [deploy.identity] names, and refuses without
# one. `sudo -E` keeps those variables for rootful podman.
iso: build
    sudo -E {{MIOSD}} artifact-build iso --root . --image {{LOCAL}}

qcow2: build
    sudo -E {{MIOSD}} artifact-build qcow2 --root . --image {{LOCAL}}

# Hyper-V: the builder's VHD converted to a dynamic VHDX, for the VM
# [deploy.formats.vhdx.vm] declares. Boot-test it with
# `miosd artifact-boot-test vhdx --disk build/vhdx/disk.vhdx --identity <key>`.
vhdx: build
    sudo -E {{MIOSD}} artifact-build vhdx --root . --image {{LOCAL}}

wsl2: build
    @mkdir -p build/wsl2
    @echo "[wsl2] BIB has no"
    -sudo podman rm -f mios-wsl2-export 2>/dev/null
    sudo podman create --name mios-wsl2-export {{LOCAL}}
    sudo podman export mios-wsl2-export | gzip -c > build/wsl2/mios-rootfs.tar.gz
    sudo podman rm -f mios-wsl2-export
    @echo "[OK] WSL2 rootfs -> build/wsl2/mios-rootfs.tar.gz"
    @echo "     import: wsl"

oci-archive: build
    @mkdir -p build/oci-archive
    podman save --format oci-archive -o build/oci-archive/mios-{{VERSION}}.tar {{LOCAL}}
    @echo "[OK] OCI archive: build/oci-archive/mios-{{VERSION}}.tar"

all: build oci-archive raw iso usb-installer qcow2 vhdx wsl2
    @echo ""
    @echo "[OK] All MiOS deployable artifacts built. Output:"
    @ls -lah build/ 2>/dev/null || true
    @echo ""
    @echo "[NEXT] Run 'just verify-images' to confirm artifact integrity"

usb-installer: iso
    @mkdir -p build/usb-installer
    @isos=$$(ls build/iso/*.iso build/iso/bootiso/*.iso build/*.iso build/bootiso/*.iso 2>/dev/null); \
    if [ -n "$$isos" ]; then \
        for src in $$isos; do \
            [ -f "$$src" ] || continue; \
            base=$$(basename "$$src" .iso); \
            dst="build/usb-installer/$${base}-usb.iso"; \
            [ -f "$$dst" ] || cp -p "$$src" "$$dst"; \
            sz=$$(stat -c%s "$$dst" 2>/dev/null || stat -f%z "$$dst"); \
            echo "[OK] USB installer: $$dst"; \
        done; \
    else \
        echo "[FAIL] no .iso in build/ or build/bootiso/ — run 'just iso' first"; exit 1; \
    fi
    @echo ""
    @echo "Flash to USB:"
    @echo "  Linux:    sudo dd if=build/usb-installer/*.iso of=/dev/sdX bs=4M status=progress conv=fdatasync"
    @echo "  macOS:    sudo dd if=build/usb-installer/*.iso of=/dev/rdiskN bs=4m"
    @echo "  Windows:  use Rufus or balenaEtcher"
    @echo ""
    @echo "WARNING: dd will destroy ALL data on the target device — verify the device first"

verify-images:
    @python3 ./tools/verify-images.py

publish: all verify-images
    @echo "[publish] Pushing OCI image"
    podman push {{LOCAL}} {{IMAGE_NAME}}:{{VERSION}}
    podman push {{LOCAL}} {{IMAGE_NAME}}:latest
    @echo "[publish] OCI image pushed: {{IMAGE_NAME}}:{{VERSION}}"
    @echo ""
    @echo "[publish] Disk images stay in build/"
    @echo "[publish] upload is operator-driven via 'gh release create' or the"
    @echo "[publish] Forgejo web UI"
    @ls -1 build/ 2>/dev/null


log-bootstrap:
    @echo "[START] Logging artifacts to MiOS-bootstrap repository"
    ./tools/log-to-bootstrap.sh
    @echo "[OK] Artifacts logged to bootstrap repository"

build-and-log: build-logged
    @echo "[START] Running bootstrap artifact logging"
    ./tools/log-to-bootstrap.sh
    @echo "[OK] Build complete with artifacts logged to bootstrap"

all-bootstrap: build rechunk log-bootstrap
    @echo "[OK] Full pipeline complete"

sbom:
    @echo "[START] Generating SBOM for {{LOCAL}}"
    @mkdir -p artifacts/sbom
    podman run --rm \
        -v ./artifacts/sbom:/out \
        -v /var/lib/containers/storage:/var/lib/containers/storage \
        anchore/syft:latest scan {{LOCAL}} -o cyclonedx-json > artifacts/sbom/mios-sbom.json
    @echo "[OK] SBOM generated: artifacts/sbom/mios-sbom.json"


init-user-space:
    @./usr/libexec/mios/user-setup.sh

reinit-user-space:
    @./usr/libexec/mios/user-setup.sh --force

show-user-space:
    @echo "'MiOS' User-Space Directories:"
    @echo "  Config:  ${XDG_CONFIG_HOME:-$HOME/.config}/mios/"
    @echo "  Data:    ${XDG_DATA_HOME:-$HOME/.local/share}/mios/"
    @echo "  Cache:   ${XDG_CACHE_HOME:-$HOME/.cache}/mios/"
    @echo "  State:   ${XDG_STATE_HOME:-$HOME/.local/state}/mios/"
    @echo "  Runtime: ${XDG_RUNTIME_DIR:-/run/user/$}/mios/"
    @echo ""
    @echo "Configuration:"
    @if [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/mios/mios.toml" ]; then \
        echo "  [OK] mios.toml"; \
    else \
        echo "  [FAIL] mios.toml"; \
    fi
    @for f in env.toml images.toml build.toml flatpaks.list; do \
        if [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/mios/$f" ]; then \
            echo "  [legacy] $f"; \
        fi; \
    done

show-env:
    @echo "'MiOS' Environment Variables:"
    @source ./tools/lib/userenv.sh && env | grep '^MIOS_' | sort | sed 's/^/  /'

edit:
    @CFG="${XDG_CONFIG_HOME:-$HOME/.config}/mios/mios.toml"; \
        if [ ! -f "$CFG" ]; then \
            echo "[FAIL] $CFG not found. Run: just init"; exit 1; \
        fi; \
        ${EDITOR:-vim} "$CFG"

forge:
    @echo "'MiOS' Forge"
    @if systemctl is-active --quiet mios-forge.service 2>/dev/null; then \
        echo "  Service:        active"; \
    else \
        echo "  Service:        inactive"; \
    fi
    @if systemctl is-active --quiet mios-forge-firstboot.service 2>/dev/null \
        || [ -f /var/lib/mios/forge/.firstboot-done ]; then \
        echo "  First-boot:     [ok] admin user created"; \
    else \
        echo "  First-boot:     pending"; \
    fi
    @echo "  Web UI:         http://localhost:${MIOS_PORTS_FORGE_HTTP:-3000}/"
    @echo "  git+ssh:        ssh://git@localhost:${MIOS_PORTS_FORGE_SSH:-2222}/<user>/<repo>.git"
    @echo "  Admin user:     $(grep -E '^MIOS_FORGE_ADMIN_USER=' /etc/mios/install.env 2>/dev/null | cut -d= -f2- | tr -d '\"' || echo '(check /etc/mios/install.env)')"
    @echo "  Admin email:    $(grep -E '^MIOS_FORGE_ADMIN_EMAIL=' /etc/mios/install.env 2>/dev/null | cut -d= -f2- | tr -d '\"' || echo '(check /etc/mios/install.env)')"
    @if [ -r /etc/mios/forge/admin-password ]; then \
        echo "  Initial pwd:    sudo cat /etc/mios/forge/admin-password"; \
    else \
        echo "  Initial pwd:"; \
    fi
    @echo "  Local push:     git remote add origin http://localhost:${MIOS_PORTS_FORGE_HTTP:-3000}/<user>/<repo>.git && git push origin main"

rechunk-conv: build
    @bash automation/build/rechunk.sh


new type name:
    python3 usr/libexec/mios/mios-new {{type}} {{name}}

vendored-size:
    @echo "[vendored-size] Reporting total size of usr/share/mios/vendored/"
    @du -sh usr/share/mios/vendored/* 2>/dev/null || du -sh usr/share/mios/vendored 2>/dev/null || echo "Vendored dir clean"

clean:
    rm -rf .staged-portal

