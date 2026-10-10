#!/usr/bin/env bash
# AI-hint: bash Negative-test harness for the new drift gates. Inject violations, assert they fail, restore, and assert pass.
# AI-doc: usr/share/doc/mios/manual/tests.md
# AI-convention: Probe values in negative tests must be assembled dynamically (e.g. string concatenation or printf) so literals never match static grep scans in 98-drift-checks.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

_NEG_SNAP="$(mktemp -d)"
_NEG_ABSENT="${_NEG_SNAP}.absent"

_neg_targets() {
    grep -oE '\$\{ROOT\}/[A-Za-z0-9._/-]+' "${BASH_SOURCE[0]}" \
        | sed 's|^\${ROOT}/||' | sort -u
}

_neg_snapshot_take() {
    local rel src
    : > "$_NEG_ABSENT"
    while IFS= read -r rel; do
        src="${ROOT}/${rel}"
        if [[ -f "$src" ]]; then
            mkdir -p "${_NEG_SNAP}/$(dirname "$rel")"
            cp -p "$src" "${_NEG_SNAP}/${rel}"
        elif [[ ! -e "$src" ]]; then
            # Genuinely absent; non-files (e.g. Quadlet dir) must not be recorded as probes.
            printf '%s\n' "$rel" >> "$_NEG_ABSENT"
        fi
    done < <(_neg_targets)
}

_neg_snapshot_restore() {
    local rel src leaked=0
    while IFS= read -r rel; do
        src="${ROOT}/${rel}"
        [[ -f "${_NEG_SNAP}/${rel}" ]] || continue
        cmp -s "${_NEG_SNAP}/${rel}" "$src" 2>/dev/null && continue
        cp -p "${_NEG_SNAP}/${rel}" "$src"
        echo "[drift-gate-negatives] RESTORED a mutation left behind: ${rel}" >&2
        leaked=$((leaked + 1))
    done < <(cd "$_NEG_SNAP" 2>/dev/null && find . -type f | sed 's|^\./||')

    # Remove probes left behind by tests before broad staging
    while IFS= read -r rel; do
        [[ -n "$rel" && -f "${ROOT}/${rel}" ]] || continue
        rm -f "${ROOT}/${rel}"
        echo "[drift-gate-negatives] REMOVED a probe left behind: ${rel}" >&2
        leaked=$((leaked + 1))
    done < "$_NEG_ABSENT"

    rm -rf "$_NEG_SNAP" "$_NEG_ABSENT"
    [[ "$leaked" -eq 0 ]] || \
        echo "[drift-gate-negatives] ${leaked} artefact(s) were left behind by a test that did not clean up" >&2
}

trap _neg_snapshot_restore EXIT
_neg_snapshot_take

log() {
    echo -e "\033[1;34m[drift-gate-negatives]\033[0m $1"
}

die() {
    echo -e "\033[1;31m[drift-gate-negatives] ERROR:\033[0m $1" >&2
    [[ -n "${_NEG_GATE_OUT:-}" ]] && printf '%s\n' "$_NEG_GATE_OUT" | tail -20 >&2
    exit 1
}

test_version_ssot() {
    log "Testing check_version_ssot"
    local version_file="${ROOT}/VERSION"
    local orig_val
    orig_val="$(cat "$version_file")"
    echo "$orig_val" > "$version_file"

    rm -f "$version_file"
    echo "9.9.9" > "$version_file"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_version_ssot >/dev/null 2>&1; then
        rm -f "$version_file"
        echo "$orig_val" > "$version_file"
        die "Check_version_ssot passed despite version drift violation"
    fi

    rm -f "$version_file"
    echo "$orig_val" > "$version_file"
    _neg_gate check_version_ssot || die "Check_version_ssot failed after restoration"
    log "Check_version_ssot negative test passed"
}

test_resolver_equivalence() {
    log "Testing check_resolver_twin_equivalence"
    local userenv_file="${ROOT}/usr/lib/mios/userenv.sh"
    local bak_file="${userenv_file}.bak"
    cp "$userenv_file" "$bak_file"

    echo 'export MIOS_AI_TEST_TEMP="invalid-drift-val"' >> "$userenv_file"

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_resolver_twin_equivalence >/dev/null 2>&1 && die "Check_resolver_twin_equivalence passed despite mismatch"

    cp "$bak_file" "$userenv_file" && rm -f "$bak_file"
    _neg_gate check_resolver_twin_equivalence || die "Check_resolver_twin_equivalence failed after restoration"
    log "Check_resolver_twin_equivalence negative test passed"
}

test_eval_safety() {
    log "Testing check_cli_eval_safety"
    # In a subdirectory on purpose: a top-level plant passed just as well when
    # the check only listed the top level.
    local temp_verb="${ROOT}/usr/libexec/mios/automation/mios-test-temp-eval"

    rm -f "$temp_verb"

    cat << 'EOF' > "$temp_verb"
#!/bin/bash
eval "$1"
EOF
    chmod +x "$temp_verb"

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_cli_eval_safety >/dev/null 2>&1 && die "Check_cli_eval_safety passed despite eval injection"

    # The reviewed-eval exemption is the annotation on the line DIRECTLY above
    # (twin: miosd drift/security.rs eval_safety). Assembled at run time so the
    # probe annotation never sits verbatim in this file.
    local attest
    attest="$(printf '# TD-%s: eval-safe, input=%s, not agent-controlled' 1 negtest-fixture)"
    printf '#!/bin/bash\n%s\n\neval "$1"\n' "$attest" > "$temp_verb"
    _neg_gate check_cli_eval_safety && { rm -f "$temp_verb"; die "Check_cli_eval_safety accepted an annotation that is not on the line directly above the eval"; }
    printf '#!/bin/bash\n%s\neval "$1"\n' "$attest" > "$temp_verb"
    _neg_gate check_cli_eval_safety || { rm -f "$temp_verb"; die "Check_cli_eval_safety rejected a reviewed eval carrying the TD-1 annotation directly above it"; }

    rm -f "$temp_verb"
    _neg_gate check_cli_eval_safety || die "Check_cli_eval_safety failed after restoration"
    log "Check_cli_eval_safety negative test passed"
}

test_container_ports() {
    log "Testing check_container_ports"
    # The port comes from the SSOT so the plant tracks a retuned [ports]; the
    # first integer, non-*_internal key is a host-side port by construction.
    local key port upper
    read -r key port < <(python3 - "${ROOT}/usr/share/mios/mios.toml" <<'PY'
import sys, tomllib
ports = tomllib.load(open(sys.argv[1], "rb")).get("ports") or {}
for k, v in ports.items():
    if k != "stack_id" and not k.endswith("_internal") and type(v) is int:
        print(k, v)
        break
PY
) || true
    [[ -n "$key" && -n "$port" ]] || die "check_container_ports: no host-side integer port in [ports] to plant"
    upper="$(printf '%s' "$key" | tr '[:lower:]' '[:upper:]')"
    local probe="${ROOT}/usr/share/containers/systemd/mios-negtest-ports.container"
    rm -f "$probe"

    # 1. A hand-copied SSOT port literal on an active line must fail, by name.
    printf '[Container]\nPublishPort=127.0.0.1:%s:%s\n' "$port" "$port" > "$probe"
    if _neg_gate check_container_ports; then
        rm -f "$probe"; die "check_container_ports passed with a hand-copied [ports].${key} literal"
    fi
    grep -q "manual port literal ${port} for \[ports\].${key}" <<<"$_NEG_GATE_OUT" \
        || { rm -f "$probe"; die "check_container_ports failed without naming [ports].${key}: ${_NEG_GATE_OUT}"; }

    # 2. The canonical MIOS_PORTS_ fallback is SSOT-wired and must pass. The
    #    legacy regex knew only MIOS_PORT_, so every such line was reported.
    printf '[Container]\nPublishPort=127.0.0.1:${MIOS_PORTS_%s:-%s}:${MIOS_PORTS_%s:-%s}\n' \
        "$upper" "$port" "$upper" "$port" > "$probe"
    _neg_gate check_container_ports \
        || { rm -f "$probe"; die "check_container_ports rejected an SSOT-wired \${MIOS_PORTS_${upper}:-${port}} fallback: ${_NEG_GATE_OUT}"; }

    # 3. Only a whole line starting with # or ; is a systemd comment: a literal
    #    after a mid-line # (a URL fragment) still reaches the unit.
    printf '[Container]\nLabel=url=http://localhost/#/%s\n' "$port" > "$probe"
    if _neg_gate check_container_ports; then
        rm -f "$probe"; die "check_container_ports passed with a literal hidden behind a mid-line #"
    fi

    rm -f "$probe"
    _neg_gate check_container_ports || die "check_container_ports failed after restoration: ${_NEG_GATE_OUT}"
    log "check_container_ports negative test passed"
}

test_shellcheck_failure() {
    log "Testing check_shellcheck"

    local tmp_bin_dir
    tmp_bin_dir="$(mktemp -d)"

    # Sub-test 1: Task AGY-1810 - shellcheck absence under MIOS_DRIFT_REQUIRE_TOOLS=1 vs unset
    cat << 'EOF' > "${tmp_bin_dir}/shellcheck"
#!/bin/sh
echo "shellcheck: command not found" >&2
exit 127
EOF
    chmod +x "${tmp_bin_dir}/shellcheck"

    local old_path="$PATH"
    export PATH="${tmp_bin_dir}:${PATH}"

    # With MIOS_DRIFT_REQUIRE_TOOLS=1, absence must FAIL
    if MIOS_DRIFT_REQUIRE_TOOLS=1 MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_shellcheck >/dev/null 2>&1; then
        export PATH="$old_path"
        rm -rf "$tmp_bin_dir"
        die "check_shellcheck passed despite absent shellcheck under MIOS_DRIFT_REQUIRE_TOOLS=1"
    fi

    # With MIOS_DRIFT_REQUIRE_TOOLS unset, absence must WARN and PASS (exit 0)
    if ! ( unset MIOS_DRIFT_REQUIRE_TOOLS; MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_shellcheck >/dev/null 2>&1 ); then
        export PATH="$old_path"
        rm -rf "$tmp_bin_dir"
        die "check_shellcheck failed on absent shellcheck when MIOS_DRIFT_REQUIRE_TOOLS was unset"
    fi

    # Sub-test 2: Injected shellcheck lint failure (exit 1) must always fail
    cat << 'EOF' > "${tmp_bin_dir}/shellcheck"
#!/bin/sh
echo "Injected shellcheck failure"
exit 1
EOF
    chmod +x "${tmp_bin_dir}/shellcheck"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_shellcheck >/dev/null 2>&1; then
        export PATH="$old_path"
        rm -rf "$tmp_bin_dir"
        die "check_shellcheck passed despite shellcheck lint failure"
    fi

    export PATH="$old_path"
    rm -rf "$tmp_bin_dir"
    _neg_gate check_shellcheck || die "check_shellcheck failed after restoration"
    log "check_shellcheck negative test passed"
}

# task_1713: the Python generator is deleted; every site runs the native twin.
_names_gen_bin() {
    local c
    for c in "${MIOS_NATIVE_BIN_DIR:-}/mios-gen" \
             "${ROOT}/tools/native/target/release/mios-gen" \
             "${ROOT}/tools/native/target/debug/mios-gen" \
             "${ROOT}/tools/native/target/release/mios-gen.exe" \
             "${ROOT}/tools/native/target/debug/mios-gen.exe" /usr/bin/mios-gen; do
        [[ -x "$c" ]] && { printf '%s' "$c"; return 0; }
    done
    return 1
}

_names_gen_run() {  # Compare in memory with the same canonical generator as the gate.
    local b; b="$(_names_gen_bin)" || return 1
    "$b" names-registry --root "$ROOT" --check
}

_test_names_registry_readonly() (
    local gate="$1" fixture bin candidate output artifact before_names before_refs
    fixture="$(mktemp -d)"
    trap 'rm -rf "$fixture"' EXIT
    trap 'exit 130' INT TERM
    bin=""
    for candidate in "${MIOS_NATIVE_BIN_DIR:-}/mios-gen" \
        "$ROOT/tools/native/target/release/mios-gen" \
        "$ROOT/tools/native/target/debug/mios-gen" /usr/bin/mios-gen /usr/libexec/mios/mios-gen; do
        [[ -x "$candidate" ]] && { bin="$candidate"; break; }
    done
    [[ -n "$bin" ]] || die "$gate requires native mios-gen"
    mkdir -p "$fixture/usr/share/mios"
    printf '[ports]\nhttp=80\n' > "$fixture/usr/share/mios/mios.toml"
    printf 'EXTERNAL_NAME\n' > "$fixture/usr/share/mios/referenced_names.txt"
    printf 'echo ${%s}\n' "$(printf 'MIOS_%s' PORTS_HTTP)" > "$fixture/consumer.sh"
    git -C "$fixture" init --quiet
    git -C "$fixture" add .
    "$bin" names-registry --root "$fixture" >/dev/null
    _names_fixture_gate() {
        MIOS_DRIFT_CHECK_ROOT="$fixture" bash "$ROOT/automation/98-drift-checks.sh" "$gate"
    }
    _names_fixture_gate >/dev/null || die "$gate failed its clean baseline"
    cp "$fixture/usr/share/mios/names.generated.txt" "$fixture/names.saved"
    cp "$fixture/usr/share/mios/referenced_names.txt" "$fixture/refs.saved"
    for artifact in names.generated.txt referenced_names.txt; do
        printf '%s\n' "$(printf 'MIOS_%s' READONLY_PROBE)" >> "$fixture/usr/share/mios/$artifact"
        before_names="$(sha256sum "$fixture/usr/share/mios/names.generated.txt")"
        before_refs="$(sha256sum "$fixture/usr/share/mios/referenced_names.txt")"
        if output="$(_names_fixture_gate 2>&1)"; then
            die "$gate passed with stale $artifact"
        fi
        [[ "$output" == *"stale names-registry projection:"*"$artifact"* ]] \
            || die "$gate rejected the wrong defect: $output"
        [[ "$(sha256sum "$fixture/usr/share/mios/names.generated.txt")" == "$before_names" \
            && "$(sha256sum "$fixture/usr/share/mios/referenced_names.txt")" == "$before_refs" ]] \
            || die "$gate changed registry bytes while checking"
        cp "$fixture/names.saved" "$fixture/usr/share/mios/names.generated.txt"
        cp "$fixture/refs.saved" "$fixture/usr/share/mios/referenced_names.txt"
        _names_fixture_gate >/dev/null || die "$gate failed after restoration"
    done
    rm "$fixture/consumer.sh"
    if output="$(_names_fixture_gate 2>&1)"; then
        die "$gate passed with an absent tracked consumer"
    fi
    [[ "$output" == *"cannot read tracked consumer consumer.sh"* ]] \
        || die "$gate rejected the wrong missing input: $output"
    cmp -s "$fixture/names.saved" "$fixture/usr/share/mios/names.generated.txt" \
        && cmp -s "$fixture/refs.saved" "$fixture/usr/share/mios/referenced_names.txt" \
        || die "$gate changed projections after a census failure"
    printf 'echo ${%s}\n' "$(printf 'MIOS_%s' PORTS_HTTP)" > "$fixture/consumer.sh"
    _names_fixture_gate >/dev/null || die "$gate failed after consumer restoration"
)

test_names_registry() {
    log "Testing check_names_registry"
    _test_names_registry_readonly check_names_registry || die "check_names_registry read-only negative failed"
    log "check_names_registry read-only negative tests passed"
}

# The gate-registry rewrite (333a5cae) dropped the only mention of
# check_dead_lane, so it ran with no negative: plant a live retired-lane port.
test_dead_lane() {
    log "Testing check_dead_lane"
    local probe="${ROOT}/etc/mios/ai/negtest-dead-lane.json"
    printf '{"url": "http://localhost:%s/v1"}\n' "$((11000 + 434))" > "$probe"
    _neg_gate check_dead_lane && { rm -f "$probe"; die "check_dead_lane passed with a live retired-lane port"; }
    [[ "$_NEG_GATE_OUT" == *negtest-dead-lane.json* ]] || { rm -f "$probe"; die "check_dead_lane failed without naming the probe: $_NEG_GATE_OUT"; }
    printf '# http://localhost:%s/v1\n' "$((11000 + 434))" > "$probe"
    _neg_gate check_dead_lane || { rm -f "$probe"; die "check_dead_lane refused a commented-out port: $_NEG_GATE_OUT"; }
    rm -f "$probe"
    _neg_gate check_dead_lane || die "check_dead_lane failed after restoration: $_NEG_GATE_OUT"
    log "check_dead_lane negative test passed"
}

# main() holds every check to read-only, so the guard is tested through a copy
# of the gate carrying two planted checks: one only reads, one writes.
test_read_only_gate() {
    log "Testing the drift gate's read-only guard"
    local src="${ROOT}/automation/98-drift-checks.sh" target="${ROOT}/usr/share/mios/names.generated.txt"
    local gate bak out=""; gate="$(mktemp)"; bak="$(mktemp)"; cp "$target" "$bak"
    _ro_fail() { cp "$bak" "$target"; rm -f "$gate" "$bak"; unset -f _ro_fail _ro_run; die "$1"; }
    _ro_run() { out="$(MIOS_DRIFT_CHECK_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" bash "$gate" "$(printf '%s_zz_%s' check "$1")" 2>&1)"; }
    [[ "$(tail -n 1 "$src")" == 'main "$@"' ]] || _ro_fail "98-drift-checks.sh no longer ends in main; the planted copy would not run"
    { sed '$d' "$src"
      printf '%s_zz_reads() { cat "$ROOT/%s" >/dev/null; }\n' check usr/share/mios/names.generated.txt
      printf '%s_zz_writes() { echo planted >> "$ROOT/%s"; }\n' check usr/share/mios/names.generated.txt
      printf 'main "$@"\n'; } > "$gate"
    _ro_run reads || _ro_fail "the read-only guard refused a check that only reads: $out"
    _ro_run writes && _ro_fail "the drift gate passed a check that wrote into the tree it grades"
    [[ "$out" == *"must be read-only: usr/share/mios/names.generated.txt"* ]] \
        || _ro_fail "the read-only guard failed without naming the written file: $out"
    cp "$bak" "$target"; rm -f "$gate" "$bak"; unset -f _ro_fail _ro_run
    log "drift gate read-only guard negative test passed"
}

# Both readers answered a refusing git with a filesystem walk that skips every
# directory named build/, so two tracked files left the corpus in silence.
test_dead_git_corpus() {
    log "Testing the names generator and the version-literal scan against a refusing git"
    # A PATH shim cannot intercept a native binary's git on Windows:
    # CreateProcess never executes an extensionless shell script, so the shim
    # is invisible to the exe and the probe would "succeed" with the REAL git.
    # The refusal path itself is unit-tested inside the twin's own crate.
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*|Windows*)
            log "dead-git corpus test SKIPPED on Windows (PATH shims cannot intercept native git)"
            return 0
            ;;
    esac
    local shim reg before after VLBIN=""
    for VLBIN in "${MIOS_NATIVE_BIN_DIR:-}/mios-gate" \
                 "${ROOT}/src/mios-rs/target/release/mios-gate.exe" \
                 "${ROOT}/src/mios-rs/target/debug/mios-gate.exe" \
                 "${ROOT}/src/mios-rs/target/release/mios-gate" \
                 "${ROOT}/src/mios-rs/target/debug/mios-gate" \
                 /usr/libexec/mios/mios-gate ""; do
        [[ -n "$VLBIN" && -x "$VLBIN" ]] && break
    done
    [[ -n "$VLBIN" ]] || die "mios-gate is not built, so the dead-git corpus test has no subject"
    shim="$(mktemp -d)"
    printf '#!/bin/sh\necho "fatal: refusing to list" >&2\nexit 128\n' > "${shim}/git"
    chmod +x "${shim}/git"
    reg="${ROOT}/usr/share/mios/referenced_names.txt"
    before="$(md5sum "$reg" | cut -d' ' -f1)"

    if PATH="${shim}:$PATH" _names_gen_run >/dev/null 2>&1; then
        rm -rf "$shim"
        die "generate-names-registry rewrote the registry on a corpus git never gave it"
    fi
    after="$(md5sum "$reg" | cut -d' ' -f1)"
    if [[ "$before" != "$after" ]]; then
        rm -rf "$shim"
        die "generate-names-registry altered referenced_names.txt while refusing to run"
    fi

    if PATH="${shim}:$PATH" \
        "$VLBIN" version-literals-ssot --root "$ROOT" >/dev/null 2>&1; then
        rm -rf "$shim"
        die "version-literals-ssot reported clean on a corpus git never gave it"
    fi
    rm -rf "$shim"

    _names_gen_run >/dev/null 2>&1 \
        || die "generate-names-registry failed with a working git"
    "$VLBIN" version-literals-ssot --root "$ROOT" >/dev/null 2>&1 \
        || die "version-literals-ssot failed with a working git"
    log "dead-git corpus negative test passed"
}

test_root_toml_subset() {
    log "Testing check_root_toml_subset"
    local root_toml="${ROOT}/mios.toml"
    local orig_val="" created=0
    if [[ -f "$root_toml" ]]; then
        orig_val="$(cat "$root_toml")"
        echo "$orig_val" > "$root_toml"
    else
        created=1
        : > "$root_toml"
    fi

    cat << 'EOF' >> "$root_toml"
[meta.nonexistent_drift_test_section]
fake_key_drift_assertion = "drift"
EOF

    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_root_toml_subset >/dev/null 2>&1; then
        if [[ $created -eq 1 ]]; then rm -f "$root_toml"; else rm -f "$root_toml" && echo "$orig_val" > "$root_toml"; fi
        die "Check_root_toml_subset passed despite invalid key injection"
    fi

    if [[ $created -eq 1 ]]; then rm -f "$root_toml"; else rm -f "$root_toml" && echo "$orig_val" > "$root_toml"; fi
    _neg_gate check_root_toml_subset || die "Check_root_toml_subset failed after restoration"
    log "Check_root_toml_subset negative test passed"
}

test_toml_projection() {
    log "Testing check_toml_projection"
    local root_toml="${ROOT}/mios.toml"
    if [[ ! -f "$root_toml" ]]; then
        log "Root mios.toml absent"
        return 0
    fi
    local orig_val
    orig_val="$(cat "$root_toml")"
    echo "$orig_val" > "$root_toml"

    python3 - "$root_toml" << 'EOF'
import sys
p = sys.argv[1]
t = open(p, encoding="utf-8").read()
new = t.replace('accent      = "#1A407F"', 'accent      = "#DEAD00"', 1)
if new == t:
    new = t.replace('#1A407F', '#DEAD00', 1)   # fallback if spacing differs
open(p, "w", encoding="utf-8").write(new)
EOF

    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_toml_projection >/dev/null 2>&1; then
        rm -f "$root_toml"
        echo "$orig_val" > "$root_toml"
        die "Check_toml_projection passed despite injected [colors] drift"
    fi

    rm -f "$root_toml"
    echo "$orig_val" > "$root_toml"
    _neg_gate check_toml_projection || die "Check_toml_projection failed after restoration"
    log "Check_toml_projection negative test passed"
}

# The `; printf X` / `${var%X}` pairs below keep a snapshot byte-exact:
# $(cat) strips EVERY trailing newline and echo puts back exactly one.
test_ratchet_direction() {
    log "Testing check_ratchet_direction"
    local main_toml="${ROOT}/usr/share/mios/mios.toml"
    local orig_val
    orig_val="$(cat "$main_toml"; printf X)"

    RATCHET_TOML="$main_toml" python3 - <<'EOF'
import os
p = os.environ["RATCHET_TOML"]
text = open(p, "r", encoding="utf-8").read()
new_text = text.replace("max_exempt_suites = 6", "max_exempt_suites = 99", 1)
open(p, "w", encoding="utf-8").write(new_text)
EOF

    if MIOS_DRIFT_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_ratchet_direction >/dev/null 2>&1; then
        printf '%s' "${orig_val%X}" > "$main_toml"
        die "check_ratchet_direction passed despite raised ratchet ceiling"
    fi

    printf '%s' "${orig_val%X}" > "$main_toml"

    # task_988: a ceiling may be declared a GENERATED budget and allowed to rise,
    # but only itemised with a reason -- a bare key is a silent skip wearing a
    # register's clothing.
    _rd_restore() { printf '%s' "${orig_val%X}" > "$main_toml"; }

    python3 - "$main_toml" <<'EOF'
import sys
p = sys.argv[1]
t = open(p, encoding="utf-8").read()
t = t.replace('[drift.generated_ceilings]\n', '[drift.generated_ceilings]\n"ci.max_exempt_suites" = ""\n', 1)
t = t.replace("max_exempt_suites = 6", "max_exempt_suites = 99", 1)
open(p, "w", encoding="utf-8").write(t)
EOF
    _neg_gate check_ratchet_direction && { _rd_restore; die "check_ratchet_direction passed with an exemption carrying no reason"; }
    case "${_NEG_GATE_OUT}" in
        *"carries no \`reason\`"*) : ;;
        *) _rd_restore; die "check_ratchet_direction failed for the wrong reason on a bare exemption: ${_NEG_GATE_OUT}" ;;
    esac
    _rd_restore

    # The positive half of the same branch: WITH a reason, the same raise is
    # allowed -- otherwise the exemption path is dead code that grants nothing.
    python3 - "$main_toml" <<'EOF'
import sys
p = sys.argv[1]
t = open(p, encoding="utf-8").read()
t = t.replace('[drift.generated_ceilings]\n', '[drift.generated_ceilings]\n"ci.max_exempt_suites" = "negative-test fixture"\n', 1)
t = t.replace("max_exempt_suites = 6", "max_exempt_suites = 99", 1)
open(p, "w", encoding="utf-8").write(t)
EOF
    _neg_gate check_ratchet_direction || { _rd_restore; die "check_ratchet_direction rejected an itemised generated-budget raise: ${_NEG_GATE_OUT}"; }
    _rd_restore

    # An exemption naming something that is not a ceiling is stale bookkeeping
    # that would quietly start covering a future key of that name.
    python3 - "$main_toml" <<'EOF'
import sys
p = sys.argv[1]
t = open(p, encoding="utf-8").read()
t = t.replace('[drift.generated_ceilings]\n', '[drift.generated_ceilings]\n"no.such.ceiling" = "stale"\n', 1)
open(p, "w", encoding="utf-8").write(t)
EOF
    _neg_gate check_ratchet_direction && { _rd_restore; die "check_ratchet_direction passed with an exemption for a key that is not a ceiling"; }
    _rd_restore
    unset -f _rd_restore

    _neg_gate check_ratchet_direction || die "check_ratchet_direction failed after restoration"
    log "check_ratchet_direction negative test passed"
}

test_render_extension_coverage() {
    log "Testing check_render_extension_coverage"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local probe="${ROOT}/usr/lib/systemd/system/mios-negtest-render.socket"
    local bak; bak="$(mktemp)"; cp "$toml" "$bak"
    _rec_fail() { cp "$bak" "$toml"; rm -f "$bak" "$probe"; unset -f _rec_fail _rec_drop; die "$1"; }
    _rec_drop() {  # $1 = an extension to take off [build.quadlet_render].extensions
        python3 - "$toml" "$1" <<'EOF' || _rec_fail "extension list shape changed; fixture is stale"
import re, sys
p, ext = sys.argv[1], sys.argv[2]
t = open(p, encoding="utf-8", newline="").read()
m = re.search(r"^extensions = \[\n(?:.*\n)*?\]\n", t, re.M)
assert m and f'"{ext}"' in m.group(0)
blk = re.sub(r'\s*"%s",' % re.escape(ext), "", m.group(0), count=1)
open(p, "w", encoding="utf-8", newline="").write(t[:m.start()] + blk + t[m.end():])
EOF
    }

    # task_977: `.socket` was off the renderer's list, so mios-cockpit-link.socket
    # shipped its port placeholder verbatim. mios-unit-gen now resolves that port
    # at projection (4c38b100): no shipped .socket carries a placeholder, and
    # dropping `socket` alone landed nowhere. Plant the task_977 shape and show the
    # list decides: covered with `socket`, named without it.
    printf '[Socket]\nListenStream=0.0.0.0:${MIOS_%s}\n' PORTS_COCKPIT_LINK > "$probe"
    _neg_gate check_render_extension_coverage || _rec_fail "check_render_extension_coverage refused a .socket placeholder while .socket is declared: ${_NEG_GATE_OUT}"
    _rec_drop socket
    _neg_gate check_render_extension_coverage && _rec_fail "check_render_extension_coverage passed with .socket removed from the renderer's scope"
    [[ "${_NEG_GATE_OUT}" == *mios-negtest-render.socket* ]] || _rec_fail "check_render_extension_coverage failed for the wrong reason: ${_NEG_GATE_OUT}"
    cp "$bak" "$toml"; rm -f "$probe"

    # No probe: shipped units still carry placeholders, so dropping `service`
    # must name one of them.
    _rec_drop service
    _neg_gate check_render_extension_coverage && _rec_fail "check_render_extension_coverage passed with .service removed from the renderer's scope"
    [[ "${_NEG_GATE_OUT}" == *"usr/lib/systemd/system/"*".service carries a"* ]] || _rec_fail "dropping .service named no shipped unit: ${_NEG_GATE_OUT}"
    cp "$bak" "$toml"

    # An empty scope is cannot-run, not a flood of findings.
    python3 - "$toml" <<'EOF'
import re, sys
p = sys.argv[1]
t = open(p, encoding="utf-8", newline="").read()
t2 = re.sub(r"extensions = \[\n(?:.*\n)*?\]\n", "extensions = []\n", t, count=1)
assert t2 != t, "extension list not found; fixture is stale"
open(p, "w", encoding="utf-8", newline="").write(t2)
EOF
    _neg_gate check_render_extension_coverage && _rec_fail "check_render_extension_coverage passed with an empty extension list"
    case "${_NEG_GATE_OUT}" in
        *"absent or empty"*) : ;;
        *) _rec_fail "empty list did not read as cannot-run: ${_NEG_GATE_OUT}" ;;
    esac
    cp "$bak" "$toml"

    rm -f "$bak"; unset -f _rec_fail _rec_drop
    _neg_gate check_render_extension_coverage || die "check_render_extension_coverage failed after restoration: ${_NEG_GATE_OUT}"
    log "check_render_extension_coverage negative test passed"
}

test_render_quadlets() {
    log "Testing check_render_quadlets"
    local unit="${ROOT}/usr/share/containers/systemd/mios-ceph.container"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local ubak tbak
    ubak="$(mktemp)"; cp "$unit" "$ubak"
    tbak="$(mktemp)"; cp "$toml" "$tbak"
    _rq_fail() {
        cp "$ubak" "$unit"; cp "$tbak" "$toml"
        rm -f "$ubak" "$tbak"; unset -f _rq_fail; die "$1"
    }

    # A placeholder nothing can resolve would ship literal into the image, which
    # is the class that shipped mios-cockpit-link.socket unparseable (task_995).
    sed -i 's|^Image=quay.io/ceph/ceph:.*|Image=quay.io/ceph/ceph:${MIOS_NOT_A_REAL_VAR}|' "$unit"
    _neg_gate check_render_quadlets && _rq_fail "check_render_quadlets passed with an unresolvable placeholder"
    cp "$ubak" "$unit"

    # An empty scan list must be cannot-run, never a quiet zero-file pass.
    sed -i 's|^dirs = \[|dirs = [] # |' "$toml"
    _neg_gate check_render_quadlets && _rq_fail "check_render_quadlets passed with an empty [build.quadlet_render].dirs"
    cp "$tbak" "$toml"

    rm -f "$ubak" "$tbak"; unset -f _rq_fail
    _neg_gate check_render_quadlets || die "check_render_quadlets failed after restoration: ${_NEG_GATE_OUT}"
    log "check_render_quadlets negative test passed"
}

test_toolchain_pin() {
    log "Testing check_toolchain_pin"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local pin="${ROOT}/rust-toolchain.toml"
    local tbak pbak
    tbak="$(mktemp)"; cp "$toml" "$tbak"
    pbak="$(mktemp)"; cp "$pin" "$pbak"
    _tp_fail() {
        cp "$tbak" "$toml"; cp "$pbak" "$pin"
        rm -f "$tbak" "$pbak"; unset -f _tp_fail; die "$1"
    }

    # A hand edit to a generated file. This is the state the check exists to
    # catch: the pin says one thing, the SSOT says another, and CI silently
    # lints with whichever the file happens to name.
    sed -i 's/^channel = ".*"/channel = "1.0.0"/' "$pin"
    _neg_gate check_toolchain_pin && _tp_fail "check_toolchain_pin passed with a hand-edited rust-toolchain.toml"
    cp "$pbak" "$pin"

    # The SSOT moved and nobody regenerated. Same divergence, opposite side.
    sed -i 's/^channel = "\([^"]*\)"$/channel = "9.9.9"/' "$toml"
    _neg_gate check_toolchain_pin && _tp_fail "check_toolchain_pin passed with the SSOT ahead of the generated pin"
    cp "$tbak" "$toml"

    # No pin at all is the pre-task_996 state: both workspaces lint-floating.
    # Absent must never read as clean.
    rm -f "$pin"
    _neg_gate check_toolchain_pin && _tp_fail "check_toolchain_pin passed with rust-toolchain.toml absent"
    cp "$pbak" "$pin"

    # An empty component list would restore the floating behaviour while
    # leaving a pin in place -- `-D warnings` means nothing without clippy.
    sed -i 's/^components = \[.*\]$/components = []/' "$toml"
    _neg_gate check_toolchain_pin && _tp_fail "check_toolchain_pin passed with no components declared"
    cp "$tbak" "$toml"

    rm -f "$tbak" "$pbak"; unset -f _tp_fail
    _neg_gate check_toolchain_pin || die "check_toolchain_pin failed after restoration: ${_NEG_GATE_OUT}"
    log "check_toolchain_pin negative test passed"
}

test_ai_config_projection() {
    log "Testing check_ai_config_projection"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local out="${ROOT}/etc/mios/ai/config.json"
    local vout="${ROOT}/usr/share/mios/ai/v1/config.json"
    local tbak obak vbak
    tbak="$(mktemp)"; cp "$toml" "$tbak"
    obak="$(mktemp)"; cp "$out" "$obak"
    vbak="$(mktemp)"; cp "$vout" "$vbak"
    _ac_fail() {
        cp "$tbak" "$toml"; cp "$obak" "$out"; cp "$vbak" "$vout"
        rm -f "$tbak" "$obak" "$vbak"; unset -f _ac_fail; die "$1"
    }

    # A hand edit back onto a retired lane's port: the state both repos' copies
    # were in before this file was generated.
    sed -i 's|"base_url":"http://localhost:[0-9]*/v1"|"base_url":"http://localhost:8642/v1"|' "$out"
    _neg_gate check_ai_config_projection && _ac_fail "check_ai_config_projection passed with a hand-edited config.json port"
    cp "$obak" "$out"

    # The vendor ai/v1 copy is projected too; it sat on :8640 by hand.
    sed -i 's|"base_url":"http://localhost:[0-9]*/v1"|"base_url":"http://localhost:8640/v1"|' "$vout"
    _neg_gate check_ai_config_projection && _ac_fail "check_ai_config_projection passed with a hand-edited usr/share/mios/ai/v1/config.json port"
    cp "$vbak" "$vout"

    # The SSOT moved and nobody regenerated.
    sed -i 's/^agent_pipe\( *\)= [0-9][0-9]*/agent_pipe\1= 1/' "$toml"
    _neg_gate check_ai_config_projection && _ac_fail "check_ai_config_projection passed with [ports].agent_pipe ahead of config.json"
    cp "$tbak" "$toml"

    # Absent must never read as clean -- either copy.
    rm -f "$out"
    _neg_gate check_ai_config_projection && _ac_fail "check_ai_config_projection passed with config.json absent"
    cp "$obak" "$out"
    rm -f "$vout"
    _neg_gate check_ai_config_projection && _ac_fail "check_ai_config_projection passed with usr/share/mios/ai/v1/config.json absent"
    cp "$vbak" "$vout"

    rm -f "$tbak" "$obak" "$vbak"; unset -f _ac_fail
    _neg_gate check_ai_config_projection || die "check_ai_config_projection failed after restoration: ${_NEG_GATE_OUT}"
    log "check_ai_config_projection negative test passed"
}

test_artifact_prompt() {
    log "Testing check_artifact_prompt"
    local out="${ROOT}/ARTIFACT-PROMPT.md" bak; bak="$(mktemp)"; cp "$out" "$bak"
    _ap_fail() { cp "$bak" "$out"; rm -f "$bak"; unset -f _ap_fail; die "$1"; }
    # A hand edit: the daily agent would follow text the SSOT never said.
    printf '\nhand edit\n' >> "$out"
    _neg_gate check_artifact_prompt && _ap_fail "check_artifact_prompt passed with a hand-edited ARTIFACT-PROMPT.md"
    # Absent is never clean: the daily task would fetch a 404.
    rm -f "$out"
    _neg_gate check_artifact_prompt && _ap_fail "check_artifact_prompt passed with ARTIFACT-PROMPT.md absent"
    cp "$bak" "$out"; rm -f "$bak"; unset -f _ap_fail
    _neg_gate check_artifact_prompt || die "check_artifact_prompt failed after restoration: ${_NEG_GATE_OUT}"
    log "check_artifact_prompt negative test passed"
}

test_ai_artifacts() {
    local sft="${ROOT}/var/lib/mios/training/sft.jsonl" bak; bak="$(mktemp)"; cp "$sft" "$bak"
    printf '%s\n' '{"input":{"messages":[]},"preferred_output":[],"non_preferred_output":[]}' >> "$sft"
    _neg_gate check_ai_artifacts; local rc=$?; cp "$bak" "$sft"; rm -f "$bak"
    (( rc )) && [[ "$_NEG_GATE_OUT" == *sft.jsonl* ]] || die "check_ai_artifacts did not reject a DPO record in sft.jsonl: ${_NEG_GATE_OUT}"
    _neg_gate check_ai_artifacts || die "check_ai_artifacts failed after restoration: ${_NEG_GATE_OUT}"
}

test_size_ceiling() {
    log "Testing check_size_ceiling"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak; bak="$(mktemp)"; cp "$toml" "$bak"
    _sz_fail() { cp "$bak" "$toml"; rm -f "$bak"; unset -f _sz_fail; die "$1"; }

    # Below the measured floor: the tree already exceeds its own budget, which
    # is the state task_988 was filed about and must not read as clean.
    sed -i 's/^max_tracked_mb\( *\)= [0-9]*/max_tracked_mb\1= 1/' "$toml"
    _neg_gate check_size_ceiling && _sz_fail "check_size_ceiling passed with a ceiling below the measured floor"
    cp "$bak" "$toml"

    # Above floor+headroom: slack nobody declared. A generated budget that can
    # drift upward without bound is not a budget.
    sed -i 's/^max_tracked_mb\( *\)= [0-9]*/max_tracked_mb\1= 99999/' "$toml"
    _neg_gate check_size_ceiling && _sz_fail "check_size_ceiling passed with a ceiling far above the declared headroom"
    cp "$bak" "$toml"

    # The allowance is operator-tunable and the tool must not invent one.
    sed -i '/^tracked_mb_headroom = /d' "$toml"
    _neg_gate check_size_ceiling && _sz_fail "check_size_ceiling passed with no tracked_mb_headroom declared"
    cp "$bak" "$toml"

    rm -f "$bak"; unset -f _sz_fail
    _neg_gate check_size_ceiling || die "check_size_ceiling failed after restoration: ${_NEG_GATE_OUT}"
    log "check_size_ceiling negative test passed"
}

test_curl_retry() {
    log "Testing check_curl_retry"
    local temp_script="${ROOT}/automation/temp_curl_test.sh"
    cat << 'EOF' > "$temp_script"
curl https://example.com/unretried_file.tar.gz -o /tmp/file.tar.gz
EOF
    chmod +x "$temp_script"

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_curl_retry >/dev/null 2>&1 && die "Check_curl_retry passed despite unretried curl fetch"

    rm -f "$temp_script"
    _neg_gate check_curl_retry || die "Check_curl_retry failed after restoration"
    log "Check_curl_retry negative test passed"
}

test_resolver_ssot_refs() {
    log "Testing check_resolver_ssot_refs"
    local target="${ROOT}/usr/libexec/mios/mios-resolve-latest"
    local backup="${target}.negtest.bak"
    cp "$target" "$backup"
    printf '    local drifted_ref="docker.io/pgvector/pgvector:pg16"\n' >> "$target"

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_resolver_ssot_refs >/dev/null 2>&1 && die "check_resolver_ssot_refs passed despite a hardcoded registry image ref"

    mv "$backup" "$target"
    _neg_gate check_resolver_ssot_refs || die "check_resolver_ssot_refs failed after restoration"
    log "check_resolver_ssot_refs negative test passed"
}

test_nested_podman_caps() {
    log "Testing check_nested_podman_caps"
    local doc_file="${ROOT}/usr/share/doc/mios/reference/nested-podman-caps.md"
    local orig_val
    orig_val="$(cat "$doc_file")"
    rm -f "$doc_file"

    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_nested_podman_caps >/dev/null 2>&1; then
        rm -f "$doc_file"
        echo "$orig_val" > "$doc_file"
        die "Check_nested_podman_caps passed despite missing reference doc"
    fi

    rm -f "$doc_file"
    echo "$orig_val" > "$doc_file"
    _neg_gate check_nested_podman_caps || die "Check_nested_podman_caps failed after restoration"
    log "Check_nested_podman_caps negative test passed"
}

test_bake_budget() {
    log "Testing check_bake_budget"
    local sbom_tsv="${ROOT}/usr/share/mios/artifacts/sbom/bound-images.tsv"
    local orig_val=""
    if [[ -f "$sbom_tsv" ]]; then
        orig_val="$(cat "$sbom_tsv")"
        echo "$orig_val" > "$sbom_tsv"
    else
        mkdir -p "$(dirname "$sbom_tsv")"
    fi

    rm -f "$sbom_tsv"
    {
        echo "$orig_val"
        for i in $(seq 1 35); do
            echo "Image_${i}	quay.io/mios/fake_${i}:latest	1.0GB"
        done
    } > "$sbom_tsv"

    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_bake_budget >/dev/null 2>&1; then
        rm -f "$sbom_tsv"
    echo "$orig_val" > "$sbom_tsv"
        die "Check_bake_budget passed despite exceeding sidecar threshold"
    fi

    rm -f "$sbom_tsv"
    echo "$orig_val" > "$sbom_tsv"
    _neg_gate check_bake_budget || die "Check_bake_budget failed after restoration"
    log "Check_bake_budget negative test passed"
}

test_module_test_coverage() {
    log "Testing check_module_test_coverage"
    # Each plant must be NAMED in the diagnostic: a gate that fails for another
    # reason (an unbuilt mios-gate, an unrelated module) proves nothing here.
    local temp_submodule="${ROOT}/usr/lib/mios/agent-pipe/mios_pipe/identity/temp_untested_mod.py"
    local temp_sibling="${ROOT}/usr/lib/mios/agent-pipe/test_mios_temp_untested_mod.py"
    _mtc_clean() {
        rm -f "$temp_submodule" "$temp_sibling" "${ROOT}/tools/temp_untested_tool_mod.py"             "${ROOT}/usr/lib/mios/agent-pipe/mios_pipe/identity/__pycache__/temp_untested_mod"* 2>/dev/null || true
    }
    _mtc_fail() { _mtc_clean; unset -f _mtc_clean _mtc_fail; die "$1"; }

    echo "# Temp untested submodule" > "$temp_submodule"
    _neg_gate check_module_test_coverage && _mtc_fail "check_module_test_coverage passed despite an untested submodule"
    grep -q "mios_pipe/identity/temp_untested_mod.py is named by no unit test" <<<"$_NEG_GATE_OUT"         || _mtc_fail "check_module_test_coverage did not name the untested submodule: ${_NEG_GATE_OUT}"

    # task_1006: a test file NAMED after the module is not the evidence -- an empty
    # one used to satisfy the old filename check.
    : > "$temp_sibling"
    _neg_gate check_module_test_coverage && _mtc_fail "check_module_test_coverage passed on an empty test named after the module"
    grep -q "temp_untested_mod.py is named by no unit test" <<<"$_NEG_GATE_OUT"         || _mtc_fail "check_module_test_coverage did not name the module behind the empty sibling: ${_NEG_GATE_OUT}"
    _mtc_clean

    echo "# Temp untested tool" > "${ROOT}/tools/temp_untested_tool_mod.py"
    _neg_gate check_module_test_coverage && _mtc_fail "check_module_test_coverage passed despite an un-grandfathered tools module"
    grep -q "untested python module not in baseline: tools/temp_untested_tool_mod.py" <<<"$_NEG_GATE_OUT"         || _mtc_fail "check_module_test_coverage did not name the untested tools module: ${_NEG_GATE_OUT}"
    _mtc_clean
    unset -f _mtc_clean _mtc_fail

    _neg_gate check_module_test_coverage         || die "Check_module_test_coverage failed after restoration: ${_NEG_GATE_OUT}"
    log "Check_module_test_coverage negative test passed"
}

test_router_parity() {
    log "Testing check_router_parity"
    local temp_mod="${ROOT}/usr/lib/mios/agent-pipe/mios_pipe/routing/temp_unmapped_router_branch.py"
    echo 'def _bogus_intent_branch(intent):' > "$temp_mod"
    echo '    if intent == "unmapped_bogus_intent": return True' >> "$temp_mod"

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_router_parity >/dev/null 2>&1 && die "Check_router_parity passed despite unmapped intent branch in routing code"

    rm -f "$temp_mod"* "${ROOT}/usr/lib/mios/agent-pipe/mios_pipe/routing/__pycache__/temp_unmapped_router_branch"* 2>/dev/null || true
    _neg_gate check_router_parity || die "Check_router_parity failed after restoration"
    log "Check_router_parity negative test passed"
}

test_council_gate_ssot() {
    log "Testing check_council_gate_ssot"
    local toml_file="${ROOT}/usr/share/mios/mios.toml"
    local bak_file="${toml_file}.council_bak"
    cp "$toml_file" "$bak_file"

    python3 - "$toml_file" << 'EOF'
import sys
p = sys.argv[1]
t = open(p, encoding="utf-8").read()
new = t.replace('diversity_threshold         = 0.92', '# diversity_threshold disabled', 1)
open(p, "w", encoding="utf-8").write(new)
EOF

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_council_gate_ssot >/dev/null 2>&1 && die "Check_council_gate_ssot passed despite missing diversity_threshold key in [agent_pipe.council]"

    cp "$bak_file" "$toml_file"
    rm -f "$bak_file"
    _neg_gate check_council_gate_ssot || die "Check_council_gate_ssot failed after restoration"
    log "Check_council_gate_ssot negative test passed"
}

test_agent_pipe_budgets() (
    log "Testing check_agent_pipe_budgets"
    local toml_file="${ROOT}/usr/share/mios/mios.toml" keep output empty_bins
    keep="$(mktemp -d)"
    cp "$toml_file" "$keep/mios.toml"
    trap 'cp "$keep/mios.toml" "$toml_file"; rm -rf "$keep"' EXIT
    trap 'exit 130' INT TERM
    _neg_gate check_agent_pipe_budgets || die "budget census failed its clean baseline"

    python3 - "$toml_file" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
source = path.read_text()
source, count = re.subn(r'^swarm_max_width\s*=.*$', '# required width removed', source, count=1, flags=re.M)
assert count == 1, 'required width subject absent'
path.write_text(source)
PY
    _neg_gate check_agent_pipe_budgets && die "budget census passed without required width"
    [[ "$_NEG_GATE_OUT" == *"required budget key(s) missing"*"swarm_max_width"* ]] \
        || die "budget census rejected the wrong missing-key defect"
    cp "$keep/mios.toml" "$toml_file"
    _neg_gate check_agent_pipe_budgets || die "budget census failed after width restoration"

    python3 - "$toml_file" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
source = path.read_text()
assert source.count('[agent_pipe]\n') == 1
probe = 'budget_census_' + 'unconsumed_probe'
path.write_text(source.replace('[agent_pipe]\n', '[agent_pipe]\n' + probe + ' = 1\n', 1))
PY
    _neg_gate check_agent_pipe_budgets && die "budget census passed with an unregistered dead key"
    [[ "$_NEG_GATE_OUT" == *"no consumer and are not registered"*"$(printf 'budget_census_%s' unconsumed_probe)"* ]] \
        || die "budget census rejected the wrong unconsumed-key defect"
    cp "$keep/mios.toml" "$toml_file"
    _neg_gate check_agent_pipe_budgets || die "budget census failed after dead-key restoration"

    empty_bins="$keep/empty-bin"
    mkdir "$empty_bins"
    if output="$(MIOS_NATIVE_BIN_DIR="$empty_bins" MIOS_DRIFT_CHECK_ROOT="$ROOT" \
        bash "$ROOT/automation/98-drift-checks.sh" check_agent_pipe_budgets 2>&1)"; then
        die "budget census silently fell back from a missing configured native tool"
    fi
    [[ "$output" == *"mios-aiplane-lint is required"* ]] \
        || die "budget census rejected the wrong missing-tool defect: $output"
    log "check_agent_pipe_budgets named negative controls and restoration passed"
)
test_agent_schema() {
    log "Testing check_agent_schema"
    local toml_file="${ROOT}/usr/share/mios/mios.toml"
    local orig_val
    orig_val="$(cat "$toml_file"; printf X)"

    python3 - "$toml_file" << 'EOF'
import sys
p = sys.argv[1]
t = open(p, encoding="utf-8").read()
new = t.replace('health_gate = true', '# health_gate removed', 1)
open(p, "w", encoding="utf-8").write(new)
EOF

    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_agent_schema >/dev/null 2>&1; then
        printf '%s' "${orig_val%X}" > "$toml_file"
        die "check_agent_schema passed despite missing health_gate in [agents.hermes]"
    fi

    printf '%s' "${orig_val%X}" > "$toml_file"
    _neg_gate check_agent_schema || die "check_agent_schema failed after restoration"
    log "check_agent_schema negative test passed"
}

test_bake_tokens() {
    log "Testing check_bake_plan with bogus firstboot token"
    local toml_file="${ROOT}/usr/share/mios/mios.toml"
    local bak_file="${toml_file}.bak"
    cp "$toml_file" "$bak_file"

    python3 - "$toml_file" << 'EOF'
import sys
p = sys.argv[1]
t = open(p, encoding="utf-8").read()
new = t.replace('firstboot_tokens = [', 'firstboot_tokens = ["bogus_unmatched_firstboot_token", ', 1)
open(p, "w", encoding="utf-8").write(new)
EOF

    # check_bake_plan drives the native generator stage 85 runs; --check writes nothing.
    _neg_gate check_bake_plan || :
    cp "$bak_file" "$toml_file" && rm -f "$bak_file"
    [[ "$_NEG_GATE_OUT" == *"Firstboot token 'bogus_unmatched_firstboot_token'"* ]] || die "check_bake_plan did not reject a bogus firstboot token: ${_NEG_GATE_OUT}"
    _neg_gate check_bake_plan || die "check_bake_plan failed after restoration: ${_NEG_GATE_OUT}"
    log "Test_bake_tokens negative test passed"
}
test_bake_unresolved_image() {
    log "Testing check_bake_plan detects an Image= that resolves nowhere"
    local q="${ROOT}/usr/share/containers/systemd/mios-ceph.container"
    local bak="${q}.bak"
    cp "$q" "$bak"
    # Restore on EVERY exit path. Without this, the `die` on the message
    # assertion below returned before the `cp` at the end of the function and
    # left mios-ceph.container mutated, which then failed two later bake tests
    # that read the same file -- one wording change cost three failures.
    _bui_restore() { [ -f "$bak" ] && cp "$bak" "$q" && rm -f "$bak"; }
    trap _bui_restore EXIT INT TERM

    sed -i 's|^Image=.*|Image=quay.io/ceph/ceph:${UNRESOLVABLE_PROBE_TAG}|' "$q"

    # Captured, not piped: the suite runs under `set -o pipefail`, so
    # `gate | grep -q` returns the gate's exit 1 even when grep matched, and
    # the detection is thrown away by the harness rather than missed.
    local out=""
    out="$(MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT"         MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh"         check_bake_plan 2>&1 || true)"

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT"         MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh"         check_bake_plan >/dev/null 2>&1 && die "check_bake_plan passed despite an Image= that resolves nowhere"

    # Assert the PROPERTY -- that the check names what failed to resolve -- not
    # one producer's prose. This matched the Python generator's wording; the
    # gate now validates the native producer (task_994), whose message is
    # different and strictly better because it names the VARIABLE.
    printf '%s' "$out" | grep -q "UNRESOLVABLE_PROBE_TAG" || die "check_bake_plan failed without naming the unresolvable Image="

    _bui_restore
    trap - EXIT INT TERM; unset -f _bui_restore
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT"         MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh"         check_bake_plan >/dev/null 2>&1         || die "check_bake_plan failed after restoration"
    log "Test_bake_unresolved_image negative test passed"
}

test_containerfile_pinned_clones() {
    log "Testing check_containerfile_pinned_clones"
    local temp_containerfile="${ROOT}/usr/share/mios/sys/Containerfile.testtemp"

    cat << 'EOF' > "$temp_containerfile"
FROM alpine
RUN git clone https://github.com/example/unpinned-repo.git /tmp/unpinned
EOF

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_containerfile_pinned_clones >/dev/null 2>&1 && die "Check_containerfile_pinned_clones passed despite unpinned git clone"

    rm -f "$temp_containerfile"
    _neg_gate check_containerfile_pinned_clones || die "Check_containerfile_pinned_clones failed after restoration"
    log "Check_containerfile_pinned_clones negative test passed"
}

test_firstboot_tier() {
    log "Testing check_firstboot_tier"
    local fb_list="${ROOT}/usr/lib/mios/bake/plan.d/firstboot.list"
    local bak_file="${fb_list}.bak"
    cp "$fb_list" "$bak_file"

    echo "docker.io/unmatched/bogus-image:latest" >> "$fb_list"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_firstboot_tier >/dev/null 2>&1; then
        cp "$bak_file" "$fb_list" && rm -f "$bak_file"
        die "Check_firstboot_tier passed despite unmatched firstboot.list entry"
    fi

    cp "$bak_file" "$fb_list" && rm -f "$bak_file"

    # Test unjustified token in mios.toml
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local toml_bak="${toml}.negbak"
    cp "$toml" "$toml_bak"
    sed -i 's/firstboot_tokens = \[/firstboot_tokens = ["bogustoken", /' "$toml"
    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_firstboot_tier >/dev/null 2>&1 && die "Check_firstboot_tier passed despite unjustified firstboot token"
    cp "$toml_bak" "$toml" && rm -f "$toml_bak"

    _neg_gate check_firstboot_tier || die "Check_firstboot_tier failed after restoration"
    log "Check_firstboot_tier negative test passed"
}

test_rechunk_budget() {
    log "Testing check_rechunk_budget"
    local script="${ROOT}/automation/build/rechunk.sh"
    local orig_val
    orig_val="$(cat "$script")"
    rm -f "$script"
    echo "$orig_val" > "$script"
    sed -i 's/rechunk_max_layers/unused_key/g' "$script"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_rechunk_budget >/dev/null 2>&1; then
        rm -f "$script"
        echo "$orig_val" > "$script"
        die "Check_rechunk_budget passed despite missing rechunk_max_layers"
    fi

    rm -f "$script"
    echo "$orig_val" > "$script"
    _neg_gate check_rechunk_budget || die "Check_rechunk_budget failed after restoration"
    log "Check_rechunk_budget negative test passed"
}

test_bake_core_reconcile() (
    log "Testing test_bake_core_reconcile"
    local toml_file="${ROOT}/usr/share/mios/mios.toml"
    local bak_file; bak_file="$(mktemp)"
    cp "$toml_file" "$bak_file"
    trap 'cp "$bak_file" "$toml_file"; rm -f "$bak_file"' EXIT
    _neg_gate check_bake_plan || die "check_bake_plan failed before core reconcile controls"
    python3 - "$toml_file" <<'PYEOF'
import re, sys
p = sys.argv[1]
with open(p, "r", encoding="utf-8", newline="") as fh:
    t = fh.read()
t, n = re.subn(r'(core\s*=\s*\[\s*\r?\n)', r'\1  "docker.io/library/unreferenced-image-xyz999:latest",\n', t, count=1)
if n != 1:
    sys.exit("test_bake_core_reconcile injection failed")
with open(p, "w", encoding="utf-8", newline="") as fh:
    fh.write(t)
PYEOF
    _neg_gate check_bake_plan && die "check_bake_plan accepted an unreferenced core image"
    [[ "$_NEG_GATE_OUT" == *"Core image 'docker.io/library/unreferenced-image-xyz999:latest' is not referenced by any Quadlet"* ]] \
        || die "check_bake_plan failed without naming the unreferenced core image"
    cp "$bak_file" "$toml_file"
    _neg_gate check_bake_plan || die "check_bake_plan failed after core reconcile restoration"
    log "Test_bake_core_reconcile negative test passed"
)

test_nested_podman_retry() {
    log "Testing check_nested_podman_caps"
    local script="${ROOT}/usr/libexec/mios/57-mios-sys-build.sh"
    local bak_file="${script}.bak"
    cp "$script" "$bak_file"

    sed -i 's/build_image_with_retry/build_image_direct/g' "$script"
    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_nested_podman_caps >/dev/null 2>&1 && die "test_nested_podman_retry: Check_nested_podman_caps passed despite missing build_image_with_retry"

    cp "$bak_file" "$script" && rm -f "$bak_file"
    _neg_gate check_nested_podman_caps || die "test_nested_podman_retry: check_nested_podman_caps failed after retry script restoration"
    log "Test_nested_podman_retry negative test passed"
}

test_gate_registry() (
    log "Testing check_gate_registry"
    local wrapper="${ROOT}/automation/98-drift-checks.sh"
    local fixture; fixture="$(mktemp -d)"
    trap 'rm -rf -- "$fixture"' EXIT
    mkdir -p "$fixture/automation" "$fixture/tools"
    local script="$fixture/automation/98-drift-checks.sh"
    local baseline="$fixture/baseline"
    cat > "$baseline" <<'EOF'
check_alpha() { :; }
main() {
    check_alpha
}
_alias() {
    check_alpha
}
EOF
    cp "$baseline" "$script"
    MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "$wrapper" check_gate_registry >"$fixture/out" 2>&1 \
        || die "gate registry clean repository failed"
    MIOS_DRIFT_CHECK_ROOT="$fixture" bash "$wrapper" check_gate_registry >"$fixture/out" 2>&1 \
        || die "gate registry counted a helper alias outside main"

    printf 'check_alpha() { :; }\n' >> "$script"
    MIOS_DRIFT_CHECK_ROOT="$fixture" bash "$wrapper" check_gate_registry >"$fixture/out" 2>&1 \
        && die "gate registry accepted a duplicate definition"
    grep -q 'duplicate function definition: check_alpha' "$fixture/out" \
        || die "gate registry did not name the duplicate definition"
    cp "$baseline" "$script"
    printf 'check_unregistered_dummy() { :; }\n' >> "$script"
    MIOS_DRIFT_CHECK_ROOT="$fixture" bash "$wrapper" check_gate_registry >"$fixture/out" 2>&1 \
        && die "gate registry accepted an unregistered definition"
    grep -q 'not registered in main(): check_unregistered_dummy' "$fixture/out" \
        || die "gate registry did not name the unregistered definition"
    sed '/^main() {/a\    check_undefined_dummy' "$baseline" > "$script"
    MIOS_DRIFT_CHECK_ROOT="$fixture" bash "$wrapper" check_gate_registry >"$fixture/out" 2>&1 \
        && die "gate registry accepted an undefined main call"
    grep -q 'main() calls undefined check: check_undefined_dummy' "$fixture/out" \
        || die "gate registry did not name the undefined call"
    cp "$baseline" "$script"
    printf '# AI-hint: A drift check.\npass\n' > "$fixture/tools/check-fixture.py"
    MIOS_DRIFT_CHECK_ROOT="$fixture" bash "$wrapper" check_gate_registry >"$fixture/out" 2>&1 \
        && die "gate registry accepted an unreferenced drift tool"
    grep -q 'tools/check-fixture.py claims drift-check identity' "$fixture/out" \
        || die "gate registry did not name the unreferenced tool"
    rm -- "$fixture/tools/check-fixture.py"
    : > "$script"
    MIOS_DRIFT_CHECK_ROOT="$fixture" bash "$wrapper" check_gate_registry >"$fixture/out" 2>&1 \
        && die "gate registry accepted empty input"
    grep -q 'expected one closed column-zero main() function' "$fixture/out" \
        || die "gate registry did not reject empty input for the expected reason"
    cp "$baseline" "$script"
    MIOS_DRIFT_CHECK_ROOT="$fixture" bash "$wrapper" check_gate_registry >"$fixture/out" 2>&1 \
        || die "gate registry failed after restoration"
    cmp -s "$baseline" "$script" || die "gate registry mutated fixture sources"
    MIOS_NATIVE_BIN_DIR="$fixture/missing" MIOS_DRIFT_CHECK_ROOT="$fixture" \
        bash "$wrapper" check_gate_registry >"$fixture/out" 2>&1 \
        && die "gate registry passed without its configured native binary"
    grep -q 'requires native mios-gate' "$fixture/out" \
        || die "gate registry missing-binary failure was not named"
    log "test_gate_registry negative test passed"
)
test_test_hermeticity() {
    log "Testing check_test_hermeticity"
    # In a subdirectory on purpose: a top-level plant passed just as well when
    # the check only listed the top level.
    local temp_test="${ROOT}/usr/lib/mios/agent-pipe/tests/test_fake_live_resource.py"

    cat << 'EOF' > "$temp_test"
import psycopg
def test_live():
    conn = psycopg.connect("dbname=mios user=mios")
EOF

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_test_hermeticity >/dev/null 2>&1 && die "Check_test_hermeticity passed despite unguarded psycopg.connect call"

    rm -f "$temp_test"
    _neg_gate check_test_hermeticity || die "Check_test_hermeticity failed after restoration"
    log "Test_test_hermeticity negative test passed"
}

test_no_mkdir_in_var() {
    log "Testing check_no_mkdir_in_var"
    local temp_script="${ROOT}/automation/99-fake-var-mkdir.sh"
    echo 'mkdir -p /var/log/fake_test' > "$temp_script"

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_no_mkdir_in_var >/dev/null 2>&1 && die "Check_no_mkdir_in_var passed despite imperative /var mkdir"

    rm -f "$temp_script"
    _neg_gate check_no_mkdir_in_var || die "Check_no_mkdir_in_var failed after restoration"
    log "Test_no_mkdir_in_var negative test passed"
}

test_quadlet_privilege() {
    log "Testing check_quadlet_privilege"
    local q_dir="${ROOT}/etc/containers/systemd"
    mkdir -p "$q_dir"
    local temp_q="${q_dir}/fake-unprivileged-violation.container"
    rm -f "$temp_q" 2>/dev/null || true
    cat << 'EOF' > "$temp_q"
[Container]
Image=docker.io/library/alpine:latest
User=root
EOF

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_quadlet_privilege >/dev/null 2>&1 && die "Check_quadlet_privilege passed despite un-allowlisted User=root"
    rm -f "$temp_q"

    # A unit with no User= at all runs as root: it must be refused by name.
    local temp_mu="${q_dir}/fake-missing-user.container" out_mu
    cat << 'EOF' > "$temp_mu"
[Container]
Image=docker.io/library/alpine:latest
Delegate=yes
EOF
    if out_mu="$(MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_quadlet_privilege 2>&1)"; then
        rm -f "$temp_mu"
        die "Check_quadlet_privilege passed despite missing User="
    fi
    rm -f "$temp_mu"
    grep -qF "fake-missing-user.container: implicitly/explicitly root (User=) but NOT in [security.privileged_quadlets].root" <<<"$out_mu" \
        || die "Check_quadlet_privilege failed the missing-User= plant without naming it"

    _neg_pod_projection_controls privilege
    _neg_gate check_quadlet_privilege || die "Check_quadlet_privilege failed after restoration"
    log "Test_quadlet_privilege negative test passed"
}

test_lint_is_final() {
    log "Testing check_lint_is_final"
    local cf="${ROOT}/Containerfile"
    local bak; bak="$(mktemp)"; cp "$cf" "$bak"
    sed -i '/RUN bootc container lint/d' "$cf"

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_lint_is_final >/dev/null 2>&1 && die "Check_lint_is_final passed despite missing bootc container lint"

    cp "$bak" "$cf"; rm -f "$bak"
    _neg_gate check_lint_is_final || die "Check_lint_is_final failed after restoration"
    log "Test_lint_is_final negative test passed"
}

test_firstboot_degrade_open() {
    log "Testing check_firstboot_degrade_open"
    local temp_fb="${ROOT}/usr/libexec/mios/mios-fake-firstboot.sh"

    cat << 'EOF' > "$temp_fb"
set -euo pipefail
curl -sfL https://example.invalid/payload.tar -o /tmp/payload.tar
EOF
    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_firstboot_degrade_open >/dev/null 2>&1         && die "check_firstboot_degrade_open passed despite an unguarded egress call under set -e"

    # Regression guard for the defect itself: an unrelated "|| true" elsewhere
    # in the file used to certify the whole script. It must not any more.
    cat << 'EOF' > "$temp_fb"
set -euo pipefail
rm -f /tmp/mios-fake-stale || true
curl -sfL https://example.invalid/payload.tar -o /tmp/payload.tar
EOF
    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_firstboot_degrade_open >/dev/null 2>&1         && die "check_firstboot_degrade_open was satisfied by an unrelated '|| true' elsewhere in the file"

    # Positive control: the same fetch, guarded, must pass.
    cat << 'EOF' > "$temp_fb"
set -euo pipefail
curl -sfL https://example.invalid/payload.tar -o /tmp/payload.tar || true
EOF
    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_firstboot_degrade_open >/dev/null 2>&1         || die "check_firstboot_degrade_open rejected a guarded egress call"

    rm -f "$temp_fb"
    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_firstboot_degrade_open >/dev/null 2>&1         || die "check_firstboot_degrade_open failed after restoration"
    log "Test_firstboot_degrade_open negative test passed"
}

test_require_tools() {
    log "Testing MIOS_DRIFT_REQUIRE_TOOLS"
    local tmp_bin="${ROOT}/tmp_no_python"
    mkdir -p "$tmp_bin"

    MIOS_DRIFT_REQUIRE_TOOLS=1 PATH="$tmp_bin" bash "${ROOT}/automation/98-drift-checks.sh" check_cli_eval_safety >/dev/null 2>&1 && die "Check_cli_eval_safety passed despite missing python3 when MIOS_DRIFT_REQUIRE_TOOLS=1"

    rm -rf "$tmp_bin"
    log "Test_require_tools negative test passed"
}

test_ssot_lint_deadkey() {
    log "Testing 97-ssot-lint.sh dead-key injection"
    local temp_q="${ROOT}/usr/share/containers/systemd/fake-deadkey-test.container"
    rm -f "$temp_q" 2>/dev/null || true
    local dummy_var="MI"
    dummy_var+="OS_FAKE_DEADKEY_UNWIRED_VAR"
    cat << EOF > "$temp_q"
[Container]
Image=docker.io/library/alpine:latest
Exec=/bin/sh -c "\${${dummy_var}:-false}"
User=mios
Group=mios
Delegate=yes
EOF

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/97-ssot-lint.sh" >/dev/null 2>&1 && die "97-ssot-lint.sh passed despite dead key injection"

    rm -f "$temp_q"
    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/97-ssot-lint.sh" >/dev/null 2>&1 \
        || die "97-ssot-lint.sh failed after restoration"
    log "Test_ssot_lint_deadkey negative test passed"
}

test_soft_mode_not_committed() {
    log "Testing check_soft_mode_not_committed"
    local gha_file="${ROOT}/.github/workflows/mios-ci.yml"
    local orig_val
    orig_val="$(cat "$gha_file")"
    printf '%s\n%s\n' "$orig_val" "MIOS_DRIFT_CHECK_SOFT=1" > "$gha_file"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_soft_mode_not_committed >/dev/null 2>&1; then
        rm -f "$gha_file"
        echo "$orig_val" > "$gha_file"
        die "Check_soft_mode_not_committed passed despite committed MIOS_DRIFT_CHECK_SOFT=1"
    fi

    rm -f "$gha_file"
    echo "$orig_val" > "$gha_file"
    _neg_gate check_soft_mode_not_committed || die "Check_soft_mode_not_committed failed after restoration"
    log "Test_soft_mode_not_committed negative test passed"
}

test_oci_archive_path() {
    log "Testing check_oci_archive_path"
    local stage_script="${ROOT}/usr/libexec/mios/mios-stage-oci-archive"
    local orig_val
    orig_val="$(cat "$stage_script")"
    rm -f "$stage_script"
    echo "$orig_val" > "$stage_script"
    sed -i 's/mios-latest\.tar/mios-mismatched-name\.tar/g' "$stage_script"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_oci_archive_path >/dev/null 2>&1; then
        rm -f "$stage_script"
        echo "$orig_val" > "$stage_script"
        die "Check_oci_archive_path passed despite producer/consumer path mismatch"
    fi

    rm -f "$stage_script"
    echo "$orig_val" > "$stage_script"
    _neg_gate check_oci_archive_path || die "Check_oci_archive_path failed after restoration"
    log "Test_oci_archive_path negative test passed"
}

test_replaceme_mount_substitution() {
    log "Testing check_replaceme_mount_substitution"
    local justfile="${ROOT}/Justfile"
    local orig_val
    orig_val="$(cat "$justfile")"
    rm -f "$justfile"
    echo "$orig_val" > "$justfile"

    cat << 'EOF' >> "$justfile"

fake-raw-bib:
    sudo podman run -v ./config/artifacts/iso.toml:/config.toml:ro bib
EOF

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_replaceme_mount_substitution >/dev/null 2>&1; then
        rm -f "$justfile"
        echo "$orig_val" > "$justfile"
        die "Check_replaceme_mount_substitution passed despite raw-mounted REPLACEME template"
    fi

    rm -f "$justfile"
    echo "$orig_val" > "$justfile"
    _neg_gate check_replaceme_mount_substitution || die "Check_replaceme_mount_substitution failed after restoration"
    log "Test_replaceme_mount_substitution negative test passed"
}

test_kickstart_shell_syntax() {
    log "Testing check_kickstart_shell_syntax"
    local cfg="${ROOT}/usr/share/mios/ventoy/mios-kickstart.cfg"
    local orig_val
    orig_val="$(cat "$cfg")"
    echo "$orig_val" > "$cfg"

    rm -f "$cfg"
    echo "$orig_val" > "$cfg"
    cat << 'EOF' >> "$cfg"
%post
if [ true ]; then
  echo "Missing fi"
%end
EOF

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_kickstart_shell_syntax >/dev/null 2>&1; then
        rm -f "$cfg"
        echo "$orig_val" > "$cfg"
        die "Check_kickstart_shell_syntax passed despite invalid bash syntax in %post"
    fi

    # Deleting the subject is not a pass. Without this the test certified only
    # that a PRESENT file parses, which is what let the delete case go unseen.
    rm -f "$cfg"
    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_kickstart_shell_syntax >/dev/null 2>&1; then
        echo "$orig_val" > "$cfg"
        die "Check_kickstart_shell_syntax passed with its tracked subject deleted"
    fi

    rm -f "$cfg"
    echo "$orig_val" > "$cfg"
    _neg_gate check_kickstart_shell_syntax || die "Check_kickstart_shell_syntax failed after restoration"
    log "Test_kickstart_shell_syntax negative test passed"
}

test_offline_install_invariant() {
    log "Testing check_offline_install_invariant"
    local install_script="${ROOT}/tools/install.sh"
    local orig_val
    orig_val="$(cat "$install_script")"
    rm -f "$install_script"
    echo "$orig_val" > "$install_script"

    echo "podman pull ghcr.io/ublue-os/ucore-hci:latest" >> "$install_script"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_offline_install_invariant >/dev/null 2>&1; then
        rm -f "$install_script"
        echo "$orig_val" > "$install_script"
        die "Check_offline_install_invariant passed despite injected podman pull"
    fi

    rm -f "$install_script"
    echo "$orig_val" > "$install_script"
    _neg_gate check_offline_install_invariant || die "Check_offline_install_invariant failed after restoration"
    log "Test_offline_install_invariant negative test passed"
}

test_installer_family_roles() {
    log "Testing check_installer_family_roles"
    # Plant the collision in a family member this repo owns (root install.sh
    # is mios-bootstrap's). cp -p keeps the executable bit on restore.
    local s_script="${ROOT}/automation/install-fhs.sh"
    local s_stash
    s_stash="$(mktemp)"
    cp -p "$s_script" "$s_stash"

    sed -i 's/MIOS_INSTALLER_ROLE=fhs-overlay-installer/MIOS_INSTALLER_ROLE=bootc-baremetal-disk-installer/g' "$s_script"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_installer_family_roles >/dev/null 2>&1; then
        cp -p "$s_stash" "$s_script"; rm -f "$s_stash"
        die "Check_installer_family_roles passed despite duplicate role marker"
    fi

    cp -p "$s_stash" "$s_script"; rm -f "$s_stash"
    _neg_gate check_installer_family_roles || die "Check_installer_family_roles failed after restoration"

    # A tracked installer absent from the worktree is a deleted deliverable, not
    # a smaller subject list. Deleting one used to shrink the corpus in silence.
    local f_script="${ROOT}/automation/install-fhs.sh"
    local f_stash
    f_stash="$(mktemp)"
    cp -p "$f_script" "$f_stash"
    rm -f "$f_script"
    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_installer_family_roles >/dev/null 2>&1; then
        cp -p "$f_stash" "$f_script"
        rm -f "$f_stash"
        die "Check_installer_family_roles passed with a tracked installer deleted"
    fi
    cp -p "$f_stash" "$f_script"
    rm -f "$f_stash"
    _neg_gate check_installer_family_roles || die "Check_installer_family_roles failed after restoring the deleted installer"
    log "Test_installer_family_roles negative test passed"
}

test_bib_configs_projection() {
    log "Testing check_bib_configs_projection"
    local bib_file="${ROOT}/config/artifacts/bib.toml"
    local orig_val
    orig_val="$(cat "$bib_file")"
    rm -f "$bib_file"
    echo "$orig_val" > "$bib_file"

    sed -i 's/minsize = "80 GiB"/minsize = "999 GiB"/g' "$bib_file"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_bib_configs_projection >/dev/null 2>&1; then
        rm -f "$bib_file"
        echo "$orig_val" > "$bib_file"
        die "Check_bib_configs_projection passed despite unprojected minsize edit"
    fi

    rm -f "$bib_file"
    echo "$orig_val" > "$bib_file"
    _neg_gate check_bib_configs_projection || die "Check_bib_configs_projection failed after restoration"
    log "Test_bib_configs_projection negative test passed"
}

test_ssot_lint_equivalence() {
    log "Testing check_ssot_lint_equivalence"
    _neg_gate check_ssot_lint_equivalence || die "check_ssot_lint_equivalence failed on the clean tree: $_NEG_GATE_OUT"
    # A linter that never reports an orphan must fail the check, not satisfy it.
    local fake; fake="$(mktemp -d)"
    printf '#!/bin/sh\necho "[97-ssot-lint] PASS"\n' > "$fake/mios-ssot-lint"; chmod +x "$fake/mios-ssot-lint"
    MIOS_NATIVE_BIN_DIR="$fake" _neg_gate check_ssot_lint_equivalence && { rm -rf "$fake"; die "check_ssot_lint_equivalence passed with a linter that never reports an orphan"; }
    rm -rf "$fake"
    log "Test_ssot_lint_equivalence negative test passed"
}

test_native_generators_fail_closed() {
    log "Testing that the checks of ported generators fail closed without their native binary"
    local empty c; empty="$(mktemp -d)"
    for c in check_roadmap_index check_pipe_boundaries check_guacamole_consistency check_pipeline_numbering \
             check_ports_category_schema check_globals_generated check_ai_manifests_fresh check_manpages \
             check_adr_index check_metal_vs_hosted check_desktop_launchers check_tmux_theme check_btop_theme \
             check_fastfetch check_edge_generators check_templates_compilation; do
        MIOS_NATIVE_BIN_DIR="$empty" _neg_gate "$c" && { rm -rf "$empty"; die "$c passed with no native generator"; }
        grep -Eq 'native mios-(gen|template-compile) is required' <<<"$_NEG_GATE_OUT" \
            || { rm -rf "$empty"; die "$c failed without naming the missing native generator: $_NEG_GATE_OUT"; }
    done
    rm -rf "$empty"
    log "Test_native_generators_fail_closed negative test passed"
}

test_repo_partition_label_ssot() {
    log "Testing check_repo_partition_label_ssot"
    local install_script="${ROOT}/tools/install.sh"
    local orig_val
    orig_val="$(cat "$install_script")"
    rm -f "$install_script"
    echo "$orig_val" > "$install_script"

    sed -i 's/MiOS-Repo/MiOS-MismatchedLabel/g' "$install_script"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_repo_partition_label_ssot >/dev/null 2>&1; then
        rm -f "$install_script"
        echo "$orig_val" > "$install_script"
        die "Check_repo_partition_label_ssot passed despite label mismatch"
    fi

    rm -f "$install_script"
    echo "$orig_val" > "$install_script"
    _neg_gate check_repo_partition_label_ssot || die "Check_repo_partition_label_ssot failed after restoration"
    log "Test_repo_partition_label_ssot negative test passed"
}

test_bib_single_config_invariant() {
    log "Testing check_bib_single_config_invariant"
    local justfile="${ROOT}/Justfile"
    local orig_val
    orig_val="$(cat "$justfile")"
    rm -f "$justfile"
    echo "$orig_val" > "$justfile"

    cat << 'EOF' >> "$justfile"

fake-double-config-bib:
    sudo podman run -v ./c1.toml:/config.toml:ro -v ./c2.toml:/config.toml:ro {{BIB}}
EOF

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_bib_single_config_invariant >/dev/null 2>&1; then
        rm -f "$justfile"
        echo "$orig_val" > "$justfile"
        die "Check_bib_single_config_invariant passed despite double config mount"
    fi

    rm -f "$justfile"
    echo "$orig_val" > "$justfile"
    _neg_gate check_bib_single_config_invariant || die "Check_bib_single_config_invariant failed after restoration"
    log "Test_bib_single_config_invariant negative test passed"
}

test_artifact_recipes() {
    log "Testing check_artifact_recipes"
    local recipe="${ROOT}/config/artifacts/bib.toml" bak entry
    bak="$(mktemp)"; cp "$recipe" "$bak"
    # The placeholder hash vhdx.toml shipped, then a real-looking key with no REPLACE token.
    for entry in 'password = "$6$REPLACEME_WITH_SHA512_HASH$REPLACEME"' \
                 'key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAInegativetestbody neg@test"'; do
        printf '\n[[customizations.user]]\nname = "neg"\n%s\n' "$entry" >> "$recipe"
        _neg_gate check_artifact_recipes && { cp "$bak" "$recipe"; rm -f "$bak"; die "check_artifact_recipes passed with a committed ${entry%% *} in a recipe"; }
        cp "$bak" "$recipe"
    done
    rm -f "$bak"
    _neg_gate check_artifact_recipes || die "check_artifact_recipes failed after restoration: ${_NEG_GATE_OUT}"
    log "check_artifact_recipes negative test passed"
}

test_chpasswd_plaintext() {
    log "Testing mios-hardcode-lint plaintext chpasswd"
    local autorun_script="${ROOT}/usr/share/mios/ventoy/autorun/01-sysrescue-firstboot.sh"
    local bak_file="${autorun_script}.bak"
    cp "$autorun_script" "$bak_file"

    echo 'echo "Root:hardcodedpass" | chpasswd' >> "$autorun_script"

    python3 "${ROOT}/usr/libexec/mios/mios-hardcode-lint" "${ROOT}" >/dev/null 2>&1 && die "Mios-hardcode-lint passed despite plaintext chpasswd injection"

    cp "$bak_file" "$autorun_script" && rm -f "$bak_file"
    python3 "${ROOT}/usr/libexec/mios/mios-hardcode-lint" "${ROOT}" >/dev/null 2>&1 \
        || die "Mios-hardcode-lint failed after restoration"
    log "Test_chpasswd_plaintext negative test passed"
}

test_hardcode_lint_anchored_allowlist() {
    log "Testing mios-hardcode-lint anchored allowlist matching"
    local probe="${ROOT}/usr/bin/test_anchored_probe.sh"
    echo 'x="http://localhost:8080"' > "$probe"

    python3 "${ROOT}/usr/libexec/mios/mios-hardcode-lint" "${ROOT}" >/dev/null 2>&1 && {
        rm -f "$probe"
        die "Mios-hardcode-lint passed despite localhost:8080 when allowlist has localhost:80"
    }

    rm -f "$probe"
    log "Test_hardcode_lint_anchored_allowlist passed"
}

test_build_artifacts_output_dir() {
    log "Testing check_build_artifacts_output_dir"
    local justfile="${ROOT}/Justfile"
    local orig_val
    orig_val="$(cat "$justfile")"
    rm -f "$justfile"
    echo "$orig_val" > "$justfile"

    cat << 'EOF' >> "$justfile"

fake-non-ssot-recipe:
    mkdir -p output/stray
EOF

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_build_artifacts_output_dir >/dev/null 2>&1; then
        rm -f "$justfile"
        echo "$orig_val" > "$justfile"
        die "Check_build_artifacts_output_dir passed despite stray output/ path"
    fi

    rm -f "$justfile"
    echo "$orig_val" > "$justfile"
    _neg_gate check_build_artifacts_output_dir || die "Check_build_artifacts_output_dir failed after restoration"
    log "Test_build_artifacts_output_dir negative test passed"
}

test_win11_vm_template_xml() {
    log "Testing check_win11_vm_template_xml"
    local xml_file="${ROOT}/tools/win11-secureboot-template.xml"
    local orig_val
    orig_val="$(cat "$xml_file")"
    rm -f "$xml_file"
    echo "$orig_val" > "$xml_file"

    echo '<invalid_xml>unclosed tag' >> "$xml_file"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_win11_vm_template_xml >/dev/null 2>&1; then
        rm -f "$xml_file"
        echo "$orig_val" > "$xml_file"
        die "Check_win11_vm_template_xml passed despite invalid XML"
    fi

    rm -f "$xml_file"
    echo "$orig_val" > "$xml_file"
    _neg_gate check_win11_vm_template_xml || die "Check_win11_vm_template_xml failed after restoration"
    log "Test_win11_vm_template_xml negative test passed"
}

_restore_service_projection() (
    source "$ROOT/automation/lib/common.sh"
    mios_project_config "$ROOT" "$1"
)

test_ipa_enroll_projection() {
    log "Testing check_ipa_enroll_projection"
    local target_file="${ROOT}/etc/mios/ipa-enroll.env"
    [[ -f "$target_file" ]] || { mkdir -p "$(dirname "$target_file")"; _restore_service_projection ipa-enroll >/dev/null 2>&1 || die "Native ipa-enroll regeneration failed"; }

    echo 'MIOS_IPA_REALM="MUTATED.REALM"' >> "$target_file"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_ipa_enroll_projection >/dev/null 2>&1; then
        _restore_service_projection ipa-enroll >/dev/null 2>&1 || die "Native ipa-enroll regeneration failed"
        die "Check_ipa_enroll_projection passed despite mutated target file"
    fi

    _restore_service_projection ipa-enroll >/dev/null 2>&1 || die "Native ipa-enroll regeneration failed"
    _neg_gate check_ipa_enroll_projection || die "Check_ipa_enroll_projection failed after restoration"
    log "Test_ipa_enroll_projection negative test passed"
}

test_bootc_install_projection() {
    log "Testing check_bootc_install_projection"
    local target_file="${ROOT}/usr/lib/bootc/install/00-mios.toml"
    _restore_service_projection bootc-install >/dev/null 2>&1 || die "Native bootc-install regeneration failed"

    sed -i 's/^root-fs-type = .*/root-fs-type = "xfs-mutated"/' "$target_file"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_bootc_install_projection >/dev/null 2>&1; then
        _restore_service_projection bootc-install >/dev/null 2>&1 || die "Native bootc-install regeneration failed"
        die "Check_bootc_install_projection passed despite mutated install config"
    fi

    _restore_service_projection bootc-install >/dev/null 2>&1 || die "Native bootc-install regeneration failed"
    _neg_gate check_bootc_install_projection || die "Check_bootc_install_projection failed after restoration"
    log "Test_bootc_install_projection negative test passed"
}

test_uki_cmdline_projection() {
    log "Testing check_uki_cmdline_projection"
    local target_file="${ROOT}/usr/lib/kernel/cmdline" corrupt_dropin="${ROOT}/usr/lib/bootc/kargs.d/99-test-corrupt.toml" unit_gen="" candidate
    for candidate in "$ROOT/tools/native/target/release/mios-unit-gen" "$ROOT/tools/native/target/debug/mios-unit-gen" /usr/libexec/mios/mios-unit-gen; do
        [[ -n "$candidate" && -x "$candidate" ]] && { unit_gen="$candidate"; break; }
    done
    [[ -n "$unit_gen" ]] || die "mios-unit-gen is required for the UKI negative control"
    [[ -f "$target_file" ]] || { mkdir -p "$(dirname "$target_file")"; "$unit_gen" uki-cmdline --root "$ROOT" >/dev/null 2>&1 || true; }
    echo 'kargs = [ unclosed' > "$corrupt_dropin"
    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_uki_cmdline_projection >/dev/null 2>&1; then
        rm -f "$corrupt_dropin"; die "Check_uki_cmdline_projection passed despite corrupt drop-in"
    fi
    rm -f "$corrupt_dropin"
    _neg_gate check_uki_cmdline_projection || die "Check_uki_cmdline_projection failed after removing corrupt drop-in"
    echo 'mutated_bogus_karg=1' >> "$target_file"
    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_uki_cmdline_projection >/dev/null 2>&1; then
        "$unit_gen" uki-cmdline --root "$ROOT" >/dev/null 2>&1 || true; die "Check_uki_cmdline_projection passed despite mutated cmdline"
    fi
    "$unit_gen" uki-cmdline --root "$ROOT" >/dev/null 2>&1 || true
    _neg_gate check_uki_cmdline_projection || die "Check_uki_cmdline_projection failed after restoration"
    log "Test_uki_cmdline_projection negative test passed"
}

test_composefs_projection() {
    log "Testing check_composefs_projection"
    local target_file="${ROOT}/usr/lib/ostree/prepare-root.conf"
    local orig_val
    orig_val="$(cat "$target_file")"
    echo "$orig_val" > "$target_file"

    echo '[composefs]' > "$target_file"
    echo 'enabled = off' >> "$target_file"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_composefs_projection >/dev/null 2>&1; then
        echo "$orig_val" > "$target_file"
        die "Check_composefs_projection passed despite mutated prepare-root.conf"
    fi

    echo "$orig_val" > "$target_file"
    _neg_gate check_composefs_projection || die "Check_composefs_projection failed after restoration"
    log "Test_composefs_projection negative test passed"
}

test_cockpit_projection() {
    log "Testing check_cockpit_projection"
    local target_file="${ROOT}/etc/cockpit/cockpit.conf"
    [[ -f "$target_file" ]] || { mkdir -p "$(dirname "$target_file")"; _restore_service_projection cockpit >/dev/null 2>&1 || die "Native cockpit regeneration failed"; }

    echo 'AllowUnencrypted = false' >> "$target_file"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_cockpit_projection >/dev/null 2>&1; then
        _restore_service_projection cockpit >/dev/null 2>&1 || die "Native cockpit regeneration failed"
        die "Check_cockpit_projection passed despite mutated cockpit.conf"
    fi

    _restore_service_projection cockpit >/dev/null 2>&1 || die "Native cockpit regeneration failed"
    _neg_gate check_cockpit_projection || die "Check_cockpit_projection failed after restoration"
    log "Test_cockpit_projection negative test passed"
}

test_chrony_ptp_dropin() {
    log "Testing check_chrony_ptp_dropin"
    local dropin_script="${ROOT}/usr/libexec/mios/mios-chrony-ptp-dropin"
    local orig_val
    orig_val="$(cat "$dropin_script")"
    rm -f "$dropin_script"
    echo "$orig_val" > "$dropin_script"

    echo 'syntax error ((((' >> "$dropin_script"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_chrony_ptp_dropin >/dev/null 2>&1; then
        rm -f "$dropin_script"
        echo "$orig_val" > "$dropin_script"
        die "Check_chrony_ptp_dropin passed despite syntax error"
    fi

    rm -f "$dropin_script"
    echo "$orig_val" > "$dropin_script"
    _neg_gate check_chrony_ptp_dropin || die "Check_chrony_ptp_dropin failed after restoration"
    log "Test_chrony_ptp_dropin negative test passed"
}

test_chrony_projection() {
    log "Testing check_chrony_projection"
    local target_file="${ROOT}/etc/chrony.conf"
    local orig_val
    orig_val="$(cat "$target_file")"
    rm -f "$target_file"
    echo "$orig_val" > "$target_file"

    echo "Server 199.99.99.99 iburst" >> "$target_file"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_chrony_projection >/dev/null 2>&1; then
        rm -f "$target_file"
        echo "$orig_val" > "$target_file"
        die "Check_chrony_projection passed despite mutated chrony.conf"
    fi

    rm -f "$target_file"
    echo "$orig_val" > "$target_file"
    _neg_gate check_chrony_projection || die "Check_chrony_projection failed after restoration"
    log "Test_chrony_projection negative test passed"
}

test_nut_projection() {
    log "Testing check_nut_projection"
    local target_file="${ROOT}/etc/ups/ups.conf"
    local orig_val
    orig_val="$(cat "$target_file")"
    rm -f "$target_file"
    echo "$orig_val" > "$target_file"

    echo "Driver = bogus" >> "$target_file"

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_nut_projection >/dev/null 2>&1; then
        rm -f "$target_file"
        echo "$orig_val" > "$target_file"
        die "Check_nut_projection passed despite mutated ups.conf"
    fi

    rm -f "$target_file"
    echo "$orig_val" > "$target_file"
    _neg_gate check_nut_projection || die "Check_nut_projection failed after restoration"
    log "Test_nut_projection negative test passed"
}

test_renderer_gate_coverage() {
    log "Testing check_renderer_gate_coverage"
    local bogus_script="${ROOT}/automation/99-bogus-render.sh"
    echo '#!/usr/bin/env bash' > "$bogus_script"
    echo 'echo "Bogus render"' >> "$bogus_script"
    chmod +x "$bogus_script"

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_renderer_gate_coverage >/dev/null 2>&1 && die "Check_renderer_gate_coverage passed despite unmapped 99-bogus-render.sh"

    rm -f "$bogus_script"
    _neg_gate check_renderer_gate_coverage || die "Check_renderer_gate_coverage failed after cleanup"
    log "Test_renderer_gate_coverage negative test passed"
}

test_clevis_luks() {
    log "Testing check_clevis_luks"
    local tmp_dir
    tmp_dir="$(mktemp -d)"
    mkdir -p "${tmp_dir}/usr/libexec/mios"
    printf '#!/bin/sh\necho "CLEVIS_BROKEN=true"\n' > "${tmp_dir}/usr/libexec/mios/mios-clevis-luks-gen"
    chmod +x "${tmp_dir}/usr/libexec/mios/mios-clevis-luks-gen" 2>/dev/null || true

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$tmp_dir" MIOS_DRIFT_CHECK_ROOT="$tmp_dir" bash "${ROOT}/automation/98-drift-checks.sh" check_clevis_luks >/dev/null 2>&1 && die "Check_clevis_luks passed despite broken generator output"

    rm -rf "$tmp_dir"
    _neg_gate check_clevis_luks || die "Check_clevis_luks failed after cleanup"
    log "Test_clevis_luks negative test passed"
}

# The broken-generator case above is a TOOL-side probe, and a gate can pass one
# of those while staying blind to the SSOT. These two cases edit mios.toml.
test_clevis_luks_ssot() {
    log "Testing check_clevis_luks against SSOT-side violations"
    local toml="${ROOT}/usr/share/mios/mios.toml" bak="${ROOT}/usr/share/mios/mios.toml.clevisbak"
    cp "$toml" "$bak"
    sed -i '/^\[security\.luks\]$/,/^pcrs/ s/^enabled = false$/enabled = true/' "$toml"
    cmp -s "$toml" "$bak" && die "fixture did not apply: [security.luks].enabled = false not found"
    _neg_gate check_clevis_luks && die "check_clevis_luks passed with [security.luks].enabled flipped and etc/mios/clevis-luks.env not re-projected"
    cp "$bak" "$toml"
    _neg_gate check_clevis_luks || die "check_clevis_luks failed after restoring the flipped key"
    sed -i '/^\[security\.luks\]$/,/^pcrs/d' "$toml"
    cmp -s "$toml" "$bak" && die "fixture did not apply: the [security.luks] table was not deleted"
    _neg_gate check_clevis_luks && die "check_clevis_luks passed with the [security.luks] table deleted from the SSOT"
    cp "$bak" "$toml" && rm -f "$bak"
    _neg_gate check_clevis_luks || die "check_clevis_luks failed after restoring the SSOT table"
    log "Test_clevis_luks_ssot negative test passed"
}

test_metal_vfio() {
    log "Testing check_metal_vfio"
    local tmp_dir
    tmp_dir="$(mktemp -d)"
    mkdir -p "${tmp_dir}/usr/libexec/mios"
    printf '#!/bin/sh\necho "METAL_BROKEN=true"\n' > "${tmp_dir}/usr/libexec/mios/mios-metal-vfio-gen"
    chmod +x "${tmp_dir}/usr/libexec/mios/mios-metal-vfio-gen" 2>/dev/null || true

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$tmp_dir" MIOS_DRIFT_CHECK_ROOT="$tmp_dir" bash "${ROOT}/automation/98-drift-checks.sh" check_metal_vfio >/dev/null 2>&1 && die "Check_metal_vfio passed despite broken generator output"

    rm -rf "$tmp_dir"
    _neg_gate check_metal_vfio || die "Check_metal_vfio failed after cleanup"
    log "Test_metal_vfio negative test passed"
}

test_target_languages() {
    log "Testing check_target_languages"
    local bogus_file="${ROOT}/usr/libexec/mios/bogus_script.cpp"
    echo '// forbidden c++ file' > "$bogus_file"

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_target_languages >/dev/null 2>&1 && die "Check_target_languages passed despite forbidden C++ file"

    rm -f "$bogus_file"
    _neg_gate check_target_languages || die "Check_target_languages failed after cleanup"
    log "Test_target_languages negative test passed"
}

test_roadmap_index() {
    log "Testing check_roadmap_index"
    local roadmap_file="${ROOT}/ROADMAP.md"
    [[ -f "$roadmap_file" ]] || return 0
    local bak_file="${roadmap_file}.bak"
    cp "$roadmap_file" "$bak_file"
    sed -i 's/\*\*Done\*\*: [0-9]*/\*\*Done\*\*: 99999/g' "$roadmap_file" 2>/dev/null || true
    bash "${ROOT}/automation/98-drift-checks.sh" check_roadmap_index >/dev/null 2>&1 && die "Check_roadmap_index passed despite corrupted rollup"
    cp "$bak_file" "$roadmap_file" && rm -f "$bak_file"
    _neg_gate check_roadmap_index || die "Check_roadmap_index failed after restoration"
    log "Test_roadmap_index negative test passed"
}

test_templates_compilation() {
    log "Testing check_templates_compilation"
    local tmpl_file="${ROOT}/usr/share/mios/templates/toml-config"
    [[ -f "$tmpl_file" ]] || return 0
    local bak_file="${tmpl_file}.bak"
    cp "$tmpl_file" "$bak_file"
    echo 'INVALID_SYNTAX_BOGUS {{' >> "$tmpl_file"
    bash "${ROOT}/automation/98-drift-checks.sh" check_templates_compilation >/dev/null 2>&1 && die "Check_templates_compilation passed despite invalid template"
    cp "$bak_file" "$tmpl_file" && rm -f "$bak_file"
    _neg_gate check_templates_compilation || die "Check_templates_compilation failed after restoration"
    log "Test_templates_compilation negative test passed"
}

test_template_conformance() {
    log "Testing check_template_conformance"
    local toml_file="${ROOT}/usr/share/mios/mios.toml"
    [[ -f "$toml_file" ]] || return 0
    local bak_file="${toml_file}.bak"
    cp "$toml_file" "$bak_file"
    sed -i 's/^\[templates\./[xtemplates./' "$toml_file"
    if bash "${ROOT}/automation/98-drift-checks.sh" check_template_conformance >/dev/null 2>&1; then
        cp "$bak_file" "$toml_file" && rm -f "$bak_file"
        die "check_template_conformance passed despite [templates] renamed to [xtemplates]"
    fi
    cp "$bak_file" "$toml_file" && rm -f "$bak_file"
    _neg_gate check_template_conformance || die "check_template_conformance failed after restoration"
    log "test_template_conformance negative test passed"
}

test_impossible_eol() {
    log "Testing check_impossible_eol_regressions"
    local toml_file="${ROOT}/usr/share/mios/mios.toml"
    if [ -f "$toml_file" ]; then
        local bak_file="${toml_file}.bak"
        cp "$toml_file" "$bak_file"
        echo 'eol_test_pkg = ["tang"]' >> "$toml_file"

        MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_impossible_eol_regressions >/dev/null 2>&1 && die "Check_impossible_eol_regressions passed despite EOL tang package"

        cp "$bak_file" "$toml_file" && rm -f "$bak_file"
        _neg_gate check_impossible_eol_regressions || die "Check_impossible_eol_regressions failed after restoration"
    fi
    log "Test_impossible_eol negative test passed"
}

test_smoke_manifest() {
    log "Testing check_smoke_manifest"
    local toml_file="${ROOT}/usr/share/mios/mios.toml" bak_file out
    [ -f "$toml_file" ] || die "check_smoke_manifest has no SSOT to plant into"
    bak_file="${toml_file}.bak"
    _smoke() {
        MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" \
            bash "${ROOT}/automation/98-drift-checks.sh" check_smoke_manifest 2>&1
    }
    cp "$toml_file" "$bak_file"

    # 1. An overlay naming no [packages] section can never be selected (task_3464).
    # Appending a NEW sub-table keeps the TOML valid, so a red result is the
    # check's verdict and not a parse error.
    printf '\n[testing.smoke_components.sections.zz-planted]\ncommands = ["x"]\n' >> "$toml_file"
    out="$(_smoke)" && { cp "$bak_file" "$toml_file"; die "check_smoke_manifest passed an overlay naming no [packages] section"; }
    grep -q "sections.zz-planted\] names 'zz-planted', no \[packages\] section" <<<"$out" \
        || { cp "$bak_file" "$toml_file"; die "check_smoke_manifest failed without naming zz-planted: $out"; }
    cp "$bak_file" "$toml_file"

    # 2. A floor component absent from the source tree.
    sed -i 's|^shims = \[$|shims = [\n    "usr/libexec/mios/non-existent-bogus-shim",|' "$toml_file"
    out="$(_smoke)" && { cp "$bak_file" "$toml_file"; die "check_smoke_manifest passed despite missing component path"; }
    grep -q 'non-existent-bogus-shim' <<<"$out" \
        || { cp "$bak_file" "$toml_file"; die "check_smoke_manifest failed without naming the missing shim: $out"; }

    cp "$bak_file" "$toml_file" && rm -f "$bak_file"
    _smoke >/dev/null || die "check_smoke_manifest failed after restoration"
    unset -f _smoke
    log "Test_smoke_manifest negative test passed"
}

test_verb_templates() {
    log "Testing check_verb_templates"
    local toml_file="${ROOT}/usr/share/mios/mios.toml"
    if [ -f "$toml_file" ]; then
        local orig_val
        orig_val="$(cat "$toml_file"; printf X)"
        printf '%s' "${orig_val%X}" > "$toml_file"
        printf '\n[verbs.bogus_broken]\ncmd = "echo {invalid_placeholder"\n' >> "$toml_file"

        if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_verb_templates >/dev/null 2>&1; then
            printf '%s' "${orig_val%X}" > "$toml_file"
            die "Check_verb_templates passed despite invalid verb template"
        fi

        printf '%s' "${orig_val%X}" > "$toml_file"
        _neg_gate check_verb_templates || die "Check_verb_templates failed after restoration"
    fi
    log "Test_verb_templates negative test passed"
}

_neg_pod_projection_controls() (
    local mode="$1" gen fixture source output
    gen="$(native_bin mios-gen)" || die "mios-gen is required for native Quadlet controls"
    fixture="$(mktemp -d)"
    trap 'rm -rf "$fixture"' EXIT
    mkdir -p "$fixture/usr/share/mios" "$fixture/empty"
    source="$fixture/usr/share/mios/mios.toml"
    export MIOS_VENDOR_TOML="$source" MIOS_VENDOR_TOML_D="$fixture/empty"
    export MIOS_HOST_TOML="$fixture/absent" MIOS_HOST_TOML_D="$fixture/empty"
    export MIOS_USER_TOML="$fixture/absent" MIOS_USER_TOML_D="$fixture/empty"
    export MIOS_POD_OUT="$fixture/units"
    cat > "$source" <<'TOML'
[build.bake]
additional_image_store = "/usr/lib/bootc/storage"
firstboot_tokens = ["floating"]
[containers.zz-planted.Container]
Image = "example/core"
User = "825"
Group = "825"
GlobalArgs = ["--log-level=debug"]
TOML
    cp "$source" "$fixture/original"
    _pod_render() { "$gen" pod-quadlets --root "$fixture"; }
    _pod_reject() {
        if output="$(_pod_render 2>&1)"; then
            die "mios-gen pod-quadlets accepted $1"
        fi
        [[ "$output" == *"$2"* ]] || die "mios-gen failed $1 without its expected diagnosis: $output"
        cp "$fixture/original" "$source"
    }
    _pod_render || die "mios-gen failed on the unmutated Quadlet fixture"
    grep -Fq -- '--log-level=debug' "$MIOS_POD_OUT/zz-planted.container" || die "generator dropped unrelated GlobalArgs"
    grep -Fq -- '--storage-opt=additionalimagestore=/usr/lib/bootc/storage' "$MIOS_POD_OUT/zz-planted.container" || die "generator omitted the bound image store"
    cp "$MIOS_POD_OUT/zz-planted.container" "$fixture/rendered"
    _pod_render || die "mios-gen failed on a repeated projection"
    cmp -s "$fixture/rendered" "$MIOS_POD_OUT/zz-planted.container" || die "Quadlet projection is not idempotent"
    case "$mode" in
        privilege)
            sed -i '/^User =/d; /^Group =/d' "$source"
            _pod_reject 'missing User=/Group=' "Container 'zz-planted' declares no User=/Group="
            ;;
        bound-store)
            sed -i 's|GlobalArgs = .*|GlobalArgs = ["--storage-opt=additionalimagestore=/other"]|' "$source"
            _pod_reject 'conflicting store' 'conflicting'
            sed -i 's|GlobalArgs = .*|GlobalArgs = ["--storage-opt=additionalimagestore=/usr/lib/bootc/storage", "--storage-opt=additionalimagestore=/usr/lib/bootc/storage"]|' "$source"
            _pod_reject 'duplicate store' 'conflicting'
            sed -i 's|GlobalArgs = .*|GlobalArgs = false|' "$source"
            _pod_reject 'malformed GlobalArgs' 'GlobalArgs must'
            sed -i 's|Image = .*|Image = "example/floating"|; s|GlobalArgs = .*|GlobalArgs = ["--storage-opt=additionalimagestore=/usr/lib/bootc/storage"]|' "$source"
            _pod_reject 'firstboot image store' 'firstboot image'
            sed -i 's|additional_image_store = .*|additional_image_store = false|' "$source"
            _pod_reject 'non-string store' 'additional_image_store must be an absolute path'
            sed -i 's|firstboot_tokens = .*|firstboot_tokens = false|' "$source"
            _pod_reject 'non-array firstboot tokens' 'firstboot_tokens must be a string array'
            ;;
        *) die "Unknown native Quadlet control: $mode" ;;
    esac
    _pod_render || die "mios-gen failed after Quadlet fixture restoration"
    cmp -s "$fixture/rendered" "$MIOS_POD_OUT/zz-planted.container" || die "restored Quadlet differs from the positive control"
    log "Native Quadlet $mode controls passed with idempotence and restoration"
)

test_bound_image_store() {
    log "Testing check_bound_image_store"
    _neg_gate check_bound_image_store || die "check_bound_image_store failed on the unmutated tree"
    python3 - "$ROOT/tools/test_drift-checks.py" <<'PYEOF'
import importlib.util, sys, unittest
spec = importlib.util.spec_from_file_location('bound_store_controls', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
loader = unittest.TestLoader()
suite = loader.loadTestsFromTestCase(module.TestBoundImageStore)
assert suite.countTestCases() >= 9, 'bound-image controls are missing'
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful() or bool(result.skipped))
PYEOF
    _neg_pod_projection_controls bound-store
    _neg_gate check_bound_image_store || die "check_bound_image_store failed after isolated controls"
    log "check_bound_image_store scoped store and binding controls passed"
}

test_pipe_boundaries() (
    log "Testing check_pipe_boundaries"
    local manifest="${ROOT}/usr/share/mios/pipe-boundaries.manifest.json"
    [[ -f "$manifest" ]] || die "Tracked boundary manifest is missing"
    _neg_gate check_pipe_boundaries || die "check_pipe_boundaries failed on the unmutated tree"
    local backup
    backup="$(mktemp)"
    cp "$manifest" "$backup"
    trap 'cp "$backup" "$manifest"; rm -f "$backup"' EXIT
    rm -f "$manifest"
    _neg_gate check_pipe_boundaries && die "check_pipe_boundaries accepted a missing manifest"
    [[ "$_NEG_GATE_OUT" == *"pipe-boundaries.manifest.json is missing"* ]] || die "Missing boundary manifest was not diagnosed"
    cp "$backup" "$manifest"
    printf '\n' >> "$manifest"
    _neg_gate check_pipe_boundaries && die "check_pipe_boundaries accepted stale content"
    [[ "$_NEG_GATE_OUT" == *"STALE:"* ]] || die "Stale boundary manifest was not diagnosed"
    cp "$backup" "$manifest"
    _neg_gate check_pipe_boundaries || die "check_pipe_boundaries failed after restoration"
    log "check_pipe_boundaries missing and stale manifest controls passed"
)

test_standardize_docs() (
    log "Testing check_standardize_docs"
    local spec="${ROOT}/specs/engineering/2026-04-26-Artifact-ENG-002-Scripts-Index.md"
    [[ -f "$spec" ]] || die "Tracked spec file is missing"
    _neg_gate check_standardize_docs || die "check_standardize_docs failed on the unmutated tree"
    local backup
    backup="$(mktemp)"
    cp "$spec" "$backup"
    trap 'cp "$backup" "$spec"; rm -f "$backup"' EXIT
    printf '\n<!-- unstandardized corrupted line -->\n' >> "$spec"
    _neg_gate check_standardize_docs && die "check_standardize_docs accepted unstandardized content"
    [[ "$_NEG_GATE_OUT" == *"UNSTANDARDIZED"* ]] || die "Unstandardized spec was not diagnosed"
    cp "$backup" "$spec"
    _neg_gate check_standardize_docs || die "check_standardize_docs failed after restoration"
    log "check_standardize_docs unstandardized spec control passed"
)

test_sync_wiki() (
    log "Testing check_sync_wiki"
    local spec="${ROOT}/specs/engineering/2026-04-26-Artifact-ENG-002-Scripts-Index.md"
    [[ -f "$spec" ]] || die "Tracked spec file is missing"
    _neg_gate check_sync_wiki || die "check_sync_wiki failed on the unmutated tree"
    local backup
    backup="$(mktemp)"
    cp "$spec" "$backup"
    trap 'cp "$backup" "$spec"; rm -f "$backup"' EXIT
    sed -i 's/"version": "0.3.0"/"version": "0.0.1"/' "$spec"
    _neg_gate check_sync_wiki && die "check_sync_wiki accepted stale version"
    [[ "$_NEG_GATE_OUT" == *"STALE"* ]] || die "Stale wiki embeds was not diagnosed"
    cp "$backup" "$spec"
    _neg_gate check_sync_wiki || die "check_sync_wiki failed after restoration"
    log "check_sync_wiki stale version control passed"
)

test_vllm_name_canonical() {
    log "Testing check_vllm_name_canonical"
    local dummy="${ROOT}/usr/lib/mios/dummy_vllm_negative_test.sh"
    echo 'MIOS_AI_VLLM_SERVED_NAME="test"' > "$dummy"

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_vllm_name_canonical >/dev/null 2>&1 && die "Check_vllm_name_canonical passed despite legacy long name"

    rm -f "$dummy"
    _neg_gate check_vllm_name_canonical || die "Check_vllm_name_canonical failed after restoration"

    log "Test_vllm_name_canonical negative test passed"
}

test_pipe_extraction_parity() {
    log "Testing check_pipe_extraction_parity"
    local test_file="${ROOT}/usr/lib/mios/agent-pipe/mios_pipe/observability/session_events.py"
    if [ -f "$test_file" ]; then
        local orig_val
        orig_val="$(cat "$test_file")"
        printf '%s\n%s\n' "$orig_val" "import server" > "$test_file"

        if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_pipe_extraction_parity >/dev/null 2>&1; then
            echo "$orig_val" > "$test_file"
            die "Check_pipe_extraction_parity passed despite forbidden import server"
        fi

        echo "$orig_val" > "$test_file"
        _neg_gate check_pipe_extraction_parity || die "Check_pipe_extraction_parity failed after restoration"
    fi
    log "Test_pipe_extraction_parity negative test passed"
}

test_bake_plan() (
    log "Testing check_bake_plan"
    local plan_file
    plan_file="$(find "${ROOT}/usr/lib/mios/bake/plan.d" -maxdepth 1 -name '[0-9][0-9]-extra.list' -print -quit 2>/dev/null)"
    [[ -n "$plan_file" && -f "$plan_file" ]] || die "No extra bake plan exists for the stale-plan control"
    local bak_file; bak_file="$(mktemp)"
    cp "$plan_file" "$bak_file"
    trap 'cp "$bak_file" "$plan_file"; rm -f "$bak_file"' EXIT
    _neg_gate check_bake_plan || die "check_bake_plan failed before stale-plan controls"
    echo "docker.io/library/bogus-image-never-exists:latest" >> "$plan_file"
    _neg_gate check_bake_plan && die "check_bake_plan accepted a stale bake plan"
    [[ "$_NEG_GATE_OUT" == *"STALE"* && "$_NEG_GATE_OUT" == *"$(basename "$plan_file")"* ]] \
        || die "check_bake_plan failed without naming the stale plan"
    cp "$bak_file" "$plan_file"
    _neg_gate check_bake_plan || die "check_bake_plan failed after stale-plan restoration"
    log "Test_bake_plan negative test passed"
)

test_bake_ref_defaults() {
    log "Testing check_bake_ref_defaults"
    local test_sh="${ROOT}/automation/34-render-quadlets.sh"
    if [ -f "$test_sh" ]; then
        local orig_val
        orig_val="$(cat "$test_sh")"
        printf '%s\n%s\n' "$orig_val" ': "${MIOS_BUILD_BAKE_REFS_ZZZ:-}"' > "$test_sh"

        if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_bake_ref_defaults >/dev/null 2>&1; then
            echo "$orig_val" > "$test_sh"
            die "Check_bake_ref_defaults passed despite empty bake ref default"
        fi

        echo "$orig_val" > "$test_sh"
        _neg_gate check_bake_ref_defaults || die "Check_bake_ref_defaults failed after restoration"
    fi
    log "Test_bake_ref_defaults negative test passed"
}

test_deploy_plane() {
    log "Testing check_deploy_plane"
    local cfg="${ROOT}/usr/share/mios/ventoy/mios-kickstart.cfg"
    if [ -f "$cfg" ]; then
        local orig_val
        orig_val="$(cat "$cfg")"
        echo "$orig_val" > "$cfg"
        grep -v "MIOS_FHS_TOTAL_ROOT_MERGE=1" "$cfg" > "${cfg}.tmp" && mv "${cfg}.tmp" "$cfg"

        if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_deploy_plane >/dev/null 2>&1; then
            echo "$orig_val" > "$cfg"
            die "Check_deploy_plane passed despite missing MIOS_FHS_TOTAL_ROOT_MERGE=1"
        fi

        echo "$orig_val" > "$cfg"
        _neg_gate check_deploy_plane || die "Check_deploy_plane failed after restoration"
    fi
    log "Test_deploy_plane negative test passed"
}

test_sbom_metadata() {
    log "Testing check_sbom_metadata"
    local sbom_file="${ROOT}/usr/share/mios/artifacts/sbom/models.tsv"
    local dir
    dir="$(dirname "$sbom_file")"
    mkdir -p "$dir"
    printf "name\tversion\tsha256\turl\nmodel1\t1.0\tdeadbeef\thttps://example.com\n" > "$sbom_file"

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_sbom_metadata >/dev/null 2>&1 && die "Check_sbom_metadata passed despite invalid sha256"

    rm -f "$sbom_file"
    _neg_gate check_sbom_metadata || die "Check_sbom_metadata failed after restoration"
    log "Test_sbom_metadata negative test passed"
}

test_negative_coverage() {
    log "Testing check_negative_coverage"
    local checks_sh="${ROOT}/automation/98-drift-checks.sh"
    if [ -f "$checks_sh" ]; then
        local orig_val
        orig_val="$(cat "$checks_sh")"
        echo "$orig_val" > "$checks_sh"
        sed -i 's/check_pipe_extraction_parity/check_pipe_extraction_parity\n    check_bogus_uncovered_gate/' "$checks_sh"

        if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_negative_coverage >/dev/null 2>&1; then
            echo "$orig_val" > "$checks_sh"
            die "Check_negative_coverage passed despite uncovered gate"
        fi

        echo "$orig_val" > "$checks_sh"
        _neg_gate check_negative_coverage || die "Check_negative_coverage failed after restoration"
    fi
    log "Test_negative_coverage negative test passed"
}

test_guacamole_consistency() {
    log "Testing check_guacamole_consistency"
    local desktop_file="${ROOT}/usr/share/applications/mios-svc-guacamole.desktop"
    local orig_val
    orig_val="$(cat "$desktop_file")"

    # Port-agnostic injection: hardcoding the current port here means the test
    # silently stops injecting anything the moment [ports].guacamole_web moves,
    # and then "passes" for the wrong reason.
    sed -i -E 's#(localhost:)[0-9]+#\19999#g' "$desktop_file"

    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_guacamole_consistency >/dev/null 2>&1; then
        echo "$orig_val" > "$desktop_file"
        die "Check_guacamole_consistency passed despite port mismatch violation"
    fi

    echo "$orig_val" > "$desktop_file"
    _neg_gate check_guacamole_consistency || die "Check_guacamole_consistency failed after restoration"
    log "Test_guacamole_consistency negative test passed"
}

test_cephfs_ssot() {
    log "Testing check_cephfs_ssot"
    local toml_file="${ROOT}/usr/share/mios/mios.toml"
    local tmpl="${ROOT}/usr/share/mios/systemd/home-@.mount.tmpl"
    local auto="${ROOT}/usr/share/mios/systemd/home-@.automount.tmpl"
    local tbak mbak abak; tbak="$(mktemp)"; mbak="$(mktemp)"; abak="$(mktemp)"
    cp "$toml_file" "$tbak"; cp "$tmpl" "$mbak"; cp "$auto" "$abak"
    _cs_fail() { cp "$tbak" "$toml_file"; cp "$mbak" "$tmpl"; cp "$abak" "$auto"; rm -f "$tbak" "$mbak" "$abak"; unset -f _cs_fail; die "$1"; }

    sed -i 's/^mount_options                   = "noatime,fsc,_netdev"/# mount_options removed/' "$toml_file"
    cmp -s "$tbak" "$toml_file" && _cs_fail "the mount_options plant landed nowhere -- [storage.cephfs] moved"
    _neg_gate check_cephfs_ssot && _cs_fail "Check_cephfs_ssot passed despite missing mount_options key"
    [[ "$_NEG_GATE_OUT" == *"no corresponding key 'mount_options'"* ]] || _cs_fail "Check_cephfs_ssot failed without naming mount_options: $_NEG_GATE_OUT"
    cp "$tbak" "$toml_file"

    # Unrecognised tokens must read as nothing compared, never clean: the 0023c51d
    # rename hid the plant above that way. Lower-casing the prefix re-enacts it.
    local pfx; pfx="MIOS_$(printf '%s' STORAGE_CEPHFS_)"
    sed -i "s/{${pfx}/{${pfx,,}/g" "$tmpl" "$auto"
    _neg_gate check_cephfs_ssot && _cs_fail "Check_cephfs_ssot passed with no recognisable template token"
    [[ "$_NEG_GATE_OUT" == *"so nothing was compared"* ]] || _cs_fail "Check_cephfs_ssot failed the zero-token plant for the wrong reason: $_NEG_GATE_OUT"

    cp "$mbak" "$tmpl"; cp "$abak" "$auto"; rm -f "$tbak" "$mbak" "$abak"; unset -f _cs_fail
    _neg_gate check_cephfs_ssot || die "Check_cephfs_ssot failed after restoration: $_NEG_GATE_OUT"
    log "Test_cephfs_ssot negative test passed"
}

test_v2v_import_ssot() {
    log "Testing check_v2v_import_ssot"
    local wrapper_file="${ROOT}/usr/libexec/mios/mios-v2v-import"
    local bak; bak="$(mktemp)"; cp -p "$wrapper_file" "$bak"
    _vv_fail() { cp -p "$bak" "$wrapper_file"; rm -f "$bak"; unset -f _vv_fail; die "$1"; }

    # The wrapper builds an argv list (5d667dbc); the old f-string plant matched
    # nothing. A format that is not the SSOT's must fail, and so must a hardcode
    # that happens to EQUAL today's SSOT value -- the one the gate could not see.
    local fmt
    for fmt in broken_format qcow2; do
        sed -i "s/\"-of\", output_format,/\"-of\", \"${fmt}\",/" "$wrapper_file"
        cmp -s "$bak" "$wrapper_file" && _vv_fail "the -of plant landed nowhere -- the wrapper's argv moved"
        _neg_gate check_v2v_import_ssot && _vv_fail "Check_v2v_import_ssot passed despite a wrapper -of hardcoded to ${fmt}"
        [[ "$_NEG_GATE_OUT" == *"ignores [virt.v2v].output_format"* ]] || _vv_fail "Check_v2v_import_ssot failed the ${fmt} plant for the wrong reason: $_NEG_GATE_OUT"
        cp -p "$bak" "$wrapper_file"
    done

    rm -f "$bak"; unset -f _vv_fail
    _neg_gate check_v2v_import_ssot || die "Check_v2v_import_ssot failed after restoration: $_NEG_GATE_OUT"
    log "Test_v2v_import_ssot negative test passed"
}

test_no_hardcode_version() {
    log "Testing check_no_hardcode_version"
    local temp_script="${ROOT}/usr/libexec/mios/mios-test-temp-verpin.sh"
    rm -f "$temp_script"

    cat << 'EOF' > "$temp_script"
curl -LO https://example.com/releases/download/v1.2.3/x
EOF
    chmod +x "$temp_script"

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_no_hardcode_version >/dev/null 2>&1 && die "Check_no_hardcode_version passed despite hardcoded version in URL"

    rm -f "$temp_script"
    _neg_gate check_no_hardcode_version || die "Check_no_hardcode_version failed after restoration"
    log "Test_no_hardcode_version negative test passed"
}

test_var_closure() {
    log "Testing check_var_closure"
    local ledger="${ROOT}/usr/share/mios/reference/var-closure-baseline.tsv"
    local bak; bak="$(mktemp)"; cp "$ledger" "$bak"
    local planted=""
    _vc_fail() {
        cp "$bak" "$ledger"; rm -f "$bak"
        [[ -n "$planted" ]] && rm -f "$planted"
        unset -f _vc_fail
        die "$1"
    }

    local probe="MIOS""_NEVER_EMITTED_PROBE"
    local d
    for d in automation tools usr/libexec/mios; do
        planted="${ROOT}/${d}/zz-varclosure-probe.sh"
        printf '#!/usr/bin/env bash\necho "${%s}"\n' "$probe" > "$planted"
        _neg_gate check_var_closure && _vc_fail "check_var_closure passed with an unemitted reference planted in ${d}/"
        case "${_NEG_GATE_OUT}" in
            *"$probe"*) : ;;
            *) _vc_fail "check_var_closure failed for the wrong reason on a plant in ${d}/: ${_NEG_GATE_OUT}" ;;
        esac
        rm -f "$planted"; planted=""
    done

    # A name dropped from the ledger must read as a NEW breach, not as slack.
    sed -i '2,$ { /^MIOS_[A-Z0-9_]*\t/ { s/^\(MIOS_[A-Z0-9_]*\)\t.*//; q } }' "$ledger"
    _neg_gate check_var_closure && _vc_fail "check_var_closure passed with a name removed from the ledger"
    cp "$bak" "$ledger"

    # A ledger entry nothing references any more must fail too: a register that
    # only grows is how 436 accumulated in the first place.
    printf '%s\tnowhere\n' "MIOS""_GHOST_NAME_NEGATIVE" >> "$ledger"
    _neg_gate check_var_closure && _vc_fail "check_var_closure passed with a stale ledger entry"
    case "${_NEG_GATE_OUT}" in
        *"no longer found"*) : ;;
        *) _vc_fail "check_var_closure failed for the wrong reason on a stale entry: ${_NEG_GATE_OUT}" ;;
    esac
    cp "$bak" "$ledger"

    # An unbounded ledger is not a ratchet.
    sed -i '/^#!ceiling/d' "$ledger"
    _neg_gate check_var_closure && _vc_fail "check_var_closure passed with no #!ceiling in the ledger"
    cp "$bak" "$ledger"

    # Absent must be a violation, not a skip: a gate with no data reporting
    # green is exactly how this check spent its whole life.
    mv "$ledger" "${ledger}.negtest"
    _neg_gate check_var_closure && { mv "${ledger}.negtest" "$ledger"; _vc_fail "check_var_closure passed with the ledger missing"; }
    mv "${ledger}.negtest" "$ledger"

    cp "$bak" "$ledger"; rm -f "$bak"
    unset -f _vc_fail
    _neg_gate check_var_closure || die "check_var_closure failed after restoration: ${_NEG_GATE_OUT}"
    log "check_var_closure negative test passed"
}

test_law_enforcers() {
    log "Testing check_law_enforcers"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local post="${ROOT}/automation/99-postcheck.sh"
    local tbak pbak
    tbak="$(mktemp)"; pbak="$(mktemp)"
    cp "$toml" "$tbak"; cp "$post" "$pbak"
    _le_fail() {
        cp "$tbak" "$toml"; cp "$pbak" "$post"; rm -f "$tbak" "$pbak"
        unset -f _le_fail
        die "$1"
    }

    # A drift-check target that names no function.
    sed -i 's/check_usr_over_etc/check_nonexistent_bogus_law/' "$toml"
    _neg_gate check_law_enforcers && _le_fail "check_law_enforcers passed with a drift-check target that is not defined"
    cp "$tbak" "$toml"

    # task_986: the defect this check could not see. A marker that exists ONLY in
    # a comment after the script's `exit 0` is not enforcement, and the previous
    # reader matched it as a bare substring for four laws at once.
    sed -i 's|99-postcheck.sh:BOUND-IMAGES|99-postcheck.sh:item14|' "$toml"
    printf '\n# References for laws: item14\n' >> "$post"
    _neg_gate check_law_enforcers && _le_fail "check_law_enforcers passed with an enforcer that exists only in a comment after exit 0"
    case "${_NEG_GATE_OUT}" in
        *"only in a comment or after"*) : ;;
        *) _le_fail "check_law_enforcers failed for the wrong reason on a commented enforcer: ${_NEG_GATE_OUT}" ;;
    esac
    cp "$tbak" "$toml"; cp "$pbak" "$post"

    # A marker that is nowhere at all must fail too, so the case above is not
    # passing merely because the name was unusual.
    sed -i 's|99-postcheck.sh:BARE-SAFE-ENV|99-postcheck.sh:NO-SUCH-MARKER-ANYWHERE|' "$toml"
    _neg_gate check_law_enforcers && _le_fail "check_law_enforcers passed with a postcheck marker that appears nowhere"
    cp "$tbak" "$toml"

    sed -i 's/check_dag_integrity,check_firstboot_degrade_open/check_dag_integrity,check_never_defined_anywhere/' "$toml"
    _neg_gate check_law_enforcers && _le_fail "check_law_enforcers passed with an undefined SECOND enforcer in a comma list"
    cp "$tbak" "$toml"

    # An enforcer kind this check cannot verify must say so rather than fall
    # through to silence, which is what happened to every unrecognised prefix.
    sed -i 's|98-drift-checks.sh:check_no_hardcode|somewhere-else.sh:check_no_hardcode|' "$toml"
    _neg_gate check_law_enforcers && _le_fail "check_law_enforcers passed with an unrecognised enforcer kind"
    cp "$tbak" "$toml"

    rm -f "$tbak" "$pbak"
    unset -f _le_fail
    _neg_gate check_law_enforcers || die "check_law_enforcers failed after restoration: ${_NEG_GATE_OUT}"
    log "check_law_enforcers negative test passed"
}

test_usr_over_etc() {
    log "Testing check_usr_over_etc"
    local temp_shadow="${ROOT}/etc/fontconfig/conf.avail/30-mios-geist.conf"
    mkdir -p "${ROOT}/etc/fontconfig/conf.avail"
    touch "$temp_shadow"
    git add -f "$temp_shadow"

    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_usr_over_etc >/dev/null 2>&1; then
        git rm -f "$temp_shadow" >/dev/null 2>&1
        die "Check_usr_over_etc passed despite /etc file shadowing /usr SSOT file"
    fi

    git rm -f "$temp_shadow" >/dev/null 2>&1
    rm -rf "${ROOT}/etc/fontconfig"
    _neg_gate check_usr_over_etc || die "Check_usr_over_etc failed after restoration"
    log "Test_usr_over_etc negative test passed"
}

test_projection_registry() (
    log "Testing check_projection_registry"
    local toml_file="${ROOT}/usr/share/mios/mios.toml"
    local bak; bak="$(mktemp)"; cp "$toml_file" "$bak"
    trap 'cp "$bak" "$toml_file"; rm -f "$bak"' EXIT
    _neg_gate check_projection_registry || die "Projection registry failed before the missing-check control"
    sed -i 's/check = "check_dotfiles_projection"/check = "check_nonexistent_proj_check"/' "$toml_file"
    _neg_gate check_projection_registry && die "Projection registry accepted a missing projection check"
    [[ "$_NEG_GATE_OUT" == *"check_nonexistent_proj_check"* ]] \
        || die "Projection registry failed without naming the missing check"
    cp "$bak" "$toml_file"
    _neg_gate check_projection_registry || die "Projection registry failed after exact restoration"
    # The generator half: a row naming a native module that is not on disk.
    sed -i 's|src/pod_quadlets.rs"|src/pod_quadlets_negtest.rs"|' "$toml_file"
    _neg_gate check_projection_registry && die "Projection registry accepted a registered generator missing from disk"
    [[ "$_NEG_GATE_OUT" == *"pod_quadlets_negtest.rs' missing from disk"* ]] || die "Projection registry did not name the absent generator"
    cp "$bak" "$toml_file"
    _neg_gate check_projection_registry || die "Projection registry failed after the generator-half restoration"
    log "Test_projection_registry negative test passed"
)

test_bake_plan_integrity() (
    log "Testing check_bake_plan_integrity"
    local list_file firstboot_image bak
    list_file="$(find "${ROOT}/usr/lib/mios/bake/plan.d" -maxdepth 1 -name '[0-9][0-9]-extra.list' -print -quit 2>/dev/null)"
    [[ -n "$list_file" && -f "$list_file" ]] || die "No extra bake plan exists for the integrity control"
    firstboot_image="$(sed -n '/[^[:space:]]/{p;q;}' "${ROOT}/usr/lib/mios/bake/plan.d/firstboot.list")"
    [[ -n "$firstboot_image" ]] || die "No firstboot image exists for the integrity control"
    bak="$(mktemp)"; cp "$list_file" "$bak"
    trap 'cp "$bak" "$list_file"; rm -f "$bak"' EXIT
    _neg_gate check_bake_plan_integrity || die "Bake integrity failed before the firstboot control"
    printf '%s\n' "$firstboot_image" >> "$list_file"
    _neg_gate check_bake_plan_integrity && die "Bake integrity accepted a firstboot image in a baked group"
    [[ "$_NEG_GATE_OUT" == *"Firstboot token"* && "$_NEG_GATE_OUT" == *"$firstboot_image"* ]] \
        || die "Bake integrity failed without naming the firstboot violation"
    cp "$bak" "$list_file"
    _neg_gate check_bake_plan_integrity || die "Bake integrity failed after exact restoration"
    log "Test_bake_plan_integrity negative test passed"
)

test_bake_ref_parity() {
    log "Testing check_bake_ref_defaults"
    local script_file="${ROOT}/automation/66-bake-quickshell.sh"
    [[ -f "$script_file" ]] || die "test_bake_ref_parity: $script_file is missing -- the test would otherwise skip silently"
    if true; then
        # printf X / ${var%X}: $(cat) + echo collapses a non-single-newline tail.
        local orig_val
        orig_val="$(cat "$script_file"; printf X)"
        _bakeref_restore() { printf '%s' "${orig_val%X}" > "$script_file"; }

        # Derive the plant from the file, never a literal: the old hardcoded
        # `:-v0.3.0` stopped matching once the default floated, so the sed no-op'd,
        # the check passed on a pristine file, and this test blamed the check for
        # its own stale plant. A no-op mutation must fail loudly (SKILL.md 6).
        local cur_ref planted='DEVLOOP-PLANTED-BAKEREF-PARITY'
        cur_ref="$(sed -n 's/.*MIOS_BUILD_BAKE_REFS_QUICKSHELL:-\([^}]*\)}.*/\1/p' "$script_file" | head -1)"
        [[ -n "$cur_ref" ]] || die "test_bake_ref_parity: no MIOS_BUILD_BAKE_REFS_QUICKSHELL default found in $script_file -- the plant would be a no-op"
        sed -i "s#MIOS_BUILD_BAKE_REFS_QUICKSHELL:-${cur_ref}}#MIOS_BUILD_BAKE_REFS_QUICKSHELL:-${planted}}#" "$script_file"
        grep -q "$planted" "$script_file" || {
            _bakeref_restore
            die "test_bake_ref_parity: the plant did not land (default read as '${cur_ref}') -- a no-op mutation makes the next assertion meaningless"
        }

        if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_bake_ref_defaults >/dev/null 2>&1; then
            _bakeref_restore
            die "Check_bake_ref_defaults passed despite wrong bake_ref default '${planted}'"
        fi

        _bakeref_restore
        [[ "$(cat "$script_file"; printf X)" == "$orig_val" ]] \
            || die "test_bake_ref_parity: $script_file was not restored byte-exactly"
        _neg_gate check_bake_ref_defaults || die "test_bake_ref_parity: check_bake_ref_defaults failed after restoration"
    fi
    log "Test_bake_ref_parity negative test passed"
}

test_db_seed_coverage() {
    log "Testing check_db_seed_coverage"
    local toml_file="${ROOT}/usr/share/mios/mios.toml"
    local seed_script="${ROOT}/usr/libexec/mios/seed-db-config.py"
    local orig_val
    local orig_seed
    local probe
    local anchor
    # Assembled, never a literal: a static scan of this file must not match it.
    probe="unseeded_$(printf 'bogus')_probe_section"
    anchor="'$(printf 'database')',"
    orig_val="$(cat "$toml_file"; printf X)"
    orig_seed="$(cat "$seed_script"; printf X)"

    _dbseed_restore() {
        printf '%s' "${orig_val%X}" > "$toml_file"
        printf '%s' "${orig_seed%X}" > "$seed_script"
    }
    _dbseed_gate() {
        MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" \
            MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" \
            check_db_seed_coverage >/dev/null 2>&1
    }

    # Baseline first: a plant proves nothing against an already-red check.
    if ! _dbseed_gate; then
        _dbseed_restore
        die "test_db_seed_coverage: check_db_seed_coverage is red on the CLEAN tree; no plant below can prove anything"
    fi

    # Direction 1: a section in the SSOT that the seeder never reaches.
    printf '\n[%s]\nKey = "value"\n' "$probe" >> "$toml_file"
    if _dbseed_gate; then
        _dbseed_restore
        die "Check_db_seed_coverage passed despite unseeded section in mios.toml"
    fi
    printf '%s' "${orig_val%X}" > "$toml_file"

    # Direction 2: an allowlist name the SSOT does not carry.
    sed -i "s|${anchor}|${anchor} '${probe}',|" "$seed_script"
    if ! grep -q "${probe}" "$seed_script"; then
        _dbseed_restore
        die "test_db_seed_coverage: stale-entry plant did not apply; the arm would have proven nothing"
    fi
    if _dbseed_gate; then
        _dbseed_restore
        die "Check_db_seed_coverage passed despite an allowlist entry naming a section mios.toml no longer has"
    fi

    _dbseed_restore
    _dbseed_gate || die "Check_db_seed_coverage failed after restoration"
    log "Test_db_seed_coverage negative test passed"
}

test_account_column_parity() {
    log "Testing check_account_column_parity"
    local schema_file="${ROOT}/usr/share/mios/postgres/schema-init.sql"
    local orig_val
    orig_val="$(cat "$schema_file"; printf X)"

    sed -i 's/name        text UNIQUE NOT NULL/-- name        text UNIQUE NOT NULL/' "$schema_file"

    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_account_column_parity >/dev/null 2>&1; then
        printf '%s' "${orig_val%X}" > "$schema_file"
        die "Check_account_column_parity passed despite missing column in schema"
    fi

    printf '%s' "${orig_val%X}" > "$schema_file"
    _neg_gate check_account_column_parity || die "Check_account_column_parity failed after restoration"
    log "Test_account_column_parity negative test passed"
}

test_ssot_consumer_keys() {
    log "Testing check_ssot_consumer_keys"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak="${toml}.sckbak"
    cp "$toml" "$bak"

    _sck_run() {
        env MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" \
            MIOS_DRIFT_CHECK_ROOT="$ROOT" \
            bash "${ROOT}/automation/98-drift-checks.sh" check_ssot_consumer_keys \
            >/dev/null 2>&1
    }
    _sck_fail() { cp "$bak" "$toml"; rm -f "$bak"; unset -f _sck_run _sck_fail; die "$1"; }

    # (1) Breaking a key a consumer reads must FAIL. Renaming [security]'s
    # api_require_auth is exactly the task_269 defect, re-created.
    sed -i '0,/^api_require_auth /s//api_require_auth_RENAMED /' "$toml"
    _sck_run && _sck_fail "check_ssot_consumer_keys passed with api_require_auth renamed out from under its consumer"
    cp "$bak" "$toml"

    # (2) Raising the ceiling to absorb the new breakage must FAIL too.
    sed -i '0,/^api_require_auth /s//api_require_auth_RENAMED /' "$toml"
    sed -i 's/^max_unresolved = [0-9]*$/max_unresolved = 999/' "$toml"
    _sck_run && _sck_fail "check_ssot_consumer_keys passed with the ceiling raised"
    cp "$bak" "$toml"

    # (3) Deleting the register must FAIL rather than read as "no breakage".
    python3 - "$toml" <<'PYX'
import io, re, sys
p = sys.argv[1]
s = io.open(p, encoding="utf-8").read()
s = re.sub(r'\[ssot_consumers\]\n.*?\n\]\n', '', s, count=1, flags=re.S)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
PYX
    _sck_run && _sck_fail "check_ssot_consumer_keys passed with [ssot_consumers] absent"

    cp "$bak" "$toml"; rm -f "$bak"
    _sck_run || { unset -f _sck_run _sck_fail; die "check_ssot_consumer_keys failed after restoration"; }
    unset -f _sck_run _sck_fail

    log "Test_ssot_consumer_keys negative test passed"
}

test_unit_projection() {
    log "Testing check_unit_projection"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak="${toml}.upbak"
    cp "$toml" "$bak"

    _up_run() {
        env MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" \
            MIOS_DRIFT_CHECK_ROOT="$ROOT" \
            bash "${ROOT}/automation/98-drift-checks.sh" check_unit_projection \
            >/dev/null 2>&1
    }
    _up_fail() { cp "$bak" "$toml"; rm -f "$bak"; unset -f _up_run _up_fail; die "$1"; }

    # (1) Raising the ceiling to absorb new drift must FAIL. The ratchet only
    # comes down; a register that may grow measures nothing.
    sed -i 's/^max_drift = [0-9]*$/max_drift = 999/' "$toml"
    _up_run && _up_fail "check_unit_projection passed with a raised ceiling"
    cp "$bak" "$toml"

    # (2) Toolchain-free half: register below its own ceiling. The renderer half
    # is asserted by tests/projection.rs. See tasks.jsonl task_267.
    python3 - "$toml" <<'PYX'
import io, re, sys
p = sys.argv[1]
s = io.open(p, encoding="utf-8").read()
start = s.index("[unit_projection]")
end = s.index("\n]\n", start) + len("\n]\n")
block = s[start:end]
block, n = re.subn(r'\n  "[^"]+",(?=\n\])', '', block, count=1)
assert n == 1, "no register entry was removed -- the mutation would prove nothing"
io.open(p, "w", encoding="utf-8", newline="\n").write(s[:start] + block + s[end:])
PYX
    _up_run && _up_fail "check_unit_projection passed with the register below its own ceiling"
    cp "$bak" "$toml"

    # (3) An entry naming a unit [units.*] does not project must FAIL.
    sed -i '0,/^drift = \[$/s//drift = [\n  "mios-not-a-real-unit.service",/' "$toml"
    _up_run && _up_fail "check_unit_projection passed on a register entry naming no projected unit"
    cp "$bak" "$toml"

    # (4) Deleting the table must FAIL rather than read as "no debt".
    python3 - "$toml" <<'PYX'
import io, re, sys
p = sys.argv[1]
s = io.open(p, encoding="utf-8").read()
s = re.sub(r'\[unit_projection\]\n.*?\n\]\n', '', s, count=1, flags=re.S)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
PYX
    _up_run && _up_fail "check_unit_projection passed with [unit_projection] absent"

    cp "$bak" "$toml"; rm -f "$bak"
    _up_run || { unset -f _up_run _up_fail; die "check_unit_projection failed after restoration"; }
    unset -f _up_run _up_fail

    log "Test_unit_projection negative test passed"
}

test_metal_vs_hosted() {
    log "Testing check_metal_vs_hosted"
    local doc="${ROOT}/usr/share/doc/mios/reference/metal-vs-hosted.md"
    local bak="${doc}.mvhbak"
    cp "$doc" "$bak"

    # (1) A hand-edited comparison must FAIL -- the whole point is that the
    # numbers are projected, so nobody can quietly "correct" them.
    sed -i 's/| Units started |.*/| Units started | **1** | **1** |/' "$doc"
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_metal_vs_hosted >/dev/null 2>&1 && die "check_metal_vs_hosted passed on a hand-edited comparison"

    # (2) A MISSING projection must FAIL rather than read as "nothing to check".
    rm -f "$doc"
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_metal_vs_hosted >/dev/null 2>&1 && die "check_metal_vs_hosted passed with the comparison absent"

    mv "$bak" "$doc"
    _neg_gate check_metal_vs_hosted || die "check_metal_vs_hosted failed after restoration"

    log "Test_metal_vs_hosted negative test passed"
}

test_node_pool() {
    log "Testing check_node_pool"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local backup="${toml}.npbak"
    cp "$toml" "$backup"

    # (1) An exact alias must FAIL -- four of six shipped nodes were byte-identical
    # copies of one heavy endpoint, so the fan-out counted one backend as four.
    python3 -c 'import io,re,sys
p=sys.argv[1]
s=io.open(p,encoding="utf-8").read()
m=re.search(r"^\[nodes\.local-heavy\]\n(?:[^\[]*\n)", s, re.M)
assert m, "node anchor moved"
blk=m.group(0).replace("local-heavy","local-negtest-alias",1)
io.open(p,"w",encoding="utf-8",newline="\n").write(s[:m.end()] + blk + s[m.end():])' "$toml"
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_node_pool >/dev/null 2>&1 && die "check_node_pool passed with two nodes on one (endpoint, model, lane)"
    cp "$backup" "$toml"

    # (2) A lane [dispatch] does not budget must FAIL: the semaphore has no bucket.
    python3 -c 'import io,re,sys
p=sys.argv[1]
s=io.open(p,encoding="utf-8").read()
m=re.search(r"^lane        = \"gpu\".*$", s, re.M)
assert m, "lane anchor moved"
io.open(p,"w",encoding="utf-8",newline="\n").write(
    s[:m.start()] + "lane        = \"negtest-quantum\"" + s[m.end():])' "$toml"
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_node_pool >/dev/null 2>&1 && die "check_node_pool passed with a lane [dispatch].lane_priority does not budget"
    cp "$backup" "$toml"

    # (3) A baked local port must FAIL: no /etc/mios overlay can move it, so the
    # node could never be offloaded to a blade.
    python3 -c 'import io,re,sys
p=sys.argv[1]
s=io.open(p,encoding="utf-8").read()
m=re.search(r"^endpoint    = \"http://localhost:\$\{MIOS_PORTS_LLM_HEAVY\}/v1\"$", s, re.M)
assert m, "endpoint anchor moved"
io.open(p,"w",encoding="utf-8",newline="\n").write(
    s[:m.start()] + "endpoint    = \"http://localhost:8520/v1\"" + s[m.end():])' "$toml"
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_node_pool >/dev/null 2>&1 && die "check_node_pool passed with an endpoint an overlay cannot move"

    mv "$backup" "$toml"
    _neg_gate check_node_pool || die "check_node_pool failed after restoration"

    log "Test_node_pool negative test passed"
}

test_port_fallbacks() {
    log "Testing check_port_fallbacks"
    local probe="${ROOT}/usr/libexec/mios/mios-negtest-port-probe"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local tbak="${toml}.pfbak"
    cp "$toml" "$tbak"
    _pf_cleanup() { rm -f "$probe"; cp "$tbak" "$toml"; }

    # (1) A stale literal beside a MIOS_PORT_* name must FAIL. Four shipped
    # units pinned exactly this shape, three of them retired ports.
    printf '#!/usr/bin/env python3\nimport os\nP = os.environ.get("MIOS_PORTS_AGENT_PIPE", "8640")\n' > "$probe"
    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_port_fallbacks >/dev/null 2>&1; then
        _pf_cleanup; rm -f "$tbak"
        die "check_port_fallbacks passed with a stale literal beside MIOS_PORTS_AGENT_PIPE"
    fi

    # (2) The DOUBLE fallback -- the second literal is the one that runs.
    printf '#!/usr/bin/env python3\nimport os\nP = int(os.environ.get("MIOS_PORTS_AGENT_PIPE", "8700") or 8640)\n' > "$probe"
    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_port_fallbacks >/dev/null 2>&1; then
        _pf_cleanup; rm -f "$tbak"
        die "check_port_fallbacks passed with a stale SECOND literal in a double fallback"
    fi

    # (3) The MIOS_<KEY>_PORT alias spelling, in a file that never says
    # MIOS_PORT_ at all -- the early-out that used to skip it.
    printf '#!/usr/bin/env python3\nimport os\nP = os.environ.get("MIOS_PORTS_ARBITER", "8650")\n' > "$probe"
    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_port_fallbacks >/dev/null 2>&1; then
        _pf_cleanup; rm -f "$tbak"
        die "check_port_fallbacks passed with a stale literal beside the alias spelling"
    fi
    rm -f "$probe"

    # (4) The register only SHRINKS: an entry that no longer reproduces must be
    # removed, not left to rot into decoration.
    python3 -c 'import io,re,sys
p=sys.argv[1]
s=io.open(p,encoding="utf-8").read()
m=re.search(r"^stale_fallbacks = \[\]$", s, re.M)
assert m, "stale_fallbacks register not found"
io.open(p,"w",encoding="utf-8",newline="\n").write(
    s[:m.start()] + "stale_fallbacks = [\"usr/libexec/mios/no-such-file:AGENT_PIPE\"]" + s[m.end():])' "$toml"
    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_port_fallbacks >/dev/null 2>&1; then
        _pf_cleanup; rm -f "$tbak"
        die "check_port_fallbacks passed with a register entry that no longer reproduces"
    fi

    _pf_cleanup
    _neg_gate check_port_fallbacks || { rm -f "$tbak"; die "check_port_fallbacks failed after restoration"; }
    rm -f "$tbak"

    log "Test_port_fallbacks negative test passed"
}

test_role_ssot() {
    log "Testing check_role_ssot"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local unit="${ROOT}/usr/lib/systemd/system/mios-hybrid.target"
    local lib="${ROOT}/usr/lib/mios/blade.sh"
    local tbak="${toml}.rolebak" ubak="${unit}.rolebak" lbak="${lib}.rolebak"
    cp "$toml" "$tbak"; cp "$unit" "$ubak"; cp "$lib" "$lbak"
    _role_restore() { cp "$tbak" "$toml"; cp "$ubak" "$unit"; cp "$lbak" "$lib"; }

    # (1) A [blade].type that names no archetype must FAIL. This is the exact
    # value the retired [profile].role shipped with.
    sed -i '0,/^type = "hybrid"$/s//type = "developer"/' "$toml"
    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_role_ssot >/dev/null 2>&1; then
        _role_restore; rm -f "$tbak" "$ubak" "$lbak"
        die "check_role_ssot passed with [blade].type naming no archetype"
    fi
    cp "$tbak" "$toml"

    # (2) A resurrected [profile] must FAIL -- one canonical name (Law 9).
    printf '\n[profile]\nrole = "developer"\n' >> "$toml"
    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_role_ssot >/dev/null 2>&1; then
        _role_restore; rm -f "$tbak" "$ubak" "$lbak"
        die "check_role_ssot passed with [profile].role resurrected"
    fi
    cp "$tbak" "$toml"

    # (3) An incomplete conflict graph must FAIL. The DEFAULT role target
    # shipped conflicting with NOTHING, so switching away never stopped it.
    sed -i '/^Conflicts=/d' "$unit"
    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_role_ssot >/dev/null 2>&1; then
        _role_restore; rm -f "$tbak" "$ubak" "$lbak"
        die "check_role_ssot passed with a role target conflicting with nothing"
    fi
    cp "$ubak" "$unit"

    # (4) An Alias= systemd cannot install must FAIL -- two role targets shipped
    # Alias=default.target.mios-<role>, whose suffix no unit name can match.
    sed -i '$a Alias=default.target.mios-hybrid' "$unit"
    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_role_ssot >/dev/null 2>&1; then
        _role_restore; rm -f "$tbak" "$ubak" "$lbak"
        die "check_role_ssot passed with an Alias= whose suffix is not the unit's"
    fi
    cp "$ubak" "$unit"

    # (5) An archetype name spelled as a literal in the blade code must FAIL:
    # the archetype table is [blade.archetypes], not a case statement.
    sed -i '$a case "$ROLE" in endpoint) TARGET=x ;; esac' "$lib"
    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_role_ssot >/dev/null 2>&1; then
        _role_restore; rm -f "$tbak" "$ubak" "$lbak"
        die "check_role_ssot passed with an archetype hardcoded in blade.sh"
    fi

    cp "$lbak" "$lib"

    # (6) A capability every archetype grants and NO unit requires must FAIL:
    # `controller` was in exactly that state, so the controller archetype
    # behaved identically to headless.
    python3 -c 'import io,re,sys
p=sys.argv[1]
s=io.open(p,encoding="utf-8").read()
m=re.search(r"^endpoint   = \[\]$", s, re.M)
assert m, "archetype anchor moved"
io.open(p,"w",encoding="utf-8",newline="\n").write(
    s[:m.start()] + "endpoint   = []\nnegtest    = [\"negtest-decorative-cap\"]" + s[m.end():])' "$toml"
    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_role_ssot >/dev/null 2>&1; then
        _role_restore; rm -f "$tbak" "$ubak" "$lbak"
        die "check_role_ssot passed with a capability no unit requires"
    fi

    _role_restore
    _neg_gate check_role_ssot || { rm -f "$tbak" "$ubak" "$lbak"; die "check_role_ssot failed after restoration"; }
    rm -f "$tbak" "$ubak" "$lbak"

    log "Test_role_ssot negative test passed"
}

test_blade_karg() {
    log "Testing check_blade_karg"
    local karg="${ROOT}/usr/lib/bootc/kargs.d/05-mios-blade.toml"
    local backup="${karg}.negbak"
    cp "$karg" "$backup"

    # (1) A hand-edited karg must FAIL -- that is what makes this a projection
    # rather than a file someone tweaks and nobody notices.
    sed -i 's/mios\.blade=[a-z-]*/mios.blade=negtesthandedited/' "$karg"
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_blade_karg >/dev/null 2>&1 && die "check_blade_karg passed on a hand-edited karg"
    cp "$backup" "$karg"

    # (2) A MISSING projection must FAIL rather than being read as "nothing to
    # check" -- the file's absence is exactly the state this task started in.
    rm -f "$karg"
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_blade_karg >/dev/null 2>&1 && die "check_blade_karg passed with the projection absent"

    mv "$backup" "$karg"
    _neg_gate check_blade_karg || die "check_blade_karg failed after restoration"

    log "Test_blade_karg negative test passed"
}

test_blade_coverage() {
    log "Testing check_blade_coverage"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local backup="${toml}.negbak"
    cp "$toml" "$backup"

    # (1) A container classified NEITHER way must FAIL -- that is the state the
    # whole activation axis was in before this gate existed. Sabotage the LIVE
    # mechanism: drop a container's [blade.requires] line while the ungated
    # register is empty, so it is gated by nothing and registered nowhere.
    python3 -c 'import io,re,sys
p=sys.argv[1]
s=io.open(p,encoding="utf-8").read()
pat=re.compile(r"^mios-searxng\s*=\s*\[[^\]]*\]\n", re.M)
assert len(pat.findall(s))==1,"blade.requires anchor moved"
io.open(p,"w",encoding="utf-8",newline="\n").write(pat.sub("", s, count=1))' "$toml"
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_blade_coverage >/dev/null 2>&1 && die "check_blade_coverage passed with a container classified neither way"
    cp "$backup" "$toml"

    # (2) A capability no archetype grants must FAIL: nothing could activate it,
    # so the unit would be dead on every blade type.
    python3 -c 'import io,sys
p=sys.argv[1]
import re
s=io.open(p,encoding="utf-8").read()
pat=re.compile(r"^mios-llm-heavy\s*=\s*\[[^\]]*\]", re.M)
assert len(pat.findall(s))==1,"requires anchor moved"
io.open(p,"w",encoding="utf-8",newline="\n").write(
    pat.sub("mios-llm-heavy     = [\"mios-negtest-uncapability\"]", s, count=1))' "$toml"
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_blade_coverage >/dev/null 2>&1 && die "check_blade_coverage passed with a capability no archetype grants"

    cp "$backup" "$toml"

    # (3) A seat-side unit whose port only a GATED unit dials must FAIL. The
    # coupling is an ADDRESS, so the dependency walk cannot see it: the second
    # CDP browser sat seat-side while its only client was gated off.
    python3 -c 'import io,re,sys
p=sys.argv[1]
s=io.open(p,encoding="utf-8").read()
m=re.search(r"^mios-hermes-browser-worker\s*=\s*\[[^\]]*\][^\n]*\n", s, re.M)
assert m, "worker-browser gate anchor moved"
s = s[:m.start()] + s[m.end():]
m2 = re.search(r'"'"'^  "mios-hermes-browser",[^\n]*\n'"'"', s, re.M)
assert m2, "seat_side anchor moved"
io.open(p,"w",encoding="utf-8",newline="\n").write(
    s[:m2.end()] + "  \"mios-hermes-browser-worker\",\n" + s[m2.end():])' "$toml"
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_blade_coverage >/dev/null 2>&1 && die "check_blade_coverage passed with a seat-side unit only a gated unit dials"

    mv "$backup" "$toml"
    _neg_gate check_blade_coverage || die "check_blade_coverage failed after restoration"

    log "Test_blade_coverage negative test passed"
}

test_ports_bound() {
    log "Testing check_ports_bound"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local backup="${toml}.negbak"
    cp "$toml" "$backup"

    # Both cases sabotage the LIVE mechanism rather than a literal copy of the
    # register's contents: anchoring on those made this test go inert the moment
    # a key drained out of it.

    # (1) A newly allocated port that nothing references and nothing registers
    # must FAIL -- otherwise the collision checker guards a number nothing binds.
    python3 -c 'import io,re,sys
p=sys.argv[1]
s=io.open(p,encoding="utf-8").read()
m=re.search(r"^\[ports\]\n", s, re.M)
assert m, "flat [ports] table not found"
io.open(p,"w",encoding="utf-8",newline="\n").write(
    s[:m.end()] + "negtest_unbound_port = 8599\n" + s[m.end():])' "$toml"
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_ports_bound >/dev/null 2>&1 && die "check_ports_bound passed with an allocated port nothing references"
    cp "$backup" "$toml"

    # (2) The register must only SHRINK: a port that IS referenced may not sit
    # in it, or the list silently rots into decoration.
    python3 -c 'import io,re,sys
p=sys.argv[1]
s=io.open(p,encoding="utf-8").read()
m=re.search(r"^unbound = \[", s, re.M)
assert m, "unbound register not found"
io.open(p,"w",encoding="utf-8",newline="\n").write(
    s[:m.end()] + "\"guacd\", " + s[m.end():])' "$toml"
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_ports_bound >/dev/null 2>&1 && die "check_ports_bound passed with a REFERENCED port still in the unbound register"

    mv "$backup" "$toml"
    _neg_gate check_ports_bound || die "check_ports_bound failed after restoration"

    log "Test_ports_bound negative test passed"
}

test_service_urls() {
    log "Testing check_service_urls"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local backup="${toml}.negbak"
    local reg_tail='"ssh", "ttyd_bash", "ttyd_powershell", "whisper",'
    local reg_head='  "adguard_dns", "adguard_ui", "agent_pipe", "ai_legacy", "arbiter", "ceph_dashboard",'
    cp "$toml" "$backup"

    # (1) A port in NEITHER [urls] nor the register must FAIL. Dropping an entry
    # from the shrink-only register is exactly how a real regression looks.
    python3 -c 'import io,sys
p,old,new=sys.argv[1],sys.argv[2],sys.argv[3]
s=io.open(p,encoding="utf-8").read()
assert s.count(old)==1,"non_addressable tail anchor moved"
io.open(p,"w",encoding="utf-8",newline="\n").write(s.replace(old,new))' \
        "$toml" "$reg_tail" '"ssh", "ttyd_bash", "ttyd_powershell",'
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_service_urls >/dev/null 2>&1 && die "check_service_urls passed with a port in neither [urls] nor the register"
    cp "$backup" "$toml"

    # (2) A register entry naming a port that does not exist must FAIL -- a stale
    # register is how these lists rot into decoration. The bogus name is
    # ASSEMBLED so this file never contains the literal it searches for.
    python3 -c 'import io,sys
p,old=sys.argv[1],sys.argv[2]
ghost="mios_negtest"+"_ghost_port"
s=io.open(p,encoding="utf-8").read()
assert s.count(old)==1,"register head anchor moved"
io.open(p,"w",encoding="utf-8",newline="\n").write(
    s.replace(old, old+"\n  \""+ghost+"\","))' "$toml" "$reg_head"
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_service_urls >/dev/null 2>&1 && die "check_service_urls passed with a register entry naming no real port"

    cp "$backup" "$toml"

    # An inter-service scheme in [urls] must FAIL: the table is the
    # browser-openable surface, and a postgresql:// DSN made it mean two things.
    python3 -c 'import io,re,sys
p=sys.argv[1]
s=io.open(p,encoding="utf-8").read()
m=re.search(r"^\[urls\]\n", s, re.M)
assert m, "[urls] table not found"
io.open(p,"w",encoding="utf-8",newline="\n").write(
    s[:m.end()] + "negtest_dsn        = \"postgresql://u@localhost:5432/d\"\n" + s[m.end():])' "$toml"
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_service_urls >/dev/null 2>&1 && die "check_service_urls passed with a non-browser scheme in [urls]"

    cp "$backup" "$toml"

    # An address with a BARE port must FAIL: an /etc/mios overlay cannot move a
    # baked number, so the service could never be offloaded.
    python3 -c 'import io,re,sys
p=sys.argv[1]
s=io.open(p,encoding="utf-8").read()
m=re.search(r"^\[urls\]\n", s, re.M)
assert m, "[urls] table not found"
io.open(p,"w",encoding="utf-8",newline="\n").write(
    s[:m.start()] + "[negtest_bare]\nendpoint = \"http://localhost:8500/v1\"\n\n" + s[m.start():])' "$toml"
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_service_urls >/dev/null 2>&1 && die "check_service_urls passed with an address an overlay cannot move"

    mv "$backup" "$toml"
    _neg_gate check_service_urls || die "check_service_urls failed after restoration"

    log "Test_service_urls negative test passed"
}

test_greenboot() {
    log "Testing check_greenboot"
    local toml="${ROOT}/usr/share/mios/mios.toml" backup="${ROOT}/usr/share/mios/mios.toml.negbak"
    local script="${ROOT}/usr/lib/greenboot/check/required.d/40-mios-ai-plane.sh" script_bak="${ROOT}/usr/lib/greenboot/check/40-mios-ai-plane.sh.negbak"
    local gb_sh="${ROOT}/automation/78-greenboot.sh" gb_bak="${ROOT}/automation/78-greenboot.sh.negbak"
    local old='critical_services = ["agent-pipe", "llm-light", "pgvector", "hermes"]' neg_rc=0
    _mut() { python3 -c "import io,sys; s=io.open(sys.argv[1],encoding='utf-8').read(); io.open(sys.argv[1],'w',encoding='utf-8',newline='\n').write(s.replace(sys.argv[2],sys.argv[3]))" "$@"; }
    cp "$toml" "$backup"
    # (1) Missing health-check script for critical service must FAIL.
    _mut "$toml" "$old" 'critical_services = ["agent-pipe", "llm-light", "pgvector", "hermes", "mios-negtest-absent"]'
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_greenboot >/dev/null 2>&1 && die "check_greenboot passed with uncovered critical service"
    cp "$backup" "$toml"
    # (2) Empty critical set must FAIL.
    _mut "$toml" "$old" 'critical_services = []'
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_greenboot >/dev/null 2>&1 && die "check_greenboot passed on empty critical set"
    cp "$backup" "$toml"
    # (3) Omitting hermes from critical_services must FAIL.
    _mut "$toml" "$old" 'critical_services = ["agent-pipe", "llm-light", "pgvector"]'
    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_greenboot >/dev/null 2>&1 && die "check_greenboot passed without hermes"
    mv "$backup" "$toml"
    _neg_gate check_greenboot || die "check_greenboot failed after restoration"
    # (4) Removing greenboot check script must FAIL (RED, exit != 0, and not 127).
    if [[ -f "$script" ]]; then
        mv "$script" "$script_bak"; neg_rc=0
        MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_greenboot >/dev/null 2>&1 || neg_rc=$?
        mv "$script_bak" "$script"
        [[ "$neg_rc" -ne 0 && "$neg_rc" -ne 127 ]] || die "check_greenboot did not fail with exit != 0 and != 127 on missing script (got $neg_rc)"
        _neg_gate check_greenboot || die "check_greenboot failed after restoring check script"
    fi
    # (5) check_greenboot_enablement fails when service enablement missing in automation/78-greenboot.sh.
    if [[ -f "$gb_sh" ]]; then
        cp "$gb_sh" "$gb_bak"
        _mut "$gb_sh" "greenboot-healthcheck.service" "disabled-healthcheck.service"
        MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_greenboot_enablement >/dev/null 2>&1 && {
            mv "$gb_bak" "$gb_sh"; die "check_greenboot_enablement passed without service enablement"; }
        mv "$gb_bak" "$gb_sh"
        _neg_gate check_greenboot_enablement || die "check_greenboot_enablement failed after restoration"
    fi
    log "Test_greenboot negative test passed"
}

test_adr_index() {
    log "Testing check_adr_index"
    local idx="${ROOT}/ADR.md"
    local backup="${idx}.negbak"
    cp "$idx" "$backup"

    printf '\n| 9999 | hand-edited row | accepted | | | |\n' >> "$idx"

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_adr_index >/dev/null 2>&1 && die "check_adr_index passed despite a hand-edited ADR.md"

    mv "$backup" "$idx"
    _neg_gate check_adr_index || die "check_adr_index failed after restoration"

    log "Test_adr_index negative test passed"
}

test_schema_consumers() {
    log "Testing check_schema_consumers"
    local sql="${ROOT}/usr/share/mios/postgres/schema-init.sql"
    local backup="${sql}.negbak"
    cp "$sql" "$backup"

    # A brand-new table nothing reads or writes must fail the gate.
    # The name is ASSEMBLED so this file never contains the literal: the gate
    # counts any non-doc file naming a table as a consumer, so a spelled-out
    # sabotage name here would make the gate "find" one and the test vacuous.
    local orphan="mios_negtest""_orphan_tbl"
    printf '\nCREATE TABLE IF NOT EXISTS %s (id bigint);\n' "$orphan" >> "$sql"

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_schema_consumers >/dev/null 2>&1 && die "check_schema_consumers passed despite a table with no reader or writer"

    mv "$backup" "$sql"
    _neg_gate check_schema_consumers || die "check_schema_consumers failed after restoration"

    log "Test_schema_consumers negative test passed"
}

test_task_store() {
    log "Testing check_task_store"
    local store="${ROOT}/tasks.jsonl" doc="${ROOT}/TASKS.md"
    local sbak dbak; sbak="$(mktemp)"; dbak="$(mktemp)"
    cp "$store" "$sbak"; cp "$doc" "$dbak"
    _ts_restore() { cp "$sbak" "$store"; cp "$dbak" "$doc"; rm -f "${ROOT}/.devloop/tasks.jsonl"; }
    _ts_fail() { _ts_restore; rm -f "$sbak" "$dbak"; unset -f _ts_fail _ts_restore _ts_plant _ts_edit _ts_pick; die "$1"; }
    # Each plant must fail check_task_store and be named in its output.
    _ts_plant() { # $1 = what was planted, $2 = text the output must contain
        _neg_gate check_task_store && _ts_fail "check_task_store passed with $1"
        printf '%s\n' "$_NEG_GATE_OUT" | grep -qF -- "$2" || _ts_fail "check_task_store did not name $1 (wanted: $2): $_NEG_GATE_OUT"
        _ts_restore
    }
    # Rewrite the record numbered $1 through a python statement over `r` (and `by`, number -> record), in the
    # bytes the dev-loop toolkit writes; prints whatever the statement leaves in `out`.
    _ts_edit() {
        python3 - "$store" "$1" "$2" <<'PYEOF'
import json, sys
p, n, stmt = sys.argv[1], int(sys.argv[2]), sys.argv[3]
with open(p, encoding="utf-8", newline="") as f:
    lines = f.read().split("\n")[:-1]
recs = [json.loads(l) for l in lines]
by = {r["number"]: r for r in recs}
out = ""
r = by[n]
exec(stmt)
lines[recs.index(r)] = json.dumps(r, ensure_ascii=False)
with open(p, "w", encoding="utf-8", newline="") as f:
    f.write("\n".join(lines) + "\n")
print(out)
PYEOF
    }
    # The number of the first record that `cond` (a python expression over `r`) holds for.
    _ts_pick() { python3 -c 'import json,sys; print(next(r["number"] for r in map(json.loads, open(sys.argv[1], encoding="utf-8")) if eval(sys.argv[2])))' "$sbak" "$1" 2>/dev/null; }
    _neg_gate check_task_store || _ts_fail "check_task_store failed on the tree as committed: $_NEG_GATE_OUT"

    # 1. A duplicate id.
    local dup; dup="$(sed -n '2p' "$sbak")"; printf '%s\n' "$dup" >> "$store"
    _ts_plant "a record twice" "duplicate id $(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["id"])' "$dup")"
    # 2. A task line of the rendered TASKS.md hand-edited outside the overrides block.
    sed -i '0,/^- `task_1` /s//- `task_1` HAND-EDITED /' "$doc"
    _ts_plant "a hand-edited TASKS.md task line" 'HAND-EDITED'
    # 3. A migrated record dropped: the frozen history names it by its typed id.
    local victim; victim="$(sed -n '100p' "$sbak" | python3 -c 'import json,sys; print("task_%d" % json.load(sys.stdin)["number"])')"
    sed -i '100d' "$store"
    _ts_plant "record ${victim} dropped" "record ${victim} was dropped"
    # 4. A retired store back in use.
    mkdir -p "${ROOT}/.devloop"; printf '{"id": "task_1"}\n' > "${ROOT}/.devloop/tasks.jsonl"
    _ts_plant "a retired .devloop/tasks.jsonl" "retired task store present: .devloop/tasks.jsonl"
    # 5. A retired list's frozen bytes edited: the slice names the task that owns it.
    local head; head="$(_frozen_text MiOS:AGY-TASKS.md)"; head="$(grep -m1 -oE '^## task_[0-9]+ ' <<< "$head")" || _ts_fail "the frozen AGY-TASKS.md has no task heading"
    _frozen_edit "MiOS:AGY-TASKS.md" "^${head}" "${head}rewritten history " || _ts_fail "could not plant an AGY-TASKS.md edit"
    _ts_plant "an edited AGY-TASKS.md slice" "MiOS:AGY-TASKS.md: the slice ${head:3:-1} owns at offset"
    # 6. An edge the lane file archived (archived_dependencies) put back into depends_on.
    local n msg
    n="$(_ts_pick 'any(x["type"] == "archived" for x in r["related"])')" || _ts_fail "no record carries an archived edge"
    msg="$(_ts_edit "$n" 'e = next(x["id"] for x in r["related"] if x["type"] == "archived"); r["depends_on"].append(e); out = "task_%d: depends_on task_%d" % (n, next(k for k, v in by.items() if v["id"] == e))')" || _ts_fail "could not plant the archived edge"
    _ts_plant "an archived edge back in depends_on" "$msg is an edge the lane file archived"
    # 7. A count-keeping swap: a record whose only slices lie outside the frozen lists, made slice-less and renamed.
    n="$(_ts_pick 'r["provenance"] and r["provenance"]["sources"] and all(not x["file"].startswith("MiOS:") for x in r["provenance"]["sources"])')" || _ts_fail "no record owns only slices outside the frozen lists"
    _ts_edit "$n" 'r["id"] = "task_7ZZZZZZZZZZZZZZZZZZZZZZZZZ"; r["number"] = 999999; r["provenance"]["sources"] = []' >/dev/null || _ts_fail "could not plant the swap"
    _ts_plant "task_${n} swapped for a record with no slices" "task_999999: a migrated record with no provenance.sources"
    # 8. A number issued twice.
    _ts_edit 6 'r["number"] = 5' >/dev/null || _ts_fail "could not plant a duplicate number"
    _ts_plant "a duplicate number" "duplicate number 5"
    # 9. A migrated record renumbered past the end: its identity no longer matches the frozen digest.
    n="$(_ts_pick 'r["provenance"]')"
    _ts_edit "$n" 'r["number"] = max(by) + 1' >/dev/null || _ts_fail "could not plant a renumbering"
    _ts_plant "a renumbered task" "[tasks.store].migrated_sha256"
    # 10. A purged id written back into a record (built here, so this file never carries one).
    local old="T""-1""169"
    _ts_edit 1 "r['notes'] = 'see $old'" >/dev/null || _ts_fail "could not plant an old id"
    _ts_plant "an old task id in a record" "carries the purged id $old"
    # 11. A carriage return on a line.
    sed -i '1s/$/\r/' "$store"
    _ts_plant "a CRLF line end" "tasks.jsonl:1: carriage return"
    # 12. No sibling checkout: a copy of the task files alone, with nothing beside it, still checks green.
    local alone; alone="$(mktemp -d)"
    mkdir -p "$alone/MiOS/usr/share/mios" "$alone/MiOS/usr/lib/mios/schemas"
    cp "$store" "$doc" "$alone/MiOS/"; cp "${ROOT}/usr/share/mios/mios.toml" "$alone/MiOS/usr/share/mios/"
    cp "${ROOT}/usr/lib/mios/schemas/task-record.schema.json" "$alone/MiOS/usr/lib/mios/schemas/"
    local bin; bin="$(_task_bin)" || { rm -rf "$alone"; _ts_fail "mios-task is not built"; }
    "$bin" check --root "$alone/MiOS" >/dev/null 2>&1 || { rm -rf "$alone"; _ts_fail "mios-task check needed a sibling checkout"; }
    rm -rf "$alone"

    rm -f "$sbak" "$dbak"; unset -f _ts_fail _ts_restore _ts_plant _ts_edit _ts_pick
    _neg_gate check_task_store || die "check_task_store failed after restoration: $_NEG_GATE_OUT"
    log "Test_task_store negative test passed"
}

test_task_ids() {
    log "Testing check_task_ids"
    # A tracked file that names a task by a purged id fails, and names the file and line; a look-alike does not.
    local f="${ROOT}/ROADMAP.md" bak old="AGY""-1""601" line; bak="$(mktemp)"; cp "$f" "$bak"
    printf '\nF-35, UTF-8 and SHA-256 are not task ids; task_1 is one.\n' >> "$f"
    _neg_gate check_task_ids || { cp "$bak" "$f"; rm -f "$bak"; die "check_task_ids failed on look-alikes and a typed id: $_NEG_GATE_OUT"; }
    printf 'see %s\n' "$old" >> "$f"; line="$(wc -l < "$f")"
    _neg_gate check_task_ids && { cp "$bak" "$f"; rm -f "$bak"; die "check_task_ids passed with $old in ROADMAP.md"; }
    printf '%s\n' "$_NEG_GATE_OUT" | grep -qF -- "ROADMAP.md:${line}: ${old} is a purged task id" \
        || { cp "$bak" "$f"; rm -f "$bak"; die "check_task_ids did not name ROADMAP.md:${line}: $_NEG_GATE_OUT"; }
    cp "$bak" "$f"; rm -f "$bak"
    _neg_gate check_task_ids || die "check_task_ids failed after restoration: $_NEG_GATE_OUT"
    log "Test_task_ids negative test passed"
}

test_tasks_status_parity() {
    log "Testing check_tasks_status_parity"
    local store="${ROOT}/tasks.jsonl" sbak; sbak="$(mktemp)"; cp "$store" "$sbak"
    # Flip ONE frozen summary-table cell (ADR-0028) away from that task's own section status.
    local tid
    local txt; txt="$(_frozen_text MiOS:TASKS.md)"
    tid="$(grep -m1 -oE '^\| task_[0-9]+ \| P[0-9] \| (done|done-by-code|planned|in-progress) \|' <<< "$txt" | awk '{print $2}')"
    [[ -z "$tid" ]] && { rm -f "$sbak"; die "test_tasks_status_parity found no resolvable summary row to sabotage"; }
    _frozen_edit "MiOS:TASKS.md" "^\\| ${tid} \\| P[0-9] \\| [a-z/-]* \\|" "| ${tid} | P1 | pending |" || { cp "$sbak" "$store"; rm -f "$sbak"; die "could not plant the ${tid} row"; }
    _neg_gate check_tasks_status_parity && { cp "$sbak" "$store"; rm -f "$sbak"; die "check_tasks_status_parity passed while the summary table contradicted ${tid}'s own Status line"; }
    cp "$sbak" "$store"

    # The '?' placeholder must fail too -- it is how the drift hid for 28 rows.
    _frozen_edit "MiOS:TASKS.md" "^\\| ${tid} \\| P[0-9] \\| [a-z/-]* \\|" "| ${tid} | P1 | ? |" || { cp "$sbak" "$store"; rm -f "$sbak"; die "could not plant the '?' row"; }
    _neg_gate check_tasks_status_parity && { cp "$sbak" "$store"; rm -f "$sbak"; die "check_tasks_status_parity accepted a '?' placeholder for ${tid}"; }
    cp "$sbak" "$store"; rm -f "$sbak"

    _neg_gate check_tasks_status_parity || die "check_tasks_status_parity failed after restoration: $_NEG_GATE_OUT"
    log "Test_tasks_status_parity negative test passed"
}

test_agy_tasks() {
    log "Testing check_agy_tasks"
    local store="${ROOT}/tasks.jsonl" sbak; sbak="$(mktemp)"; cp "$store" "$sbak"

    # Inject a second heading for a task the frozen AGY-TASKS.md already heads.
    local head; head="$(_frozen_text MiOS:AGY-TASKS.md)"; head="$(grep -m1 -oE '^## task_[0-9]+' <<< "$head")" || { rm -f "$sbak"; die "the frozen AGY-TASKS.md has no task heading"; }
    _frozen_edit "MiOS:AGY-TASKS.md" '\Z' $'\n'"${head}"$' -- Duplicate header test\n' || { rm -f "$sbak"; die "could not plant a duplicate ${head#\#\# }"; }
    _neg_gate check_agy_tasks && { cp "$sbak" "$store"; rm -f "$sbak"; die "check_agy_tasks passed despite a duplicate task heading"; }
    cp "$sbak" "$store"

    # Inject a Dep naming a task that does not exist.
    _frozen_edit "MiOS:AGY-TASKS.md" '\Z' $'\n## task_9999999 -- Test task\n**Dep:** task_99999999\n' || { cp "$sbak" "$store"; rm -f "$sbak"; die "could not plant task_9999999"; }
    _neg_gate check_agy_tasks && { cp "$sbak" "$store"; rm -f "$sbak"; die "check_agy_tasks passed despite dangling Dep reference"; }
    cp "$sbak" "$store"; rm -f "$sbak"

    _neg_gate check_agy_tasks || die "check_agy_tasks failed after restoration: $_NEG_GATE_OUT"
    log "check_agy_tasks negative test passed"
}

test_mios_toml_integrity() {
    log "Testing check_mios_toml_integrity"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local backup="${toml}.negbak"
    cp "$toml" "$backup"

    # Truncate mios.toml
    head -n 50 "$backup" > "$toml"

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_mios_toml_integrity >/dev/null 2>&1 && die "check_mios_toml_integrity passed despite truncated mios.toml"
    mv "$backup" "$toml"

    _neg_gate check_mios_toml_integrity || die "check_mios_toml_integrity failed after restoration"

    log "check_mios_toml_integrity negative test passed"
}

test_privileged_quadlets_minimal() {
    log "Testing check_privileged_quadlets_minimal"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local backup="${toml}.negbak"
    cp "$toml" "$backup"

    # Strip comment from a root entry
    sed -i 's/"mios-ceph.container",.*#.*/"mios-ceph.container",/' "$toml"

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_privileged_quadlets_minimal >/dev/null 2>&1 && die "check_privileged_quadlets_minimal passed despite un-commented root entry"
    mv "$backup" "$toml"

    _neg_gate check_privileged_quadlets_minimal || die "check_privileged_quadlets_minimal failed after restoration"

    log "check_privileged_quadlets_minimal negative test passed"
}

test_container_names() {
    log "Testing check_container_names"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local backup="${toml}.cnbak"
    cp "$toml" "$backup"

    # Drop ONE ContainerName from the SSOT: Quadlet would then name that
    # container systemd-<unit>, which no `systemctl` name matches.
    python3 - "$toml" <<'PYEOF'
import re, sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
m = re.search(r'^ContainerName = "[^"]+"\n', s, re.M)
assert m, "no ContainerName to remove"
open(p, "w", encoding="utf-8").write(s[:m.start()] + s[m.end():])
PYEOF

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_container_names >/dev/null 2>&1 && die "check_container_names passed while a Quadlet declared no ContainerName"
    mv "$backup" "$toml"

    _neg_gate check_container_names || die "check_container_names failed after restoration"

    log "Test_container_names negative test passed"
}

test_firstboot_provisioners() {
    log "Testing check_firstboot_provisioners"
    local unit="${ROOT}/usr/lib/systemd/system/mios-models-firstboot.service"
    local backup="${unit}.negbak"
    cp "$unit" "$backup"

    # Break the sentinel gate: point it at a path the fetcher never writes, so
    # the oneshot would run on every boot forever.
    sed -i 's#^ConditionPathExists=.*#ConditionPathExists=!/var/lib/mios/.not-a-real-sentinel#' "$unit"

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_firstboot_provisioners >/dev/null 2>&1 && die "check_firstboot_provisioners passed despite a sentinel the fetcher never writes"

    mv "$backup" "$unit"
    _neg_gate check_firstboot_provisioners || die "check_firstboot_provisioners failed after restoration"

    log "Test_firstboot_provisioners negative test passed"
}

test_module_length() {
    log "Testing check_module_length"
    # Two directories deep on purpose: the former -maxdepth 1 body could not
    # see a nested module, which is where every real one lives.
    local dir="${ROOT}/usr/lib/mios/agent-pipe/mios_pipe/routing"
    mkdir -p "$dir"
    local dummy_file="${dir}/test_dummy_length.py"

    seq 1 801 > "$dummy_file"

    MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_module_length >/dev/null 2>&1 && die "Check_module_length passed despite 801-line file"

    rm -f "$dummy_file"
    _neg_gate check_module_length || die "Check_module_length failed after restoration"

    log "Test_module_length negative test passed"
}

test_vendored_assets_non_stub() {
    log "Testing check_vendored_assets_non_stub"
    local stub_file="${ROOT}/usr/share/mios/vendored/fake_stub.tmp"
    echo "Stub" > "$stub_file"

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_vendored_assets_non_stub >/dev/null 2>&1 && die "Check_vendored_assets_non_stub passed despite injected stub file"

    rm -f "$stub_file"
    _neg_gate check_vendored_assets_non_stub || die "Check_vendored_assets_non_stub failed after restoration"
    log "Test_vendored_assets_non_stub negative test passed"
}

test_resolved_env_lossless() {
    log "Testing check_resolved_env_lossless"
    local base_file="${ROOT}/usr/share/mios/reference/env-baseline.txt"
    if [[ ! -f "$base_file" ]]; then
        log "Env-baseline.txt absent"
        return 0
    fi
    local backup_tmp; backup_tmp="$(mktemp)"
    cp "$base_file" "$backup_tmp"
    echo "MIOS_INJECTED_DUMMY_KEY=injected_val" >> "$base_file"

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_resolved_env_lossless >/dev/null 2>&1 && die "Check_resolved_env_lossless passed despite injected baseline drift"

    cp "$backup_tmp" "$base_file"
    rm -f "$backup_tmp"
    local lossless_out
    if ! lossless_out=$(MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_resolved_env_lossless 2>&1); then
        # The check prints the actual +/- diff; swallowing it makes an
        # environment-dependent baseline impossible to diagnose from CI.
        printf '%s\n' "$lossless_out" | tail -n 25 >&2
        die "Check_resolved_env_lossless failed after restoration"
    fi
    log "Test_resolved_env_lossless negative test passed"
}

test_no_duplicate_value_key() {
    # Every case mutates the SSOT, never the tool: a new group, task_942's one exit, a joiner.
    log "Testing check_no_duplicate_value_key"
    local toml_file="${ROOT}/usr/share/mios/mios.toml" ledger="${ROOT}/usr/share/mios/reference/value-dup-baseline.tsv"
    local aliases="${ROOT}/usr/share/mios/reference/value-aliases.tsv" bak_file="${ROOT}/usr/share/mios/mios.toml.bak" dup_value exit_rc=0
    [[ -f "$toml_file" && -f "$ledger" ]] || { log "Test_no_duplicate_value_key skipped (SSOT or ledger absent)"; return 0; }
    _neg_gate check_no_duplicate_value_key || die "check_no_duplicate_value_key failed on the unmutated tree: ${_NEG_GATE_OUT}"
    cp "$toml_file" "$bak_file"; cp "$aliases" "${aliases}.bak"
    # Case 1 -- a brand-new group: two SSOT keys given one novel value.
    printf '\n[negtest_value_dup]\nalpha = "ZZNEGDUPVALUE4271"\nbeta = "ZZNEGDUPVALUE4271"\n' >> "$toml_file"
    _neg_gate check_no_duplicate_value_key && { mv -f "$bak_file" "$toml_file"; mv -f "${aliases}.bak" "$aliases"; die "check_no_duplicate_value_key passed despite two SSOT keys resolving to one unlisted value"; }
    # Case 2 -- task_942's one exit: the same plant passes once value-aliases.tsv classifies it keep-distinct.
    printf 'MIOS_NEGTEST_VALUE_DUP_ALPHA\tMIOS_NEGTEST_VALUE_DUP_BETA\tkeep-distinct\t# two planted settings that agree by accident\n' >> "$aliases"
    _neg_gate check_no_duplicate_value_key || exit_rc=$?; cp "$bak_file" "$toml_file"; mv -f "${aliases}.bak" "$aliases"
    (( exit_rc == 0 )) || { mv -f "$bak_file" "$toml_file"; die "check_no_duplicate_value_key rejected a keep-distinct coincidence: ${_NEG_GATE_OUT}"; }
    # Case 3 -- one new key joining a group the ledger tolerates: the count holds, only the per-group comparison sees it.
    dup_value="$(awk -F'\t' 'substr($0,1,1) != "#" && NF == 3 && $1 ~ /^[A-Za-z0-9._-]+$/ { print $1; exit }' "$ledger")"
    [[ -n "$dup_value" ]] || { mv -f "$bak_file" "$toml_file"; die "value-dup-baseline.tsv yielded no usable group value -- the ledger the gate ratchets against is empty or malformed"; }
    printf '\n[negtest_value_grow]\njoiner = "%s"\n' "$dup_value" >> "$toml_file"
    _neg_gate check_no_duplicate_value_key && { mv -f "$bak_file" "$toml_file"; die "check_no_duplicate_value_key passed despite a new SSOT key joining the already-listed value group '${dup_value}'"; }
    mv -f "$bak_file" "$toml_file"
    _neg_gate check_no_duplicate_value_key || die "check_no_duplicate_value_key failed after restoration: ${_NEG_GATE_OUT}"
    log "Test_no_duplicate_value_key negative test passed"
}

test_no_hardcoded_ssot_literal() {
    log "Testing check_no_hardcoded_ssot_literal"
    local inj_file="${ROOT}/automation/temp_inj_test_hardcode.sh"
    echo 'echo "hardcoded fedora-99"' > "$inj_file"
    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_no_hardcoded_ssot_literal >/dev/null 2>&1 && die "check_no_hardcoded_ssot_literal passed despite injected hardcoded fedora-99 literal"
    rm -f "$inj_file"
    _neg_gate check_no_hardcoded_ssot_literal || die "Check_no_hardcoded_ssot_literal failed after restoration"
    log "Test_no_hardcoded_ssot_literal passed"
}

test_pipeline_numbering() {
    log "Testing check_pipeline_numbering"
    local f="${ROOT}/automation/98-drift-checks.sh"
    local backup; backup="$(mktemp)"
    cp "$f" "$backup"
    printf '\n# NEG-TEST [98-drift-checks]   (99) injected colliding label\n' >> "$f"
    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "$f" check_pipeline_numbering >/dev/null 2>&1 && die "Check_pipeline_numbering passed despite an injected check label"
    cp "$backup" "$f"; rm -f "$backup"
    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "$f" check_pipeline_numbering >/dev/null 2>&1 \
        || die "Check_pipeline_numbering failed after restoration"
    log "Test_pipeline_numbering negative test passed"
}

test_value_aliases() {
    log "Testing check_value_aliases"
    local f="${ROOT}/usr/share/mios/reference/value-aliases.tsv"
    [[ -f "$f" ]] || { log "Value-aliases.tsv absent"; return 0; }
    local backup; backup="$(mktemp)"
    cp "$f" "$backup"
    printf 'MIOS_A2A_COUNCIL\tMIOS_A2A_DISCOVER_PORT\tderive\n' >> "$f"
    _neg_gate check_value_aliases && { cp "$backup" "$f"; die "Check_value_aliases passed despite a derive-pair with divergent values"; }
    # task_942: keep-distinct needs two declarations and a reason; an alias or a bare pair fails.
    local row; for row in 'MIOS_PORTS_RADOSGW\tMIOS_RADOSGW_PORT\tkeep-distinct\t# one port, mislabelled a coincidence' \
        'MIOS_SCHED_URGENCY_HIGH\tMIOS_SSOT_TABLES_MAX_UNCONSUMED\tkeep-distinct'; do cp "$backup" "$f"; printf "${row}\n" >> "$f"
        _neg_gate check_value_aliases && { cp "$backup" "$f"; rm -f "$backup"; die "Check_value_aliases accepted an unjustified keep-distinct row: ${row}"; }
    done
    cp "$backup" "$f"; rm -f "$backup"
    _neg_gate check_value_aliases || die "Check_value_aliases failed after restoration: ${_NEG_GATE_OUT}"
    log "Test_value_aliases negative test passed"
}

test_bash_phase_ratchet() {
    log "Testing check_bash_phase_ratchet"
    local dummy_script="${ROOT}/automation/99-dummy-test-phase.sh"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak; bak="$(mktemp)"; cp "$toml" "$bak"
    # Every exit path removes the probe: die() used to leave it in automation/.
    _bpr_fail() { rm -f "$dummy_script"; cp "$bak" "$toml"; rm -f "$bak"; unset -f _bpr_fail; die "$1"; }

    touch "$dummy_script"
    _neg_gate check_bash_phase_ratchet && _bpr_fail "Check_bash_phase_ratchet passed despite extra bash phase script exceeding ratchet baseline"
    [[ "$_NEG_GATE_OUT" == *"fold a phase"* ]] || _bpr_fail "Check_bash_phase_ratchet failed the extra-script plant for the wrong reason: $_NEG_GATE_OUT"
    rm -f "$dummy_script"

    # Slack is the hole the probe above fell through (a ceiling of 79 over 77
    # scripts), and an absent ceiling is not a default.
    sed -i 's/^max_phase_scripts = \([0-9]*\)/max_phase_scripts = 9\1/' "$toml"
    cmp -s "$bak" "$toml" && _bpr_fail "the slack plant landed nowhere -- [build.ratchet].max_phase_scripts moved"
    _neg_gate check_bash_phase_ratchet && _bpr_fail "Check_bash_phase_ratchet passed with a ceiling above the phase-script count"
    cp "$bak" "$toml"
    sed -i '/^max_phase_scripts = /d' "$toml"
    _neg_gate check_bash_phase_ratchet && _bpr_fail "Check_bash_phase_ratchet passed with [build.ratchet].max_phase_scripts absent"

    cp "$bak" "$toml"; rm -f "$bak"; unset -f _bpr_fail
    _neg_gate check_bash_phase_ratchet || die "Check_bash_phase_ratchet failed after restoration: $_NEG_GATE_OUT"
    log "Test_bash_phase_ratchet negative test passed"
}

test_check_no_silent_tool_skips() {
    log "Testing check_no_silent_tool_skips..."
    local dummy="$ROOT/automation/lint-dummy-skip-test.sh"
    echo '#!/bin/bash' > "$dummy"
    echo 'command -v non_existent_tool || return 0' >> "$dummy"
    chmod +x "$dummy"

    MIOS_DRIFT_REQUIRE_TOOLS=1 bash "$ROOT/automation/98-drift-checks.sh" check_no_silent_tool_skips >/dev/null 2>&1 && die "check_no_silent_tool_skips failed to detect unhandled silent tool skip"
    rm -f "$dummy"
    log "check_no_silent_tool_skips correctly caught unhandled silent tool skip"
}

test_check_negatives_are_effective() {
    log "Testing check_negatives_are_effective..."
    local neg_sh="${ROOT}/tests/drift-gate-negatives.sh"
    local bak; bak="$(mktemp)"
    cp "$neg_sh" "$bak"

    # Test 1: Inject a function that only logs a check name (no gate call execution)
    {
        echo "test_fake_ineffective_log_only() {"
        echo "    log \"Testing check_version_ssot\""
        echo "    die \"Fake failure assertion\""
        echo "}"
    } >> "$neg_sh"

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_negatives_are_effective >/dev/null 2>&1 && die "check_negatives_are_effective passed despite logging-only fake negative test"

    # Test 2: Inject a function longer than 1500 chars with valid gate call to prove full scanning
    cp "$bak" "$neg_sh"
    {
        echo "test_fake_long_effective() {"
        echo "    log \"Testing check_version_ssot...\""
        for _ in {1..50}; do
            echo "    # Padding line to ensure function body exceeds 1500 characters in length"
        done
        echo "    if ! _neg_gate check_version_ssot; then"
        echo "        die \"check_version_ssot failed\""
        echo "    fi"
        echo "}"
    } >> "$neg_sh"

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_negatives_are_effective >/dev/null 2>&1 || die "check_negatives_are_effective failed on long function with valid gate call (>1500 chars)"

    cp "$bak" "$neg_sh"; rm -f "$bak"
    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_negatives_are_effective >/dev/null 2>&1 || die "check_negatives_are_effective failed on HEAD after restoration"
    log "check_negatives_are_effective negative test passed"
}

test_pipefail_grep_lint() {
    log "Testing check_pipefail_grep_lint..."
    local neg_sh="${ROOT}/tests/drift-gate-negatives.sh"
    local bak; bak="$(mktemp)"
    cp "$neg_sh" "$bak"

    {
        echo "test_fake_pipefail_grep() {"
        echo "    cat /dev/null | grep -q \"foo\" || die \"failed\""
        echo "}"
    } >> "$neg_sh"

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_pipefail_grep_lint >/dev/null 2>&1 && die "check_pipefail_grep_lint passed despite injected piped grep reading from non-echo/printf"

    cp "$bak" "$neg_sh"; rm -f "$bak"
    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_pipefail_grep_lint >/dev/null 2>&1 || die "check_pipefail_grep_lint failed on HEAD after restoration"
    log "check_pipefail_grep_lint negative test passed"
}

test_check_skip_list_covered() {
    log "Testing check_skip_list_covered"
    local toml="${ROOT}/usr/share/mios/mios.toml" wf="${ROOT}/.github/workflows/mios-ci.yml"
    local tbak wbak plant; tbak="$(mktemp)"; wbak="$(mktemp)"; plant="$(mktemp)"; cp "$toml" "$tbak"; cp "$wf" "$wbak"
    _sl_fail() { cp "$tbak" "$toml"; cp "$wbak" "$wf"; rm -f "$tbak" "$wbak" "$plant"; unset -f _sl_fail _sl_plant; die "$1"; }
    # $1: keys added under [ci.globs.agent-pipe]; $2: what the violation must name.
    _sl_plant() {
        cp "$tbak" "$toml"; printf '%s\n' "$1" > "$plant"
        sed -i "/^\[ci\.globs\.agent-pipe\]\$/r $plant" "$toml"
        cmp -s "$tbak" "$toml" && _sl_fail "the [ci.globs.agent-pipe] plant did not land"
        _neg_gate check_skip_list_covered && _sl_fail "check_skip_list_covered passed with $2 planted"
        [[ "$_NEG_GATE_OUT" == *"$2"* ]] || _sl_fail "check_skip_list_covered failed without naming $2: $_NEG_GATE_OUT"
    }
    _neg_gate check_skip_list_covered || _sl_fail "check_skip_list_covered red on the CLEAN tree: $_NEG_GATE_OUT"
    _sl_plant 'skip = ["test_mios_gateway_queue.py"]' "skip_reason"
    _sl_plant $'skip = ["test_mios_no_such_suite.py"]\nskip_reason = "negative probe"' "test_mios_no_such_suite.py"
    cp "$tbak" "$toml"; printf '      # SKIP=negative-probe\n' >> "$wf"
    _neg_gate check_skip_list_covered && _sl_fail "check_skip_list_covered passed with an inline SKIP= in a workflow"
    [[ "$_NEG_GATE_OUT" == *"inline SKIP="* ]] || _sl_fail "check_skip_list_covered failed without naming the inline SKIP="
    cp "$wbak" "$wf"; rm -f "$tbak" "$wbak" "$plant"; unset -f _sl_fail _sl_plant
    _neg_gate check_skip_list_covered || die "check_skip_list_covered failed after restoration: $_NEG_GATE_OUT"
    log "check_skip_list_covered negative test passed"
}

test_ai_manifests_fresh() {
    log "Testing check_ai_manifests_fresh"
    local mf="${ROOT}/tools/manifest.json"
    local backup; backup="$(mktemp)"
    cp "$mf" "$backup"
    echo '{"drift":"injected"}' > "$mf"
    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_ai_manifests_fresh >/dev/null 2>&1 && die "check_ai_manifests_fresh passed despite injected manifest drift"
    cp "$backup" "$mf"; rm -f "$backup"
    local restored_out
    if ! restored_out=$(MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_ai_manifests_fresh 2>&1); then
        printf '%s\n' "$restored_out" | tail -n 15 >&2
        die "check_ai_manifests_fresh failed after restoration"
    fi
    log "test_ai_manifests_fresh passed"
}

test_unpinned_runtime_fetches() {
    log "Testing check_unpinned_runtime_fetches"
    local probe="${ROOT}/usr/share/mios/windows/negative-probe-fetch.ps1"
    # A runtime download with no SHA-256 verification (ADR-0003).
    printf 'Invoke-WebRequest -Uri "https://example.com/x.zip" -OutFile x.zip\n' > "$probe"

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_unpinned_runtime_fetches >/dev/null 2>&1 && die "check_unpinned_runtime_fetches passed despite an unverified download"
    rm -f "$probe"

    _neg_gate check_unpinned_runtime_fetches || die "check_unpinned_runtime_fetches failed after restoration"
    log "test_unpinned_runtime_fetches passed"
}

test_windows_exe_provenance() {
    log "Testing check_windows_exe_provenance"
    local probe="${ROOT}/usr/share/mios/windows/negative-probe-tool.exe"
    # A shipped .exe with no corresponding .cs source to build it from.
    printf 'MZ-not-a-real-binary\n' > "$probe"

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_windows_exe_provenance >/dev/null 2>&1 && die "check_windows_exe_provenance passed despite a source-less .exe"
    rm -f "$probe"

    _neg_gate check_windows_exe_provenance || die "check_windows_exe_provenance failed after restoration"
    log "test_windows_exe_provenance passed"
}

test_ps_redirectors() {
    log "Testing check_ps_redirectors"
    # The check asserts the thin redirector .ps1 entry points stay thin.
    # Pick whichever one this tree actually has.
    local target=""
    local f
    for f in mios-build-local.ps1 run-pipeline.ps1; do
        if [ -f "${ROOT}/$f" ]; then target="${ROOT}/$f"; break; fi
    done
    if [ -z "$target" ]; then
        log "no redirector present -- skipping test_ps_redirectors"
        return 0
    fi

    local backup; backup="$(mktemp)"
    cp "$target" "$backup"
    # Fatten it well past the line ceiling.
    for _ in $(seq 1 120); do echo "# negative-test filler line" >> "$target"; done

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_ps_redirectors >/dev/null 2>&1 && die "check_ps_redirectors passed despite an over-long redirector"
    cp "$backup" "$target"; rm -f "$backup"

    _neg_gate check_ps_redirectors || die "check_ps_redirectors failed after restoration"
    log "test_ps_redirectors passed"
}

test_cargo_deny() {
    log "Testing check_cargo_deny"
    local policy="${ROOT}/tools/native/deny.toml"
    local backup; backup="$(mktemp)"
    cp "$policy" "$backup"
    rm -f "$policy"

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_cargo_deny >/dev/null 2>&1 && die "check_cargo_deny passed despite a missing supply-chain policy"
    cp "$backup" "$policy"; rm -f "$backup"

    _neg_gate check_cargo_deny || die "check_cargo_deny failed after restoration"
    log "test_cargo_deny passed"
}

test_powershell_parse() {
    log "Testing check_powershell_parse"

    # Check if we can actually run the parser, skip if not available
    local dry_run
    dry_run=$(bash "${ROOT}/automation/lint-powershell.sh" 2>&1 || true)
    if echo "$dry_run" | grep -q "skipping AST parse-gate"; then
        log "powershell missing or un-executable, skipping negative test"
        return 0
    fi

    local bad="${ROOT}/automation/lint-ps-negative-probe.ps1"
    # An unterminated block is an AST parse error in any PowerShell version.
    printf 'function Broken {\n' > "$bad"
    # lint-powershell.sh enumerates `git ls-files "*.ps1"`, so an UNTRACKED
    # probe is invisible and the check would pass for the wrong reason.
    # `add -N` records intent-to-add, which is enough for ls-files.
    git -C "$ROOT" add -N "$bad" >/dev/null 2>&1 || true

    if MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_powershell_parse >/dev/null 2>&1; then
        git -C "$ROOT" rm --cached -q --force "$bad" >/dev/null 2>&1 || true
        die "check_powershell_parse passed despite an unparseable .ps1"
    fi
    git -C "$ROOT" rm --cached -q --force "$bad" >/dev/null 2>&1 || true
    rm -f "$bad"

    _neg_gate check_powershell_parse || die "check_powershell_parse failed after restoration"
    log "test_powershell_parse passed"
}

test_ports_category_schema() {
    log "Testing check_ports_category_schema"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local backup; backup="$(mktemp)"
    cp "$toml" "$backup"

    # Collide two categories by dragging one band on top of another.
    python3 - "$toml" <<'PY'
import re, sys
p = sys.argv[1]
with open(p, "r", encoding="utf-8", newline="") as fh:
    text = fh.read()
# line-ending agnostic: the file is CRLF, so a literal "\n" match silently no-ops
new, n = re.subn(r"(\[ports\.categories\.webtools\]\s*\r?\n\s*base\s*=\s*)8800",
                 r"\g<1>8700", text, count=1)
if n != 1:
    sys.exit("negative-test injection did not apply")
with open(p, "w", encoding="utf-8", newline="") as fh:
    fh.write(new)
PY

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_ports_category_schema >/dev/null 2>&1 && die "check_ports_category_schema passed despite an injected category band collision"
    cp "$backup" "$toml"; rm -f "$backup"

    _neg_gate check_ports_category_schema || die "check_ports_category_schema failed after restoration"
    log "test_ports_category_schema passed"
}

test_globals_generated() {
    log "Testing check_globals_generated"
    local target="${ROOT}/automation/lib/globals.sh"
    local backup; backup="$(mktemp)"
    cp "$target" "$backup"

    # Hand-edit a generated constant -- exactly what the gate exists to catch.
    printf '\n[ -n "${MIOS_INJECTED_DRIFT+x}" ] || MIOS_INJECTED_DRIFT=1\n' >> "$target"

    MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_globals_generated >/dev/null 2>&1 && die "check_globals_generated passed despite a hand-edited generated resolver"
    cp "$backup" "$target"; rm -f "$backup"

    local restored_out
    if ! restored_out=$(MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_globals_generated 2>&1); then
        printf '%s\n' "$restored_out" | tail -n 10 >&2
        # show the first divergent line so CI names the culprit
        ( cd "$ROOT" && {
            local _g=""
            if command -v mios-gen >/dev/null 2>&1; then _g="mios-gen";
            elif [[ -x tools/native/target/release/mios-gen ]]; then _g="tools/native/target/release/mios-gen";
            elif [[ -x tools/native/target/debug/mios-gen ]]; then _g="tools/native/target/debug/mios-gen";
            fi
            if [[ -n "$_g" ]]; then
                "$_g" render-globals --root "$ROOT"
            elif [[ -f tools/render-globals.py ]]; then
                python3 tools/render-globals.py
            fi
          } >/dev/null 2>&1 \
          && git --no-pager diff --unified=0 -- automation/lib/globals.sh automation/lib/globals.ps1 \
             | head -n 20 ) >&2 || true
        die "check_globals_generated failed after restoration"
    fi
    log "test_globals_generated passed"
}

_FAILED=()
# The gate's own native_bin, not a copy: CI installs the catalog via 55-native-build.sh
# (target-triple dirs, then /usr/bin), where a tools/native/target/{release,debug} probe never looks.
eval "$(sed -n '/^native_bin() {$/,/^}$/p' "${ROOT}/automation/98-drift-checks.sh")"
declare -F native_bin >/dev/null || die "automation/98-drift-checks.sh no longer defines native_bin()"
_task_bin() { native_bin mios-task || die "mios-task is not built (cd tools/native && cargo build -p mios-task)"; }
_frozen_text() { # $1 = a frozen list, e.g. MiOS:TASKS.md: its bytes, rebuilt from tasks.jsonl (ADR-0028)
    local b; b="$(_task_bin)" || exit 1
    "$b" source "$1" --root "$ROOT" || die "mios-task could not rebuild $1 from tasks.jsonl"
}

_frozen_edit() { # $1 = frozen list, $2 = Python regex (re.M), $3 = replacement: plant into the first slice that matches
    python3 - "${ROOT}/tasks.jsonl" "$1" "$2" "$3" <<'PY'
import json, re, sys
path, src, pat, rep = sys.argv[1:]
lines = open(path, encoding="utf-8").read().split("\n")
for i, line in enumerate(lines):
    if not line:
        continue
    rec = json.loads(line)
    for s in (rec.get("provenance") or {}).get("sources") or []:
        if s["file"] == src and re.search(pat, s["text"], re.M):
            s["text"] = re.sub(pat, lambda _m: rep, s["text"], count=1, flags=re.M)
            lines[i] = json.dumps(rec, ensure_ascii=False)
            open(path, "w", encoding="utf-8").write("\n".join(lines))
            sys.exit(0)
sys.exit(1)
PY
}

_run_test() {
    if ( "$1" ); then :; else _FAILED+=("$1"); fi
}

_neg_gate() {
    _NEG_GATE_OUT="$(MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" \
    MIOS_DRIFT_ROOT="$ROOT" \
    MIOS_DRIFT_REQUIRE_TOOLS="${MIOS_DRIFT_REQUIRE_TOOLS:-0}" \
        bash "${ROOT}/automation/98-drift-checks.sh" "$1" 2>&1)"
}

test_fleet_safety() {
    log "Testing check_fleet_safety"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak="${toml}.fleetbak"
    cp "$toml" "$bak"

    # Drop an accepted hazard the detector still reproduces. The register is
    # shrink-only by RETIREMENT -- the detector stops finding it -- not by
    # editing the list, so removing a live entry must fail.
    python3 - "$toml" <<'PYEOF'
import sys
p = sys.argv[1]
with open(p, "r", encoding="utf-8", newline="") as fh:
    t = fh.read()
t = t.replace('  "pacemaker-unfenced",\n', "", 1)
with open(p, "w", encoding="utf-8", newline="") as fh:
    fh.write(t)
PYEOF

    _neg_gate check_fleet_safety && die "check_fleet_safety passed with a live hazard missing from the register"

    cp "$bak" "$toml" && rm -f "$bak"
    _neg_gate check_fleet_safety || die "check_fleet_safety failed after restoration"
    log "check_fleet_safety negative test passed"
}

test_cargo_manifest_generated() {
    log "Testing check_cargo_manifest_generated"
    # The defect: the generator's member list was a literal two crates behind
    # the tree, so a crate on disk could sit outside the workspace unreported.
    local manifest="${ROOT}/tools/native/Cargo.toml"
    local bak; bak="$(mktemp)"; cp "$manifest" "$bak"

    python3 - "$manifest" <<'PYEOF'
import sys
p = sys.argv[1]
with open(p, "r", encoding="utf-8", newline="") as fh:
    t = fh.read()
out = t.replace('    "xtask",\n', "", 1)
assert out != t, "the plant did not land -- the members list has no xtask entry"
with open(p, "w", encoding="utf-8", newline="") as fh:
    fh.write(out)
PYEOF

    _neg_gate check_cargo_manifest_generated && { cp "$bak" "$manifest"; rm -f "$bak"; \
        die "check_cargo_manifest_generated passed with a crate dropped from the workspace members"; }

    cp "$bak" "$manifest"; rm -f "$bak"
    _neg_gate check_cargo_manifest_generated || die "check_cargo_manifest_generated failed after restoration"
    log "check_cargo_manifest_generated negative test passed"
}

test_ai_metadata_fresh() {
    log "Testing check_ai_metadata_fresh"
    # The defect: usr/share/mios/ai/v1/metadata.json was exported from the AI-*
    # headers, but nothing regenerated or compared it, so it drifted thousands
    # of lines behind the tree. A hand edit to one entry must fail, naming it.
    local md="${ROOT}/usr/share/mios/ai/v1/metadata.json"
    local bak; bak="$(mktemp)"; cp "$md" "$bak"

    python3 - "$md" <<'PYEOF'
import sys
p = sys.argv[1]
with open(p, "r", encoding="utf-8", newline="") as fh:
    t = fh.read()
anchor = '"path": "usr/libexec/mios/mios-ai-metadata.py",\n      "hint": "'
out = t.replace(anchor, anchor + "planted ", 1)
assert out != t, "the plant did not land -- the exporter has no entry of its own"
with open(p, "w", encoding="utf-8", newline="") as fh:
    fh.write(out)
PYEOF

    if _neg_gate check_ai_metadata_fresh; then
        cp "$bak" "$md"; rm -f "$bak"
        die "check_ai_metadata_fresh passed with a hand-edited metadata.json entry"
    fi
    if [[ "$_NEG_GATE_OUT" != *"entry usr/libexec/mios/mios-ai-metadata.py: differs in hint"* ]]; then
        cp "$bak" "$md"; rm -f "$bak"
        printf '%s\n' "$_NEG_GATE_OUT" >&2
        die "check_ai_metadata_fresh failed but did not name the planted entry"
    fi

    cp "$bak" "$md"; rm -f "$bak"
    _neg_gate check_ai_metadata_fresh || { printf '%s\n' "$_NEG_GATE_OUT" >&2; die "check_ai_metadata_fresh failed after restoration"; }
    log "check_ai_metadata_fresh negative test passed"
}

test_tracked_readable() {
    log "Testing check_tracked_readable"
    # The defect: a file in the index but gone from the worktree leaves every
    # corpus-scanning gate reporting output byte-identical to a clean run,
    # because each one drops it with `except OSError: continue`.
    local vf="${ROOT}/tools/ascii-sweep.py"
    local bak; bak="$(mktemp)"; cp "$vf" "$bak"

    rm -f "$vf"
    _neg_gate check_tracked_readable && die "check_tracked_readable passed with a tracked file removed from the worktree"

    cp "$bak" "$vf"; rm -f "$bak"
    _neg_gate check_tracked_readable         || die "check_tracked_readable failed after restoration"
    log "check_tracked_readable negative test passed"
}

test_leaked_fixtures() {
    log "Testing check_leaked_fixtures"
    local vf="${ROOT}/VERSION"
    local bak; bak="$(mktemp)"; cp "$vf" "$bak"

    # Append a marker of the shape a negative test injects. VERSION strips
    # comment lines, so even a failed restore cannot change the resolved
    # version -- the file is chosen for that reason.
    printf '# mios-%stest-leak-probe\n' "neg" >> "$vf"

    _neg_gate check_leaked_fixtures && die "check_leaked_fixtures passed with an injected fixture in a tracked file"

    cp "$bak" "$vf"; rm -f "$bak"
    _neg_gate check_leaked_fixtures \
        || die "check_leaked_fixtures failed after restoration"
    log "check_leaked_fixtures negative test passed"
}

test_manpages() {
    log "Testing check_manpages"
    local page="${ROOT}/usr/share/man/man1/mios.1" existed=0
    [[ -f "$page" ]] && existed=1
    [[ "$existed" -eq 0 ]] && (which mios-gen >/dev/null 2>&1 && mios-gen render-manpages --root "$ROOT" >/dev/null 2>&1 || true)
    [[ -f "$page" ]] || { log "test_manpages skipped (no mios-gen)"; return 0; }
    local bak; bak="$(mktemp)"; cp "$page" "$bak"
    printf '.PP\nan edit the SSOT does not describe\n' >> "$page"
    _neg_gate check_manpages && {
        cp "$bak" "$page"; rm -f "$bak"; [[ "$existed" -eq 0 ]] && rm -f "$page"
        die "check_manpages passed with a page that drifted from the SSOT"
    }
    cp "$bak" "$page"; rm -f "$bak"; [[ "$existed" -eq 0 ]] && rm -f "$page"
    _neg_gate check_manpages || die "check_manpages failed after restoration"
    log "check_manpages negative test passed"
}

test_temp_fixture_cleanup() {
    log "Testing check_temp_fixture_cleanup"
    local probe="${ROOT}/tools/test_mios-leaky-fixture-probe.py"

    # A test that makes a temporary directory and never removes it leaves one
    # behind on every run; five turned up in an editor's source control view.
    {
        printf '%s\n' '#!/usr/bin/env python3'
        printf '%s\n' '# AI-hint: probe.'
        printf '%s\n' 'import tempfile'
        printf '%s\n' 'd = tempfile.mkdtemp()'
    } > "$probe"

    # The gate enumerates what git tracks, because an untracked local file is
    # not shipped. --intent-to-add makes the probe visible to that enumeration
    # without staging its content.
    git -C "$ROOT" add -N -- "$probe" >/dev/null 2>&1 || true

    _neg_gate check_temp_fixture_cleanup && {
        git -C "$ROOT" rm -q --cached -- "$probe" >/dev/null 2>&1 || true
        rm -f "$probe"
        die "check_temp_fixture_cleanup passed with a fixture nothing removes"
    }

    git -C "$ROOT" rm -q --cached -- "$probe" >/dev/null 2>&1 || true
    rm -f "$probe"
    _neg_gate check_temp_fixture_cleanup \
        || die "check_temp_fixture_cleanup failed after restoration"
    log "check_temp_fixture_cleanup negative test passed"
}

test_negatives_registered() {
    log "Testing check_negatives_registered"
    local harness="${ROOT}/tests/drift-gate-negatives.sh"
    local bak; bak="$(mktemp)"; cp "$harness" "$bak"

    # Define a test and never invoke it. Nineteen were in exactly this state:
    # written, committed, and proving nothing because nothing called them.
    printf '\ntest_mios_unreachable_probe() {\n    log "unreachable"\n}\n' >> "$harness"

    _neg_gate check_negatives_registered && {
        cp "$bak" "$harness"; rm -f "$bak"
        die "check_negatives_registered passed with a test nothing invokes"
    }

    cp "$bak" "$harness"; rm -f "$bak"
    _neg_gate check_negatives_registered \
        || die "check_negatives_registered failed after restoration"
    log "check_negatives_registered negative test passed"
}

test_variant_registry() {
    log "Testing check_variant_registry"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak; bak="$(mktemp)"; cp "$toml" "$bak"

    # Point a variant at an edition that does not exist. A registry naming a
    # thing the SSOT does not define is a product line nobody can build.
    sed 's|edition   = "mios-xbox"|edition   = "mios-nosuchedition"|' "$bak" > "$toml"

    _neg_gate check_variant_registry && {
        cp "$bak" "$toml"; rm -f "$bak"
        die "check_variant_registry passed with a variant naming a missing edition"
    }

    cp "$bak" "$toml"; rm -f "$bak"
    _neg_gate check_variant_registry \
        || die "check_variant_registry failed after restoration"
    log "check_variant_registry negative test passed"
}

test_deploy_formats() {
    log "Testing check_deploy_formats"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak; bak="$(mktemp)"; cp "$toml" "$bak"

    # Point a format at a build target the Justfile does not define. A matrix
    # that names an unbuildable format promises a deployment nobody can make.
    sed 's|target  = "wsl2"|target  = "no-such-target"|' "$bak" > "$toml"

    _neg_gate check_deploy_formats && {
        cp "$bak" "$toml"; rm -f "$bak"
        die "check_deploy_formats passed with a format naming a missing target"
    }

    cp "$bak" "$toml"; rm -f "$bak"
    _neg_gate check_deploy_formats \
        || die "check_deploy_formats failed after restoration"
    log "check_deploy_formats negative test passed"
}

test_verify_images() {
    log "Testing check_verify_images"
    local just="${ROOT}/Justfile"
    local bak; bak="$(mktemp)"; cp "$just" "$bak"

    # Put the shipped shape back: a recipe that ends on a failure counter the
    # glob loop never increments returns success over a build tree with nothing
    # in it, and publish pushes on the strength of it.
    sed 's|@python3 ./tools/verify-images.py|@true|' "$bak" > "$just"

    _neg_gate check_verify_images && {
        cp "$bak" "$just"; rm -f "$bak"
        die "check_verify_images passed with a verify-images recipe that checks nothing"
    }

    cp "$bak" "$just"; rm -f "$bak"
    _neg_gate check_verify_images \
        || die "check_verify_images failed after restoration"
    log "check_verify_images negative test passed"
}

test_header_comment_syntax() {
    log "Testing check_header_comment_syntax"
    local unit="${ROOT}/usr/lib/systemd/system/mios-agent-pipe.service"
    local bak; bak="$(mktemp)"; cp "$unit" "$bak"

    printf '/%s AI-doc: probe %s/\n' '*' '*' >> "$unit"

    _neg_gate check_header_comment_syntax && {
        cp "$bak" "$unit"; rm -f "$bak"
        die "check_header_comment_syntax passed with a C-style header in a unit"
    }

    cp "$bak" "$unit"; rm -f "$bak"
    _neg_gate check_header_comment_syntax \
        || die "check_header_comment_syntax failed after restoration"
    log "check_header_comment_syntax negative test passed"
}

test_repo_partition_label_ssot() {
    log "Testing check_repo_partition_label_ssot"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak; bak="$(mktemp)"; cp "$toml" "$bak"

    # Rename the table the label lives in. The old gate defaulted to the literal
    # "MiOS-Repo" when the anchor vanished, so it passed on exactly this change.
    sed 's|\[field\.repo_partition\]|[mios_negtest_gone.repo_partition]|' "$bak" > "$toml"

    _neg_gate check_repo_partition_label_ssot && {
        cp "$bak" "$toml"; rm -f "$bak"
        die "check_repo_partition_label_ssot passed with [field.repo_partition] renamed away"
    }

    cp "$bak" "$toml"; rm -f "$bak"
    _neg_gate check_repo_partition_label_ssot \
        || die "check_repo_partition_label_ssot failed after restoration"
    log "check_repo_partition_label_ssot negative test passed"
}

test_rust_test_coverage() {
    log "Testing check_rust_test_coverage"
    local temp_crate="${ROOT}/tools/native/temp_untested_crate"
    mkdir -p "${temp_crate}/src"
    cat << 'EOF' > "${temp_crate}/Cargo.toml"
[package]
name = "temp_untested_crate"
version = "0.1.0"
edition = "2021"
EOF
    cat << 'EOF' > "${temp_crate}/src/lib.rs"
pub fn dummy() {}
EOF

    _neg_gate check_rust_test_coverage && {
        rm -rf "$temp_crate"
        die "check_rust_test_coverage passed with an untested crate unregistered"
    }

    rm -rf "$temp_crate"
    _neg_gate check_rust_test_coverage \
        || die "check_rust_test_coverage failed after restoration"
    log "check_rust_test_coverage negative test passed"
}

test_ci_suite_coverage() {
    log "Testing check_ci_suite_coverage"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak; bak="$(mktemp)"; cp "$toml" "$bak"

    # Unregister one suite the registry lists. The gate must notice it is
    # tracked and running nowhere -- the state thirteen suites were in.
    grep -v 'tests/test-a2o-fallback.sh' "$bak" > "$toml"

    _neg_gate check_ci_suite_coverage && die "check_ci_suite_coverage passed with a tracked suite in no tier"

    cp "$bak" "$toml"; rm -f "$bak"
    _neg_gate check_ci_suite_coverage \
        || die "check_ci_suite_coverage failed after restoration"
    log "check_ci_suite_coverage negative test passed"
}

test_task_schema() {
    log "Testing check_task_schema"
    local store="${ROOT}/tasks.jsonl" sbak; sbak="$(mktemp)"; cp "$store" "$sbak"
    # A task with no Verify line is a task anyone can declare done, and a Dep
    # naming a missing id is an ordering nobody can follow.
    local probe='
## task_9999999 -- schema probe  (WS-PROCESS | P2 | S)
**Goal:** x
**What+How:** x
**Where:** x
**Done When:** x
**Why:** x
**Dep:** task_42424242
'
    _frozen_edit "MiOS:AGY-TASKS.md" '\Z' "$probe" || { rm -f "$sbak"; die "could not plant task_9999999"; }
    _neg_gate check_task_schema && { cp "$sbak" "$store"; rm -f "$sbak"; die "check_task_schema passed on a task with no Verify and a dangling Dep"; }
    cp "$sbak" "$store"; rm -f "$sbak"
    _neg_gate check_task_schema || die "check_task_schema failed after restoration"
    log "check_task_schema negative test passed"
}

test_neg_gate_harness() {
    log "Testing the _neg_gate harness itself"
    _neg_gate check_gate_registry         || die "_neg_gate returned non-zero for a check that passes"
    _neg_gate mios_negtest_no_such_check_exists && die "_neg_gate returned zero for a check that does not exist"
    log "_neg_gate harness verified in both directions"
}

test_adhoc_toml_parsers() {
    log "Testing check_adhoc_toml_parsers"
    local probe="${ROOT}/tests/mios-negtest-adhoc-toml.ps1"
    cat > "$probe" <<'PS1'
# negative-test probe: a hand-rolled TOML reader, which the gate must reject
$raw = Get-Content -Raw 'mios.toml'
if ($raw -match '(?s)\[ports\](.*)') { $ports = $Matches[1] }
PS1
    _neg_gate check_adhoc_toml_parsers && die "check_adhoc_toml_parsers passed despite a hand-rolled TOML parser"
    rm -f "$probe"
    _neg_gate check_adhoc_toml_parsers \
        || die "check_adhoc_toml_parsers failed after restoration"
    log "check_adhoc_toml_parsers negative test passed"
}

test_install_uninstall_symmetry() {
    log "Testing check_install_uninstall_symmetry"
    local uninst="${ROOT}/Uninstall-MiOS.ps1"
    local bak="${uninst}.negbak"
    cp "$uninst" "$bak"
    # Delete the scheduled-task sweep while [windows.owned_artifacts].task_names
    # still declares tasks -- the asymmetry the gate exists to catch.
    rm -f "$uninst" 2>/dev/null || true
    grep -v 'Unregister-ScheduledTask' "$bak" > "$uninst"
    _neg_gate check_install_uninstall_symmetry && die "check_install_uninstall_symmetry passed despite a missing task sweep"
    rm -f "$uninst" 2>/dev/null || true
    cp "$bak" "$uninst" && rm -f "$bak"
    _neg_gate check_install_uninstall_symmetry \
        || die "check_install_uninstall_symmetry failed after restoration"
    log "check_install_uninstall_symmetry negative test passed"
}

test_ps_port_fallback_ssot() {
    log "Testing check_ps_port_fallback_ssot"
    local f="${ROOT}/usr/share/mios/windows/mios-tailscale-serve.ps1"
    local bak="${f}.negbak"
    cp "$f" "$bak"
    # Drift one last-resort literal away from mios.toml [ports].cockpit.
    rm -f "$f" 2>/dev/null || true
    sed "s/'cockpit' 8110/'cockpit' 9090/" "$bak" > "$f"
    _neg_gate check_ps_port_fallback_ssot && die "check_ps_port_fallback_ssot passed despite a drifted port fallback"
    rm -f "$f" 2>/dev/null || true
    cp "$bak" "$f" && rm -f "$bak"
    _neg_gate check_ps_port_fallback_ssot \
        || die "check_ps_port_fallback_ssot failed after restoration"
    log "check_ps_port_fallback_ssot negative test passed"
}

test_github_slug_casing() {
    log "Testing check_github_slug_casing"
    local probe="${ROOT}/tests/mios-negtest-slug.txt"
    # Assembled from fragments: spelling the bad slug literally here would make
    # this very file a standing violation of the check it is testing.
    local host='raw.githubusercontent.com'
    local badorg='MiOS'
    badorg="${badorg}-DEV"
    printf 'curl https://%s/%s/mios-bootstrap/main/bootstrap.ps1\n' "$host" "$badorg" > "$probe"
    _neg_gate check_github_slug_casing && die "check_github_slug_casing passed despite a non-canonical org slug"
    rm -f "$probe"
    _neg_gate check_github_slug_casing \
        || die "check_github_slug_casing failed after restoration"
    log "check_github_slug_casing negative test passed"
}

test_ps_encoding_and_bom() {
    log "Testing check_ps_encoding_and_bom"
    local probe="${ROOT}/tests/mios-negtest-bom.ps1"
    # Non-ASCII with no BOM: exactly what PowerShell 5.1 would read as ANSI.
    printf 'Write-Host "run \xE2\x94\x80\xE2\x94\x80 done"\n' > "$probe"
    if _neg_gate check_ps_encoding_and_bom; then
        git -C "$ROOT" rm -q --cached --force -- "$probe" >/dev/null 2>&1 || true; rm -f "$probe"
        die "check_ps_encoding_and_bom passed despite non-ASCII with no BOM"
    fi
    git -C "$ROOT" rm -q --cached --force -- "$probe" >/dev/null 2>&1 || true; rm -f "$probe"
    _neg_gate check_ps_encoding_and_bom \
        || die "check_ps_encoding_and_bom failed after restoration"
    log "check_ps_encoding_and_bom negative test passed"
}

test_ps_irm_iex_entry() {
    log "Testing check_ps_encoding_and_bom (irm|iex entry point)"
    # An isolated root: the rule is about one file's bytes, not the tree.
    local tmp; tmp="$(mktemp -d)"
    printf '\xEF\xBB\xBF# Designed for: irm https://example.invalid/x.ps1 | iex\nparam([string]$A = "a")\n' > "${tmp}/x.ps1"
    if MIOS_DRIFT_ROOT="$tmp" python3 "${ROOT}/tools/drift-checks.py" ps-encoding-and-bom >"${tmp}/out" 2>&1 \
        || ! grep -q "x.ps1 is an irm|iex entry point" "${tmp}/out"; then
        rm -rf "$tmp"; die "check_ps_encoding_and_bom passed a BOM on an irm|iex entry point"
    fi
    printf '# Designed for: irm https://example.invalid/x.ps1 | iex\nparam([string]$A = "a")\n' > "${tmp}/x.ps1"
    if ! MIOS_DRIFT_ROOT="$tmp" python3 "${ROOT}/tools/drift-checks.py" ps-encoding-and-bom >"${tmp}/out" 2>&1; then
        cat "${tmp}/out" >&2; rm -rf "$tmp"; die "check_ps_encoding_and_bom rejected a pure-ASCII irm|iex entry point"
    fi
    rm -rf "$tmp"
    log "check_ps_encoding_and_bom irm|iex negative test passed"
}

test_secret_handling() {
    log "Testing check_secret_handling"
    local probe="${ROOT}/tests/mios-negtest-secrets.ps1"
    printf '$SecretsFile = Join-Path $env:TEMP "mios-secrets.env"\n' > "$probe"
    if _neg_gate check_secret_handling; then
        git -C "$ROOT" rm -q --cached --force -- "$probe" >/dev/null 2>&1 || true; rm -f "$probe"
        die "check_secret_handling passed despite a plaintext %TEMP% secrets path"
    fi
    git -C "$ROOT" rm -q --cached --force -- "$probe" >/dev/null 2>&1 || true; rm -f "$probe"
    _neg_gate check_secret_handling \
        || die "check_secret_handling failed after restoration"
    log "check_secret_handling negative test passed"
}

test_wsl_distro_resolution() {
    log "Testing check_wsl_distro_resolution"
    # The check only scans usr/share/mios/windows, so the probe must live there.
    local probe="${ROOT}/usr/share/mios/windows/mios-negtest-distro.ps1"
    printf 'wsl.exe -d podman-MiOS-DEV -- true\n' > "$probe"
    _neg_gate check_wsl_distro_resolution && die "check_wsl_distro_resolution passed despite a hardcoded distro literal"
    rm -f "$probe"
    _neg_gate check_wsl_distro_resolution \
        || die "check_wsl_distro_resolution failed after restoration"
    log "check_wsl_distro_resolution negative test passed"
}

test_unit_security() {
    log "Testing check_unit_security"
    local attest_file="${ROOT}/usr/lib/systemd/system/mios-attest.service"
    local bak; bak="$(mktemp)"; cp "$attest_file" "$bak"

    # Ruling Q7: WantedBy= in mios-attest.service must be rejected
    printf '\n[Install]\nWantedBy=multi-user.target\n' >> "$attest_file"
    if _neg_gate check_unit_security; then
        cp "$bak" "$attest_file"; rm -f "$bak"
        die "check_unit_security passed despite WantedBy in mios-attest.service"
    fi
    if [[ "$_NEG_GATE_OUT" != *"mios-attest"* ]]; then
        cp "$bak" "$attest_file"; rm -f "$bak"
        die "check_unit_security failed without naming mios-attest: $_NEG_GATE_OUT"
    fi
    cp "$bak" "$attest_file"; rm -f "$bak"

    # Ruling Q7: preset enable must also be rejected
    local preset_file="${ROOT}/usr/lib/systemd/system-preset/90-mios.preset"
    local pbak; pbak="$(mktemp)"; cp "$preset_file" "$pbak"
    printf '\nenable mios-attest.service\n' >> "$preset_file"
    if _neg_gate check_unit_security; then
        cp "$pbak" "$preset_file"; rm -f "$pbak"
        die "check_unit_security passed despite enable mios-attest.service in preset"
    fi
    if [[ "$_NEG_GATE_OUT" != *"mios-attest"* ]]; then
        cp "$pbak" "$preset_file"; rm -f "$pbak"
        die "check_unit_security failed without naming mios-attest in preset: $_NEG_GATE_OUT"
    fi
    cp "$pbak" "$preset_file"; rm -f "$pbak"

    _neg_gate check_unit_security || die "check_unit_security failed after restoration: $_NEG_GATE_OUT"
    log "check_unit_security negative test passed"
}

test_unit_dependency_closure() {
    log "Testing check_unit_dependency_closure"
    local probe="${ROOT}/usr/lib/systemd/system/mios-negtest-dangling.service"
    cat > "$probe" <<'UNIT'
[Unit]
Description=negative-test probe with a dependency that resolves to nothing
After=mios-this-unit-does-not-exist.service

[Service]
ExecStart=/bin/true
UNIT
    _neg_gate check_unit_dependency_closure && die "check_unit_dependency_closure passed despite a dangling After= target"
    rm -f "$probe"
    _neg_gate check_unit_dependency_closure \
        || die "check_unit_dependency_closure failed after restoration"
    log "check_unit_dependency_closure negative test passed"
}

test_comment_lex_equivalence() {
    log "Testing check_comment_lex_equivalence"
    local d; d="$(mktemp -d)"
    # A native lexer that reports no comment at all must never pass as equivalent.
    printf '#!/bin/sh\necho "[]"\n' > "$d/mios-comment-lex"; chmod +x "$d/mios-comment-lex"
    MIOS_NATIVE_BIN_DIR="$d" _neg_gate check_comment_lex_equivalence && { rm -rf "$d"; die "check_comment_lex_equivalence passed with a native lexer that drops every block"; }
    rm -f "$d/mios-comment-lex"
    MIOS_NATIVE_BIN_DIR="$d" MIOS_DRIFT_REQUIRE_TOOLS=1 _neg_gate check_comment_lex_equivalence && { rm -rf "$d"; die "check_comment_lex_equivalence passed with no native lexer under MIOS_DRIFT_REQUIRE_TOOLS=1"; }
    rm -rf "$d"
    _neg_gate check_comment_lex_equivalence || die "check_comment_lex_equivalence failed on the unmodified tree: ${_NEG_GATE_OUT}"
    log "check_comment_lex_equivalence negative test passed"
}

# Counts, not exit codes: narrative and stale-refs are both above a ceiling of
# 0, so an exit-code arm passes whether or not the plant was seen.
_docs_counts() {
    _neg_gate check_docs_ratchet || :
    local n m
    n="$(printf '%s' "$_NEG_GATE_OUT" | sed -n 's/.*narrative=\([0-9][0-9]*\)\/.*/\1/p' | head -1)"
    m="$(printf '%s' "$_NEG_GATE_OUT" | sed -n 's/.*stale-refs=\([0-9][0-9]*\)\/.*/\1/p' | head -1)"
    case "${n}:${m}" in *[!0-9:]*|:*|*:) echo "-1:-1";; *) echo "${n}:${m}";; esac
}

test_docs_ratchet() {
    log "Testing check_docs_ratchet"
    local probe="${ROOT}/automation/mios-negtest-narrative.sh"
    local base planted restored
    base="$(_docs_counts)"
    [[ "$base" != "-1:-1" ]] \
        || die "test_docs_ratchet: could not read narrative/stale-refs counts; the arms below would prove nothing"

    cat > "$probe" <<'EOF'
#!/usr/bin/env bash
# The operator hit a regression here previously and the root cause was a race.
# This was reverted once, then reinstated with a different invariant entirely.
# See ADR-0004 and Law 8 for why the projection must never be bypassed here.
# Previously the alternative was rejected because it degraded the serving lane.
# The rationale is recorded so the next reader does not repeat the experiment.
# An incident followed and the operator asked for this guard to remain in place.
true
EOF
    git -C "$ROOT" add -N -- "$probe" >/dev/null 2>&1
    planted="$(_docs_counts)"
    git -C "$ROOT" rm -q --cached --force -- "$probe" >/dev/null 2>&1 || :
    rm -f "$probe"
    [[ "${planted%%:*}" -gt "${base%%:*}" ]] \
        || die "check_docs_ratchet did not count the planted narrative block (narrative ${base%%:*} -> ${planted%%:*})"

    local stale_probe="${ROOT}/automation/mios-negtest-stale-ref.sh"
    cat > "$stale_probe" <<'EOF'
#!/usr/bin/env bash
# AI-related: automation/mios-negtest-missing-target-xyz-99.sh
true
EOF
    git -C "$ROOT" add -N -- "$stale_probe" >/dev/null 2>&1
    planted="$(_docs_counts)"
    git -C "$ROOT" rm -q --cached --force -- "$stale_probe" >/dev/null 2>&1 || :
    rm -f "$stale_probe"
    [[ "${planted##*:}" -gt "${base##*:}" ]] \
        || die "check_docs_ratchet did not count the planted stale reference (stale-refs ${base##*:} -> ${planted##*:})"

    restored="$(_docs_counts)"
    [[ "$restored" == "$base" ]] \
        || die "check_docs_ratchet did not return to its baseline ($base -> $restored)"
    log "check_docs_ratchet negative test passed"
}

test_docs_ratchet_monotone() {
    log "Testing check_docs_ratchet_monotone"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local backup; backup="$(mktemp)"
    cp "$toml" "$backup"
    # Raising a ceiling is the loosening move the gate exists to reject.
    sed -i 's/^max_unmigrated_narrative = .*/max_unmigrated_narrative = 99999/' "$toml"
    _neg_gate check_docs_ratchet_monotone && die "check_docs_ratchet_monotone passed despite a RAISED ceiling"
    cp "$backup" "$toml"
    _neg_gate check_docs_ratchet_monotone \
        || { rm -f "$backup"; die "check_docs_ratchet_monotone failed after restoration"; }

    # The durable floor: a ceiling matching HEAD but above the lowest value ever
    # recorded is still a raise, and the HEAD comparison alone cannot see it.
    local floor="${ROOT}/usr/share/mios/reference/doc-ratchet-floor.tsv"
    local fbackup; fbackup="$(mktemp)"
    cp "$floor" "$fbackup"
    python3 -c "import re, sys; p=sys.argv[1]; s=open(p).read(); s=re.sub(r'^max_unmigrated_narrative\t.*', 'max_unmigrated_narrative\t-1', s, flags=re.M); open(p,'w').write(s)" "$floor"
    _neg_gate check_docs_ratchet_monotone && die "check_docs_ratchet_monotone passed despite a ceiling above the recorded floor"
    cp "$fbackup" "$floor"; rm -f "$fbackup"
    _neg_gate check_docs_ratchet_monotone \
        || die "check_docs_ratchet_monotone failed after floor restoration"
    log "check_docs_ratchet_monotone negative test passed (HEAD + durable floor)"
}

test_generator_host_parity() (
    log "Testing check_generator_host_parity"
    # Plant in a registered Python generator the generate-/render- naming convention misses.
    local rel script
    rel="$(python3 -c 'import os, sys, tomllib; print(next(g for g in (r["generator"] for r in tomllib.load(open(sys.argv[1], "rb"))["laws"]["projection_registry"]["surfaces"]) if g.endswith(".py") and not os.path.basename(g).startswith(("generate-", "render-"))))' "${ROOT}/usr/share/mios/mios.toml")"
    script="${ROOT}/${rel}"
    local backup; backup="$(mktemp)"
    cp "$script" "$backup"
    trap 'cp "$backup" "$script"; rm -f "$backup"' EXIT
    _neg_gate check_generator_host_parity || die "Generator portability failed before the idiom control"
    printf '\nimport fnmatch\nfnmatch.fnmatch("x", "x")\n' >> "$script"
    _neg_gate check_generator_host_parity && die "check_generator_host_parity passed despite non-portable fnmatch usage in ${rel}"
    [[ "$_NEG_GATE_OUT" == *"${rel} uses non-portable"* ]] || die "Generator portability failed without naming ${rel}"
    cp "$backup" "$script"
    _neg_gate check_generator_host_parity || die "check_generator_host_parity failed after restoration"
    log "check_generator_host_parity negative test passed"
)

# The >=20 guard counts what git LISTED, not what was read, so a corpus that is
# listed but absent scanned nothing and printed the success line anyway.
test_generator_host_parity_unreadable_corpus() (
    log "Testing check_generator_host_parity against a listed-but-absent corpus"
    local tmp_dir i
    tmp_dir="$(mktemp -d)"
    trap 'rm -rf "$tmp_dir"' EXIT
    mkdir -p "${tmp_dir}/tools"
    git -C "$tmp_dir" init -q
    for ((i = 1; i <= 25; i++)); do
        : > "${tmp_dir}/tools/generate-probe-${i}.py"
        git -C "$tmp_dir" add -- "tools/generate-probe-${i}.py"
    done
    mkdir -p "$tmp_dir/usr/share/mios"
    printf '[laws.projection_registry]\nsurfaces = [{ generator = "tools/generate-probe-1.py" }]\n' > "$tmp_dir/usr/share/mios/mios.toml"
    local out
    out="$(MIOS_DRIFT_ROOT="$tmp_dir" python3 "${ROOT}/tools/drift-checks.py" generator-host-parity 2>&1)" \
        || die "Listed generator corpus failed before removing its subjects: $out"
    rm -f "${tmp_dir}"/tools/generate-probe-*.py
    if out="$(MIOS_DRIFT_ROOT="$tmp_dir" python3 "${ROOT}/tools/drift-checks.py" generator-host-parity 2>&1)"; then
        die "check_generator_host_parity passed with 25 generators listed and none readable"
    fi
    [[ "$out" == *"cannot read generator tools/generate-probe-"* ]] \
        || die "Listed generator corpus failed without naming an unreadable subject: $out"
    _neg_gate check_generator_host_parity || die "check_generator_host_parity failed after cleanup"
    log "check_generator_host_parity unreadable-corpus negative test passed"
)

test_manual_generated() {
    log "Testing check_manual_generated"
    local doc="${ROOT}/usr/share/doc/mios/reference/ports-and-laws.md"
    local backup; backup="$(mktemp)"
    cp "$doc" "$backup"
    # Corrupt a DERIVED table cell: the gate must notice the doc no longer
    # matches mios.toml.
    sed -i 's/| admin | ssh | [0-9]* |/| admin | ssh | 9999 |/' "$doc"
    _neg_gate check_manual_generated && die "check_manual_generated passed despite a stale derived section"
    cp "$backup" "$doc"
    _neg_gate check_manual_generated || { rm -f "$backup"; die "check_manual_generated failed after restoration"; }

    # Second phase, and the load-bearing one: authored prose OUTSIDE a marker
    # must NOT move the gate. Without this a generator that owns whole files
    # would satisfy the gate -- the exact failure the marker protocol prevents.
    printf '\nAuthored paragraph the generator must never touch.\n' >> "$doc"
    _neg_gate check_manual_generated || die "check_manual_generated went red on prose OUTSIDE a marker"
    grep -q "Authored paragraph the generator must never touch" "$doc" || {
        die "render destroyed authored prose outside a marker"; }
    cp "$backup" "$doc"; rm -f "$backup"

    # Third phase: a missing manual chapter must turn the gate red -- the
    # chapter index in manual.md is how absence becomes visible.
    local ch
    ch="$(ls "${ROOT}"/usr/share/doc/mios/manual/ch[0-9]*.md | head -1)"
    [ -f "$ch" ] || die "no manual chapter files found to hide"
    mv "$ch" "${ch}.neg-hidden"
    _neg_gate check_manual_generated && die "check_manual_generated passed despite a missing chapter file"
    mv "${ch}.neg-hidden" "$ch"
    _neg_gate check_manual_generated || die "check_manual_generated failed after restoring the chapter"
    log "check_manual_generated negative test passed (all three phases)"
}

test_manual_ledger() {
    log "Testing check_manual_ledger"
    python3 "${ROOT}/usr/libexec/mios/mios-manual" --root "$ROOT" ledger --write >/dev/null 2>&1
    local tsv="${ROOT}/usr/share/mios/reference/manual-corpus.tsv"
    local backup; backup="$(mktemp)"
    cp "$tsv" "$backup"
    # Mutate the first data row's word count (column 5): the ledger must no
    # longer regenerate verbatim from the tracked tree.
    local tmp; tmp="$(mktemp)"
    awk -F'\t' 'BEGIN{OFS="\t"} NR==2{$5=$5+1} {print}' "$tsv" > "$tmp" && cp "$tmp" "$tsv" && rm -f "$tmp"
    _neg_gate check_manual_ledger && die "check_manual_ledger passed despite a hand-edited corpus ledger"
    cp "$backup" "$tsv"; rm -f "$backup"
    _neg_gate check_manual_ledger || die "check_manual_ledger failed after restoration"
    log "check_manual_ledger negative test passed"
}

test_credential_literals() {
    log "Testing check_credential_literals"
    local unit="${ROOT}/usr/share/containers/systemd/mios-searxng.container"
    local backup; backup="$(mktemp)"
    cp "$unit" "$backup"
    printf 'Environment=NEGATIVE_TEST_SECRET_KEY=hunter2\n' >> "$unit"
    _neg_gate check_credential_literals && die "check_credential_literals passed despite a new baked-in credential"
    cp "$backup" "$unit"
    local pg="${ROOT}/usr/share/containers/systemd/mios-pgvector.container"
    local pgbak; pgbak="$(mktemp)"; cp "$pg" "$pgbak"
    sed -i 's/^Environment=POSTGRES_PASSWORD=mios$/Environment=POSTGRES_PASSWORD=NOT-A-REAL-PASSWORD-negative-test/' "$pg"
    _neg_gate check_credential_literals && { cp "$pgbak" "$pg"; rm -f "$backup" "$pgbak"; die "check_credential_literals passed with a real password on a grandfathered key"; }
    cp "$pgbak" "$pg"; rm -f "$pgbak"
    _neg_gate check_credential_literals || { rm -f "$backup"; die "check_credential_literals failed after restoration"; }
    rm -f "$backup"
    log "check_credential_literals negative test passed"
}

test_names_registry_equivalence() {
    log "Testing check_names_registry_equivalence"
    # The helper is a subshell: without `|| die` its failure was swallowed here.
    _test_names_registry_readonly check_names_registry_equivalence || die "check_names_registry_equivalence read-only negative failed"
    log "check_names_registry_equivalence read-only negative tests passed"
}
test_protected_refs() {
    log "Testing check_protected_refs"
    local unit="${ROOT}/usr/lib/systemd/system/mios-agents.service"
    local backup; backup="$(mktemp)"
    cp "$unit" "$backup"

    # Remove the SUPPLY, not the reference. ExecStart still carries the bare
    # ${MIOS_FRONTIER_LANE_B_MODEL} and the unit still declares [Service] env, so the
    # renderer still protects it -- and now nothing on the host provides it.
    # That is the shipped state task_3362 found: systemd expands the absent name to
    # "" and Lane B runs with --model ''.
    sed -i '/^Environment="\{0,1\}MIOS_FRONTIER_LANE_B_MODEL=/d' "$unit"   # any value: a literal since 491c36a2
    if cmp -s "$backup" "$unit" || ! grep -q 'MIOS_FRONTIER_LANE_B_MODEL}' "$unit"; then
        cp "$backup" "$unit"; rm -f "$backup"
        die "check_protected_refs negative test planted nothing -- no supply line matched, or the ExecStart reference is gone"
    fi
    _neg_gate check_protected_refs && { cp "$backup" "$unit"; rm -f "$backup"; die "check_protected_refs passed with a protected ref that nothing supplies"; }
    case "$_NEG_GATE_OUT" in
        *MIOS_FRONTIER_LANE_B_MODEL*) ;;
        *) cp "$backup" "$unit"; rm -f "$backup"
           die "check_protected_refs failed, but not for the planted name -- it reported: $_NEG_GATE_OUT" ;;
    esac

    cp "$backup" "$unit"; rm -f "$backup"
    _neg_gate check_protected_refs || die "check_protected_refs failed after restoration: $_NEG_GATE_OUT"
    log "check_protected_refs negative test passed"
}

test_redact_coverage() {
    log "Testing check_redact_coverage"
    local sql="${ROOT}/usr/share/mios/postgres/schema-init.sql"
    local backup; backup="$(mktemp)"
    cp "$sql" "$backup"
    # A new table that is in neither [security.redact] list.
    printf '\nCREATE TABLE IF NOT EXISTS negative_test_sink (id int);\n' >> "$sql"
    _neg_gate check_redact_coverage && die "check_redact_coverage passed despite an unclassified table"
    cp "$backup" "$sql"
    _neg_gate check_redact_coverage || { rm -f "$backup"; die "check_redact_coverage failed after restoration"; }
    rm -f "$backup"
    log "check_redact_coverage negative test passed"
}

test_daemon_governor() {
    log "Testing check_daemon_governor"
    local d="${ROOT}/usr/libexec/mios/mios-daemon"
    local backup; backup="$(mktemp)"
    cp "$d" "$backup"
    # An autonomous loop that never consults the host-pressure gate.
    printf '\n\ndef negative_test_loop() -> None:\n    while not _stop_event.is_set():\n        time.sleep(1)\n' >> "$d"
    _neg_gate check_daemon_governor && die "check_daemon_governor passed despite an ungated autonomous loop"
    cp "$backup" "$d"
    _neg_gate check_daemon_governor || { rm -f "$backup"; die "check_daemon_governor failed after restoration"; }
    rm -f "$backup"
    log "check_daemon_governor negative test passed"
}

test_manual_links() {
    log "Testing check_manual_links"
    local doc="${ROOT}/usr/share/doc/mios/manual.md"
    local backup; backup="$(mktemp)"
    cp "$doc" "$backup"
    # Point a ToC entry at a chapter that does not exist.
    sed -i '0,/(manual\/ch01-/s//(manual\/ch01-does-not-exist.md#x)(manual\/ch01-/' "$doc"
    _neg_gate check_manual_links && die "check_manual_links passed despite a dangling ToC link"
    cp "$backup" "$doc"
    _neg_gate check_manual_links || { rm -f "$backup"; die "check_manual_links failed after restoration"; }

    # Second phase: a chapter file that the ToC never mentions must also fail.
    local orphan="${ROOT}/usr/share/doc/mios/manual/ch99-orphan-negative-test.md"
    printf '<!-- AI-hint: negative-test orphan chapter. -->\n\n# Chapter 99\n' > "$orphan"
    _neg_gate check_manual_links && die "check_manual_links passed despite an unreachable chapter file"
    rm -f "$orphan"
    _neg_gate check_manual_links || { rm -f "$backup"; die "check_manual_links failed after orphan removal"; }
    rm -f "$backup"
    log "check_manual_links negative test passed (both phases)"
}

test_doc_port_scheme() {
    log "Testing check_doc_port_scheme"
    local doc="${ROOT}/README.md"
    local backup; backup="$(mktemp)"
    cp "$doc" "$backup"
    rm -f "$doc" 2>/dev/null || true
    cp "$backup" "$doc"
    local probe_port; probe_port="$(printf '%s%s' 114 50)"
    printf '\nA retired lane on `:%s` sneaks back in.\n' "$probe_port" >> "$doc"
    _neg_gate check_doc_port_scheme && die "check_doc_port_scheme passed despite a retired port literal in docs"
    rm -f "$doc" 2>/dev/null || true
    cp "$backup" "$doc"
    rm -f "$backup"
    _neg_gate check_doc_port_scheme || die "check_doc_port_scheme failed after doc restoration"

    # task_946 (LAW5-01): planted retired port in a live code file must fail and name file + port
    local code_file="${ROOT}/usr/libexec/mios/mios-cron-director"
    local code_bak; code_bak="$(mktemp)"
    cp "$code_file" "$code_bak"
    local code_probe_port; code_probe_port="$(printf '%s%s' 86 40)"
    printf '\n# Planted retired port for negative testing\nLEGACY_PORT = %s\n' "$code_probe_port" >> "$code_file"
    if _neg_gate check_doc_port_scheme; then
        cp -f "$code_bak" "$code_file"
        rm -f "$code_bak"
        die "check_doc_port_scheme passed despite a retired port in code file"
    fi
    if ! echo "$_NEG_GATE_OUT" | grep -q "$code_probe_port"; then
        cp -f "$code_bak" "$code_file"
        rm -f "$code_bak"
        die "check_doc_port_scheme failure output did not name planted port $code_probe_port"
    fi
    if ! echo "$_NEG_GATE_OUT" | grep -q "usr/libexec/mios/mios-cron-director"; then
        cp -f "$code_bak" "$code_file"
        rm -f "$code_bak"
        die "check_doc_port_scheme failure output did not name code file usr/libexec/mios/mios-cron-director"
    fi
    cp -f "$code_bak" "$code_file"
    rm -f "$code_bak"
    _neg_gate check_doc_port_scheme || die "check_doc_port_scheme failed after code restoration"

    log "check_doc_port_scheme negative test passed"
}

test_comment_landing() {
    log "Testing check_comment_landing"
    local tsv="${ROOT}/usr/share/mios/reference/manual-corpus.tsv"
    local backup; backup="$(mktemp)"
    cp "$tsv" "$backup"
    # Claim a block was pruned into a doc that does not carry its anchor: the
    # gate must refuse to accept a deletion whose proof does not resolve.
    printf 'tests/fixture-never-existed.sh\t1\t2\t2\t40\tfeedfacecafe\tMIGRATE\tmidsize-narrative\t\t0\tusr/share/doc/mios/manual.md\tmios-src:feedfacecafe\t40\t1\n' >> "$tsv"
    _neg_gate check_comment_landing && die "check_comment_landing passed despite a pruned block with no landing proof"
    cp "$backup" "$tsv"; rm -f "$backup"
    _neg_gate check_comment_landing || die "check_comment_landing failed after restoration"
    log "check_comment_landing negative test passed"
}

test_blade_reconcile_schema() {
    log "Testing check_blade_reconcile_schema"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak; bak="$(mktemp)"; cp "$toml" "$bak"
    sed -i 's/^config_kv    = "conflict-is-error"/config_kv    = "conflict-is-error"\nbogus_class  = "union-by-hash"/' "$toml"
    _neg_gate check_blade_reconcile_schema && die "check_blade_reconcile_schema passed with divergence enabled and no provenance columns"
    cp "$bak" "$toml"; rm -f "$bak"
    _neg_gate check_blade_reconcile_schema \
        || die "check_blade_reconcile_schema failed after restoration"
    log "check_blade_reconcile_schema negative test passed"
}

test_desktop_launchers() {
    log "Testing check_desktop_launchers"
    local f="${ROOT}/usr/share/applications/mios-svc-cockpit.desktop"
    [ -f "$f" ] || { log "no rendered launcher to mutate; skipping"; return 0; }
    local bak; bak="$(mktemp)"; cp "$f" "$bak"
    # Mutate a RENDERED field. Adding a stray .desktop proves nothing: the
    # renderer compares only what SSOT declares.
    sed -i 's/^Name=.*/Name=DRIFTED/' "$f"
    _neg_gate check_desktop_launchers && die "check_desktop_launchers passed despite a drifted rendered launcher"
    cp "$bak" "$f"; rm -f "$bak"
    _neg_gate check_desktop_launchers || die "check_desktop_launchers failed after restoration"
    log "check_desktop_launchers negative test passed"
}

# Count, not exit code: this check has a standing red baseline, so an exit-code
# arm passes whether or not the plant was seen.
_docrefs_count() {
    _neg_gate check_doc_refs_resolve || :
    local n
    n="$(printf '%s' "$_NEG_GATE_OUT" | sed -n 's/.*[^0-9]\([0-9][0-9]*\) stale reference(s).*/\1/p' | head -1)"
    case "$n" in ''|*[!0-9]*) echo -1;; *) echo "$n";; esac
}

test_doc_refs_resolve() {
    log "Testing check_doc_refs_resolve"
    local probe="${ROOT}/automation/mios-negtest-docref.sh"
    local before after restored
    before="$(_docrefs_count)"
    if [[ "$before" == "-1" ]]; then
        die "test_doc_refs_resolve: could not read a finding count from the gate; the arms below would prove nothing"
    fi

    # An AI-related line naming a file that does not exist is a stale reference.
    printf '#!/usr/bin/env bash\n# AI-hint: negtest probe.\n# AI-related: automation/no-such-'"$$"'.sh\ntrue\n' > "$probe"
    # Staged, or the tracked corpus never sees it and the plant is invisible.
    git -C "$ROOT" add -N -- "$probe" >/dev/null 2>&1
    after="$(_docrefs_count)"
    # Tolerant, and before the assertion: a cleanup that can fail under set -e
    # exits the test first and the arm below never gets to report.
    git -C "$ROOT" rm -q --cached --force -- "$probe" >/dev/null 2>&1 || :
    rm -f "$probe"
    if [[ "$after" != "$((before + 1))" ]]; then
        die "check_doc_refs_resolve did not report the planted dangling reference (${before} -> ${after})"
    fi

    restored="$(_docrefs_count)"
    [[ "$restored" == "$before" ]] \
        || die "check_doc_refs_resolve did not return to its baseline (${before} -> ${restored})"
    log "check_doc_refs_resolve negative test passed"
}

test_drift_stubs() {
    log "Testing check_drift_stubs"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local src="${ROOT}/src/mios-rs/miosd/src/drift/bake.rs"
    local bak; bak="$(mktemp)"; cp "$toml" "$bak"
    local sbak; sbak="$(mktemp)"; cp "$src" "$sbak"
    _ds_fail() {
        cp "$bak" "$toml"; cp "$sbak" "$src"; rm -f "$bak" "$sbak"
        unset -f _ds_fail _ds_plant
        die "$1"
    }
    # Every Check is implemented (15332e0a): the plant replaces check_bake_plan's
    # real run() body, and refuses to run if that body cannot be found.
    local real='super::audit::native(ctx, "mios-bake-plan", &["--check"])'
    _ds_plant() {
        local t; t="$(cat "$sbak"; printf X)"; t="${t%X}"
        [[ "$t" == *"$real"* ]] || _ds_fail "check_bake_plan's run() changed shape; the plant would land nowhere"
        printf '%s' "${t/"$real"/$1}" > "$src"
    }
    # The defect itself: a run() that consults nothing and still claims a Pass.
    _ds_plant 'Verdict::Pass("Bake plan check passed".into())'
    _neg_gate check_drift_stubs && _ds_fail "check_drift_stubs passed with a stub claiming Verdict::Pass"
    [[ "$_NEG_GATE_OUT" == *"check_bake_plan: run() never consults"* ]] || _ds_fail "check_drift_stubs failed without naming the planted Pass"
    # A stub that is not on the register, and a raised ceiling must not absorb it.
    _ds_plant 'Verdict::Skip("NOT IMPLEMENTED: Bake plan".into())'
    _neg_gate check_drift_stubs && _ds_fail "check_drift_stubs passed with an unregistered stub"
    [[ "$_NEG_GATE_OUT" == *"check_bake_plan: an unimplemented check that is not on"* ]] || _ds_fail "check_drift_stubs failed without naming the unregistered stub"
    sed -i 's/^max_unimplemented = [0-9]*$/max_unimplemented = 999/' "$toml"
    _neg_gate check_drift_stubs && _ds_fail "check_drift_stubs passed with a stub hidden under a raised ceiling"
    # Registering it at an exact ceiling is the sanctioned escape -- and the only one.
    sed -i 's/^max_unimplemented = [0-9]*$/max_unimplemented = 1/; /^max_unimplemented = 1$/{n;s/^checks = \[\]$/checks = ["check_bake_plan"]/}' "$toml"
    grep -qx 'checks = \["check_bake_plan"\]' "$toml" || _ds_fail "the register plant did not land in [drift.unimplemented]"
    _neg_gate check_drift_stubs || _ds_fail "check_drift_stubs refused a registered stub at an exact ceiling: $_NEG_GATE_OUT"
    cp "$sbak" "$src"; cp "$bak" "$toml"
    # Deleting the ceiling must read as unbounded debt, not as no debt.
    sed -i '/^max_unimplemented = [0-9]*$/d' "$toml"
    _neg_gate check_drift_stubs && _ds_fail "check_drift_stubs passed with [drift.unimplemented].max_unimplemented absent"
    cp "$bak" "$toml"; rm -f "$bak" "$sbak"
    unset -f _ds_fail _ds_plant
    _neg_gate check_drift_stubs || die "check_drift_stubs failed after restoration"
    log "check_drift_stubs negative test passed"
}

test_native_twinless() {
    log "Testing check_native_twinless"
    local sql="${ROOT}/usr/share/mios/postgres/schema-init.sql"
    local bak empty; bak="$(mktemp)"; empty="$(mktemp -d)"; cp "$sql" "$bak"
    _nt_fail() { cp "$bak" "$sql"; rm -rf "$bak" "$empty"; unset -f _nt_fail; die "$1"; }
    _neg_gate check_native_twinless || _nt_fail "check_native_twinless red on the CLEAN tree: $_NEG_GATE_OUT"
    [[ "$_NEG_GATE_OUT" == *"twinless native Check(s) ran here"* ]] || _nt_fail "check_native_twinless did not report what it ran"
    # check_backfill_coverage has no bash twin, so only this gate runs it: a
    # vector table the backfill does not know must fail here, named.
    printf '\nCREATE TABLE IF NOT EXISTS neg_twinless_probe (id bigint PRIMARY KEY, emb vector(768));\n' >> "$sql"
    _neg_gate check_native_twinless && _nt_fail "check_native_twinless passed with an emb table no backfill covers"
    [[ "$_NEG_GATE_OUT" == *"check_backfill_coverage"*"neg_twinless_probe"* ]] \
        || _nt_fail "check_native_twinless failed without naming check_backfill_coverage and the planted table"
    cp "$bak" "$sql"
    # No miosd to run is a violation, never a skip.
    MIOS_NATIVE_BIN_DIR="$empty" _neg_gate check_native_twinless && _nt_fail "check_native_twinless passed with no miosd to run"
    [[ "$_NEG_GATE_OUT" == *"miosd is not built"* ]] || _nt_fail "check_native_twinless failed without saying miosd is missing"
    rm -rf "$bak" "$empty"; unset -f _nt_fail
    _neg_gate check_native_twinless || die "check_native_twinless failed after restoration: $_NEG_GATE_OUT"
    log "check_native_twinless negative test passed"
}

test_phase_registry() {
    log "Testing check_phase_registry"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak; bak="$(mktemp)"; cp "$toml" "$bak"
    local planted="${ROOT}/automation/89-negtest-phase.sh"
    _pr_fail() {
        cp "$bak" "$toml"; rm -f "$bak" "$planted"
        unset -f _pr_fail
        die "$1"
    }
    # A phase script on disk that build.sh would never run.
    printf '#!/usr/bin/env bash\ntrue\n' > "$planted"
    _neg_gate check_phase_registry && _pr_fail "check_phase_registry passed with an unregistered phase script on disk"
    # Raising the ceiling must not absorb it: the file is still absent from the
    # register, which is a finding on its own.
    sed -i 's/^max_unregistered = [0-9]*$/max_unregistered = 999/' "$toml"
    _neg_gate check_phase_registry && _pr_fail "check_phase_registry passed with a plant hidden under a raised ceiling"
    cp "$bak" "$toml"; rm -f "$planted"
    # A registered script that does not exist: build.sh drops it with no else
    # branch, so the pipeline runs one stage short and exits 0.
    sed -i 's/script = "56-fonts.sh"/script = "56-fontsx.sh"/' "$toml"
    _neg_gate check_phase_registry && _pr_fail "check_phase_registry passed while the list named a script that does not exist"
    cp "$bak" "$toml"
    # Emptying the list must read as unbounded debt, not as no debt --
    # build.sh falls back to a hardcoded 6-phase registry when it cannot load.
    python3 - "$toml" <<'PYEOF2'
import re, sys
p = sys.argv[1]
s = open(p).read()
s2, n = re.subn(r'^list = \[.*?^\]\n', 'list = []\n', s, count=1, flags=re.S | re.M)
assert n == 1, "[build.phases].list was not emptied -- the mutation would prove nothing"
open(p, "w").write(s2)
PYEOF2
    _neg_gate check_phase_registry && _pr_fail "check_phase_registry passed with [build.phases].list empty"
    cp "$bak" "$toml"; rm -f "$bak"
    unset -f _pr_fail
    _neg_gate check_phase_registry || die "check_phase_registry failed after restoration"
    log "check_phase_registry negative test passed"
}

test_signature_policy() {
    log "Testing check_signature_policy"
    local art="${ROOT}/usr/lib/containers/policy.json"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local abak tbak
    abak="$(mktemp)"; tbak="$(mktemp)"
    cp "$art" "$abak"; cp "$toml" "$tbak"
    _sp_fail() {
        cp "$abak" "$art"; cp "$tbak" "$toml"; rm -f "$abak" "$tbak"
        unset -f _sp_fail
        die "$1"
    }

    # A hand-edited policy is the Law 8 case: a derived security artifact
    # changed without its SSOT.
    printf '{\n  "default": [\n    {\n      "type": "reject"\n    }\n  ]\n}\n' > "$art"
    _neg_gate check_signature_policy && _sp_fail "check_signature_policy passed with a hand-edited policy.json"
    cp "$abak" "$art"

    # Same MEANING, different bytes. The generator's own --check compared parsed
    # JSON and was blind to exactly this, while the tracked file really had
    # drifted from what the writer emits.
    printf '{"default":[{"type":"insecureAcceptEverything"}]}\n' > "$art"
    _neg_gate check_signature_policy && _sp_fail "check_signature_policy passed on a semantically equal but byte-different policy.json"
    cp "$abak" "$art"

    # SSOT moved and the artifact did not.
    sed -i 's/^policy_mode = .*/policy_mode = "reject"/' "$toml"
    _neg_gate check_signature_policy && _sp_fail "check_signature_policy passed with policy_mode changed and policy.json stale"
    cp "$tbak" "$toml"

    # Absent must be a violation, not a skip: a policy that is not there is an
    # unknown policy, not a permissive one.
    mv "$art" "${art}.negtest"
    _neg_gate check_signature_policy && { mv "${art}.negtest" "$art"; _sp_fail "check_signature_policy passed with policy.json missing"; }
    mv "${art}.negtest" "$art"

    cp "$abak" "$art"; cp "$tbak" "$toml"; rm -f "$abak" "$tbak"
    unset -f _sp_fail
    _neg_gate check_signature_policy || die "check_signature_policy failed after restoration: ${_NEG_GATE_OUT}"
    log "check_signature_policy negative test passed"
}

test_projection_coverage() {
    log "Testing check_projection_coverage"
    _neg_gate check_projection_coverage || die "check_projection_coverage failed on the unmutated tree"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak; bak="$(mktemp)"; cp "$toml" "$bak"
    # A new mios-gen module is the primary plant; the Python-era globs still claim tools/.
    local planted_rel="tools/native/mios-gen/src/negtest_surface.rs"; local planted="${ROOT}/${planted_rel}"
    local short_name="${ROOT}/tools/gen-negtest-surface.py"
    _pc_fail() {
        cp "$bak" "$toml"; rm -f "$bak" "$planted" "$short_name"
        unset -f _pc_fail
        die "$1"
    }
    _pc_exempt() { sed -i "/^\[laws.projection_registry\]$/,/^\[/{s|^exempt = \[\]$|exempt = [{ generator = \"$planted_rel\", reason = \"$1\" }]|;s|^max_exempt = [0-9]*$|max_exempt = 1|}" "$toml"; }

    # The defect this check exists for: a NEW generator that projects a tracked
    # file, with no drift check and no registry row. check_projection_registry
    # walks the register forward and is silent on a generator that is on no row,
    # so before this check the plant below passed the whole gate.
    printf 'pub fn run() { let _ = std::fs::write("usr/share/mios/negtest.txt", "x"); }\n' > "$planted"
    _neg_gate check_projection_coverage && _pc_fail "check_projection_coverage passed with an unregistered generator on disk"
    [[ "$_NEG_GATE_OUT" == *"$planted_rel"* ]] || _pc_fail "check_projection_coverage did not name the unregistered native generator: ${_NEG_GATE_OUT}"

    printf '#!/usr/bin/env python3\nopen("usr/share/mios/negtest.txt", "w").write("x")\n' > "$short_name"
    _neg_gate check_projection_coverage && _pc_fail "check_projection_coverage ignored an unregistered gen-prefix generator"
    [[ "$_NEG_GATE_OUT" == *"tools/gen-negtest-surface.py"* ]] || _pc_fail "check_projection_coverage did not name the gen-prefix generator"
    rm -f "$short_name"

    # Raising the exemption ceiling must not absorb the plant: the generator is
    # still on neither list, so the ceiling buys nothing.
    sed -i 's/^max_exempt = [0-9]*$/max_exempt = 999/' "$toml"
    _neg_gate check_projection_coverage && _pc_fail "check_projection_coverage passed with a plant hidden under a raised exemption ceiling"
    cp "$bak" "$toml"

    # An exemption with no reason is a count, not an itemised register. Asserted
    # on the MESSAGE, not the exit code: the plant is still on disk here, so a
    # mutation that silently missed would fail the check for the earlier reason
    # and the assertion would pass without having tested anything.
    _pc_exempt ""
    _neg_gate check_projection_coverage && _pc_fail "check_projection_coverage passed with a bare exemption carrying no reason"
    case "${_NEG_GATE_OUT}" in
        *"carries no \`reason\`"*) : ;;
        *) _pc_fail "check_projection_coverage failed for the wrong reason on a bare exemption: ${_NEG_GATE_OUT}" ;;
    esac
    cp "$bak" "$toml"

    # The positive half of the same branch: the SAME plant, exempted WITH a
    # reason under a ceiling that admits it, must pass. Without this the
    # exemption path could be dead code that never grants anything.
    _pc_exempt "negative-test plant"
    _neg_gate check_projection_coverage || _pc_fail "check_projection_coverage rejected an itemised exemption within its ceiling: ${_NEG_GATE_OUT}"
    cp "$bak" "$toml"; rm -f "$planted"

    # The scope is this check's own allowlist, so narrowing it must not buy a
    # pass -- the register anchors the globs from outside.
    sed -i 's|"tools/native/mios-gen/src/\*\.rs"|"tools/native/mios-gen/src/render_*.rs"|' "$toml"
    _neg_gate check_projection_coverage || :
    [[ "$_NEG_GATE_OUT" == *"no glob matches it"* ]] || _pc_fail "check_projection_coverage passed a narrowed native discovery glob: ${_NEG_GATE_OUT}"
    cp "$bak" "$toml"
    python3 - "$toml" <<'PYEOF'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
text, count = re.subn(r'(?m)^generator_globs\s*=\s*\[[^\]]*\]',
                      'generator_globs = ["tools/generate-*.py"]', path.read_text(), count=1)
assert count == 1, "projection discovery scope control anchor is missing"
path.write_text(text)
PYEOF
    _neg_gate check_projection_coverage && _pc_fail "check_projection_coverage passed after a discovery glob was deleted"
    [[ "$_NEG_GATE_OUT" == *"matched no file"* || "$_NEG_GATE_OUT" == *"the scope has narrowed"* ]] \
        || _pc_fail "check_projection_coverage failed without naming the narrowed or empty scope"
    cp "$bak" "$toml"

    # A registry row naming a check function that does not exist.
    sed -i 's|check = "check_manpages"|check = "check_manpages_negtest"|' "$toml"
    _neg_gate check_projection_coverage && _pc_fail "check_projection_coverage passed with a row naming a check that is not defined"
    cp "$bak" "$toml"

    # A row that does not say what it projects cannot be regenerate-and-diffed.
    sed -i 's|check = "check_blade_karg", output = "[^"]*"|check = "check_blade_karg", output = ""|' "$toml"
    _neg_gate check_projection_coverage && _pc_fail "check_projection_coverage passed with a registry row declaring no output"
    cp "$bak" "$toml"

    rm -f "$bak" "$planted"
    unset -f _pc_fail _pc_exempt
    _neg_gate check_projection_coverage || die "check_projection_coverage failed after restoration: ${_NEG_GATE_OUT}"
    log "check_projection_coverage negative test passed"
}

test_build_tool_dispatch() {
    log "Testing check_build_tool_dispatch"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak; bak="$(mktemp)"; cp "$toml" "$bak"
    local planted="${ROOT}/automation/89-negtest-dispatch.sh"
    _btd_fail() {
        cp "$bak" "$toml"; rm -f "$bak" "$planted" "${ROOT}/usr/bin/miosd"
        unset -f _btd_fail
        die "$1"
    }
    # A NEW stage gating its Rust path on a PATH lookup that cannot resolve.
    printf '#!/usr/bin/env bash\nif command -v miosd >/dev/null 2>&1; then :; fi\n' > "$planted"
    _neg_gate check_build_tool_dispatch && _btd_fail "check_build_tool_dispatch passed with a new unreachable dispatch gate"
    # Raising the ceiling must not absorb the plant: the file is still absent
    # from the register, which is a violation on its own.
    sed -i 's/^max_unreachable = [0-9]*$/max_unreachable = 999/' "$toml"
    _neg_gate check_build_tool_dispatch && _btd_fail "check_build_tool_dispatch passed with a plant hidden under a raised ceiling"
    cp "$bak" "$toml"
    # The half that proves the check RESOLVES rather than counting strings: the
    # plant, REGISTERED, is accepted; giving its binary a PATH location makes it
    # reachable, so the register must read as stale rather than staying green.
    # (task_962 emptied the shipped register, so the plant supplies the entry.)
    python3 - "$toml" "${planted#"${ROOT}"/}" <<'PYEOF'
import re, sys
p, rel = sys.argv[1:3]
s = open(p).read()
m = re.search(r'(\[build\.tool_dispatch\][^\[]*?max_unreachable = )(\d+)(\nunreachable = \[)', s, re.S)
assert m, "the [build.tool_dispatch] register was not found -- the mutation would prove nothing"
open(p, "w").write(s[:m.start()] + m.group(1) + str(int(m.group(2)) + 1) + m.group(3) + '"%s", ' % rel + s[m.end():])
PYEOF
    _neg_gate check_build_tool_dispatch || _btd_fail "check_build_tool_dispatch rejected a registered unreachable gate: $_NEG_GATE_OUT"
    mkdir -p "${ROOT}/usr/bin"; : > "${ROOT}/usr/bin/miosd"
    _neg_gate check_build_tool_dispatch && _btd_fail "check_build_tool_dispatch passed while its register described gates that had become reachable"
    rm -f "${ROOT}/usr/bin/miosd" "$planted"; cp "$bak" "$toml"
    # Deleting the register must read as unbounded debt, not as no debt.
    python3 - "$toml" <<'PYEOF'
import re, sys
p = sys.argv[1]
s = open(p).read()
s2, n = re.subn(r'\[build\.tool_dispatch\].*?\n\]\n', '', s, count=1, flags=re.S)
assert n == 1, "the [build.tool_dispatch] register was not removed -- the mutation would prove nothing"
open(p, "w").write(s2)
PYEOF
    _neg_gate check_build_tool_dispatch && _btd_fail "check_build_tool_dispatch passed with [build.tool_dispatch] absent"
    cp "$bak" "$toml"; rm -f "$bak"
    unset -f _btd_fail
    _neg_gate check_build_tool_dispatch || die "check_build_tool_dispatch failed after restoration"
    log "check_build_tool_dispatch negative test passed"
}

test_profile_integrity() {
    log "Testing check_profile_integrity"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak; bak="$(mktemp)"; cp "$toml" "$bak"
    _neg_gate check_profile_integrity || { cp "$bak" "$toml"; rm -f "$bak"; die "test_profile_integrity: red on the CLEAN tree: $_NEG_GATE_OUT"; }
    _pi_plant() {  # $1 = python replace old, $2 = new, $3 = text the gate must name
        python3 - "$toml" "$1" "$2" <<'PY'
import sys
p, old, new = sys.argv[1:4]
s = open(p, encoding="utf-8").read()
assert s.count(old) == 1, old
open(p, "w", encoding="utf-8").write(s.replace(old, new))
PY
        if _neg_gate check_profile_integrity; then cp "$bak" "$toml"; rm -f "$bak"; die "check_profile_integrity passed with $3 planted"; fi
        case "$_NEG_GATE_OUT" in *"$3"*) : ;; *) cp "$bak" "$toml"; rm -f "$bak"; die "check_profile_integrity did not name $3: $_NEG_GATE_OUT";; esac
        cp "$bak" "$toml"
    }
    _pi_plant '"cleanup", "ssot-lint"' '"cleanup", "devloop-planted-phase", "ssot-lint"' "devloop-planted-phase"
    _pi_plant 'all     = true' 'all     = true
extends = ["devloop-planted-profile"]' "devloop-planted-profile"
    _pi_plant 'all     = true' 'all     = true
floor   = true' "more than one profile declares floor"
    _pi_plant '[profiles.full]' '[profiles.devloop-planted-partial]
package_sections = ["devcontainer"]

[profiles.full]' "does not contain the floor"
    rm -f "$bak"; unset -f _pi_plant
    _neg_gate check_profile_integrity || die "check_profile_integrity failed after restoration: $_NEG_GATE_OUT"
    log "check_profile_integrity negative test passed"
}

# Findings, not exit codes: [gpu] is unconsumed on main, so `_neg_gate && fail`
# can never fire. Each arm asserts the gate NAMES what it planted.
_nist_names() {
    _neg_gate check_no_inert_ssot_tables || :
    case "$_NEG_GATE_OUT" in *"$1"*) return 0;; *) return 1;; esac
}

test_no_inert_ssot_tables() {
    log "Testing check_no_inert_ssot_tables"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak; bak="$(mktemp)"; cp "$toml" "$bak"
    _neg_gate check_no_inert_ssot_tables || :
    case "$_NEG_GATE_OUT" in
        *"access-shaped"*|*"every mios.toml SSOT table"*) : ;;
        *) rm -f "$bak"
           die "test_no_inert_ssot_tables: the gate produced no recognisable output; the arms below would prove nothing" ;;
    esac
    _nist_fail() {
        cp "$bak" "$toml"; rm -f "$bak"
        unset -f _nist_fail
        die "$1"
    }
    # A table nothing reads is dead SSOT: it looks configurable and is not.
    # Assembled at runtime: spelling the table name literally in this .sh file
    # would satisfy the OLD name-appearance gate; the access-shaped gate must
    # fail it either way, but the assembly keeps the plant honest against both.
    local tbl="mios_negtest"; tbl="${tbl}_inert_tbl"
    printf '
[%s]
unused_key = "nothing reads this"
' "$tbl" >> "$toml"
    _nist_names "$tbl" || _nist_fail "check_no_inert_ssot_tables did not name the planted table with no consumer"
    # THE FALSE-CREDIT ARM (task_945); git add -N or the corpus cannot see the decoy.
    local decoy="${ROOT}/usr/libexec/mios/mios-negtest-decoy.py"
    printf '#!/usr/bin/env python3\n"""Reads usr/share/mios/mios.toml elsewhere."""\nd = compute()\nif d["%s"]:\n    pass\n' "$tbl" > "$decoy"
    git -C "$ROOT" add -N -- "$decoy" >/dev/null 2>&1
    _nist_decoy_clean() { git -C "$ROOT" rm --cached -q -- "$decoy" >/dev/null 2>&1; rm -f "$decoy"; unset -f _nist_decoy_clean; }
    if ! _nist_names "$tbl"; then
        _nist_decoy_clean
        _nist_fail "check_no_inert_ssot_tables was SATISFIED by a local dict subscript -- the task_945 false credit is back"
    fi
    _nist_decoy_clean
    # Raising the ceiling must not absorb the plant: the ceiling has to EQUAL
    # the register, so a raised ceiling is itself a violation.
    sed -i 's/^max_unconsumed = [0-9]*$/max_unconsumed = 999/' "$toml"
    _nist_names "max_unconsumed" || _nist_fail "check_no_inert_ssot_tables did not report the raised [ssot_tables].max_unconsumed ceiling"
    cp "$bak" "$toml"
    # The register's first live entry, read rather than named: a drained entry
    # (browser was) must not turn the two arms below into no-ops.
    local entry
    entry="$(python3 -c 'import sys,tomllib; print((tomllib.load(open(sys.argv[1],"rb"))["ssot_tables"]["unconsumed"] or [""])[0])' "$toml")"
    [[ -n "$entry" ]] || _nist_fail "[ssot_tables].unconsumed is empty -- the padding and drop arms have no subject"
    python3 - "$toml" "$entry" <<'PYEOF'
import re, sys
p, entry = sys.argv[1], sys.argv[2]
s = open(p).read()
s2, n = re.subn(r'\n  "%s",[^\n]*' % re.escape(entry), '\n  "blades",', s, count=1)
assert n == 1, "no register entry was swapped -- the mutation would prove nothing"
open(p, "w").write(s2)
PYEOF
    _nist_names "blades" || _nist_fail "check_no_inert_ssot_tables did not name the consumed table padding the register"
    cp "$bak" "$toml"
    # Removing a register entry leaves its table unconsumed and unregistered.
    python3 - "$toml" "$entry" <<'PYEOF'
import re, sys
p, entry = sys.argv[1], sys.argv[2]
s = open(p).read()
s2, n = re.subn(r'\n  "%s",[^\n]*' % re.escape(entry), '', s, count=1)
assert n == 1, "no register entry was removed -- the mutation would prove nothing"
open(p, "w").write(s2)
PYEOF
    _nist_names "[$entry] has no access-shaped consumer" || _nist_fail "check_no_inert_ssot_tables did not name the table whose register entry was dropped"
    cp "$bak" "$toml"
    # Deleting the whole register must read as unbounded debt, not as no debt.
    python3 - "$toml" <<'PYEOF'
import re, sys
p = sys.argv[1]
s = open(p).read()
s2, n = re.subn(r'# -+\n# \[ssot_tables\].*?\n\]\n', '', s, count=1, flags=re.S)
assert n == 1, "the [ssot_tables] register was not removed -- the mutation would prove nothing"
open(p, "w").write(s2)
PYEOF
    _nist_names "ssot_tables" || _nist_fail "check_no_inert_ssot_tables did not report the missing [ssot_tables] register"
    cp "$bak" "$toml"; rm -f "$bak"
    unset -f _nist_fail
    # Restoration: the planted table must be GONE from the findings. Asserting
    # exit 0 here is what made this test fail in CI -- [gpu] keeps it red.
    ! _nist_names "$tbl" || die "check_no_inert_ssot_tables still names the planted table after restoration"
    log "check_no_inert_ssot_tables negative test passed"
}

test_bootstrap_sync() {
    log "Testing check_bootstrap_sync"
    # Absence leg: runs with no sibling checkout at all.
    local had_root="${MIOS_BOOTSTRAP_ROOT+x}" saved_root="${MIOS_BOOTSTRAP_ROOT:-}"
    export MIOS_BOOTSTRAP_ROOT="${ROOT}/.no-such-bootstrap"
    MIOS_DRIFT_REQUIRE_TOOLS=0 _neg_gate check_bootstrap_sync \
        && die "check_bootstrap_sync passed with no bootstrap checkout"
    [[ "$_NEG_GATE_OUT" == *"Law 15 NOT checked"* ]] \
        || die "check_bootstrap_sync failed without naming the absent checkout: ${_NEG_GATE_OUT}"
    if [ -n "$had_root" ]; then export MIOS_BOOTSTRAP_ROOT="$saved_root"; else unset MIOS_BOOTSTRAP_ROOT; fi

    # Sibling resolution as tools/sync-bootstrap.py does it (task_970). The old
    # MSYS-only /c/ default skipped every local run.
    local boot="${MIOS_BOOTSTRAP_ROOT:-}"
    if [ -z "$boot" ]; then
        for _b in "$(dirname "$ROOT")/mios-bootstrap" "/c/mios-bootstrap"; do
            [ -d "$_b" ] && { boot="$_b"; break; }
        done
    fi
    if [ -z "$boot" ] || [ ! -d "$boot" ]; then
        [ "${MIOS_DRIFT_REQUIRE_TOOLS:-0}" = "1" ] && die "bootstrap repo absent, so the file and table legs cannot be planted"
        log "bootstrap repo absent; file and table legs skipped"; return 0
    fi
    local f="${boot}/installation/UNIFY.md" t="${boot}/mios.toml"
    [ -f "$f" ] && [ -f "$t" ] || die "bootstrap at ${boot} lacks installation/UNIFY.md or mios.toml"
    local bak tbak; bak="$(mktemp)"; tbak="$(mktemp)"; cp "$f" "$bak"; cp "$t" "$tbak"
    # Drift in a MIRRORED file must fail: mios.git is the authority, and the
    # whole point is that a shared surface cannot change in only one repo.
    printf '\nDRIFT PROBE\n' >> "$f"
    _neg_gate check_bootstrap_sync && { cp "$bak" "$f"; die "check_bootstrap_sync passed despite a mirrored file drifting in bootstrap"; }
    cp "$bak" "$f"
    # The native port (484040ed) names each drifted surface path, not "<file>: differs".
    [[ "$_NEG_GATE_OUT" == *"drifted from mios.git: "*"/installation/UNIFY.md"* ]] \
        || die "check_bootstrap_sync failed without naming installation/UNIFY.md: ${_NEG_GATE_OUT}"
    # A mirrored TABLE value: [colors] had no parsed compare but the retired
    # ports-drift check, whose scalars this leg now covers.
    sed -i '/^\[colors\]/,/^\[/ s/^bg\( *\)= "[^"]*"/bg\1= "#010203"/' "$t"
    grep -q '^bg *= "#010203"' "$t" || { cp "$tbak" "$t"; die "the [colors].bg plant did not land in ${t}"; }
    _neg_gate check_bootstrap_sync && { cp "$tbak" "$t"; die "check_bootstrap_sync passed despite [colors].bg drifting in bootstrap"; }
    cp "$tbak" "$t"
    [[ "$_NEG_GATE_OUT" == *"drifted from mios.git: "*"/mios.toml"* ]] \
        || die "check_bootstrap_sync failed without naming the bootstrap mios.toml: ${_NEG_GATE_OUT}"
    rm -f "$bak" "$tbak"
    _neg_gate check_bootstrap_sync || die "check_bootstrap_sync failed after restoration: ${_NEG_GATE_OUT}"
    log "check_bootstrap_sync negative test passed"
}

test_legibility_ratchet() {
    log "Testing check_legibility_ratchet"
    local probe="${ROOT}/automation/mios-negtest-bulk.sh"
    local pytool="${ROOT}/tools/mios-negtest-bulk.py"
    local pytest_probe="${ROOT}/tools/test-mios-negtest-bulk.py"
    # Each leg reads ITS metric from the gate's own table. The overall verdict
    # also carries every other floor and the probe's own tracked file, so on a
    # tree already over the shell floor the plants looked caught and the
    # sibling-test leg looked miscounted when nothing had counted it.
    _lr_metric() { sed -n "s/.*[] ]$1=\([0-9]*\)\/\([0-9]*\).*/\1 \2/p" <<<"$_NEG_GATE_OUT" | head -1; }
    _lr_plant() { local i; for i in $(seq 1 "$2"); do echo "# filler $i"; done > "$1"; git -C "$ROOT" add -f -- "$1" >/dev/null 2>&1; }
    _lr_unplant() { git -C "$ROOT" rm -q --cached --force -- "$@" >/dev/null 2>&1; rm -f "$@"; }
    _lr_fail() { _lr_unplant "$probe" "$pytool" "$pytest_probe"; unset -f _lr_metric _lr_plant _lr_unplant _lr_fail; die "$1"; }

    _neg_gate check_legibility_ratchet || :
    local sh0 shcap py0 pycap got n
    read -r sh0 shcap < <(_lr_metric shell_lines)
    read -r py0 pycap < <(_lr_metric tooling_python_lines)
    [[ -n "$sh0" && -n "$py0" ]] || _lr_fail "check_legibility_ratchet printed no measurement table: $_NEG_GATE_OUT"

    # Shell lines must bite: bash is glue only, and the floors only fall. Sized
    # to cross the floor by one line whatever the slack.
    n=$(( shcap - sh0 + 1 )); (( n > 0 )) || n=1
    _lr_plant "$probe" "$n"
    _neg_gate check_legibility_ratchet && _lr_fail "check_legibility_ratchet passed despite $n new shell lines over the floor"
    read -r got _ < <(_lr_metric shell_lines)
    [[ "$got" == "$(( sh0 + n ))" && "$_NEG_GATE_OUT" == *"shell_lines = $got, over the floor"* ]] \
        || _lr_fail "check_legibility_ratchet did not count and refuse $n planted shell lines: $_NEG_GATE_OUT"
    _lr_unplant "$probe"

    # The Python arm, both ways. A tooling file must bite; a sibling unit test
    # must NOT count, or this ratchet pulls against check_module_test_coverage
    # and the cheapest way to stay green is to not write the test (task_981).
    n=$(( pycap - py0 + 1 )); (( n > 0 )) || n=1
    _lr_plant "$pytool" "$n"
    _neg_gate check_legibility_ratchet && _lr_fail "check_legibility_ratchet passed despite $n new tooling-python lines over the floor"
    read -r got _ < <(_lr_metric tooling_python_lines)
    [[ "$got" == "$(( py0 + n ))" && "$_NEG_GATE_OUT" == *"tooling_python_lines = $got, over the floor"* ]] \
        || _lr_fail "check_legibility_ratchet did not count and refuse $n planted tooling-python lines: $_NEG_GATE_OUT"
    _lr_unplant "$pytool"
    _lr_plant "$pytest_probe" 600
    _neg_gate check_legibility_ratchet || :
    read -r got _ < <(_lr_metric tooling_python_lines)
    [[ "$got" == "$py0" ]] || _lr_fail "check_legibility_ratchet counted a sibling unit test as tooling ($py0 -> ${got:-none}): $_NEG_GATE_OUT"
    _lr_unplant "$pytest_probe"
    # ADR-0030: one planted file is charged to its domain AND the total.
    local f0; read -r f0 _ < <(_lr_metric files.automation)
    _lr_plant "$probe" 1
    _neg_gate check_legibility_ratchet && _lr_fail "check_legibility_ratchet passed despite a new file over the automation budget"
    [[ -n "$f0" && "$_NEG_GATE_OUT" == *"files.automation = $(( f0 + 1 )), over the floor"* && "$_NEG_GATE_OUT" == *"tracked_files = "*", over the floor"* ]] \
        || _lr_fail "check_legibility_ratchet did not charge the planted file to its domain and the total: $_NEG_GATE_OUT"
    _lr_unplant "$probe"

    # task_3662: planted file in dependency domain is refused by files.dependency
    local dep_probe="${ROOT}/usr/share/mios/vendored/fake_dep.whl"
    local d0; read -r d0 _ < <(_lr_metric files.dependency)
    _lr_plant "$dep_probe" 1
    _neg_gate check_legibility_ratchet && _lr_fail "check_legibility_ratchet passed despite a new file over the dependency budget"
    [[ -n "$d0" && "$_NEG_GATE_OUT" == *"files.dependency = $(( d0 + 1 )), over the floor"* ]] \
        || _lr_fail "check_legibility_ratchet did not charge planted file to dependency domain: $_NEG_GATE_OUT"
    _lr_unplant "$dep_probe"
    unset -f _lr_metric _lr_plant _lr_unplant _lr_fail

    _neg_gate check_legibility_ratchet || die "check_legibility_ratchet failed after restoration: $_NEG_GATE_OUT"
    log "check_legibility_ratchet negative test passed"
}

test_resolver_differential_parity() {
    log "Testing resolver parity with a configured catalog"
    MIOS_DRIFT_REQUIRE_TOOLS=1 _neg_gate check_resolver_differential_parity \
        || die "resolver parity baseline failed"
    local missing out rc=0
    missing="$(mktemp -d)" || die "cannot make missing-tool catalog"
    out="$(MIOS_NATIVE_BIN_DIR="$missing" MIOS_DRIFT_REQUIRE_TOOLS=1 \
        bash "${ROOT}/automation/98-drift-checks.sh" check_resolver_differential_parity 2>&1)" || rc=$?
    rmdir "$missing" || die "missing-tool catalog was unexpectedly written"
    [ "$rc" -ne 0 ] || die "resolver parity accepted a missing configured resolver"
    grep -q 'configured resolver is missing' <<< "$out" \
        || die "resolver parity failed for a different reason: $out"
    MIOS_DRIFT_REQUIRE_TOOLS=1 _neg_gate check_resolver_differential_parity \
        || die "resolver parity failed after restoring the configured catalog"
    log "check_resolver_differential_parity negative test passed"
}

test_resolver_twin_parity() {
    log "Testing check_resolver_twin_parity negative control"
    _neg_gate check_resolver_twin_parity || die "check_resolver_twin_parity baseline failed"

    # Negative control 1: missing configured resolver fails hard
    local missing out rc=0
    missing="$(mktemp -d)" || die "cannot make missing-tool catalog"
    out="$(MIOS_NATIVE_BIN_DIR="$missing" \
        bash "${ROOT}/automation/98-drift-checks.sh" check_resolver_twin_parity 2>&1)" || rc=$?
    rmdir "$missing" || die "missing-tool catalog was unexpectedly written"
    [ "$rc" -ne 0 ] || die "check_resolver_twin_parity accepted a missing configured resolver"
    grep -q 'configured resolver is missing' <<< "$out" \
        || die "check_resolver_twin_parity failed for a different reason: $out"

    # Negative control 2: resolver divergence on endpoint
    local userenv="${ROOT}/usr/lib/mios/userenv.sh"
    local ubak="${userenv}.bak"
    cp "$userenv" "$ubak"
    echo 'export MIOS_AI_ENDPOINT="corrupted-drift-endpoint"' >> "$userenv"
    if bash "${ROOT}/automation/98-drift-checks.sh" check_resolver_twin_parity >/dev/null 2>&1; then
        cp "$ubak" "$userenv"; rm -f "$ubak"
        die "check_resolver_twin_parity passed despite divergence in exported endpoint"
    fi
    cp "$ubak" "$userenv"; rm -f "$ubak"

    _neg_gate check_resolver_twin_parity || die "check_resolver_twin_parity failed after restoration"
    log "check_resolver_twin_parity negative test passed"
}

test_no_generated_prose_in_resolvers() {
    log "Testing check_no_generated_prose_in_resolvers"
    local sh_file="${ROOT}/automation/lib/globals.sh"
    local backup; backup="$(mktemp)"
    cp "$sh_file" "$backup"
    echo "# AI-hint: test prose line" >> "$sh_file"
    _neg_gate check_no_generated_prose_in_resolvers && die "check_no_generated_prose_in_resolvers passed despite AI-hint prose line"
    cp "$backup" "$sh_file"; rm -f "$backup"
    _neg_gate check_no_generated_prose_in_resolvers || die "check_no_generated_prose_in_resolvers failed after restoration"
    log "check_no_generated_prose_in_resolvers negative test passed"
}

test_header_integrity() {
    log "Testing check_header_integrity"
    local probe="${ROOT}/automation/mios-negtest-header.sh"
    # Exactly the damage found on main: the '#' reused as the hint's marker with
    # the interpreter left as hint TEXT, so the file has no shebang at all.
    printf '# AI-hint: !/usr/bin/env bash A probe with its shebang absorbed.\ntrue\n' > "$probe"
    git -C "$ROOT" add -N -- "$probe" >/dev/null 2>&1
    if _neg_gate check_header_integrity; then
        git -C "$ROOT" rm -q --cached --force -- "$probe" >/dev/null 2>&1; rm -f "$probe"
        die "check_header_integrity passed despite an absorbed shebang"
    fi
    git -C "$ROOT" rm -q --cached --force -- "$probe" >/dev/null 2>&1; rm -f "$probe"
    if ! _neg_gate check_header_integrity; then
        # Show WHY rather than only that it failed: a restoration failure means
        # the gate is flagging something the probe did not create.
        local violog
        violog=$(MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" MIOS_DRIFT_ROOT="$ROOT" bash "${ROOT}/automation/98-drift-checks.sh" check_header_integrity 2>&1 || true)
        echo "$violog" | grep -i 'violation' | head -5 >&2
        die "check_header_integrity failed after restoration"
    fi
    log "check_header_integrity negative test passed"
}

# The empty-listing guard counts what git LISTED; every listed file that was not
# on disk was then skipped in silence, so nothing was inspected and it passed.
test_header_integrity_unreadable_corpus() {
    log "Testing check_header_integrity against a listed-but-absent corpus"
    local tmp_dir
    tmp_dir="$(mktemp -d)"
    git -C "$tmp_dir" init -q
    printf 'true\n' > "${tmp_dir}/probe.sh"
    git -C "$tmp_dir" add -- probe.sh
    rm -f "${tmp_dir}/probe.sh"
    if MIOS_DRIFT_ROOT="$tmp_dir" python3 "${ROOT}/tools/drift-checks.py" \
        header-integrity >/dev/null 2>&1; then
        rm -rf "$tmp_dir"
        die "check_header_integrity passed with a tracked file listed and none readable"
    fi
    rm -rf "$tmp_dir"
    _neg_gate check_header_integrity || die "check_header_integrity failed after cleanup"
    log "check_header_integrity unreadable-corpus negative test passed"
}

test_os_update_timer_enabled() {
    log "Testing check_os_update_timer_enabled"
    local installer="${ROOT}/automation/50-uupd-installer.sh"
    [[ -f "$installer" ]] || die "50-uupd-installer.sh is absent; the probe cannot run"
    mv "$installer" "${installer}.negtest"
    if _neg_gate check_os_update_timer_enabled; then
        mv "${installer}.negtest" "$installer"
        die "check_os_update_timer_enabled passed despite no bake phase enabling the update timer"
    fi
    mv "${installer}.negtest" "$installer"
    _neg_gate check_os_update_timer_enabled \
        || die "check_os_update_timer_enabled failed after restoration"
    log "check_os_update_timer_enabled negative test passed"
}

test_verb_stub_backends() {
    log "Testing check_verb_stub_backends"
    local probe="${ROOT}/usr/libexec/mios/mios-negtest-stub.sh"
    printf '#!/usr/bin/env bash\n# AI-hint: probe stub\n\n' > "$probe"
    git -C "$ROOT" add -N -- "$probe" >/dev/null 2>&1
    if _neg_gate check_verb_stub_backends; then
        git -C "$ROOT" rm -q --cached --force -- "$probe" >/dev/null 2>&1; rm -f "$probe"
        die "check_verb_stub_backends passed despite stub script body"
    fi
    git -C "$ROOT" rm -q --cached --force -- "$probe" >/dev/null 2>&1; rm -f "$probe"
    _neg_gate check_verb_stub_backends || die "check_verb_stub_backends failed after restoration"
    log "check_verb_stub_backends negative test passed"
}

test_package_registry() {
    log "Testing check_package_registry"
    local reg_file="${ROOT}/usr/share/mios/ai/v1/packages/registry.json"
    mkdir -p "$(dirname "$reg_file")"
    echo '{"stale": true}' > "$reg_file"

    if _neg_gate check_package_registry; then
        rm -f "$reg_file"
        die "check_package_registry passed despite stale registry.json with dormant package_registry"
    fi

    rm -f "$reg_file"
    _neg_gate check_package_registry \
        || die "check_package_registry failed after restoration"
    log "check_package_registry negative test passed"
}

# Campaign 1: four dispatched checks that had no negative test at all.
# Probe values are assembled from pieces per the AI-convention above: a
# literal URL or model-id here would be found by the scans under test.

test_vendor_urls() {
    log "Testing check_vendor_urls"
    local probe="${ROOT}/etc/mios/ai/drift-neg-probe.yaml"
    local url; url="https://api.$(printf open)$(printf ai).com/v1"

    printf 'endpoint: %s\n' "$url" > "$probe"
    _neg_gate check_vendor_urls && {
        rm -f "$probe"
        die "check_vendor_urls passed with a vendor cloud URL in active AI-plane config"
    }

    # The check strips comment lines before scanning, and that is the property that makes it a
    # check of the ACTIVE surface rather than of file text. Assert it, or a future "simplify"
    # that drops the stripping would go unnoticed -- and would then fire on every historical
    # note in the tree.
    printf '# endpoint: %s\n' "$url" > "$probe"
    _neg_gate check_vendor_urls || {
        rm -f "$probe"
        die "check_vendor_urls failed on a COMMENTED-OUT URL: ${_NEG_GATE_OUT}"
    }

    rm -f "$probe"
    _neg_gate check_vendor_urls || die "check_vendor_urls failed after restoration: ${_NEG_GATE_OUT}"
    log "check_vendor_urls negative test passed"
}

test_retired_models() {
    log "Testing check_retired_models"
    local probe="${ROOT}/etc/mios/ai/drift-neg-probe-model.yaml"
    local retired; retired="gem$(printf ma4)"

    printf 'model: %s\n' "$retired" > "$probe"
    _neg_gate check_retired_models && {
        rm -f "$probe"
        die "check_retired_models passed with a retired model-id in a consumer unit"
    }

    rm -f "$probe"
    _neg_gate check_retired_models || die "check_retired_models failed after restoration: ${_NEG_GATE_OUT}"
    log "check_retired_models negative test passed"
}

test_etc_duplicates() {
    log "Testing check_etc_duplicates"
    local usr_dir="${ROOT}/usr/share/containers/systemd"
    local etc_dir="${ROOT}/etc/containers/systemd"
    local base
    base="$(find "$usr_dir" -maxdepth 1 -name '*.container' -printf '%f\n' 2>/dev/null | sort | head -1)"
    # Empty-Set Pass guard: with no generated unit to shadow there is nothing to plant, and a
    # test that silently asserts about an empty set is the defect this suite exists to catch.
    [[ -n "$base" ]] || die "check_etc_duplicates: no generated .container in $usr_dir to shadow"

    mkdir -p "$etc_dir"
    printf '# drift-gate-negatives probe\n' > "${etc_dir}/${base}"
    _neg_gate check_etc_duplicates && {
        rm -f "${etc_dir}/${base}"
        die "check_etc_duplicates passed with an etc/ unit shadowing generated ${base}"
    }

    rm -f "${etc_dir}/${base}"
    _neg_gate check_etc_duplicates || die "check_etc_duplicates failed after restoration: ${_NEG_GATE_OUT}"
    log "check_etc_duplicates negative test passed"
}

test_canonical_bools() {
    log "Testing check_canonical_bools"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local backup="${toml}.neg-bak"
    cp -p "$toml" "$backup"
    _cb_restore() { cp -p "$backup" "$toml"; rm -f "$backup"; }

    # The probe must land in a REAL verb table. [verbs._defaults] is skipped by the check by
    # design, and planting there produced a green gate and very nearly a false report that the
    # check was hollow -- a broken control inverts a result rather than weakening it.
    local line
    line="$(awk '/^\[verbs\./ && !/_defaults/ { print NR; exit }' "$toml")"
    [[ -n "$line" ]] || { _cb_restore; die "check_canonical_bools: no non-default [verbs.*] table to plant in"; }

    sed -i "$((line + 1))i hidden = \"false\"" "$toml"
    _neg_gate check_canonical_bools && { _cb_restore; die "check_canonical_bools passed with a quoted bool in a verb table"; }

    _cb_restore
    _neg_gate check_canonical_bools || die "check_canonical_bools failed after restoration: ${_NEG_GATE_OUT}"
    log "check_canonical_bools negative test passed"
}

# Campaign 1 batch 2: generated surfaces with no negative test (Law 8).

test_gate_index() {
    log "Testing check_gate_index"
    local tsv="${ROOT}/usr/share/mios/reference/drift-gate-index.tsv"
    local bak; bak="$(mktemp)"; cp "$tsv" "$bak"
    _gi_restore() { cp "$bak" "$tsv"; rm -f "$bak"; unset -f _gi_restore; }

    [[ -s "$tsv" ]] || { _gi_restore; die "check_gate_index: the index is empty, so a diff proves nothing"; }
    printf 'check_%s_planted\tplanted\tplanted\n' "devloop" >> "$tsv"
    _neg_gate check_gate_index && { _gi_restore; die "check_gate_index passed with a hand-added row in the generated index"; }

    _gi_restore
    _neg_gate check_gate_index || die "check_gate_index failed after restoration: ${_NEG_GATE_OUT}"
    log "check_gate_index negative test passed"
}

test_pod_quadlets() {
    log "Testing check_pod_quadlets"
    local dir="${ROOT}/usr/share/containers/systemd"
    local f; f="$(find "$dir" -maxdepth 1 -type f -name '*.image' -o -maxdepth 1 -type f -name '*.container' 2>/dev/null | sort | head -1)"
    [[ -n "$f" ]] || die "check_pod_quadlets: no generated unit in $dir to mutate"
    local bak; bak="$(mktemp)"; cp "$f" "$bak"
    _pq_restore() { cp "$bak" "$f"; rm -f "$bak"; unset -f _pq_restore; }

    printf '\n# devloop planted\n' >> "$f"
    _neg_gate check_pod_quadlets && { _pq_restore; die "check_pod_quadlets passed with a hand-edited generated unit"; }
    _pq_restore
    _neg_gate check_pod_quadlets || die "check_pod_quadlets failed after restoration: ${_NEG_GATE_OUT}"

    # Planted MIOS_PG_PASS (Law 11) and MIOS_ADGUARD_UID=0 (Law 6)
    MIOS_PG_PASS="planted-secret-123" _neg_gate check_pod_quadlets && die "check_pod_quadlets passed with planted MIOS_PG_PASS"
    _neg_gate check_pod_quadlets || die "check_pod_quadlets failed after restoring MIOS_PG_PASS: ${_NEG_GATE_OUT}"
    MIOS_ADGUARD_UID=0 _neg_gate check_pod_quadlets && die "check_pod_quadlets passed with planted MIOS_ADGUARD_UID=0"
    _neg_gate check_pod_quadlets || die "check_pod_quadlets failed after restoring MIOS_ADGUARD_UID: ${_NEG_GATE_OUT}"
    log "check_pod_quadlets negative test passed"
}

test_tmux_theme() {
    log "Testing check_tmux_theme"
    local conf="${ROOT}/usr/share/mios/tmux/mios-theme.tmux.conf"
    [[ -s "$conf" ]] || die "check_tmux_theme: mios-theme.tmux.conf is missing or empty"
    local bak; bak="$(mktemp)"; cp "$conf" "$bak"
    _tt_restore() { cp "$bak" "$conf"; rm -f "$bak"; unset -f _tt_restore; }
    printf '\n# devloop planted mutation\nset -g status off\n' >> "$conf"
    _neg_gate check_tmux_theme && { _tt_restore; die "check_tmux_theme passed with a hand-edited mios-theme.tmux.conf"; }
    _tt_restore
    _neg_gate check_tmux_theme || die "check_tmux_theme failed after restoration: ${_NEG_GATE_OUT}"
    log "check_tmux_theme negative test passed"
}

test_btop_theme() {
    log "Testing check_btop_theme"
    local theme="${ROOT}/etc/btop/themes/mios.theme"
    [[ -s "$theme" ]] || die "check_btop_theme: etc/btop/themes/mios.theme is missing or empty"
    local bak; bak="$(mktemp)"; cp "$theme" "$bak"
    _bt_restore() { cp "$bak" "$theme"; rm -f "$bak"; unset -f _bt_restore; }
    printf '\n# devloop planted mutation\ntheme[main_bg]="#000000"\n' >> "$theme"
    _neg_gate check_btop_theme && { _bt_restore; die "check_btop_theme passed with a hand-edited mios.theme"; }
    _bt_restore
    _neg_gate check_btop_theme || die "check_btop_theme failed after restoration: ${_NEG_GATE_OUT}"
    log "check_btop_theme negative test passed"
}

test_fastfetch() {
    log "Testing check_fastfetch"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak; bak="$(mktemp)"; cp -p "$toml" "$bak"
    _ff_restore() { cp -p "$bak" "$toml"; rm -f "$bak"; unset -f _ff_restore; }
    # (1) Positive control: clean run passes
    _neg_gate check_fastfetch || { _ff_restore; die "check_fastfetch failed on clean tree: ${_NEG_GATE_OUT}"; }
    # (2) Negative control: corrupted hex color in mios.toml makes render-fastfetch fail
    sed -i 's/^bg\( *\)= "#[0-9a-fA-F]*"/bg\1= "INVALID_HEX"/' "$toml"
    _neg_gate check_fastfetch && { _ff_restore; die "check_fastfetch passed with invalid hex color in mios.toml"; }
    _ff_restore
    _neg_gate check_fastfetch || die "check_fastfetch failed after restoration: ${_NEG_GATE_OUT}"
    # (3) Negative control: a hand-edited persisted golden fixture must fail the gate
    local fx="${ROOT}/tests/golden/fastfetch/mock.jsonc" fxbak; fxbak="$(mktemp)"; cp -p "$fx" "$fxbak"
    printf '\n// devloop planted\n' >> "$fx"
    if _neg_gate check_fastfetch; then cp -p "$fxbak" "$fx"; rm -f "$fxbak"; die "check_fastfetch passed with a corrupted golden fixture"; fi
    cp -p "$fxbak" "$fx"; rm -f "$fxbak"
    _neg_gate check_fastfetch || die "check_fastfetch failed after fixture restoration: ${_NEG_GATE_OUT}"
    log "check_fastfetch negative test passed"
}

test_egress_firewall() {
    log "Testing check_egress_firewall"
    local nft="${ROOT}/usr/share/mios/security/egress.nft"
    local bak; bak="$(mktemp)"; cp "$nft" "$bak"
    _ef_restore() { cp "$bak" "$nft"; rm -f "$bak"; unset -f _ef_restore; }
    [[ -s "$nft" ]] || { _ef_restore; die "check_egress_firewall: the ruleset is empty, so a diff proves nothing"; }
    printf '\n# devloop planted\n' >> "$nft"
    _neg_gate check_egress_firewall && { _ef_restore; die "check_egress_firewall passed with a hand-edited generated ruleset"; }
    _ef_restore
    _neg_gate check_egress_firewall || die "check_egress_firewall failed after restoration: ${_NEG_GATE_OUT}"
    log "check_egress_firewall negative test passed"
}

test_dotfiles_projection() {
    log "Testing check_dotfiles_projection"
    local surface="${ROOT}/.devcontainer/devcontainer.json"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local bak_s bak_t; bak_s="$(mktemp)"; bak_t="$(mktemp)"
    cp "$surface" "$bak_s"; cp "$toml" "$bak_t"
    _dp_restore() { cp "$bak_s" "$surface"; cp "$bak_t" "$toml"; rm -f "$bak_s" "$bak_t"; unset -f _dp_restore; }

    _neg_gate check_dotfiles_projection || { _dp_restore; die "check_dotfiles_projection is red before any plant, so a plant proves nothing: ${_NEG_GATE_OUT}"; }

    # (0) The theme leg: a hand edit to the btop surface (the one two renderers write).
    local theme="${ROOT}/etc/btop/themes/mios.theme" bak_th; bak_th="$(mktemp)"; cp "$theme" "$bak_th"
    printf 'theme[title]="#000001"\n' >> "$theme"; _neg_gate check_dotfiles_projection || :; cp "$bak_th" "$theme"; rm -f "$bak_th"
    grep -q "btop: etc/btop/themes/mios.theme drifted" <<<"$_NEG_GATE_OUT" || { _dp_restore; die "check_dotfiles_projection did not name a hand-edited btop theme: ${_NEG_GATE_OUT}"; }

    # (1) A desktop-only key back on an API-applied surface must go red naming
    # the file AND the key (ADR-0024).
    python3 - "$surface" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p, encoding="utf-8"))
d["customizations"]["vscode"]["settings"]["window.customTitleBarVisibility"] = "never"
open(p, "w", encoding="utf-8", newline="").write(json.dumps(d, indent=2) + "\n")
PY
    _neg_gate check_dotfiles_projection && { _dp_restore; die "check_dotfiles_projection passed with a desktop-only key on a client-portable surface"; }
    grep -q "window.customTitleBarVisibility" <<<"$_NEG_GATE_OUT" || { _dp_restore; die "check_dotfiles_projection went red without naming the planted key: ${_NEG_GATE_OUT}"; }
    grep -q "devcontainer/devcontainer.json" <<<"$_NEG_GATE_OUT" || { _dp_restore; die "check_dotfiles_projection went red without naming the planted file: ${_NEG_GATE_OUT}"; }
    cp "$bak_s" "$surface"

    # (2) Dropping the key from [dotfiles.vscode].desktop_only_keys makes it
    # portable again, so every surface WITHOUT it is now stale (it would flow
    # back on the next sync): the SSOT/surface disagreement must go red too. A
    # check that only scans surfaces for listed keys passes here -- that is the
    # Check-Without-Diff this plant exists to catch.
    python3 - "$toml" <<'PY'
import sys
p = sys.argv[1]
lines = open(p, encoding="utf-8").read().split("\n")
hits = [i for i, l in enumerate(lines) if l.lstrip().startswith('"window.customTitleBarVisibility",')]
assert len(hits) == 1, hits
del lines[hits[0]]
open(p, "w", encoding="utf-8", newline="").write("\n".join(lines))
PY
    _neg_gate check_dotfiles_projection && { _dp_restore; die "check_dotfiles_projection passed after a desktop-only key left the SSOT list while the surfaces still lack it"; }
    grep -q "window.customTitleBarVisibility" <<<"$_NEG_GATE_OUT" || { _dp_restore; die "check_dotfiles_projection went red without naming the de-listed key: ${_NEG_GATE_OUT}"; }

    _dp_restore
    _neg_gate check_dotfiles_projection || die "check_dotfiles_projection failed after restoration: ${_NEG_GATE_OUT}"
    log "check_dotfiles_projection negative test passed"
}

test_edge_generators() {
    log "Testing check_edge_generators"
    local conf="${ROOT}/usr/share/mios/hyprland/hyprland.conf"
    local bak plant; bak="$(mktemp)"; cp -p "$conf" "$bak"
    plant="$(printf 'gaps_%s = %d' out 10)"
    _eg_restore() { cp -p "$bak" "$conf"; rm -f "$bak"; unset -f _eg_restore; }

    _neg_gate check_edge_generators || { _eg_restore; die "check_edge_generators is red before any plant, so a plant proves nothing: ${_NEG_GATE_OUT}"; }
    sed -i "s/^    gaps_out = .*/    ${plant}/" "$conf"
    grep -q "^    ${plant}\$" "$conf" || { _eg_restore; die "the gaps_out plant did not land in hyprland.conf"; }
    _neg_gate check_edge_generators && { _eg_restore; die "check_edge_generators passed with a non-zero gap planted in the hyprland.conf the image installs"; }
    grep -q "hyprland.conf" <<<"$_NEG_GATE_OUT" || { _eg_restore; die "check_edge_generators went red without naming the planted file: ${_NEG_GATE_OUT}"; }
    grep -qF "$plant" <<<"$_NEG_GATE_OUT" || { _eg_restore; die "check_edge_generators went red without naming the planted line: ${_NEG_GATE_OUT}"; }

    _eg_restore
    _neg_gate check_edge_generators || die "check_edge_generators failed after restoration: ${_NEG_GATE_OUT}"
    log "check_edge_generators negative test passed"
}

test_edge_status() {
    log "Testing check_edge_status"
    local conf="${ROOT}/etc/skel/.config/alacritty/alacritty.toml" toml="${ROOT}/usr/share/mios/mios.toml"
    local bak tbak shim plant d f; bak="$(mktemp)"; tbak="$(mktemp)"; shim="$(mktemp -d)"; cp -p "$conf" "$bak"; cp -p "$toml" "$tbak"
    plant="$(printf 'padding.x=%d' 7)"
    _es_restore() { cp -p "$bak" "$conf"; cp -p "$tbak" "$toml"; rm -f "$bak" "$tbak"; rm -rf "$shim"; unset -f _es_restore; }

    _neg_gate check_edge_status || { _es_restore; die "check_edge_status is red before any plant, so a plant proves nothing: ${_NEG_GATE_OUT}"; }
    # (1) An artifact that no longer measures edge-to-edge must go red naming the key and the value.
    sed -i 's/^padding = { x = 0, y = 0 }$/padding = { x = 7, y = 0 }/' "$conf"
    grep -q '^padding = { x = 7' "$conf" || { _es_restore; die "the padding plant did not land in alacritty.toml"; }
    _neg_gate check_edge_status && { _es_restore; die "check_edge_status passed with alacritty padding planted at 7"; }
    grep -q "edge alacritty DRIFT" <<<"$_NEG_GATE_OUT" || { _es_restore; die "check_edge_status went red without naming the planted key: ${_NEG_GATE_OUT}"; }
    grep -qF "$plant" <<<"$_NEG_GATE_OUT" || { _es_restore; die "check_edge_status went red without naming the planted value: ${_NEG_GATE_OUT}"; }
    cp -p "$bak" "$conf"

    # (2) A reach = "full" entry still naming an owed lane must go red naming the entry and the lane.
    sed -i 's|^\(alacritty  *= { reach = "full".*\) }$|\1, pending = "owed-lane" }|' "$toml"
    grep -q '^alacritty .*pending = "owed-lane" }$' "$toml" || { _es_restore; die "the pending plant did not land in mios.toml"; }
    _neg_gate check_edge_status && { _es_restore; die "check_edge_status passed with pending on a reach = full entry"; }
    grep -q "edge alacritty DRIFT pending=owed-lane" <<<"$_NEG_GATE_OUT" || { _es_restore; die "check_edge_status went red without naming the pending entry: ${_NEG_GATE_OUT}"; }
    cp -p "$tbak" "$toml"

    # (3) No cargo on PATH is a violation, never a skip: mirror PATH minus cargo.
    for d in ${PATH//:/ }; do
        for f in "$d"/*; do
            [[ -x "$f" && "${f##*/}" != cargo && ! -e "$shim/${f##*/}" ]] && ln -s "$f" "$shim/${f##*/}"
        done
    done
    PATH="$shim" _neg_gate check_edge_status && { _es_restore; die "check_edge_status passed with cargo absent from PATH"; }
    grep -q "cargo is not installed" <<<"$_NEG_GATE_OUT" || { _es_restore; die "check_edge_status went red without naming the missing cargo: ${_NEG_GATE_OUT}"; }

    _es_restore
    _neg_gate check_edge_status || die "check_edge_status failed after restoration: ${_NEG_GATE_OUT}"
    log "check_edge_status negative test passed"
}
test_legacy_archive() {  # A stray file in the store must fail; tools/test_drift-checks.py TestLegacyArchive plants the rest.
    local p="${ROOT}/.archive/hooks/neg-probe"; mkdir -p "${p%/*}"; : > "$p"; _neg_gate check_legacy_archive && { rm -f "$p"; die "check_legacy_archive passed with a stray file in .archive"; }; rm -f "$p"; rmdir "${p%/*}" 2>/dev/null || :; _neg_gate check_legacy_archive || die "check_legacy_archive failed after restoration: $_NEG_GATE_OUT"; }

main() {
    if [[ $# -eq 1 && -n "$1" ]]; then
        if declare -f "$1" >/dev/null; then
            "$1"
            exit 0
        else
            die "Unknown test function: $1"
        fi
    fi
    log "Starting negative-test suite"
    _run_test test_neg_gate_harness
    _run_test test_task_schema
_run_test test_ci_suite_coverage
_run_test test_cargo_manifest_generated
_run_test test_ai_metadata_fresh
_run_test test_tracked_readable
_run_test test_leaked_fixtures
    _run_test test_fleet_safety
    _run_test test_ai_manifests_fresh
    _run_test test_version_ssot
    _run_test test_check_no_silent_tool_skips
    _run_test test_check_negatives_are_effective
    _run_test test_pipefail_grep_lint
    _run_test test_check_skip_list_covered
    _run_test test_resolver_equivalence
    _run_test test_eval_safety
    _run_test test_container_ports
    _run_test test_shellcheck_failure
    _run_test test_names_registry
    _run_test test_read_only_gate
    _run_test test_dead_lane
    _run_test test_dead_git_corpus
    _run_test test_root_toml_subset
    _run_test test_toml_projection
    _run_test test_ratchet_direction
    _run_test test_size_ceiling
    _run_test test_toolchain_pin
    _run_test test_ai_config_projection
    _run_test test_render_quadlets
    _run_test test_render_extension_coverage
    _run_test test_curl_retry
    _run_test test_resolver_ssot_refs
    _run_test test_nested_podman_caps
    _run_test test_bake_budget
    _run_test test_module_test_coverage
    _run_test test_router_parity
    _run_test test_council_gate_ssot
    _run_test test_agent_pipe_budgets
    _run_test test_agent_schema
    _run_test test_bake_tokens
    _run_test test_bake_unresolved_image
    _run_test test_containerfile_pinned_clones
    _run_test test_firstboot_tier
    _run_test test_bound_image_store
    _run_test test_rechunk_budget
    _run_test test_bake_core_reconcile
    _run_test test_nested_podman_retry
    _run_test test_gate_registry
    _run_test test_adhoc_toml_parsers
    _run_test test_install_uninstall_symmetry
    _run_test test_ps_port_fallback_ssot
    _run_test test_github_slug_casing
    _run_test test_ps_encoding_and_bom
    _run_test test_ps_irm_iex_entry
    _run_test test_secret_handling
    _run_test test_wsl_distro_resolution
    _run_test test_comment_lex_equivalence
    _run_test test_docs_ratchet
    _run_test test_header_integrity
    _run_test test_header_integrity_unreadable_corpus
    _run_test test_resolver_differential_parity
    _run_test test_resolver_twin_parity
    _run_test test_generator_host_parity
    _run_test test_generator_host_parity_unreadable_corpus
    _run_test test_legibility_ratchet
    _run_test test_bootstrap_sync
    _run_test test_no_inert_ssot_tables
    _run_test test_profile_integrity
    _run_test test_build_tool_dispatch
    _run_test test_phase_registry
    _run_test test_signature_policy
    _run_test test_projection_coverage
    _run_test test_drift_stubs
    _run_test test_native_twinless
    _run_test test_doc_refs_resolve
    _run_test test_desktop_launchers
    _run_test test_blade_reconcile_schema
    _run_test test_docs_ratchet_monotone
    _run_test test_manual_generated
    _run_test test_manual_ledger
    _run_test test_comment_landing
    _run_test test_credential_literals
    _run_test test_protected_refs
    _run_test test_names_registry_equivalence
    _run_test test_redact_coverage
    _run_test test_daemon_governor
    _run_test test_manual_links
    _run_test test_doc_port_scheme
    _run_test test_unit_dependency_closure
    _run_test test_unit_security
    _run_test test_test_hermeticity
    _run_test test_no_mkdir_in_var
    _run_test test_quadlet_privilege
    _run_test test_lint_is_final
    _run_test test_firstboot_degrade_open
    _run_test test_require_tools
    _run_test test_ssot_lint_deadkey
    _run_test test_soft_mode_not_committed
    _run_test test_oci_archive_path
    _run_test test_replaceme_mount_substitution
    _run_test test_kickstart_shell_syntax
    _run_test test_offline_install_invariant
    _run_test test_installer_family_roles
    _run_test test_bib_configs_projection
    _run_test test_ssot_lint_equivalence
    _run_test test_repo_partition_label_ssot
    _run_test test_bib_single_config_invariant
    _run_test test_artifact_recipes
    _run_test test_chpasswd_plaintext
    _run_test test_hardcode_lint_anchored_allowlist
    _run_test test_build_artifacts_output_dir
    _run_test test_win11_vm_template_xml
    _run_test test_ipa_enroll_projection
    _run_test test_bootc_install_projection
    _run_test test_uki_cmdline_projection
    _run_test test_composefs_projection
    _run_test test_cockpit_projection
    _run_test test_chrony_ptp_dropin
    _run_test test_chrony_projection
    _run_test test_nut_projection
    _run_test test_renderer_gate_coverage
    _run_test test_bake_plan
    _run_test test_bake_ref_defaults
    _run_test test_deploy_plane
    _run_test test_sbom_metadata
    _run_test test_clevis_luks
    _run_test test_clevis_luks_ssot
    _run_test test_metal_vfio
    _run_test test_target_languages
    _run_test test_roadmap_index
    _run_test test_templates_compilation
    _run_test test_template_conformance
    _run_test test_impossible_eol
    _run_test test_smoke_manifest
    _run_test test_negative_coverage
    _run_test test_verb_templates
    _run_test test_pipe_boundaries
    _run_test test_standardize_docs
    _run_test test_sync_wiki
    _run_test test_vllm_name_canonical
    _run_test test_pipe_extraction_parity
    _run_test test_guacamole_consistency
    _run_test test_cephfs_ssot
    _run_test test_v2v_import_ssot
    _run_test test_no_hardcode_version
    _run_test test_law_enforcers
    _run_test test_var_closure
    _run_test test_usr_over_etc
    _run_test test_projection_registry
    _run_test test_bake_plan_integrity
    _run_test test_bake_ref_parity
    _run_test test_db_seed_coverage
    _run_test test_account_column_parity
    _run_test test_module_length
    _run_test test_firstboot_provisioners
    _run_test test_schema_consumers
    _run_test test_task_store
    _run_test test_task_ids
    _run_test test_tasks_status_parity
    _run_test test_agy_tasks
    _run_test test_mios_toml_integrity
    _run_test test_privileged_quadlets_minimal
    _run_test test_container_names
    _run_test test_adr_index
    _run_test test_greenboot
    _run_test test_service_urls
    _run_test test_ports_bound
    _run_test test_blade_coverage
    _run_test test_blade_karg
    _run_test test_role_ssot
    _run_test test_no_generated_prose_in_resolvers
    _run_test test_manpages
    _run_test test_temp_fixture_cleanup
    _run_test test_negatives_registered

    # A negative test nobody invokes looks like coverage in a file listing
    # and provides none; check_negatives_registered keeps this list whole.
    _run_test test_ssot_consumer_keys
    _run_test test_unit_projection
    _run_test test_metal_vs_hosted
    _run_test test_node_pool
    _run_test test_port_fallbacks
    _run_test test_vendored_assets_non_stub
    _run_test test_resolved_env_lossless
    _run_test test_no_duplicate_value_key
    _run_test test_no_hardcoded_ssot_literal
    _run_test test_pipeline_numbering
    _run_test test_value_aliases
    _run_test test_bash_phase_ratchet
    _run_test test_unpinned_runtime_fetches
    _run_test test_windows_exe_provenance
    _run_test test_ps_redirectors
    _run_test test_cargo_deny
    _run_test test_powershell_parse
    _run_test test_ports_category_schema
    _run_test test_globals_generated
    _run_test test_variant_registry
    _run_test test_deploy_formats
    _run_test test_verify_images
    _run_test test_header_comment_syntax
    _run_test test_rust_test_coverage
    _run_test test_os_update_timer_enabled
    _run_test test_verb_stub_backends
    _run_test test_package_registry
    _run_test test_vendor_urls
    _run_test test_retired_models
    _run_test test_etc_duplicates
    _run_test test_canonical_bools
    _run_test test_gate_index
    _run_test test_pod_quadlets
    _run_test test_egress_firewall
    _run_test test_dotfiles_projection
    _run_test test_tmux_theme
    _run_test test_btop_theme
    _run_test test_fastfetch
    _run_test test_native_generators_fail_closed
    _run_test test_edge_generators
    _run_test test_edge_status
    _run_test test_artifact_prompt
    _run_test test_ai_artifacts
    _run_test test_rust_categories
    _run_test test_legacy_archive
    if (( ${#_FAILED[@]} )); then
        echo -e "[1;31m[drift-gate-negatives][0m ${#_FAILED[@]} test(s) failed:" >&2
        printf '  %s
' "${_FAILED[@]}" >&2
        echo "  (a later failure may be a consequence of an earlier one)" >&2
        exit 1
    fi
    log "All negative tests completed successfully"
}

test_rust_categories() {
    log "Testing check_rust_categories"
    _neg_gate check_rust_categories || die "rust-categories is red before the probe: $_NEG_GATE_OUT"
    local toml="${ROOT}/usr/share/mios/mios.toml"
    local backup; backup="$(mktemp)"; cp -p "$toml" "$backup"
    # Planted owner-less category must fail check_rust_categories
    local probe_owner; probe_owner="$(printf 'ow%sner = ""' 'o')"
    local probe_table; probe_table="$(printf '[rust.categories.probe-%s]' "$(date +%s)")"
    printf '\n%s\n%s\n' "$probe_table" "$probe_owner" >> "$toml"
    if _neg_gate check_rust_categories; then
        cp -p "$backup" "$toml" && rm -f "$backup"
        die "rust-categories reported clean with a planted owner-less category"
    fi
    cp -p "$backup" "$toml" && rm -f "$backup"
    _neg_gate check_rust_categories || die "rust-categories failed after restoration: $_NEG_GATE_OUT"
    log "check_rust_categories negative test passed"
}

main "$@"
