#!/usr/bin/env python3
# AI-hint: Sibling test for tools/drift-checks.py; asserts each extracted check is importable, dispatchable and agrees with the shell gate.
# AI-related: tools/drift-checks.py, automation/98-drift-checks.sh
"""These three checks used to be heredocs, where a syntax error surfaced only
when the check ran and nothing could lint them. The point of the extraction is
that they are now reachable from a test, so this asserts exactly that.
"""
import ast
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_MOD_PATH = os.path.join(_HERE, "drift-checks.py")

def _load():
    spec = importlib.util.spec_from_file_location("drift_checks", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

MOD = _load()

class TestExtractedChecks(unittest.TestCase):
    def test_the_module_imports(self):
        """A heredoc could not be imported at all; that was the defect."""
        self.assertTrue(callable(MOD.check_resolver_differential_parity))

    def test_every_subcommand_maps_to_a_callable(self):
        # Not a fixed count: the module grows as more checks leave their
        # heredocs, and asserting the number only breaks the test when the
        # extraction it exists to encourage actually happens.
        self.assertGreaterEqual(len(MOD.SUBCOMMANDS), 3)
        for name, fn in MOD.SUBCOMMANDS.items():
            self.assertTrue(callable(fn), name)
            self.assertNotIn("_", name, "subcommands are hyphenated: %s" % name)

    def test_an_unknown_subcommand_exits_two(self):
        r = subprocess.run([sys.executable, _MOD_PATH, "no-such-check"],
                           capture_output=True, text=True, cwd=_ROOT)
        self.assertEqual(2, r.returncode)

    def test_no_subcommand_exits_two(self):
        r = subprocess.run([sys.executable, _MOD_PATH],
                           capture_output=True, text=True, cwd=_ROOT)
        self.assertEqual(2, r.returncode)

    def test_each_check_runs_against_the_shipped_tree(self):
        env = dict(os.environ, MIOS_DRIFT_ROOT=_ROOT, MIOS_ROOT=_ROOT)
        for name in MOD.SUBCOMMANDS:
            r = subprocess.run([sys.executable, _MOD_PATH, name],
                               capture_output=True, text=True, cwd=_ROOT, env=env)
            if r.returncode == 0:
                continue
            # A check whose input tool cannot execute on THIS host must still
            # report that as a violation rather than crash. Asserting a bare 0
            # made the test depend on the host: mios-env-snapshot's shebang
            # does not resolve on Windows, so the check correctly reports a
            # missing input there while passing on Linux.
            # Non-zero has two legitimate causes and one illegitimate one.
            # Legitimate: the check found a real violation, or its input tool
            # cannot execute on THIS host (mios-env-snapshot's shebang does not
            # resolve on Windows) -- both are the check REPORTING. Illegitimate:
            # it crashed, or it exited non-zero saying nothing at all, which is
            # indistinguishable from a pass to anyone reading the log.
            #
            # Requiring a specific phrase here was wrong: it made a check that
            # correctly reported a real legibility violation look like a broken
            # check, because the violation text does not say "missing".
            out = (r.stdout or "") + (r.stderr or "")
            self.assertNotIn("Traceback", out,
                             "%s crashed instead of reporting" % name)
            self.assertTrue(
                out.strip(),
                "%s exited %d silently -- a non-zero exit with no diagnostic "
                "cannot be acted on" % (name, r.returncode))

    def test_the_shell_gate_calls_the_module_not_a_heredoc(self):
        with open(os.path.join(_ROOT, "automation/98-drift-checks.sh"), encoding="utf-8", errors="replace") as fh:
            gate = fh.read()
        for name in MOD.SUBCOMMANDS:
            self.assertIn("tools/drift-checks.py %s" % name, gate,
                          "check_%s no longer dispatches to the module"
                          % name.replace("-", "_"))


# A checkout that never had a file is a skip; a TRACKED file that has gone
# missing is the gate's own subject disappearing, and nineteen checks answered
# that with a silent 0.
class TestMissingDeliverable(unittest.TestCase):
    def _repo(self, rel, track=True):
        d = tempfile.mkdtemp(prefix="absent-")
        self.addCleanup(shutil.rmtree, d, True)
        full = os.path.join(d, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write("x\n")
        subprocess.run(["git", "-C", d, "init", "-q"], check=True)
        if track:
            subprocess.run(["git", "-C", d, "add", "-A"], check=True,
                           capture_output=True)
        return d, full

    def test_a_present_file_is_not_a_verdict(self):
        d, full = self._repo("usr/share/mios/mios.toml")
        self.assertIsNone(MOD._absent(d, full))

    def test_a_tracked_file_that_went_missing_fails(self):
        d, full = self._repo("usr/share/mios/mios.toml")
        os.remove(full)
        self.assertEqual(1, MOD._absent(d, full))

    def test_an_untracked_missing_file_still_skips(self):
        d, full = self._repo("usr/share/mios/mios.toml", track=False)
        os.remove(full)
        self.assertEqual(0, MOD._absent(d, full))

    def test_a_root_that_is_not_a_checkout_still_skips(self):
        """Fixture roots are bare temp directories, not repositories."""
        d = tempfile.mkdtemp(prefix="absent-plain-")
        self.addCleanup(shutil.rmtree, d, True)
        self.assertEqual(0, MOD._absent(d, os.path.join(d, "nothing/here.toml")))

    def test_no_check_still_answers_a_missing_subject_with_a_bare_zero(self):
        with open(_MOD_PATH, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        offenders, seen = [], 0
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef) or not fn.name.startswith("check_"):
                continue
            seen += 1
            for node in ast.walk(fn):
                if not isinstance(node, ast.If):
                    continue
                # Any presence test, not just `not isfile(x)`: the multi-subject
                # forms `not A or not B` and `not (A and B)` hid three of these.
                if not any(isinstance(n, ast.Attribute)
                           and n.attr in ("isfile", "isdir", "exists")
                           for n in ast.walk(node.test)):
                    continue
                if len(node.body) != 1:
                    continue
                s = node.body[0]
                bare = (isinstance(s, ast.Return) and isinstance(s.value, ast.Constant)
                        and type(s.value.value) is int and s.value.value == 0)
                if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call):
                    f = s.value.func
                    if isinstance(f, ast.Attribute) and f.attr == "exit" and s.value.args:
                        a = s.value.args[0]
                        bare = (isinstance(a, ast.Constant)
                                and type(a.value) is int and a.value == 0)
                if bare:
                    offenders.append("%s:%d" % (fn.name, node.lineno))
        self.assertGreater(seen, 50, "the module did not parse into checks")
        self.assertEqual([], offenders,
                         "route these through _absent(root, path)")

    def test_no_check_answers_a_failed_import_of_a_repo_module_with_success(self):
        """The sibling shape the isfile guard above cannot see.

        `try: import mios_X / except: return 0` reads as a dependency guard, but
        every mios_* module here is a tracked deliverable importing stdlib only,
        so the only way the import fails is the subject going missing. 4ca3d35
        converted three of these and left check_docs_ratchet's mios_comments.
        """
        repo_mods = set()
        for dirpath, dirnames, filenames in os.walk(_ROOT):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
            for name in filenames:
                if name.endswith(".py"):
                    repo_mods.add(name[:-3])
        stdlib = set(sys.stdlib_module_names)

        with open(_MOD_PATH, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        offenders, handlers = [], 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            imported = []
            for sub in ast.walk(node):
                if isinstance(sub, ast.Import):
                    imported += [a.name.split(".")[0] for a in sub.names]
                elif isinstance(sub, ast.ImportFrom) and sub.module:
                    imported.append(sub.module.split(".")[0])
            if not imported:
                continue
            for h in node.handlers:
                handlers += 1
                answered_ok = False
                for sub in ast.walk(h):
                    if (isinstance(sub, ast.Return) and isinstance(sub.value, ast.Constant)
                            and type(sub.value.value) is int and sub.value.value == 0):
                        answered_ok = True
                if not answered_ok:
                    continue
                hit = sorted({m for m in imported
                              if m in repo_mods and m not in stdlib})
                if hit:
                    offenders.append("line %d imports %s" % (node.lineno, ",".join(hit)))
        self.assertGreater(handlers, 0, "no import guard was examined at all")
        self.assertGreater(len(repo_mods), 100, "the repo module index is empty")
        self.assertEqual([], offenders,
                         "a tracked module that will not import is a dropped "
                         "subject -- gate it with _absent(root, path) and fail")


# A check whose corpus is `git ls-files` answers a refusing git with an empty
# list, and every scan of an empty list is clean. _absent covers one named
# file; _tracked covers the listing the walk is built from.
_CORPUS_CHECKS = ("usr-over-etc", "legibility-ratchet", "bake-refs-parity",
                  "no-inert-ssot-tables")

@unittest.skipUnless(os.name == "posix", "the refusing-git shim is a shell script")
class TestUnlistableCorpus(unittest.TestCase):
    def _refusing_git(self):
        d = tempfile.mkdtemp(prefix="nogit-")
        self.addCleanup(shutil.rmtree, d, True)
        shim = os.path.join(d, "git")
        with open(shim, "w") as fh:
            fh.write('#!/bin/sh\necho "fatal: dubious ownership" >&2\nexit 128\n')
        os.chmod(shim, 0o755)
        return d

    def _repo(self, empty=False):
        d = tempfile.mkdtemp(prefix="corpus-")
        self.addCleanup(shutil.rmtree, d, True)
        subprocess.run(["git", "-C", d, "init", "-q"], check=True)
        if not empty:
            with open(os.path.join(d, "kept.txt"), "w") as fh:
                fh.write("x\n")
            subprocess.run(["git", "-C", d, "add", "-A"], check=True,
                           capture_output=True)
        return d

    def test_a_listed_corpus_is_not_a_verdict(self):
        paths, rc = MOD._tracked(self._repo())
        self.assertIsNone(rc)
        self.assertIn("kept.txt", paths)

    def test_a_root_that_is_not_a_checkout_still_skips(self):
        d = tempfile.mkdtemp(prefix="corpus-plain-")
        self.addCleanup(shutil.rmtree, d, True)
        self.assertEqual((None, 0), MOD._tracked(d))

    def test_a_checkout_git_refuses_to_list_fails(self):
        d = self._repo()
        old = os.environ["PATH"]
        os.environ["PATH"] = self._refusing_git() + os.pathsep + old
        self.addCleanup(os.environ.__setitem__, "PATH", old)
        self.assertEqual((None, 1), MOD._tracked(d))

    def test_a_checkout_with_nothing_tracked_fails(self):
        """An empty index is not "no violations" -- nothing was read."""
        self.assertEqual((None, 1), MOD._tracked(self._repo(empty=True)))

    def test_no_corpus_check_reports_success_without_a_corpus(self):
        """The before/after control: all three exited 0 here pre-repair."""
        env = dict(os.environ, MIOS_DRIFT_ROOT=_ROOT, MIOS_ROOT=_ROOT)
        env["PATH"] = self._refusing_git() + os.pathsep + env["PATH"]
        for name in _CORPUS_CHECKS:
            r = subprocess.run([sys.executable, _MOD_PATH, name],
                               capture_output=True, text=True, cwd=_ROOT, env=env)
            out = (r.stdout or "") + (r.stderr or "")
            self.assertNotEqual(
                0, r.returncode,
                "%s reported success on a corpus git never gave it" % name)
            self.assertTrue(out.strip(), "%s failed silently" % name)

class TestBoundImageStore(unittest.TestCase):
    """Exercise the source tree and baked binding directory as separate states."""

    STORE = "/usr/lib/bootc/storage"

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="bound-store-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.write("usr/share/mios/mios.toml", '[build.bake]\n'
                   f'additional_image_store = "{self.STORE}"\n'
                   'firstboot_tokens = ["floating"]\n')
        self.unit = self.write("usr/share/containers/systemd/core.container",
                               self.container("example/core:stable", self.STORE))
        self.write("usr/lib/bootc/bound-images.d/.gitkeep", "")

    def write(self, relative, content):
        path = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    def container(self, image, store=None):
        return "[Container]\nImage=" + image + "\n" + (
            f"GlobalArgs=--storage-opt=additionalimagestore={store}\n" if store else "")

    def check(self, expected, message=""):
        result = subprocess.run([sys.executable, _MOD_PATH, "bound-image-store"],
                                env=dict(os.environ, MIOS_DRIFT_ROOT=self.root),
                                capture_output=True, text=True)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        if message:
            self.assertIn(message, result.stderr)

    def test_source_and_commented_global_store_pass(self):
        self.write("etc/containers/storage.conf", '[storage.options]\n'
                   f'# additionalimagestores = ["{self.STORE}"]\n')
        self.check(0)

    def test_missing_ssot_fails(self):
        os.unlink(os.path.join(self.root, "usr/share/mios/mios.toml"))
        self.check(1, "SSOT or generated Quadlet directory is missing")

    def test_missing_unit_store_fails(self):
        self.write("usr/share/containers/systemd/core.container", self.container("example/core:stable"))
        self.check(1, "core.container: expected one additionalimagestore")

    def test_firstboot_store_fails(self):
        self.write("usr/share/containers/systemd/float.container", self.container("example/floating", self.STORE))
        self.check(1, "float.container: firstboot or user-scope")

    def test_user_store_fails(self):
        self.write("usr/share/containers/systemd/users/user.container", self.container("example/user", self.STORE))
        self.check(1, "user.container: firstboot or user-scope")

    def test_empty_baked_directory_fails(self):
        os.unlink(os.path.join(self.root, "usr/lib/bootc/bound-images.d/.gitkeep"))
        self.check(1, "core.container: missing bound Quadlet symlink")

    def test_global_store_fails(self):
        self.write("etc/containers/storage.conf", '[storage.options]\n'
                   f'additionalimagestores = ["{self.STORE}"]\n')
        self.check(1, "bootc store must not be enabled globally")

    def test_host_override_takes_precedence(self):
        self.write("usr/share/containers/systemd/core.container", self.container("example/core"))
        self.write("etc/containers/systemd/core.container", self.container("example/core", self.STORE))
        self.check(0)

    def test_complete_binding_and_wrong_target(self):
        marker = os.path.join(self.root, "usr/lib/bootc/bound-images.d/.gitkeep")
        link = os.path.join(os.path.dirname(marker), "core.container")
        try:
            os.symlink(self.unit, link)
        except OSError as exc:
            self.skipTest(f"host cannot create symlinks: {exc}")
        os.unlink(marker)
        self.check(0)
        os.unlink(link)
        other = self.write("other.container", self.container("example/other", self.STORE))
        os.symlink(other, link)
        self.check(1, "symlink targets wrong Quadlet")


# The bound-image-store projection left Python with tools/generate-pod-quadlets.py
# (f22b85ff, ported to `mios-gen pod-quadlets`). Its six cases now run beside the
# implementation as the bound_store_* tests in tools/native/mios-gen/src/pod_quadlets.rs.


class TestMonitorRegistry(unittest.TestCase):
    """Exercise monitor collectors without importing or launching the UI."""
    def setUp(self):
        import json
        import platform
        import socket
        from unittest.mock import patch
        self.patch = patch
        library = os.path.join(_ROOT, "usr", "lib", "mios")
        sys.path.insert(0, library)
        self.addCleanup(lambda: sys.path.remove(library))
        import mios_toml
        path = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-mon.py")
        with open(path, encoding="utf-8") as source:
            tree = ast.parse(source.read())
        names = {"monitor_config", "monitor_sources_config", "check_port", "engine_online", "get_services", "load_ssot_colors", "running_wsl_distros"}
        nodes = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual(names, {node.name for node in nodes})
        self.ns = {"os": os, "glob": __import__("glob"), "subprocess": subprocess, "json": json,
                   "socket": socket, "platform": platform, "IS_WINDOWS": False, "__file__": path,
                   "layer_paths": mios_toml.layer_paths, "load_merged": mios_toml.load_merged,
                   "process_val": mios_toml.process_val, "mios_colors": mios_toml.colors,
                   "running_wsl_distros": lambda: set()}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), self.ns)

    def test_registry_uses_layered_categories_and_shared_offset_rules(self):
        import mios_toml
        with tempfile.TemporaryDirectory() as directory:
            paths = [os.path.join(directory, name) for name in ("vendor.toml", "host.toml", "user.toml")]
            for path, text in zip(paths, ('[ports]\nstack_id = 1\nllm_light = 1\nadguard_dns = 53\n[ports.categories.ai]\nbase = 32000\nstride = 10\nmembers = ["llm_light"]\n', '[ports.categories.ai]\nbase = 33000\n', '[ports.categories.ai]\nbase = 34000\n')):
                with open(path, "w", encoding="utf-8") as output:
                    output.write(text)
            data = mios_toml.load_merged(layers=paths)
        seen = []
        self.ns.update(monitor_config=lambda: data, check_port=lambda host, port: seen.append(port) or port == 44000,
                       engine_online=lambda: False)
        services = {name: (port, state) for name, port, state in self.ns["get_services"]()}
        self.assertEqual((44000, True), services["llm_light"])
        self.assertEqual((53, False), services["adguard_dns"])
        self.assertEqual([44000, 53], seen)
        self.assertNotIn("categories", services)
        self.assertFalse(services["podman-machine"][1])

    def test_missing_ports_or_windows_host_do_not_fabricate_health(self):
        self.ns.update(IS_WINDOWS=True, monitor_config=lambda: {}, engine_online=lambda: False, running_wsl_distros=lambda: set())
        self.assertEqual([("wsl-engine", 0, False), ("podman-machine", 0, False)], self.ns["get_services"]())

    def test_wsl_listing_requires_a_successful_response(self):
        for code, output, expected in ((0, "MiOS\n".encode("utf-16-le"), {"MiOS"}),
                                       (0, b"MiOS\n", {"MiOS"}), (1, b"ERROR: listing failed", set())):
            with self.subTest(code=code, output=output), self.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], code, output)):
                self.assertEqual(expected, self.ns["running_wsl_distros"]())

    def test_windows_fragments_preserve_vendor_host_user_precedence(self):
        with tempfile.TemporaryDirectory() as root:
            relative = ("usr/share/mios/mios.toml", "usr/lib/mios/mios.d/10-theme.toml",
                        "etc/mios/mios.toml", "etc/mios/mios.d/10-theme.toml",
                        "user/mios.toml", "user/mios.d/10-theme.toml")
            for index, name in enumerate(relative):
                path = os.path.join(root, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as output:
                    output.write(f'[colors]\nbg = "#{index:06x}"\n')
            self.ns.update(IS_WINDOWS=True, __file__=os.path.join(root, "usr/libexec/mios/mios-mon.py"),
                           layer_paths=lambda: [os.path.join(root, relative[0]), "/etc/mios/mios.toml", os.path.join(root, relative[4])])
            self.assertEqual("#000005", self.ns["monitor_config"]()["colors"]["bg"])

    def test_engine_requires_successful_host_json(self):
        for code, value, expected in ((0, '{"host":{"arch":"amd64"}}', True), (1, '{"host":{"arch":"amd64"}}', False),
                                      (0, '{}', False), (0, '[]', False), (0, 'invalid', False)):
            with self.subTest(code=code, value=value), self.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], code, value)):
                self.assertEqual(expected, self.ns["engine_online"]())
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("podman", 2)):
            with self.patch.object(subprocess, "run", side_effect=error):
                self.assertFalse(self.ns["engine_online"]())

    def test_disabled_invalid_and_closed_ports_are_offline(self):
        from unittest.mock import Mock
        probe = Mock(side_effect=OSError("closed"))
        with self.patch.object(self.ns["socket"], "create_connection", probe):
            for port in (None, 0, -1, True, "42", 65536):
                self.assertFalse(self.ns["check_port"]("127.0.0.1", port))
            probe.assert_not_called()
            self.assertFalse(self.ns["check_port"]("127.0.0.1", 44000))
            self.assertEqual(2, probe.call_count)

    def test_palette_and_transparency_read_the_same_overlay(self):
        self.ns.update(IS_WINDOWS=True, monitor_config=lambda: {"colors":{"bg":"#102030", "surface":"#203040"}, "theme":{"acrylic":True, "opacity":75}})
        palette, transparent = self.ns["load_ssot_colors"]()
        self.assertEqual("#102030", palette["bg"])
        self.assertEqual("#203040", palette["surface"])
        self.assertTrue(transparent)
        # Transparency follows the SSOT on every platform, as the tmux theme's
        # does (mios-service-core tmux_theme.rs): the monitor runs under WSL inside
        # Windows Terminal, where IS_WINDOWS is False and the acrylic is real.
        self.ns["IS_WINDOWS"] = False
        self.assertTrue(self.ns["load_ssot_colors"]()[1])
        for theme in ({"acrylic": False, "opacity": 75}, {"acrylic": True, "opacity": 100}, {}):
            self.ns.update(monitor_config=lambda theme=theme: {"colors": {"bg": "#102030"}, "theme": theme})
            self.assertFalse(self.ns["load_ssot_colors"]()[1], theme)


