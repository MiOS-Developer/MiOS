#!/usr/bin/env python3
# AI-hint: Regenerates the recorded in-guest probe outputs (probe-conforming.recorded.json, probe-legacy.recorded.json) from xbox-spec.fixture.json; run after the fixture spec is re-rendered. The legacy recording encodes the documented state of the legacy VM mios-xbox-current (left taskbar TaskbarAl=0, search box shown, Start_Show* folders, no QEMU-GA/virtio/guest tools, no MiOS components, legacy 'user' autologon, RID-500 enabled, no GPU step record, Insider 29683 rs_prerelease).
# AI-related: tests/_mios_xbox_fixtures.py, tests/test-mios-xbox-probe.py, /home/mios/.claude memory mios-xbox-guest-access
"""python3 tests/fixtures/mios-xbox/regen-recorded.py  -> rewrites the two recorded probe outputs."""
import copy
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..")))
import _mios_xbox_fixtures as FX  # noqa: E402

BRANCH = "rs_prerelease"


def main() -> int:
    doc = FX.load_fixture_spec()
    with tempfile.TemporaryDirectory() as t:
        root = FX.fixture_root(t, doc)
        items = FX.S.items_from_spec(doc, root)
    good = FX.conforming_probe(items, BRANCH, root, doc)
    legacy = copy.deepcopy(good)
    obs = legacy["observations"]
    for it in items:
        o = obs[it["id"]]
        k = it["kind"]
        if k == "reg" and it["scope"] == "user":
            name = it["name"].lower()
            if name == "taskbaral":
                obs[it["id"]] = {"present": True, "type": "dword", "value": 0}
            elif name == "searchboxtaskbarmode":
                obs[it["id"]] = {"present": True, "type": "dword", "value": 1}
            elif name in ("taskbarda", "taskbarmn", "showtaskviewbutton", "hideicons"):
                pass  # the legacy generator wrote these
            else:
                obs[it["id"]] = {"present": False}
        elif k == "reg" and it.get("owner", "").startswith(("start.", "debloat.", "settings.")):
            obs[it["id"]] = {"present": False}
        elif k in ("driver", "component"):
            obs[it["id"]] = {"present": False}
        elif k == "guest_tool" and it.get("service"):
            obs[it["id"]] = {"service": {"present": False, "status": None, "start": None}}
        elif k == "capability":
            obs[it["id"]] = {"state": "NotPresent"}
        elif k == "build_family":
            obs[it["id"]] = {"present": True, "sha256": "0" * 64}
        elif k == "user" and it.get("role") == "desktop":
            obs[it["id"]] = {"present": False, "enabled": None, "groups": []}
        elif k == "rid500":
            obs[it["id"]] = {"present": True, "enabled": True}
        elif k == "computer_name":
            obs[it["id"]] = {"value": "MIOS-XBOX"}
        elif k == "appx":
            obs[it["id"]] = {"present": False, "version": None}
        elif k == "gpu_step":
            obs[it["id"]] = {"present": False}
    legacy["auth_user"] = "user"
    legacy["desktop_user"] = {"name": good["desktop_user"]["name"], "sid": None, "hive": "unavailable"}
    legacy["autologon"] = {"auto_admin_logon": "1", "default_user": "user", "logon_count": 1, "password_sha256": None}
    legacy["display"] = [{"device_id": "PCI\\VEN_10DE&DEV_2684&SUBSYS_00000000&REV_A1\\4&1", "name": "NVIDIA GeForce RTX 4090",
                          "provider": "NVIDIA", "version": "32.0.15.8180", "inf": "oem3.inf"}]
    legacy["errors"] = ["no profile for " + good["desktop_user"]["name"]]
    legacy["probed_at"] = "2026-10-15T23:00:00Z"
    for name, data in (("probe-conforming.recorded.json", good), ("probe-legacy.recorded.json", legacy)):
        with open(os.path.join(HERE, name), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"wrote {name} ({len(data['observations'])} observations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
