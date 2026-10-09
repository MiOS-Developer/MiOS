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

native_build_checks() {
    local fixture="$TMP/native-fixture"
    mkdir -p "$fixture/automation" "$fixture/tools/native" "$fixture/src/mios-rs" "$fixture/out"
    cp "$ROOT/automation/55-native-build.sh" "$fixture/automation/55-native-build.sh"
    printf '[workspace]\n' > "$fixture/src/mios-rs/Cargo.toml"
    mkdir -p "$fixture/lib" "$fixture/toolchain/lib/rustlib/fixture-host/bin"
    touch "$fixture/lib/libstd-fixture.rlib" "$fixture/toolchain/lib/rustlib/fixture-host/bin/rust-lld"
    chmod +x "$fixture/toolchain/lib/rustlib/fixture-host/bin/rust-lld"
    cat > "$TMP/bin/rustc" <<'EOF'
#!/bin/bash
case "$*" in
    '-vV') echo 'host: fixture-host' ;;
    '--print target-libdir'* )
        if [[ "${MIOS_TEST_NATIVE_MODE:-}" == missing-std ]]; then echo "$MIOS_TEST_NATIVE_ROOT/missing-std";
        else echo "$MIOS_TEST_NATIVE_ROOT/lib"; fi ;;
    '--print sysroot')
        if [[ "${MIOS_TEST_NATIVE_MODE:-}" == missing-linker ]]; then echo "$MIOS_TEST_NATIVE_ROOT/missing-linker";
        else echo "$MIOS_TEST_NATIVE_ROOT/toolchain"; fi ;;
    *) exit 1 ;;
esac
EOF
    printf '#!/bin/sh\necho "missing Rust target standard library fixture" >&2\nexit 1\n' > "$TMP/bin/rustup"
    chmod +x "$TMP/bin/rustc" "$TMP/bin/rustup"
    cat > "$TMP/bin/cargo" <<'EOF'
#!/bin/bash
set -euo pipefail
if [[ "$1" == build ]]; then
    printf '%s\n' "$*" >> "$MIOS_TEST_CARGO_LOG"
    name=miosd
    while (( $# )); do
        case "$1" in
            --target-dir) out="$2"; shift 2 ;;
            --target) target="$2"; shift 2 ;;
            --bin) name="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    out="$out/$target"
    mkdir -p "$out/release"
    if [[ "$name" == miosd ]]; then
        cat > "$out/release/miosd" <<'PLAN'
#!/bin/bash
case "$1" in
native-toolchain-check)
    if [[ "${MIOS_TEST_NATIVE_MODE:-}" == missing-clippy ]]; then echo 'required clippy component unavailable' >&2; exit 1; fi
    exit 0 ;;
native-runtime-check)
    if [[ "${MIOS_TEST_NATIVE_MODE:-}" == dynamic ]]; then echo 'static policy rejects ELF interpreter (PT_INTERP)' >&2; exit 1; fi
    exit 0 ;;
native-build-settings) printf 'fixture-musl\trust-lld\t-C target-feature=+crt-static\t2\n'; exit 0 ;;
native-artifact-check)
    case "${MIOS_TEST_NATIVE_MODE:-}" in
        foreign) echo 'expected a complete little-endian ELF64 executable' >&2; exit 1 ;;
        dynamic) echo 'static policy rejects ELF interpreter (PT_INTERP)' >&2; exit 1 ;;
    esac
    exit 0 ;;
native-targets) ;;
*) exit 1 ;;
esac
if [[ "${MIOS_TEST_NATIVE_MODE:-}" == catalog ]]; then echo 'fixture category conflict' >&2; exit 1; fi
printf 'tools/native\tfixture\tmios-test-native\tcli\t/usr/bin\tfalse\t/usr/libexec/mios\n'
printf 'src/mios-rs\tfixture\tmios-test-system\tdaemons\t/usr/libexec/mios\ttrue\t-\n'
PLAN
        chmod +x "$out/release/miosd"
        exit 0
    fi
    if [[ "${MIOS_TEST_NATIVE_MODE:-}" != missing ]]; then
        if [[ "${MIOS_TEST_NATIVE_MODE:-}" == foreign ]]; then printf 'MZfixture' > "$out/release/$name";
        else printf '\177ELFfixture' > "$out/release/$name"; fi
        chmod +x "$out/release/$name"
    fi
    printf 'sidecar' > "$out/release/$name.d"
    printf 'MZforeign' > "$out/release/$name.exe"
    chmod +x "$out/release/$name.d" "$out/release/$name.exe"
