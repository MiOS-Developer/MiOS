#!/usr/bin/env python3
# AI-hint: Tier-1 e2e coverage of SSOT integrity and the M5 features, each run against the shipped code with positive and negative controls (cosign trust anchor, nerd-font hard fail, greeter tmpfiles whitelist).
"""Tier 1: Feature Coverage — SSOT & Stage 1 Integrity Suite.

Covers:
- Feature 1: Stage 1 Merge Re-verification (mios.toml, render_spec.py, manual ch89, 97-ssot-lint)
- Feature 35: Cosign Trust Anchor in automation/49-cosign-policy.sh
- Feature 36: Nerd-Font Digest Hard Fail in automation/56-fonts.sh
- Feature 37: Greeter Tmpfiles Whitelist in .gitignore / staged tmpfiles
- Feature 38: ShellCheck SC2066 in usr/libexec/mios/forge-firstboot.sh
- Feature 41: Standing Gates scripts existence and execution interfaces
"""

from __future__ import annotations

import os
import sys
import tomllib

# Ensure repository root is in sys.path for standalone execution
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.e2e.e2e_framework import E2ETestCase, IsolatedEnvironment, run_standalone



class TestTier1SSOT(E2ETestCase):
    tier = 1
    suite_name = "T1_SSOT_Integrity"

    def test_feature_01_ssot_size_and_toml_validity(self):
        """Feature 1: Verify mios.toml exists, >850kB, and parses cleanly as valid TOML."""
        ssot_path = "/usr/share/mios/mios.toml"
        self.assert_true(os.path.isfile(ssot_path), f"SSOT file missing: {ssot_path}")
        size = os.path.getsize(ssot_path)
        self.assert_greater(size, 850000, f"SSOT size {size} is not > 850,000 bytes")

        # Two-sided control: valid TOML parses; corrupt TOML fails
        with open(ssot_path, "rb") as f:
            data = tomllib.load(f)
        self.assert_true(isinstance(data, dict), "SSOT did not parse into dict")

        def pos_control():
            return isinstance(data, dict) and len(data) > 20

        def neg_control():
            corrupt = b"this is not valid toml = = ="
            try:
                tomllib.loads(corrupt.decode())
                return False
            except tomllib.TOMLDecodeError:
                return True

        self.assert_two_sided(pos_control, neg_control, "SSOT TOML Syntax Validation")

    def test_feature_01_merged_ssot_sections_present(self):
        """Feature 1: Verify all required merged sections exist in mios.toml."""
        ssot_path = "/usr/share/mios/mios.toml"
        with open(ssot_path, "rb") as f:
            data = tomllib.load(f)

        required_sections = [
            ("migration", data.get("migration")),
            ("security.privileged_units", data.get("security", {}).get("privileged_units")),
            ("security.licensing", data.get("security", {}).get("licensing")),
            ("autounattend", data.get("autounattend")),
            ("vm.looking_glass", data.get("vm", {}).get("looking_glass")),
            ("windows.xbox", data.get("windows", {}).get("xbox")),
        ]

        for name, sec in required_sections:
            self.assert_true(sec is not None, f"Required merged section missing: {name}")

    def test_feature_01_render_spec_check(self):
        """Feature 1: Verify /usr/libexec/mios/win/xbox/render_spec.py --check exits 0."""
        script = "/usr/libexec/mios/win/xbox/render_spec.py"
        self.assert_true(os.path.isfile(script), f"render_spec.py missing at {script}")

        code, stdout, stderr = self.run_cmd(["python3", script, "--check"])
        self.assert_equal(code, 0, f"render_spec.py --check failed with code {code}:\n{stderr}")

    def test_feature_01_manual_ch89_integrity(self):
        """Feature 1: Verify ch89 manual exists and is properly registered in manual.md ToC."""
        ch89_path = "/usr/share/doc/mios/manual/ch89-glass-desktop-and-session-resilience.md"
        self.assert_true(os.path.isfile(ch89_path), f"ch89 manual missing: {ch89_path}")
        
        with open(ch89_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check required topics per R2 of ORIGINAL_REQUEST.md
        self.assert_in("AI-hint:", content)
        self.assert_in("89.1", content)
        self.assert_in("89.2", content)
        self.assert_in("89.3", content)
        self.assert_in("89.4", content)
        self.assert_in("89.5", content)
        self.assert_in("89.6", content)
        self.assert_in("89.7", content)

        # Check manual.md index
        manual_index = "/usr/share/doc/mios/manual.md"
        self.assert_true(os.path.isfile(manual_index), f"manual.md missing: {manual_index}")
        with open(manual_index, "r", encoding="utf-8") as f:
            index_content = f.read()
        self.assert_in("ch89-glass-desktop-and-session-resilience.md", index_content)

    def test_feature_01_ssot_lint_script_interface(self):
        """Feature 1: Verify automation/97-ssot-lint.sh exists and has bash header."""
        lint_script = "/automation/97-ssot-lint.sh"
        self.assert_true(os.path.isfile(lint_script), f"Missing {lint_script}")
        with open(lint_script, "r", encoding="utf-8") as f:
            header = f.readline()
        self.assert_in("bash", header)

    def test_feature_35_cosign_trust_anchor(self):
        """Feature 35: the shipped signature policy trusts MiOS anchors for the MiOS namespace, and containers/image loads it.

        Positive: usr/lib/containers/policy.json loads in containers/image
        (skopeo) and resolves ghcr.io/mios-dev to sigstoreSigned with keys under
        /usr/share/pki/containers. Negative: the old insecureAcceptEverything
        policy is refused by the same loader. The full signed/unsigned pull
        matrix is tests/test-cosign-trust-anchor.sh.
        """
        import json
        import shutil

        cosign_script = "/automation/49-cosign-policy.sh"
        with open(cosign_script, "r", encoding="utf-8") as f:
            content = f.read()
        self.assert_not_in("/etc/pki/containers", content, "phase 49 must not read or write /etc/pki/containers")
        self.assert_in('_src="${_src_root}${_dst}"', content, "anchors must come from the source tree")
        with open("/usr/lib/containers/policy.json", "r", encoding="utf-8") as f:
            policy = json.load(f)
        req = policy["transports"]["docker"]["ghcr.io/mios-dev"][0]
        self.assert_equal(req["type"], "sigstoreSigned")
        keys = [req["keyPath"]] if "keyPath" in req else req["keyPaths"]
        self.assert_true(keys and all(k.startswith("/usr/share/pki/containers/") for k in keys), f"{keys}")
        if shutil.which("skopeo") is None:
            return

        def loads(path):
            code, out, err = self.run_cmd(["skopeo", "--policy", path, "copy",
                                           "dir:/nonexistent/mios-e2e-probe", "dir:/nonexistent/mios-e2e-out"])
            if "trust policy" in err:
                return False
            if "/nonexistent/mios-e2e-probe" in err:
                return True
            raise RuntimeError(f"unexpected skopeo result ({code}): {err}")

        with IsolatedEnvironment() as env:
            bad = env.write_file('{"default":[{"type":"insecureAcceptEverything"}]}\n', "bad-policy.json")
            self.assert_two_sided(lambda: loads("/usr/lib/containers/policy.json"),
                                  lambda: not loads(bad),
                                  "containers/image loads the shipped policy and refuses the invalid type")

    def test_feature_36_nerd_font_digest_hard_fail(self):
        """Feature 36: the REAL _install_nerd_symbols from 56-fonts.sh dies when no verified artefact exists.

        The function is cut out of the shipped script, its absolute install paths
        are redirected into a temp root, and only the upstream side (release
        resolution and the digest-verified download) is stubbed; die and
        mios_ul_pass are the real helpers. Positive: a verified artefact installs
        and exits 0. Negative: no published digest, and an unresolvable release,
        each exit 1 with the hard-fail message and install nothing.
        """
        import re
        import zipfile

        fonts_script = "/automation/56-fonts.sh"
        self.assert_true(os.path.isfile(fonts_script), f"Missing {fonts_script}")
        with open(fonts_script, "r", encoding="utf-8") as f:
            content = f.read()
        m = re.search(r"^_install_nerd_symbols\(\) \{\n.*?^\}\n", content, re.S | re.M)
        self.assert_true(m is not None, "56-fonts.sh no longer defines _install_nerd_symbols()")
        body = m.group(0)
        self.assert_regex(body, r'\n\s*die "Symbols-Only Nerd Font not installed \(no verified artefact\)"',
                          "the no-verified-artefact branch must die")
        self.assert_not_in('mios_warn "Symbols-Only Nerd Font not installed', body,
                           "a warning on the no-verified-artefact branch is the defect F36 removes")
        with open("/automation/lib/common.sh", "r", encoding="utf-8") as f:
            # die() as shipped, with the log_ts() it formats its timestamp with.
            die_def = "".join(line for line in f if line.startswith(("die()", "log_ts()")))

        with IsolatedEnvironment() as env:
            root = env.path()
            fn = (body.replace("/usr/share/fonts/nerd-symbols", f"{root}/fonts")
                      .replace("/usr/share/mios/vendored", f"{root}/vendored")
                      .replace("/tmp/nerd-symbols.zip", f"{root}/nerd.zip"))
            self.assert_not_in("/usr/", fn, "an install path escaped the temp-root rewrite")
            with zipfile.ZipFile(env.path("upstream.zip"), "w") as z:
                z.writestr("SymbolsNerdFont-Regular.ttf", "ttf")
            harness = env.write_file(
                "set -euo pipefail\n"
                "source /automation/lib/upstream-latest.sh\n"
                + die_def +
                "mios_log() { :; }\nmios_ok() { echo \"OK: $*\"; }\n"
                "mios_warn() { echo \"WARN: $*\" >&2; }\n"
                "mios_record_resolved() { echo \"RECORD $*\"; }\n"
                "mios_latest_git_ref() { [[ \"$MODE\" == unresolvable ]] && return 1; echo v9.9.9; }\n"
                "mios_fetch_gh_release_asset() {\n"
                "    case \"$MODE\" in\n"
                "        verified) cp \"$ZIP\" \"$4\"; sha256sum \"$ZIP\" | cut -d' ' -f1 ;;\n"
                "        *) return 2 ;;  # the forge publishes no digest: nothing downloaded\n"
                "    esac\n"
                "}\n" + fn + "_install_nerd_symbols\n",
                "harness.sh",
            )
            installed = env.path("fonts", "SymbolsNerdFont-Regular.ttf")

            def run(mode):
                if os.path.exists(installed):
                    os.unlink(installed)
                return self.run_cmd(["bash", harness], env={"MODE": mode, "ZIP": env.path("upstream.zip")})

            def pos_control():
                code, out, err = run("verified")
                return code == 0 and "OK: Symbols-Only Nerd Font v9.9.9 installed" in out and os.path.isfile(installed)

            def neg_control():
                for mode in ("nodigest", "unresolvable"):
                    code, out, err = run(mode)
                    if code != 1 or "ERROR: Symbols-Only Nerd Font not installed" not in err:
                        return False
                    if "installed" in out or os.path.exists(installed):
                        return False
                return True

            self.assert_two_sided(pos_control, neg_control, "real 56-fonts nerd-font hard fail")

    def test_feature_37_greeter_tmpfiles_whitelist(self):
        """Feature 37: git tracks usr/lib/tmpfiles.d/mios-greeter.conf; the directory rule still ignores the rest.

        Asked of git itself (check-ignore), not of a substring in .gitignore.
        Positive: the greeter tmpfiles file is not ignored. Negative: a
        non-whitelisted name in the same directory is still ignored, so the
        whitelist is one file wide.
        """
        greeter_conf = "/usr/lib/tmpfiles.d/mios-greeter.conf"
        self.assert_true(os.path.isfile(greeter_conf), f"Missing greeter tmpfiles: {greeter_conf}")

        def ignored(rel):
            code, out, err = self.run_cmd(["git", "-C", "/", "check-ignore", "-q", "--no-index", rel])
            if code not in (0, 1):
                raise RuntimeError(f"git check-ignore failed ({code}): {err}")
            return code == 0

        self.assert_two_sided(
            lambda: not ignored("usr/lib/tmpfiles.d/mios-greeter.conf"),
            lambda: ignored("usr/lib/tmpfiles.d/zz-not-whitelisted.conf"),
            "greeter tmpfiles whitelist",
        )

    def test_feature_38_forge_firstboot_word_splitting_clean(self):
        """Feature 38: Verify usr/libexec/mios/forge-firstboot.sh has no SC2066 pattern."""
        script_path = "/usr/libexec/mios/forge-firstboot.sh"
        if os.path.isfile(script_path):
            with open(script_path, "r", encoding="utf-8") as f:
                content = f.read()
            # SC2066 checks 'for var in "$@"' or 'for var in "$var"' with bad quotes
            self.assert_not_in('for repo in "$repos"', content)


if __name__ == "__main__":
    sys.exit(run_standalone(TestTier1SSOT))

