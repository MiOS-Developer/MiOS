#!/usr/bin/env python3
# AI-hint: Gate for the staged MiOS-Xbox feature catalogue (research lane, Q3 decision): every Controlled-Feature-
#   Rollout id from mios-xbox-features.txt is present exactly once as a named, sourced, ENABLED toggle in
#   [windows.xbox.xbox.feature_overrides]; research additions carry names/sources; the table is a drop-in
#   replacement for the spec lane's table (same ids, same entry shape). Loaded through mios_toml.load_merged on a
#   temp layer stack (live vendor SSOT + fragment). Negative controls plant a dropped id, a duplicate, a disabled
#   shipped id, a blank description, an unknown source and a lost spec-lane id.
# AI-related: usr/lib/mios/mios_toml.py, usr/share/mios/mios.toml
"""Run: python3 -I tests/test-mios-xbox-features-fragment.py -v"""
from __future__ import annotations

import copy
import os
import sys
import tomllib
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_VENDOR = os.environ.get("MIOS_VENDOR_TOML") or os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")
_STAGED = os.environ.get("MIOS_XBOX_STAGED_DIR", "/home/mios/mios-desktop-work-1/staged-ssot")
_FRAGMENT = os.path.join(_STAGED, "xbox-features.toml")
_SPEC = os.path.join(_STAGED, "xbox-spec.toml")
_IDS_FILE = os.environ.get("MIOS_XBOX_FEATURES_TXT",
                           "/var/lib/mios-build/repos/mios-bootstrap-build/field/autounattend/mios-xbox-features.txt")

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(_ROOT, "usr", "lib", "mios"))
import mios_toml  # noqa: E402  (read-only: load_merged with explicit layers)
sys.path.pop(0)

CONFIDENCE = {"dictionary", "community", "unverified"}
SHAPE = {"id", "enabled", "group", "desc"}  # the spec lane's entry shape (render_spec.py consumes these)


def shipped_ids() -> list[int]:
    with open(_IDS_FILE, encoding="utf-8") as fh:
        return [int(t) for t in (line.split("#", 1)[0].strip() for line in fh) if t]


def check_catalog(data: dict, required: list[int], spec_ids: set[int] | None = None) -> list[str]:
    """Findings (empty = conforming)."""
    xb = (data.get("windows") or {}).get("xbox") or {}
    table = (xb.get("xbox") or {}).get("feature_overrides") or {}
    sources = set(((xb.get("research") or {}).get("sources") or {}))
    out: list[str] = []
    seen: dict[int, str] = {}
    for key, e in table.items():
        if not isinstance(e, dict):
            out.append(f"{key}: not a table")
            continue
        if not SHAPE <= set(e):
            out.append(f"{key}: missing {sorted(SHAPE - set(e))}")
            continue
        if not isinstance(e["id"], int) or isinstance(e["id"], bool):
            out.append(f"{key}: id is not an integer")
            continue
        if e["id"] in seen:
            out.append(f"{key}: duplicate id {e['id']} (also {seen[e['id']]})")
        seen[e["id"]] = key
        if not key.endswith(f"_{e['id']}") or key.startswith(("shell_2026_", "bundle_", "base_rollout_")):
            out.append(f"{key}: key is not a descriptive <name>_<id>")
        if not str(e.get("desc", "")).strip():
            out.append(f"{key}: blank desc")
        if e.get("confidence") not in CONFIDENCE:
            out.append(f"{key}: confidence {e.get('confidence')!r}")
        if not e.get("sources") or not set(e["sources"]) <= sources:
            out.append(f"{key}: sources {e.get('sources')} not all in [windows.xbox.research.sources]")
        if not isinstance(e["enabled"], bool):
            out.append(f"{key}: enabled is not a boolean")
    for i in required:
        if i not in seen:
            out.append(f"shipped id {i} missing")
        elif table[seen[i]]["enabled"] is not True:
            out.append(f"shipped id {i} ({seen[i]}) is not enabled (operator: all ship)")
    for i in sorted((spec_ids or set()) - set(seen)):
        out.append(f"spec-lane id {i} would be lost by the replacement")
    for key, e in ((xb.get("research") or {}).get("settings") or {}).items():
        if not {"enabled", "method", "target", "desc", "sources"} <= set(e):
            out.append(f"research.settings.{key}: incomplete")
        elif not set(e["sources"]) <= sources:
            out.append(f"research.settings.{key}: unknown source")
    return out


class FeatureCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_FRAGMENT):
            raise unittest.SkipTest(f"{_FRAGMENT} not present (merged or not staged)")
        cls.data = mios_toml.load_merged([_VENDOR, _FRAGMENT])
        cls.required = shipped_ids() if os.path.isfile(_IDS_FILE) else []
        cls.spec_ids = set()
        if os.path.isfile(_SPEC):
            with open(_SPEC, "rb") as fh:
                spec = tomllib.load(fh)
            cls.spec_ids = {e["id"] for e in spec["windows"]["xbox"]["xbox"]["feature_overrides"].values()}

    def table(self, d):
        return d["windows"]["xbox"]["xbox"]["feature_overrides"]

    # positive controls
    def test_catalog_conforms(self):
        self.assertEqual(len(self.required), 109)
        self.assertEqual(check_catalog(self.data, self.required, self.spec_ids), [])

    def test_vendor_layer_survives_the_merge(self):
        self.assertEqual(self.data["locale"]["language"], mios_toml.load_merged([_VENDOR])["locale"]["language"])

    def test_corrections_and_additions(self):
        by_id = {e["id"]: e for e in self.table(self.data).values()}
        self.assertEqual(by_id[42550315].get("name"), "RunInPlaceUpgrade")
        self.assertEqual(by_id[55305888].get("name"), "KnowYourPC_W11FAQList")
        added = [e for e in by_id.values() if e["group"] == "research_2026"]
        self.assertGreaterEqual(len(added), 20)
        self.assertTrue(by_id[60716524]["enabled"])           # Low Latency Profile recommended
        self.assertFalse(by_id[59213768]["enabled"])          # taskbar position stays spec-owned
        self.assertFalse(by_id[49579327]["enabled"])          # Auto SR: no NPU in the VM

    # negative controls (one planted defect each)
    def _plant(self, mutate):
        d = copy.deepcopy(self.data)
        mutate(d)
        return check_catalog(d, self.required, self.spec_ids)

    def _key(self, i):
        return next(k for k, e in self.table(self.data).items() if e["id"] == i)

    def test_dropped_shipped_id_fails(self):
        f = self._plant(lambda d: self.table(d).pop(self._key(58989070)))
        self.assertIn("shipped id 58989070 missing", f)

    def test_disabled_shipped_id_fails(self):
        f = self._plant(lambda d: self.table(d)[self._key(59765208)].__setitem__("enabled", False))
        self.assertTrue(any("59765208" in x and "not enabled" in x for x in f), f)

    def test_duplicate_id_fails(self):
        f = self._plant(lambda d: self.table(d).__setitem__("again_59765208", dict(self.table(d)[self._key(59765208)])))
        self.assertTrue(any("duplicate id 59765208" in x for x in f), f)

    def test_blank_desc_and_unknown_source_fail(self):
        k = self._key(42592269)
        f = self._plant(lambda d: self.table(d)[k].update(desc=" ", sources=["nowhere"]))
        self.assertIn(f"{k}: blank desc", f)
        self.assertTrue(any(x.startswith(f"{k}: sources") for x in f), f)

    def test_non_descriptive_key_fails(self):
        f = self._plant(lambda d: self.table(d).__setitem__("shell_2026_47205210", self.table(d).pop(self._key(47205210))))
        self.assertTrue(any("not a descriptive" in x for x in f), f)

    def test_lost_spec_lane_id_fails(self):
        if not self.spec_ids:
            self.skipTest("spec-lane fragment not staged")
        f = check_catalog(self.data, [], self.spec_ids | {99999999})
        self.assertIn("spec-lane id 99999999 would be lost by the replacement", f)


if __name__ == "__main__":
    unittest.main()
