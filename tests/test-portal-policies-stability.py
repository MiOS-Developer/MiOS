#!/usr/bin/env python3
# AI-hint: Regression test suite for Firefox 158 Enterprise Policy engine stability compliance
# and zero-warning policy configuration under distribution/policies.json.
# AI-related: usr/share/mios/theme/templates/portal-policies.json.tmpl, src/mios-portal/install/distribution/policies.json

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile

PORTAL_BIN = "/usr/lib/mios/portal/firefox"
OMNI_JA = "/usr/lib/mios/portal/browser/omni.ja"
POLICIES_JSON = "/usr/lib/mios/portal/distribution/policies.json"
TEMPLATE_POLICIES = "usr/share/mios/theme/templates/portal-policies.json.tmpl"


def get_allowed_prefixes():
    """Extract allowedPrefixes from Firefox's Policies.sys.mjs inside omni.ja."""
    if not os.path.exists(OMNI_JA):
        return [
            "accessibility.", "alerts.", "app.update.", "browser.",
            "datareporting.policy.", "devtools.", "dom.", "extensions.",
            "general.autoScroll", "general.smoothScroll", "geo.", "gfx.",
            "identity.fxaccounts.toolbar.", "intl.", "keyword.enabled",
            "layers.", "layout.", "mathml.disabled", "media.", "network.",
            "pdfjs.", "places.", "pref.", "print.", "privacy.", "sidebar.",
            "signon.", "spellchecker.", "svg.",
            "toolkit.legacyUserProfileCustomizations.stylesheets",
            "ui.", "webgl.", "widget.", "xpinstall."
        ]
    with zipfile.ZipFile(OMNI_JA) as z:
        content = z.read("modules/policies/Policies.sys.mjs").decode("utf-8", errors="ignore")
    m = re.search(r"const allowedPrefixes = \[(.*?)\];", content, re.DOTALL)
    assert m, "Could not extract allowedPrefixes from Policies.sys.mjs"
    return [p.strip().strip("\"'") for p in m.group(1).split(",") if p.strip()]


class TestPortalPoliciesStability(unittest.TestCase):
    def setUp(self):
        self.allowed_prefixes = get_allowed_prefixes()

    def test_positive_policies_have_zero_stability_restricted_preferences(self):
        """Verify that policies.json contains no stability-restricted preferences."""
        if not os.path.exists(POLICIES_JSON):
            self.skipTest(f"{POLICIES_JSON} not present")

        with open(POLICIES_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)

        policies = data.get("policies", {})
        prefs = policies.get("Preferences", {})

        # Stability restricted prefs that must NEVER appear under Preferences
        forbidden = [
            "services.settings.server",
            "datareporting.healthreport.uploadEnabled",
            "toolkit.telemetry.enabled",
            "toolkit.telemetry.server",
        ]
        for bad in forbidden:
            self.assertNotIn(
                bad,
                prefs,
                f"Forbidden stability-restricted preference '{bad}' found in Preferences"
            )

        # Every pref under Preferences must match an allowedPrefix
        for pref_name in prefs:
            is_allowed = any(pref_name.startswith(pfx) for pfx in self.allowed_prefixes)
            self.assertTrue(
                is_allowed,
                f"Preference '{pref_name}' violates stability restrictions (not in allowedPrefixes)"
            )

        # Dedicated top-level policy keys must be present
        self.assertTrue(policies.get("DisableTelemetry"), "DisableTelemetry must be true")
        self.assertTrue(
            policies.get("DisableRemoteSettingsAndAcceptSecurityConsequences"),
            "DisableRemoteSettingsAndAcceptSecurityConsequences must be true"
        )
        self.assertTrue(policies.get("DisableFeedbackCommands"), "DisableFeedbackCommands must be true")
        self.assertFalse(policies.get("CaptivePortal"), "CaptivePortal must be false")
        self.assertFalse(policies.get("NetworkPrediction"), "NetworkPrediction must be false")

    def test_negative_stability_violation_is_detected(self):
        """Negative control: inject restricted preferences into a fixture and assert violation."""
        dummy_prefs = {
            "browser.ml.enable": {"Value": False, "Status": "locked"},
            "services.settings.server": {"Value": "", "Status": "locked"},
            "toolkit.telemetry.enabled": {"Value": False, "Status": "locked"}
        }

        violations = [
            p for p in dummy_prefs
            if not any(p.startswith(pfx) for pfx in self.allowed_prefixes)
        ]

        self.assertIn("services.settings.server", violations)
        self.assertIn("toolkit.telemetry.enabled", violations)
        self.assertEqual(len(violations), 2)

    def test_positive_headless_startup_emits_zero_policy_stability_errors(self):
        """Assert Firefox startup produces zero stability policy errors in console."""
        if not os.path.exists(PORTAL_BIN):
            self.skipTest(f"{PORTAL_BIN} not present")

        with tempfile.TemporaryDirectory() as tmp_profile:
            cmd = [
                "timeout", "4s",
                PORTAL_BIN,
                "--headless",
                "--profile", tmp_profile,
                "about:blank"
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

            self.assertNotIn(
                "Preference not allowed for stability reasons",
                res.stdout,
                f"Firefox emitted stability preference rejection:\n{res.stdout}"
            )
            self.assertNotIn(
                "Ignoring preference override of remote settings server",
                res.stdout,
                f"Firefox emitted remote settings override warning:\n{res.stdout}"
            )


if __name__ == "__main__":
    unittest.main()
