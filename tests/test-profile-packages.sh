#!/bin/bash
# AI-hint: Verifies package profile filtering, shared section dependency closures, strict retry failures and default toolchain retention without installing or removing host packages.
# AI-related: automation/lib/packages.sh, automation/build.sh, src/mios-rs/mios-build/src/lib.rs, usr/share/mios/mios.toml
# AI-functions: main

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d /tmp/test-profile-packages.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
fails=0
pass() { echo "  [PASS] $*"; }
fail() { echo "  [FAIL] $*"; fails=$((fails + 1)); }

# dnf is stubbed: every call is logged, nothing is installed.
mkdir -p "$TMP/bin"
for b in dnf dnf5; do printf '#!/bin/sh\necho "$*" >> "%s/dnf.log"\nexit "${MIOS_TEST_DNF_RC:-0}"\n' "$TMP" > "$TMP/bin/$b"; chmod +x "$TMP/bin/$b"; done

dependency_checks() {
    local fixture="$TMP/fixture" got
    mkdir -p "$fixture/lib"
    cp "$ROOT/automation/lib/packages.sh" "$fixture/lib/packages.sh"
    cp "$ROOT/automation/91-strip-build-toolchain.sh" "$fixture/91-strip-build-toolchain.sh"
    # Isolated library context: no real package manager or resolver runs.
    cat > "$fixture/lib/common.sh" <<'EOF'
DNF_BIN=dnf5
DNF_SETOPT=()
DNF_OPTS=()
mios_log() { echo "$*"; }
mios_ok() { echo "$*"; }
mios_warn() { echo "$*" >&2; }
mios_err() { echo "$*" >&2; }
EOF
    printf '#!/bin/sh\nexit 0\n' > "$TMP/bin/sleep"
    chmod +x "$TMP/bin/sleep"
    cat > "$fixture/packages.toml" <<'EOF'
[packages.child]
enable = true
pkgs = ["shared", "compiler"]
[packages.parent]
enable = true
requires_sections = ["child"]
pkgs = ["shared", "runtime"]
[packages.self-build]
enable = true
retain_toolchain = true
requires_sections = ["parent"]
pkgs = ["builder"]
EOF
    resolve() (
        source "$fixture/lib/packages.sh"
        export MIOS_TOML="$fixture/packages.toml"
        "$@"
    )
    got="$(resolve get_packages parent)"
    [[ "$got" == "shared compiler runtime " ]] && pass "section dependencies are ordered and deduplicated" || fail "unexpected closure: $got"
    got="$(resolve get_packages_from_toml parent "$fixture/packages.toml")"
    [[ "$got" == "shared compiler runtime " ]] && pass "explicit TOML reader resolves the same dependency closure" || fail "explicit TOML bypassed closure: $got"
    got="$(resolve get_packages optional-absent)"
    [[ -z "$got" ]] && pass "absent optional root retains its empty-reader contract" || fail "absent optional root unexpectedly resolved"
    cp "$fixture/packages.toml" "$fixture/valid.toml"
    sed -i 's/requires_sections = \["child"\]/requires_sections = ["missing-child"]/' "$fixture/packages.toml"
    if resolve get_packages parent > "$TMP/resolved.log" 2> "$TMP/err.log"; then
        fail "missing dependency was accepted"
    else
        [[ ! -s "$TMP/resolved.log" ]] && grep -q 'missing-child' "$TMP/err.log" && pass "missing dependency fails without partial output" || fail "missing dependency was not named"
    fi
    cp "$fixture/valid.toml" "$fixture/packages.toml"
    sed -i '0,/enable = true/s//enable = false/' "$fixture/packages.toml"
    if resolve get_packages parent > "$TMP/resolved.log" 2> "$TMP/err.log"; then
        fail "disabled dependency was accepted"
    else
        grep -q 'requires disabled.*child' "$TMP/err.log" && pass "disabled required section fails explicitly" || fail "disabled dependency failure was not explicit"
    fi
    cp "$fixture/valid.toml" "$fixture/packages.toml"
    sed -i '/\[packages.child\]/a requires_sections = ["parent"]' "$fixture/packages.toml"
    if resolve get_packages parent > "$TMP/resolved.log" 2> "$TMP/err.log"; then
        fail "cyclic dependency was accepted"
    else
        grep -q 'cyclic.*parent.*child.*parent' "$TMP/err.log" && pass "cyclic section dependency fails with the chain" || fail "cycle was not diagnosed"
    fi
    cp "$fixture/valid.toml" "$fixture/packages.toml"
    printf '\nbuild_catalog_authoritative = true\n' >> "$fixture/packages.toml"
    # Match the real global catalog switch; it may occur anywhere in TOML.
    printf '[{"name":"child","pkgs":["catalog-compiler"]},{"name":"parent","pkgs":["catalog-runtime"]}]\n' > "$fixture/package_sets.json"
    got="$(resolve get_packages parent)"
    [[ "$got" == "catalog-compiler catalog-runtime " ]] && pass "authoritative catalog retains declared dependency closure" || fail "catalog bypassed dependencies: $got"
    mv "$fixture/package_sets.json" "$fixture/catalog.saved"
    if resolve get_packages parent > "$TMP/resolved.log" 2> "$TMP/err.log"; then
        fail "missing authoritative catalog silently fell back to TOML"
    else
        [[ ! -s "$TMP/resolved.log" ]] && grep -q 'authoritative package catalog is missing' "$TMP/err.log" && pass "missing authoritative catalog fails without fallback" || fail "missing catalog failure was not explicit"
    fi
    printf '{invalid json\n' > "$fixture/package_sets.json"
    if resolve get_packages parent > "$TMP/resolved.log" 2> "$TMP/err.log"; then
        fail "malformed authoritative catalog silently fell back to TOML"
    else
        [[ ! -s "$TMP/resolved.log" ]] && grep -q 'authoritative package_sets.json' "$TMP/err.log" && pass "malformed authoritative catalog fails without fallback" || fail "malformed catalog failure was not explicit"
    fi
    mv "$fixture/catalog.saved" "$fixture/package_sets.json"
    cp "$fixture/valid.toml" "$fixture/packages.toml"
    : > "$TMP/dnf.log"
    (
        export PATH="$TMP/bin:$PATH" MIOS_TOML="$fixture/packages.toml"
        bash "$fixture/91-strip-build-toolchain.sh"
    ) > "$TMP/out.log" 2>&1
    [[ ! -s "$TMP/dnf.log" ]] && grep -q 'Retaining build dependencies' "$TMP/out.log" && pass "retain_toolchain=true never invokes package removal" || fail "retention removed dependencies"
    sed -i 's/retain_toolchain = true/retain_toolchain = false/' "$fixture/packages.toml"
    # Execute only the real selection code, before destructive removal or
    # symlink cleanup. Even with fake dnf, that cleanup must not touch the host.
    awk '/^for grp in "\$\{BUILD_GROUPS\[@\]\}"; do/ { exit } { print }' "$fixture/91-strip-build-toolchain.sh" > "$fixture/strip-plan.sh"
    printf '\nprintf "GROUP=%%s\\n" "${BUILD_GROUPS[@]}"\n' >> "$fixture/strip-plan.sh"
    (export PATH="$TMP/bin:$PATH" MIOS_TOML="$fixture/packages.toml"; bash "$fixture/strip-plan.sh") > "$TMP/out.log" 2>&1
    if grep -qx 'GROUP=build-toolchain' "$TMP/out.log" && ! grep -qx 'GROUP=self-build' "$TMP/out.log"; then pass "compiler opt-out preserves the self-build runtime group"; else fail "compiler opt-out selected the self-build runtime for removal"; fi
    cp "$fixture/valid.toml" "$fixture/packages.toml"
    : > "$TMP/dnf.log"
    sed -i '/retain_toolchain = true/d' "$fixture/packages.toml"
    if (export PATH="$TMP/bin:$PATH" MIOS_TOML="$fixture/packages.toml"; bash "$fixture/91-strip-build-toolchain.sh") > "$TMP/out.log" 2>&1; then
        fail "missing retention policy was accepted"
    else
        [[ ! -s "$TMP/dnf.log" ]] && pass "missing retention policy refuses destructive removal" || fail "missing policy removed packages"
    fi
    cp "$fixture/valid.toml" "$fixture/packages.toml"
    : > "$TMP/dnf.log"
    if (export PATH="$TMP/bin:$PATH" MIOS_TEST_DNF_RC=42; resolve install_packages_strict parent) > "$TMP/out.log" 2>&1; then
        fail "strict install hid the package manager failure"
    else
        [[ "$(wc -l < "$TMP/dnf.log" | tr -d ' ')" == 3 ]] && pass "strict install retries and propagates the final failure" || fail "strict retry count differs"
    fi
    if grep -Eq 'strict=0|skip-unavailable' "$TMP/dnf.log"; then fail "strict install permits missing dependencies"; else pass "strict install requires every requested package"; fi
    # Verify the actual MiOS closures, including the transitive developer image.
    for group in self-build devcontainer; do
        got="$(resolve get_packages_from_toml "$group" "$ROOT/usr/share/mios/mios.toml")"
        for pkg in rust cargo rustup clippy rustfmt musl-devel musl-gcc musl-libc-static just python3-pytest python3-jsonschema python3-pyflakes; do
            [[ " $got" == *" $pkg "* ]] || fail "$group lacks $pkg"
        done
        pass "$group includes the native compiler, static linker and development-check dependency closure"
    done
    # Run the actual repos-phase install dispatch without repository or host
    # mutations. Core omits both virt and the browser phase that install ai.
    sed -n '/^for _build_section /,$p' "$ROOT/automation/05-repos.sh" > "$fixture/install-selected.sh"
    : > "$TMP/dnf.log"
    (
        source "$fixture/lib/packages.sh"
        export PATH="$TMP/bin:$PATH" MIOS_TOML="$ROOT/usr/share/mios/mios.toml"
        export BUILD_PROFILE_SECTIONS='containers build-toolchain self-build ai utils'
        source "$fixture/install-selected.sh"
    ) > "$TMP/out.log" 2>&1
    if grep -q 'python3-psycopg3' "$TMP/dnf.log" && grep -q 'python3-cryptography' "$TMP/dnf.log" && grep -q 'cargo' "$TMP/dnf.log"; then
        pass "core install dispatch includes direct service and compiler dependencies"
    else fail "core dependency declarations did not reach package installation"; fi
    unset -f resolve
}