else exit 1; fi
EOF
    chmod +x "$TMP/bin/cargo"
    run_native() (
        export PATH="$TMP/bin:$PATH" MIOS_NATIVE_DEST_DIR="$fixture/out" MIOS_TEST_CARGO_LOG="$TMP/cargo.log" CARGO_TARGET_DIR="$TMP/unrelated-output" MIOS_TEST_NATIVE_ROOT="$fixture"
        bash "$fixture/automation/55-native-build.sh"
    )
    run_native
    if [[ -x "$fixture/out/mios-test-native" && -x "$fixture/out/mios-test-system" && ! -e "$fixture/out/mios-test-native.d" && ! -e "$fixture/out/mios-test-native.exe" ]]; then
        pass "native installation follows Cargo binaries and excludes executable sidecars"
    else fail "native artifact installation was incomplete or included foreign artifacts"; fi
    if grep -q -- "--target-dir $fixture/tools/native/target" "$TMP/cargo.log" && [[ ! -e "$TMP/unrelated-output" ]]; then pass "native build controls its output despite inherited CARGO_TARGET_DIR"; else fail "native build used an unrelated target directory"; fi
    if MIOS_TEST_NATIVE_MODE=catalog run_native > "$TMP/native.log" 2>&1; then fail "native build accepted invalid role catalog";
    elif grep -q 'fixture category conflict' "$TMP/native.log"; then pass "native build propagates Rust catalog rejection";
    else fail "catalog rejection lacked expected diagnostic"; fi
    (
        export PATH="$TMP/bin:$PATH" MIOS_NATIVE_INSTALL_ROOT="$fixture/stage" MIOS_TEST_CARGO_LOG="$TMP/cargo.log" MIOS_TEST_NATIVE_ROOT="$fixture"
        unset MIOS_NATIVE_DEST_DIR
        mkdir -p "$fixture/stage/usr/bin" "$fixture/stage/usr/libexec/mios"
        printf 'old executable' > "$fixture/stage/usr/libexec/mios/mios-test-native"
        ln -s ../libexec/mios/mios-test-native "$fixture/stage/usr/bin/mios-test-native"
        bash "$fixture/automation/55-native-build.sh"
    ) > "$TMP/native-stage.log" 2>&1
    if [[ ! -L "$fixture/stage/usr/bin/mios-test-native" && -x "$fixture/stage/usr/bin/mios-test-native" ]] &&
       [[ "$(readlink "$fixture/stage/usr/libexec/mios/mios-test-native")" == /usr/bin/mios-test-native ]] &&
       [[ "$(readlink "$fixture/stage/usr/bin/mios-test-system")" == /usr/libexec/mios/mios-test-system ]]; then
        pass "FHS staging replaces legacy symlinks and excludes staging prefixes from aliases"
    else fail "FHS staging created a cycle or leaked a staging path"; fi
    for mode in foreign missing dynamic missing-std missing-linker missing-clippy; do
        rm -f "$fixture/tools/native/target/fixture-musl/release/mios-test-native"
        if MIOS_TEST_NATIVE_MODE="$mode" run_native > "$TMP/native.log" 2>&1; then fail "native build accepted $mode artifact";
        elif grep -Eq 'ELF64 executable|build did not produce|PT_INTERP|missing Rust target standard library|linker.*unavailable|required clippy' "$TMP/native.log"; then pass "native build rejects $mode artifact with named diagnostics";
        else fail "native $mode failure lacked expected diagnostics"; fi
    done
    mv "$fixture/src/mios-rs/Cargo.toml" "$fixture/src/mios-rs/Cargo.toml.saved"
    if run_native > "$TMP/native.log" 2>&1; then fail "partial bake context accepted missing prebuilt tools";
    elif grep -q 'missing prebuilt' "$TMP/native.log"; then pass "partial bake context requires installed native tools";
    else fail "partial bake failure lacked expected diagnostics"; fi
    for name in miosd mios-gate mios-probe mios-node mios-resolver mios-unit-gen mios-agent-relay mios-render-quadlets mios-bake-plan; do
        if [[ "$name" == miosd ]]; then cp "$fixture/tools/native/target/fixture-host/release/miosd" "$fixture/out/$name";
        else printf '\177ELFfixture' > "$fixture/out/$name"; fi
        chmod +x "$fixture/out/$name"
    done
    if run_native > "$TMP/native.log" 2>&1 && grep -q 'required prebuilt' "$TMP/native.log"; then pass "partial bake context uses prebuilt native tools without rebuilding"; else fail "prebuilt bake context was rejected"; fi
    if MIOS_TEST_NATIVE_MODE=dynamic run_native > "$TMP/native.log" 2>&1; then fail "partial bake accepted a dynamic prebuilt artifact";
    elif grep -q PT_INTERP "$TMP/native.log"; then pass "partial bake context verifies prebuilt linkage";
    else fail "partial bake rejection lacked linkage diagnostic"; fi
    unset -f run_native
}

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

main() {
    dependency_checks
    native_build_checks
    if [[ "${1:-}" == --dependency-only ]]; then
        echo "[test-profile-packages] dependency failures: $fails"
        (( fails == 0 ))
        return
    fi
    local miosd="${MIOS_MIOSD_BIN:-$ROOT/src/mios-rs/target/debug/miosd}"
    if [[ ! -x "$miosd" ]]; then
        (cd "$ROOT/src/mios-rs" && cargo build -q -p miosd) || { echo "[test-profile-packages] ERROR: cannot build miosd" >&2; exit 1; }
    fi
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
    n_end="$(grep -n "^TOTAL_SCRIPTS=" "$ROOT/automation/build.sh" | cut -d: -f1)"
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
