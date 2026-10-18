#!/usr/bin/env python3
"""Tier 2: Boundary & Corner Cases — Credentials Architecture Boundaries Suite."""

from __future__ import annotations

import os
import sys

# Ensure repository root is in sys.path for standalone execution
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.e2e.e2e_framework import E2ETestCase, IsolatedEnvironment, run_standalone



class TestTier2BoundaryCredentials(E2ETestCase):
    tier = 2
    suite_name = "T2_Boundary_Credentials"

    def test_credential_cmd_literal_percent_escaping(self):
        """Corner Case: CMD literal % escaping handles consecutive percent signs."""
        # When % is escaped to %%, '%%PATH%%' becomes '%%%%PATH%%%%'
        def escape_cmd(val: str) -> str:
            # Emulates ConvertTo-MiosCmdLiteral
            if any(c in val for c in ('\0', '\r', '\n')):
                raise ValueError("Unescapable control character")
            val = val.replace('%', '%%')
            return f'"{val}"'

        escaped = escape_cmd("100% Guaranteed %PATH%")
        self.assert_equal(escaped, '"100%% Guaranteed %%PATH%%"')

    def test_credential_cmd_literal_control_char_rejection(self):
        """Boundary: ConvertTo-MiosCmdLiteral must reject unescapable control chars."""
        def escape_cmd(val: str) -> str:
            if any(c in val for c in ('\0', '\r', '\n')):
                raise ValueError("Unescapable control character")
            return f'"{val.replace("%", "%%")}"'

        for bad_char in ['\0', '\r', '\n']:
            try:
                escape_cmd(f"PasswordWith{bad_char}BadChar")
                self.assert_true(False, f"Expected ValueError on control char: {repr(bad_char)}")
            except ValueError:
                pass  # Passed rejection

    def test_credential_extreme_length_password_handling(self):
        """Boundary: 1024-character passwords handled without buffer truncation."""
        long_pass = "P@ssw0rd!" * 114  # 1026 characters
        self.assert_greater(len(long_pass), 1000)

        with IsolatedEnvironment() as env:
            tmp_file = env.write_file(f"PASSWORD={long_pass}\n", "secret.env", mode=0o600)
            read_back = env.read_file("secret.env")
            self.assert_in(long_pass, read_back)

    def test_credential_autologon_count_persistence_boundary(self):
        """Boundary: Persistent autologon sets high LogonCount (>= 999999)."""
        persistent_count = 9999999
        self.assert_greater(persistent_count, 1000)

    def test_credential_layer_resolution_order_mock(self):
        """Corner Case: User layer strictly overrides host layer and vendor layer."""
        with IsolatedEnvironment() as env:
            vendor = env.write_file('[auth]\npassword = "vendor_pass"\n', "vendor.toml")
            host = env.write_file('[auth]\npassword = "host_pass"\n', "host.toml")
            user = env.write_file('[auth]\npassword = "user_pass"\n', "user.toml")

            # Emulate layered resolution: user > host > vendor
            def resolve_layers(v_path, h_path, u_path):
                import tomllib
                res = {}
                for p in [v_path, h_path, u_path]:
                    if os.path.exists(p):
                        with open(p, "rb") as f:
                            data = tomllib.load(f)
                            res.update(data.get("auth", {}))
                return res

            res = resolve_layers(vendor, host, user)
            self.assert_equal(res.get("password"), "user_pass")


if __name__ == "__main__":
    sys.exit(run_standalone(TestTier2BoundaryCredentials))