class TestValueAliasRegistry(unittest.TestCase):
    """value-aliases.tsv vouches for names the resolver emits.

    A row whose names were not emitted used to be skipped as informational.
    A lost table header stranded fifteen [pgvector] keys under [offline].
    Fourteen had rows here, every one was skipped, and each consumer quietly
    took its inline default; the fifteenth, rls_enable, had no row at all.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-value-aliases-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _run(self, emitted, rows):
        snap = os.path.join(self.tmp, "snapshot.sh")
        with open(snap, "w", encoding="utf-8") as fh:
            fh.write("#!/usr/bin/env bash\n")
            for k, v in emitted.items():
                fh.write("printf '%%s\\n' '%s=%s'\n" % (k, v))
        tsv = os.path.join(self.tmp, "value-aliases.tsv")
        with open(tsv, "w", encoding="utf-8") as fh:
            fh.write("# canonical\talias\tdisposition\n")
            for row in rows:
                fh.write("\t".join(row) + "\n")
        env = dict(os.environ, MIOS_DRIFT_ROOT=self.tmp)
        return subprocess.run([sys.executable, _MOD_PATH, "value-aliases", snap, tsv],
                              capture_output=True, text=True, cwd=_ROOT, env=env)

    def test_an_emitted_derive_pair_with_equal_values_passes(self):
        r = self._run({"T_A": "1", "T_B": "1"},
                      [("T_A", "T_B", "derive")])
        self.assertEqual(0, r.returncode, r.stderr)

    def test_a_divergent_derive_pair_fails(self):
        r = self._run({"T_A": "1", "T_B": "2"},
                      [("T_A", "T_B", "derive")])
        self.assertEqual(1, r.returncode)
        self.assertIn("MUST be equal", r.stderr)

    def test_an_equal_keep_distinct_pair_fails(self):
        r = self._run({"T_A": "1", "T_B": "1"},
                      [("T_A", "T_B", "keep-distinct")])
        self.assertEqual(1, r.returncode)
        self.assertIn("keep-distinct", r.stderr)

    def test_a_stranded_family_fails_naming_both_variables(self):
        # The lost-header shape: the key now parses under another table, so
        # the resolver emits neither spelling the registry vouches for.
        r = self._run({"OFFLINE_HNSW_ITERATIVE_SCAN": "strict_order"},
                      [("PGVECTOR_HNSW_ITERATIVE_SCAN",
                        "PG_HNSW_ITERATIVE_SCAN", "derive")])
        self.assertEqual(1, r.returncode)
        self.assertIn("PG_HNSW_ITERATIVE_SCAN is registered", r.stderr)
        self.assertIn("PGVECTOR_HNSW_ITERATIVE_SCAN is registered", r.stderr)

    def test_only_the_unemitted_side_is_named(self):
        r = self._run({"T_A": "1"}, [("T_A", "T_B", "derive")])
        self.assertEqual(1, r.returncode)
        self.assertIn("T_B is registered", r.stderr)
        self.assertNotIn("T_A is registered", r.stderr)

    def test_a_family_prefix_row_names_no_variable(self):
        r = self._run({}, [("T_", "U_", "derive")])
        self.assertEqual(0, r.returncode, r.stderr)

    def _gate_over(self, toml_text):
        """The real gate, snapshot tool and registry over a copy of the SSOT."""
        root = os.path.join(self.tmp, "root")
        os.makedirs(os.path.join(root, "usr/share/mios"), exist_ok=True)
        with open(os.path.join(root, "usr/share/mios/mios.toml"), "w", encoding="utf-8") as fh:
            fh.write(toml_text)
        return subprocess.run(
            [sys.executable, _MOD_PATH, "value-aliases",
             os.path.join(_ROOT, "usr/libexec/mios/mios-env-snapshot"),
             os.path.join(_ROOT, "usr/share/mios/reference/value-aliases.tsv")],
            capture_output=True, text=True, cwd=_ROOT,
            env=dict(os.environ, MIOS_DRIFT_ROOT=root))

    def test_the_lost_pgvector_header_is_named_by_variable(self):
        # Replays the defect on the shipped SSOT: [lsfs] and [offline] opened
        # mid-[pgvector], so rls_enable..listen_loopback parsed into [offline].
        with open(os.path.join(_ROOT, "usr/share/mios/mios.toml"), encoding="utf-8") as fh:
            shipped = fh.read()
        m = re.search(r"^rls_enable\s*=.*?^listen_loopback\s*=[^\n]*\n", shipped, re.S | re.M)
        anchor = "\nfallback_to_online = true\n"
        self.assertTrue(m and shipped.count(anchor) == 1,
                        "[pgvector]/[offline] shape changed; this fixture is stale")
        stranded = (shipped[:m.start()] + shipped[m.end():]).replace(anchor, anchor + m.group(0), 1)

        control = self._gate_over(shipped)
        self.assertEqual(0, control.returncode, control.stderr)

        r = self._gate_over(stranded)
        self.assertEqual(1, r.returncode, r.stderr)
        # The names the pgvector Quadlet, agent-pipe pg.py and mios-pg-query read.
        for name in ("MIOS_PGVECTOR_HNSW_ITERATIVE_SCAN", "MIOS_PGVECTOR_POOL_ENABLE", "MIOS_PGVECTOR_RLS_ENABLE"):
            self.assertIn(name + " is registered", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