native_build_checks() (
    # This shell phase only bootstraps/delegates. Artifact, catalog and atomic
    # install controls execute against the real Rust native_build engine.
    local fixture="$TMP/native-fixture"
    mkdir -p "$fixture/automation" "$fixture/src/mios-rs" "$fixture/output"
    cp "$ROOT/automation/55-native-build.sh" "$fixture/automation/"
    printf '[workspace]\n' > "$fixture/src/mios-rs/Cargo.toml"
    cat > "$TMP/bin/rustc" <<'EOF'
#!/bin/sh
printf 'host: fixture-host\n'
EOF
    cat > "$TMP/bin/cargo" <<'EOF'
#!/bin/bash
set -euo pipefail
printf '%s\n' "$@" > "$MIOS_TEST_CARGO_LOG"
while (( $# )); do
    if [[ "$1" == --target-dir ]]; then out="$2"; shift 2; else shift; fi
done
mkdir -p "$out/fixture-host/release"
cat > "$out/fixture-host/release/miosd" <<'ENGINE'
#!/bin/bash
printf '%s\n' "$@" > "$MIOS_TEST_ENGINE_LOG"
if [[ "${MIOS_TEST_ENGINE_RC:-0}" != 0 ]]; then echo 'planted native engine rejection' >&2; fi
exit "${MIOS_TEST_ENGINE_RC:-0}"
ENGINE
chmod +x "$out/fixture-host/release/miosd"
EOF
    printf '#!/bin/sh\nexit 0\n' > "$TMP/bin/rustup"
    chmod +x "$TMP/bin/cargo" "$TMP/bin/rustc" "$TMP/bin/rustup"
    export PATH="$TMP/bin:$PATH" CARGO_TARGET_DIR="$fixture/output"
    export MIOS_NATIVE_INSTALL_ROOT="$fixture/stage"
    export MIOS_TEST_CARGO_LOG="$TMP/cargo.log" MIOS_TEST_ENGINE_LOG="$TMP/engine.log"
    unset MIOS_NATIVE_DEST_DIR
    bash "$fixture/automation/55-native-build.sh"
    grep -Fxq native-build "$TMP/engine.log"
    grep -Fxq "$fixture/output" "$TMP/engine.log"
    grep -Fxq "$fixture/stage" "$TMP/engine.log"
    grep -Fxq -- --locked "$TMP/cargo.log"
    pass "native phase delegates to the engine with explicit output and FHS install roots"
    if MIOS_TEST_ENGINE_RC=35 bash "$fixture/automation/55-native-build.sh" > "$TMP/native.log" 2>&1; then
        fail "native engine rejection was swallowed"
    elif grep -q 'planted native engine rejection' "$TMP/native.log"; then
        pass "native engine rejection reaches the phase caller"
    else fail "native engine rejection lacked its diagnostic"; fi
    if MIOS_NATIVE_DEST_DIR="$fixture/legacy" bash "$fixture/automation/55-native-build.sh" > "$TMP/native.log" 2>&1; then
        fail "obsolete flat install destination was accepted"
    elif grep -q 'Use MIOS_NATIVE_INSTALL_ROOT' "$TMP/native.log"; then
        pass "obsolete destination fails with the current FHS migration diagnostic"
    else fail "obsolete destination lacked its diagnostic"; fi
)

# $1 = BUILD_PROFILE_SECTIONS (or "unset"), $2 = install function, $3 = section; prints dnf call count.
calls() {
    : > "$TMP/dnf.log"
    (
        export PATH="$TMP/bin:$PATH"
        if [[ "$1" == "unset" ]]; then unset BUILD_PROFILE_SECTIONS; else export BUILD_PROFILE_SECTIONS="$1"; fi
        # shellcheck source=/dev/null
        source "$ROOT/automation/lib/packages.sh" >/dev/null 2>&1
        # Sourcing exports the resolved environment, whose MIOS_TOML is the image path; read this checkout.
        export MIOS_TOML="$ROOT/usr/share/mios/mios.toml"
        "$2" "$3" > "$TMP/out.log" 2>&1
    ) || { echo "rc!=0"; return 0; }
    wc -l < "$TMP/dnf.log" | tr -d ' '
}

health_checks() (
    # Execute the production health function without running image build stages.
    # shellcheck source=/dev/null  # one function excerpted from automation/build.sh at run time
    source <(sed -n '/^_check_critical_packages() {/,/^}/p' "$ROOT/automation/build.sh")
    local catalog='gnome-shell gdm podman bootc libvirt kernel-core firewalld cockpit NetworkManager pipewire tuned chrony openssh-server'
    local catalog_rc=0 missing=''
    get_packages_strict() { printf '%s\n' "$catalog"; return "$catalog_rc"; }
    rpm() {
        printf '%s\n' "$@" >> "$TMP/rpm-health.log"
        [[ $# -eq 2 && "$1" == -q && "$2" != "$missing" ]]
    }
    : > "$TMP/rpm-health.log"
    _check_critical_packages > "$TMP/health.log" || exit 1
    [[ "$PKG_OK" -eq 13 && "$PKG_MISS" -eq 0 && "$VALIDATION_FAIL" -eq 0 ]] || exit 1
    [[ $(grep -c '^\[\|MISS' "$TMP/health.log" || true) -eq 0 ]] || exit 1
    [[ $(wc -l < "$TMP/rpm-health.log") -eq 26 ]] || exit 1
    [[ $(grep -c 'gnome-shell gdm' "$TMP/rpm-health.log" || true) -eq 0 ]] || exit 1
    pass "critical catalog queries thirteen separate RPM names without combined-list false misses"
    missing=kernel-core
    if _check_critical_packages > "$TMP/health.log"; then exit 1; fi
    [[ "$PKG_OK" -eq 12 && "$PKG_MISS" -eq 1 && "$VALIDATION_FAIL" -eq 1 ]] || exit 1
    grep -q 'kernel-core.*\[MISS\]' "$TMP/health.log" || exit 1
    pass "one missing critical package is named and fails health validation"
    # build.sh runs the health gate as a post-build stage: _run_stage records it
    # in FAIL_LOG and in the native progress ledger, whose final receipt is the
    # build's exit. Drive those production functions over a one-stage ledger.
    # shellcheck source=/dev/null  # production functions excerpted at run time
    source <(sed -n -e '/^_post_package_health() {/,/^}/p' -e '/^_finding() {/,/^}/p' \
        -e '/^_run_stage() {/,/^}/p' -e '/^_stage_child() {/,/^}/p' "$ROOT/automation/build.sh")
    for fn in _post_package_health _finding _run_stage _stage_child; do
        declare -F "$fn" >/dev/null || exit 1
    done
    local -A PHASE_FATAL=([package-health]=true)
    local _miosd="$MIOSD" _mios_root="$ROOT" PROGRESS_DIR PROGRESS_STATE SCRIPT_COUNT
    local -a FAIL_LOG WARN_LOG WARNED_JSON
    ledger() {  # one build whose only stage is the health gate; returns the receipt's exit
        PROGRESS_DIR="$(mktemp -d "$TMP/ledger.XXXXXX")"; PROGRESS_STATE="$PROGRESS_DIR/state.json"
        SCRIPT_COUNT=0; FAIL_LOG=(); WARN_LOG=(); WARNED_JSON=()
        printf 'package-health\n' | "$_miosd" build-progress --root "$_mios_root" \
            --state "$PROGRESS_STATE" --event init > "$TMP/stage.log" 2>&1 || exit 1
        _run_stage package-health _post_package_health >> "$TMP/stage.log" 2>&1
        "$_miosd" build-progress --root "$_mios_root" --state "$PROGRESS_STATE" --event finish >> "$TMP/stage.log" 2>&1
    }
    missing=''
    ledger || exit 1
    [[ ${#FAIL_LOG[@]} -eq 0 && $SCRIPT_COUNT -eq 1 && ${#WARN_LOG[@]} -eq 0 && ${#WARNED_JSON[@]} -eq 0 && ${PHASE_FATAL[package-health]} == true ]] || exit 1
    missing=kernel-core
    if ledger; then exit 1; fi
    [[ ${#FAIL_LOG[@]} -eq 1 && "${FAIL_LOG[0]}" == "package-health: exit=1" ]] || exit 1
    grep -q 'Critical package health: 1 missing' "$TMP/stage.log" || exit 1
    pass "actual build aggregation and final exit fail for missing critical packages"
    catalog_rc=1
    : > "$TMP/rpm-health.log"
    if _check_critical_packages > "$TMP/health.log" 2>&1; then exit 1; fi
    [[ "$VALIDATION_FAIL" -eq 1 && ! -s "$TMP/rpm-health.log" ]] || exit 1
    pass "failed package resolution stops before any RPM query"
)

# The build's native registry and progress engine; build.sh refuses to run without it.
resolve_miosd() {
    MIOSD="${MIOS_MIOSD_BIN:-$ROOT/src/mios-rs/target/debug/miosd}"
    if [[ ! -x "$MIOSD" ]]; then
        (cd "$ROOT/src/mios-rs" && cargo build -q -p miosd) || { echo "[test-profile-packages] ERROR: cannot build miosd" >&2; exit 1; }
    fi
}

main() {
    resolve_miosd
    if ! health_checks; then fail "critical package health controls"; fi
    if [[ "${1:-}" == --health-only ]]; then (( fails == 0 )); return; fi
    dependency_checks
    native_build_checks
    if [[ "${1:-}" == --dependency-only ]]; then
        echo "[test-profile-packages] dependency failures: $fails"
        (( fails == 0 ))
        return
    fi
    local miosd="$MIOSD"
    local core
    core="$(MIOS_ROOT="$ROOT" "$miosd" build --sections --profile core | tr '\n' ' ')"
    [[ " $core " == *" utils "* && " $core " != *" gaming "* ]] \
        && pass "miosd build --sections --profile core selects utils, not gaming: $core" \
        || fail "core sections unexpected: $core"
    [[ "$(MIOS_ROOT="$ROOT" "$miosd" build --sections --profile full)" == "*" ]] \
        && pass "the full profile selects every section (*)" || fail "full profile did not print *"

    local n
    n="$(calls "$core" install_packages gaming)"
    [[ "$n" == "0" ]] && grep -q "outside the build profile" "$TMP/out.log" \
        && pass "core: install_packages gaming is skipped, dnf never called" || fail "core: gaming made $n dnf call(s)"
    n="$(calls "$core" install_packages_strict hyprland)"
    [[ "$n" == "0" ]] && pass "core: install_packages_strict hyprland is a skip, not a failure" || fail "core: strict hyprland gave '$n'"
    n="$(calls "$core" install_packages utils)"
    [[ "$n" == "1" ]] && pass "core: install_packages utils installs (1 dnf call)" || fail "core: utils made $n dnf call(s)"
    n="$(calls "*" install_packages gaming)"
    [[ "$n" == "1" ]] && pass "*: install_packages gaming installs" || fail "*: gaming made $n dnf call(s)"
    n="$(calls unset install_packages gaming)"
    [[ "$n" == "1" ]] && pass "no profile set: install_packages gaming installs (today's behaviour)" || fail "unset: gaming made $n dnf call(s)"

    # build.sh itself, truncated before any stage runs: the caller's profile must survive common.sh re-exporting the SSOT env.
    local n_end probe out
    # Truncate at the plan guard: ALL_SCRIPTS is complete there and no stage has run.
    n_end="$(grep -n '^\[\[ \${#ALL_SCRIPTS\[@\]} -gt 0 \]\]' "$ROOT/automation/build.sh" | head -1 | cut -d: -f1 || true)"
    if [[ -z "$n_end" ]]; then
        fail "build.sh no longer guards an empty phase plan, so the profile probe has no truncation point"
        echo "[test-profile-packages] failures: $fails"
        return 1
    fi
    probe="$TMP/build-head.sh"
    head -n "$n_end" "$ROOT/automation/build.sh" \
        | sed "s|^SCRIPT_DIR=.*|SCRIPT_DIR=\"$ROOT/automation\"|" \
        | awk -v t="$ROOT/usr/share/mios/mios.toml" '{print} /^source "\$\{SCRIPT_DIR\}\/lib\/packages.sh"/{print "export MIOS_TOML=\"" t "\""}' > "$probe"
    echo 'echo "PHASES=${#ALL_SCRIPTS[@]} SECTIONS=[${BUILD_PROFILE_SECTIONS:-}]"' >> "$probe"
    out="$(MIOS_MIOSD_BIN="$miosd" MIOS_PROFILES_DEFAULT=core bash "$probe" 2>&1 | grep "^PHASES=" || true)"
    [[ "$out" == *"SECTIONS=[ai base"* && "$out" != *"SECTIONS=[*"* ]] \
        && pass "build.sh honours MIOS_PROFILES_DEFAULT=core: $out" || fail "build.sh ignored the requested profile: ${out:-no output}"
    out="$(MIOS_MIOSD_BIN="$miosd" MIOS_PROFILES_DEFAULT=zz-planted-profile bash "$probe" 2>&1 || true)"
    [[ "$out" == *"FATAL"*"zz-planted-profile"* ]] \
        && pass "build.sh refuses an undeclared profile, naming it" || fail "build.sh accepted an undeclared profile"

    echo "[test-profile-packages] failures: $fails"
    (( fails == 0 ))
}

main "$@"
