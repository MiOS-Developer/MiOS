#!/usr/bin/env python3
# AI-hint: Two-sided tests for the MiOS Windows shell in MiOS-Xbox images (xbox-bm-39): resolver, render, payload, the SYSTEM PowerShell 7 install, image gate and probe each refuse or name a planted drift; the profile runs in PowerShell 5.1 and 7.
# AI-related: usr/share/mios/mios.toml, usr/libexec/mios/win/xbox/resolve_latest.py, usr/libexec/mios/win/xbox/fetch.py, usr/libexec/mios/win/xbox/render_spec.py, usr/libexec/mios/win/xbox/answer.py, usr/libexec/mios/win/xbox/spec_items.py, usr/libexec/mios/win/xbox/conform.py, usr/libexec/mios/win/xbox/probe.py, usr/libexec/mios/win/wt_profile_inject.py, usr/share/mios/windows/build/New-MiOSXboxImage.ps1, tests/_mios_xbox_fixtures.py
"""Run: python3 -I tests/test-mios-xbox-shell.py -v   (temp dirs only; no network, no VM; the profile-execution cases run
with Windows PowerShell and/or pwsh and are skipped, named as UNRUN, without them)"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("test_mios_xbox_provisioning_shell", os.path.join(HERE, "test-mios-xbox-provisioning.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)
A, C, R, S, FX = P.A, P.C, P.R, P.S, P.FX
_probe_spec = importlib.util.spec_from_file_location("mios_xbox_probe_shell", os.path.join(P.XBOX, "probe.py"))
PR = importlib.util.module_from_spec(_probe_spec)
_probe_spec.loader.exec_module(PR)
RL = P.RL

REG_REL = A.STATE_DIR.split("/") + ["xbox-registry.reg"]


def _sha(b: bytes) -> str:
    return __import__("hashlib").sha256(b).hexdigest()


def shell_items(items):
    return [it for it in items if it["id"].startswith(("shell.", "reg.shell."))]


def read_reg(path: str) -> str:
    with open(path, "rb") as fh:
        return fh.read()[2:].decode("utf-16-le")


def write_reg(path: str, text: str) -> None:
    with open(path, "wb") as fh:
        fh.write(b"\xff\xfe" + text.encode("utf-16-le"))


class Http:
    """A GitHub releases/latest answer for the resolver; any other URL is an unmocked call."""

    def __init__(self, url: str, rel: dict):
        self.url, self.rel = url, rel

    def request(self, url, method="GET", follow=True, headers=None, limit=None):
        if (method, url) != ("GET", self.url):
            raise AssertionError(f"unmocked upstream call {method} {url}")
        return RL.Response(200, {}, json.dumps(self.rel).encode())


def release(repo: str, tag: str, names, digest=True, draft=False, url=None) -> dict:
    return {"tag_name": tag, "draft": draft, "prerelease": False, "html_url": f"https://github.com/{repo}/releases/{tag}",
            "assets": [{"name": n, "size": 1, **({"digest": "sha256:" + _sha(n.encode())} if digest else {}),
                        "browser_download_url": url or f"https://github.com/{repo}/releases/download/{tag}/{n}"} for n in names]}


class Base(P.PayloadBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.items = S.items_from_spec(cls.doc, P.ROOT)
        cls.shell = shell_items(cls.items)
        cls.sh = cls.doc["shell"]
        cls.files = {f["name"]: f for f in cls.sh["files"]}
        cls.inst_rel = A._win_rel(cls.x["components"]["dest_dir"]) + "/installers"

    def scratch(self, prefix: str = "t.") -> str:
        """A fresh directory under the class scratch, removed when the test ends."""
        d = tempfile.mkdtemp(prefix=prefix, dir=self.tmp)
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def resolved(self, merged):
        x = merged["windows"]["xbox"]
        return x, {p: R.resolve_ref(merged, p, t) for p, t in R._walk_refs(x) if p.startswith("shell.")}


# ---------------------------------------------------------------------------
# resolver: newest release, ONE asset of the glob, GitHub's digest; no pins
# ---------------------------------------------------------------------------

class TestResolver(Base):
    CFG = {"github_api": "https://api.github.com", "pwsh_repo": "PowerShell/PowerShell", "pwsh_asset": "PowerShell-*-win-x64.msi"}
    URL = "https://api.github.com/repos/PowerShell/PowerShell/releases/latest"

    def resolve(self, rel):
        return RL.resolve_github_asset(self.CFG, Http(self.URL, rel), "pwsh", sleep=lambda s: None)

    def test_positive_one_asset_with_its_digest(self):
        names = ["PowerShell-7.99.1-win-x64.msi", "PowerShell-7.99.1-win-arm64.msi", "PowerShell-7.99.1-win-x64.zip"]
        a = self.resolve(release("PowerShell/PowerShell", "v7.99.1", names))
        self.assertEqual((a["asset"], a["version"]), ("PowerShell-7.99.1-win-x64.msi", "7.99.1"))
        self.assertEqual(a["integrity"], {"kind": "sha256", "value": _sha(b"PowerShell-7.99.1-win-x64.msi"),
                                          "source": "GitHub release asset digest"})
        cfg = RL.load_config(self.merged)
        self.assertEqual({t: (cfg[f"{t}_repo"], cfg[f"{t}_asset"]) for t in RL.SHELL_TOOLS},
                         {t: (self.sh["tools"][t]["upstream"].split("github.com/")[1], self.sh["tools"][t]["asset"]) for t in RL.SHELL_TOOLS})

    def test_negative_refuses_what_it_cannot_anchor(self):
        repo, tag, ok = "PowerShell/PowerShell", "v7.99.1", "PowerShell-7.99.1-win-x64.msi"
        cases = {
            "a draft": (release(repo, tag, [ok], draft=True), "draft"),
            "no matching asset": (release(repo, tag, ["PowerShell-7.99.1-win-arm64.msi"]), "expected exactly one"),
            "two matching assets": (release(repo, tag, [ok, "PowerShell-7.99.2-win-x64.msi"]), "expected exactly one"),
            "no sha256 digest": (release(repo, tag, [ok], digest=False), "no sha256 digest"),
            "a foreign download URL": (release(repo, tag, [ok], url="https://example.invalid/x.msi"), "not this repository"),
        }
        for name, (rel, why) in cases.items():
            with self.subTest(case=name), self.assertRaisesRegex(RL.ResolveError, why):
                self.resolve(rel)

    def test_negative_load_config_refuses_pins_and_non_github_upstreams(self):
        cases = {
            "channel pinned": (("pwsh", "channel"), "7.4", "expected 'latest'"),
            "asset names a version": (("fastfetch", "asset"), "fastfetch-2.71.0-windows-amd64.zip", "never a version"),
            "upstream not GitHub": (("oh_my_posh", "upstream"), "https://example.invalid/omp", "missing resolver inputs"),
        }
        for name, ((tool, key), val, why) in cases.items():
            merged = copy.deepcopy(self.merged)
            merged["windows"]["xbox"]["shell"][tool][key] = val
            with self.subTest(case=name), self.assertRaisesRegex(RL.ResolveError, why):
                RL.load_config(merged)


# ---------------------------------------------------------------------------
# render: every shell file from the SSOT, exact bytes; refusals
# ---------------------------------------------------------------------------

class TestRender(Base):
    def shell_tree(self):
        """Only the three tree sources render_shell needs; paths retain the real SSOT conventions."""
        tree = self.scratch("shell-tree.")
        _, res = self.resolved(self.merged)
        sources = [self.files["omp_config"]["source"], self.files["fastfetch_logo"]["source"],
                   "usr/share/mios/" + res["shell.fastfetch_config_ref"]]
        for rel in sources:
            dst = os.path.join(tree, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(P.ROOT, *rel.split("/")), dst)
        return tree

    def test_positive_fhs_and_relative_prompt_sources_render_identically(self):
        tree = self.shell_tree()
        x, res = self.resolved(self.merged)
        self.assertTrue(res["shell.prompt_config_ref"].startswith("/usr/"))
        absolute, _ = R.render_shell(x, res, self.merged, tree)
        res["shell.prompt_config_ref"] = res["shell.prompt_config_ref"].lstrip("/")
        relative, _ = R.render_shell(x, res, self.merged, tree)
        self.assertEqual(absolute, relative)
        self.assertEqual(absolute, self.sh)

    def test_negative_existing_prompt_file_outside_the_tree_is_not_embedded(self):
        sandbox = self.scratch("prompt-boundary.")
        tree = os.path.join(sandbox, "nested", "tree")
        shutil.copytree(self.shell_tree(), tree)
        outside = os.path.join(sandbox, "external-prompt.json")
        with open(outside, "wb") as fh:
            fh.write(b'{"fixture":"external builder file"}')
        x, res = self.resolved(self.merged)
        res["shell.prompt_config_ref"] = "../../external-prompt.json"
        with self.assertRaisesRegex(R.SpecError, "shell.prompt_config_ref"):
            R.render_shell(x, res, self.merged, tree)

    def test_negative_prompt_and_logo_sources_cannot_follow_external_symlinks(self):
        for name, key in (("omp_config", "shell.prompt_config_ref"), ("fastfetch_logo", "shell.fastfetch_config_ref")):
            with self.subTest(source=name):
                tree = self.shell_tree()
                rel = self.files[name]["source"]
                source = os.path.join(tree, *rel.split("/"))
                os.unlink(source)
                try:
                    os.symlink(os.path.join(P.ROOT, *rel.split("/")), source)
                except (OSError, NotImplementedError) as exc:
                    self.skipTest(f"UNRUN: file symlinks unavailable: {type(exc).__name__}")
                x, res = self.resolved(self.merged)
                with self.assertRaisesRegex(R.SpecError, key):
                    R.render_shell(x, res, self.merged, tree)

    def test_positive_terminal_fragment_from_the_ssot(self):
        f = self.files["wt_fragment"]
        self.assertTrue(f["path"].lower().startswith("programdata/microsoft/windows terminal/fragments/"))
        frag = json.loads(f["text"])
        prof, m = frag["profiles"][0], self.merged
        self.assertEqual(prof["name"], m["theme"]["terminal"]["profile_name"])
        self.assertEqual(prof["colorScheme"], m["theme"]["terminal"]["scheme_name"])
        self.assertEqual(prof["font"], {"face": m["theme"]["font"]["family"], "size": m["theme"]["font"]["size"]})
        self.assertEqual((prof["opacity"], prof["useAcrylic"], prof["cursorShape"]),
                         (m["theme"]["opacity"], m["theme"]["acrylic"], m["theme"]["cursor_shape"]))
        self.assertIn(self.sh["tools"]["pwsh"]["exe"].replace("/", "\\"), prof["commandline"])
        self.assertEqual(frag["schemes"], [R._wt().color_scheme(m)])   # the ONE scheme projection, never a copy
        self.assertRegex(prof["guid"], r"^\{[0-9a-f-]{36}\}$")

    def test_positive_profile_hooks_configs_and_policy(self):
        prof = self.files["profile"]
        data = R.shell_file_bytes(prof)
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))   # Windows PowerShell 5.1 reads a BOM-less script as ANSI
        self.assertNotIn("\n", prof["text"].replace("\r\n", ""))
        for v in self.sh["verbs"]:
            self.assertIn(f"    '{v}' = '", prof["text"])
        self.assertEqual(set(self.sh["local_verbs"]), {"dash", "help"})
        hooks = {k: v for k, v in self.files.items() if k.startswith("profile_hook:")}
        self.assertEqual({h["path"].lower() for h in hooks.values()},
                         {"windows/system32/windowspowershell/v1.0/profile.ps1",
                          self.sh["tools"]["pwsh"]["install_dir"].lower() + "/profile.ps1"})
        for h in hooks.values():
            self.assertIn("C:\\" + prof["path"].replace("/", "\\"), h["text"])
        ff = self.files["fastfetch_config"]["text"]
        tf = self.merged["theme"]["fastfetch"]
        self.assertIn(f'"source": "C:/{self.sh["shell_dir"]}/{os.path.basename(self.files["fastfetch_logo"]["path"])}"', ff)
        self.assertIn(f'"1": "{tf["logo_color"]}"', ff)
        self.assertIn(f'"padding": {{ "top": {tf["logo_padding_top"]}, "right": {tf["logo_padding_right"]}, "left": {tf["logo_padding_left"]} }}', ff)
        self.assertNotIn("/usr/share/mios", ff.split('"logo"', 1)[1].split("}", 1)[0])
        reg = [r for r in self.doc["registry"] if r["owner"] == "shell.execution_policy"]
        self.assertEqual([(r["hive"], r["name"], r["data"]) for r in reg], [("SOFTWARE", "ExecutionPolicy", "RemoteSigned")])
        for f in self.sh["files"]:
            self.assertEqual(_sha(R.shell_file_bytes(f)), f["sha256"], f["name"])

    def test_negative_render_shell_refuses_drift(self):
        def xs(path, val):
            return lambda m: _set(m["windows"]["xbox"]["shell"], path, val)

        def _set(d, path, val):
            *head, last = path
            for k in head:
                d = d[k]
            if val is _DEL:
                del d[last]
            else:
                d[last] = val
        cases = {
            "upstream not GitHub": xs(("pwsh", "upstream"), "https://example.invalid/pwsh"),
            "channel pinned": xs(("oh_my_posh", "channel"), "v32"),
            "asset names a version": xs(("fastfetch", "asset"), "fastfetch-2.71.0-windows-amd64.zip"),
            "exe not a plain name": xs(("oh_my_posh", "exe"), "..\\evil.exe"),
            "MSI property injection": xs(("pwsh", "msi_properties"), ["ADD_PATH=1 /i other.msi"]),
            "PowerShell 7 not in the SYSTEM stage": xs(("pwsh", "install_at"), "FirstLogon"),
            "PowerShell 7 without a signer": xs(("pwsh", "signer"), " "),
            "machine-wide Bypass": xs(("execution_policy",), "Bypass"),
            "Unrestricted": xs(("execution_policy",), "Unrestricted"),
            "fragment outside Fragments": xs(("wt_fragment",), "ProgramData\\MiOS\\mios.json"),
            "fragment not json": xs(("wt_fragment",), "ProgramData\\Microsoft\\Windows Terminal\\Fragments\\MiOS\\mios.txt"),
            "only one all-users profile": xs(("all_users_profiles",), ["Windows\\System32\\WindowsPowerShell\\v1.0\\profile.ps1"]),
            "no dash verb": lambda m: m["verbs"].pop("dash"),
            "a verb without a description": lambda m: m["verbs"]["dash"].__setitem__("description", " "),
            "unknown cursor shape": lambda m: m["theme"].__setitem__("cursor_shape", "block"),
            "opacity out of range": lambda m: m["theme"].__setitem__("opacity", 150),
            "fastfetch colour not a name": lambda m: m["theme"]["fastfetch"].__setitem__("logo_color", "#123456"),
            "fastfetch padding negative": lambda m: m["theme"]["fastfetch"].__setitem__("logo_padding_top", -1),
            "prompt source traversal": lambda m: m["theme"]["prompt"].__setitem__("omp_path", "../../outside.json"),
            "prompt source drive": lambda m: m["theme"]["prompt"].__setitem__("omp_path", r"C:\outside.json"),
            "prompt source UNC": lambda m: m["theme"]["prompt"].__setitem__("omp_path", r"\\host\share\outside.json"),
            "fastfetch config traversal": lambda m: m["branding"].__setitem__("fastfetch_config", "../../outside.jsonc"),
        }
        for name, plant in cases.items():
            merged = copy.deepcopy(self.merged)
            plant(merged)
            x, res = self.resolved(merged)
            with self.subTest(case=name), self.assertRaises(R.SpecError):
                R.render_shell(x, res, merged)

    def test_negative_render_refuses_a_bypass_policy_end_to_end(self):
        merged = copy.deepcopy(self.merged)
        merged["windows"]["xbox"]["shell"]["execution_policy"] = "Bypass"
        with self.assertRaises(R.SpecError):
            R.render(merged, P.LAYERS)


class _Del:
    pass


_DEL = _Del()


# ---------------------------------------------------------------------------
# payload + SYSTEM stage
# ---------------------------------------------------------------------------

class TestPayload(Base):
    def stage(self, doc, inputs):
        return A.stage_shell(doc, inputs, self.scratch("shell."), P.ROOT)

    def test_positive_payload_carries_every_shell_file(self):
        rows = {r["name"]: r for r in self.summary["shell"]}
        want = {f"shell:{f['name']}" for f in self.sh["files"]} | {"shell:oh_my_posh", "shell:fastfetch/fastfetch.exe",
                                                                     "shell:fastfetch/presets/examples/1.jsonc"}
        self.assertEqual(set(rows), want)
        for r in rows.values():
            with open(os.path.join(self.pay, *r["path"].split("/")), "rb") as fh:
                self.assertEqual(_sha(fh.read()), r["sha256"], r["name"])
        with open(os.path.join(self.pay, *A.STATE_DIR.split("/"), "payload.json")) as fh:
            self.assertEqual(json.load(fh)["shell"], self.summary["shell"])
        self.assertEqual(rows["shell:fastfetch/fastfetch.exe"]["path"], self.sh["tools"]["fastfetch"]["path"])
        self.assertEqual(rows["shell:oh_my_posh"]["path"], self.sh["tools"]["oh_my_posh"]["path"])

    def test_positive_system_stage_installs_powershell_7(self):
        body = self.step("shell_pwsh")
        ins = self.inputs["installers"]["pwsh"]
        name = os.path.basename(ins["path"])
        self.assertIn(f"Join-Path $Inst '{name}'", body)
        self.assertIn(f"Assert-MiOSSha256 $f '{ins['sha256']}'", body)
        self.assertIn(f"Assert-MiOSSigned $f '{ins['signer']}'", body)
        props = ", ".join(f"'{p}'" for p in self.x["shell"]["pwsh"]["msi_properties"])
        self.assertIn(f"Invoke-MiOSMsi $f 'pwsh' @({props})", body)
        self.assertIn(self.sh["tools"]["pwsh"]["exe"].replace("/", "\\"), body)
        self.assertTrue(os.path.isfile(os.path.join(self.pay, *self.inst_rel.split("/"), name)))

    def test_negative_shell_file_destination_cannot_escape_the_payload(self):
        sandbox = self.scratch("shell-boundary.")
        out = os.path.join(sandbox, "nested", "payload")
        victim = os.path.join(sandbox, "outside.json")
        os.makedirs(out)
        with open(victim, "wb") as fh:
            fh.write(b"external fixture must survive")
        doc = copy.deepcopy(self.doc)
        doc["shell"]["files"][0]["path"] = "../../outside.json"
        with self.assertRaises(R.SpecError):
            A.stage_shell(doc, self.inputs, out, P.ROOT)
        with open(victim, "rb") as fh:
            self.assertEqual(fh.read(), b"external fixture must survive")

    def test_negative_payload_rechecks_tree_source_boundaries_after_render(self):
        tree = self.scratch("shell-source.")
        external = os.path.join(os.path.dirname(tree), "external-shell.json")
        with open(external, "wb") as fh:
            fh.write(b'{"fixture":"external builder file"}')
        self.addCleanup(os.unlink, external)
        doc = copy.deepcopy(self.doc)
        entry = next(f for f in doc["shell"]["files"] if f["name"] == "omp_config")
        entry.update(source="../external-shell.json", sha256=A._sha256(external))
        with self.assertRaisesRegex(A.PayloadError, "shell file omp_config"):
            A.stage_shell(doc, self.inputs, self.scratch("shell-output."), tree)

    def test_negative_tool_destination_cannot_follow_an_external_symlink(self):
        doc = copy.deepcopy(self.doc)
        out, outside = self.scratch("shell-payload."), self.scratch("shell-external.")
        try:
            os.symlink(outside, os.path.join(out, "tool-link"), target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"UNRUN: directory symlinks unavailable: {type(exc).__name__}")
        doc["shell"]["tools"]["oh_my_posh"]["path"] = "tool-link/oh-my-posh.exe"
        with self.assertRaisesRegex(R.SpecError, "outside"):
            A.stage_shell(doc, self.inputs, out, P.ROOT)
        self.assertEqual(os.listdir(outside), [])

    def test_negative_stage_refuses_tampered_or_unverified_inputs(self):
        def tampered_text(d, i):
            d["shell"]["files"][0]["text"] += " "

        def bad_zip(member):
            p = os.path.join(self.scratch("zip."), "fastfetch-windows-amd64.zip")
            with zipfile.ZipFile(p, "w") as z:
                z.writestr(member, b"x")
            inputs = copy.deepcopy(self.inputs)
            inputs["shell"]["fastfetch"].update(path=p, sha256=A._sha256(p))
            return inputs

        cases = {
            "spec text tampered": (lambda: (lambda d: (tampered_text(d, 0), d)[1])(copy.deepcopy(self.doc)), lambda: self.inputs,
                                   "bytes differ"),
            "tool not fetched": (lambda: self.doc, lambda: {**self.inputs, "shell": {}}, "not fetched"),
            "tool changed since the fetch": (lambda: self.doc, lambda: {**self.inputs, "shell": {
                **self.inputs["shell"], "oh_my_posh": {**self.inputs["shell"]["oh_my_posh"], "sha256": "0" * 64}}}, "differs from inputs.json"),
            "archive member escapes": (lambda: self.doc, lambda: bad_zip("../evil.exe"), "unsafe archive member"),
            "archive member absolute": (lambda: self.doc, lambda: bad_zip("C:/evil.exe"), "unsafe archive member"),
            "archive without fastfetch.exe": (lambda: self.doc, lambda: bad_zip("flashfetch.exe"), "has no fastfetch.exe"),
        }
        for name, (doc, inputs, why) in cases.items():
            with self.subTest(case=name), self.assertRaisesRegex(A.PayloadError, why):
                self.stage(doc(), inputs())

    def test_negative_installer_must_match_the_glob_and_carry_a_signer(self):
        inputs = copy.deepcopy(self.inputs)
        other = os.path.join(self.tmp, "PowerShell-7.99.0-win-arm64.msi")
        shutil.copyfile(inputs["installers"]["pwsh"]["path"], other)
        inputs["installers"]["pwsh"]["path"] = other
        with self.assertRaisesRegex(A.PayloadError, "does not match shell.pwsh.asset"):
            A.installer_names(self.doc, inputs)
        inputs = copy.deepcopy(self.inputs)
        inputs["installers"]["pwsh"]["signer"] = ""
        with self.assertRaisesRegex(A.PayloadError, "no Authenticode signer"):
            A.render_setup_script(self.doc, inputs, P.ROOT)


# ---------------------------------------------------------------------------
# image gate (conform, rendered inputs) and in-guest probe
# ---------------------------------------------------------------------------

class TestImageGate(Base):
    def ids(self, pay):
        obs = C.collect_rendered([pay, self.inputs["drivers_dir"]])
        _, findings = C.evaluate(self.shell, obs, "rendered", S.spec_policy(self.doc))
        return {f.id for f in findings if "shell" in f.id}

    def test_positive_conforming_payload(self):
        self.assertEqual(self.ids(self.pay), set())

    def test_negative_missing_or_altered_shell_is_named(self):
        name = os.path.basename(self.inputs["installers"]["pwsh"]["path"])
        setup = os.path.join("Windows", "Setup", "Scripts", A.SETUP_SCRIPT)

        def path(d, rel):
            return os.path.join(d, *rel.split("/"))

        def edit(d, rel, fn, enc="utf-8"):
            p = path(d, rel.replace("\\", "/"))
            with open(p, encoding=enc) as fh:
                t = fh.read()
            with open(p, "w", encoding=enc, newline="") as fh:
                fh.write(fn(t))

        def append(p):
            with open(p, "ab") as fh:
                fh.write(b" ")

        def reg(d, fn):
            p = os.path.join(d, *REG_REL)
            write_reg(p, fn(read_reg(p)))
        cases = {
            "fragment altered": (lambda d: append(path(d, self.files["wt_fragment"]["path"])), "off-spec:shell.wt_fragment"),
            "profile removed": (lambda d: os.remove(path(d, self.files["profile"]["path"])), "missing:shell.profile"),
            "pwsh hook removed": (lambda d: os.remove(path(d, self.files["profile_hook:pwsh"]["path"])), "missing:shell.profile_hook:pwsh"),
            "oh-my-posh removed": (lambda d: os.remove(path(d, self.sh["tools"]["oh_my_posh"]["path"])), "missing:shell.oh_my_posh"),
            "fastfetch removed": (lambda d: os.remove(path(d, self.sh["tools"]["fastfetch"]["path"])), "missing:shell.fastfetch"),
            "PowerShell installer removed": (lambda d: os.remove(path(d, f"{self.inst_rel}/{name}")), "missing:shell.pwsh"),
            "PowerShell installer never run": (lambda d: edit(d, setup, lambda t: t.replace(name, "nothing.msi")), "missing:shell.pwsh"),
            "execution policy off-spec": (lambda d: reg(d, lambda t: t.replace('"ExecutionPolicy"="RemoteSigned"', '"ExecutionPolicy"="Bypass"')),
                                          "off-spec:reg.shell.execution_policy"),
        }
        for case, (plant, want) in cases.items():
            d = self.copy_payload()
            plant(d)
            with self.subTest(case=case):
                self.assertIn(want, self.ids(d))


class TestProbe(Base):
    def probe(self):
        users = [it for it in self.items if it["kind"] == "user"]
        return FX.conforming_probe(self.shell + users, "", P.ROOT, self.doc)

    def ev(self, doc):
        results, findings = PR.evaluate(self.shell, doc)
        return {r["id"]: r for r in results}, {f.id for f in findings}

    def test_positive_conforming_guest(self):
        res, ids = self.ev(self.probe())
        self.assertEqual(ids, set())
        self.assertEqual(res["shell.pwsh"]["status"], "pass")

    def test_negative_guest_drift_is_named(self):
        cases = {
            "pwsh.exe absent": ("shell.pwsh", {"present": False}, "missing:shell.pwsh"),
            "profile altered": ("shell.profile", {"present": True, "sha256": "0" * 64}, "off-spec:shell.profile"),
            "oh-my-posh absent": ("shell.oh_my_posh", {"present": False}, "missing:shell.oh_my_posh"),
            "policy Restricted": ("reg.shell.execution_policy", {"present": True, "type": "sz", "value": "Restricted"},
                                  "off-spec:reg.shell.execution_policy"),
        }
        for name, (iid, obs, want) in cases.items():
            doc = self.probe()
            doc["observations"][iid] = obs
            with self.subTest(case=name):
                self.assertIn(want, self.ev(doc)[1])


# ---------------------------------------------------------------------------
# the rendered profile, EXECUTED in Windows PowerShell 5.1 and PowerShell 7
# ---------------------------------------------------------------------------

class TestProfileRuns(Base):
    def hosts(self):
        out = []
        if os.name == "nt":
            ps51 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
            if os.path.isfile(ps51):
                out.append(("5.1", ps51))
        pw = shutil.which("pwsh")
        if pw:
            out.append(("7", pw))
        return out

    def test_profile_defines_the_verbs_both_ways(self):
        hosts = self.hosts()
        if not hosts:
            self.skipTest("UNRUN: no Windows PowerShell / pwsh on this host")
        prof = os.path.join(self.tmp, "mios-profile.ps1")
        with open(prof, "wb") as fh:
            fh.write(R.shell_file_bytes(self.files["profile"]))
        cmd = (f". '{prof}'; 'PARSED'; mios help; mios build; "
               "try { mios no-such-verb -ErrorAction Stop } catch { 'REFUSED: ' + $_.Exception.Message }")
        for label, exe in hosts:
            with self.subTest(host=label):
                out = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                                     capture_output=True, text=True, timeout=120)
                self.assertEqual(out.returncode, 0, out.stderr[-1500:])
                lines = out.stdout.splitlines()
                self.assertIn("PARSED", lines)
                for v in self.sh["verbs"]:
                    self.assertTrue(any(re.match(rf"^{re.escape(v)}\s+\S", ln) for ln in lines), f"{label}: mios help lacks {v}")
                self.assertTrue(any(ln.startswith("mios build: needs the MiOS host layer") for ln in lines), out.stdout[-800:])
                self.assertTrue(any(ln.startswith("REFUSED: mios: unknown verb 'no-such-verb'") for ln in lines), out.stdout[-800:])


if __name__ == "__main__":
    unittest.main()
