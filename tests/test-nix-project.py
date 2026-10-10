#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Task task_525/task_526 Nix Subsystem and Declarative Flake Projection Generator.
# AI-related: usr/libexec/mios/config/nix_project.py, usr/share/mios/nix/flake-template.nix, usr/share/mios/nix/nix.conf, usr/lib/tmpfiles.d/50-mios.conf, automation/59-tools.sh
"""Automated unit test suite for Declarative Nix Subsystem & NixProject (task_525, task_526)."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_MODULE_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "config", "nix_project.py")

spec = importlib.util.spec_from_file_location("nix_project", _MODULE_PATH)
if spec and spec.loader:
    nix_project = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = nix_project
    spec.loader.exec_module(nix_project)
else:
    raise ImportError(f"Could not load nix_project module from {_MODULE_PATH}")

class TestNixProjectSubsystem(unittest.TestCase):
    """Validates multi-user Nix subsystem integration, tmpfiles specifications, and flake projection."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="mios_nix_test_")
        self.toml_path = os.path.join(self.temp_dir, "test_mios.toml")
        self.template_path = os.path.join(self.temp_dir, "test_template.nix")
        self.gen_dir = os.path.join(self.temp_dir, "generations")
        self.output_flake = os.path.join(self.temp_dir, "flake.nix")

        # Write sample mios.toml
        sample_toml = """
[packages.nix]
pkgs = ["ripgrep", "fd", "bat", "jq", "python3Packages.requests"]

[packages.user]
pkgs = ["fzf", "zoxide", "starship"]

[packages.utils]
pkgs = ["curl", "git"]

[shell]
default_shell = "bash"
alias_ll = "ls -la --color=auto"
alias_gs = "git status"
alias_gp = "git push origin main"

[dotfiles.registry.btop]
template = "usr/share/mios/theme/templates/btop-mios.theme.tmpl"
target = "etc/btop/themes/mios.theme"
[dotfiles.registry.btop.apply.target]
linux = "~/.config/btop/themes/mios.theme"

[dotfiles.registry.fastfetch]
template = "usr/share/mios/theme/templates/fastfetch-config.jsonc.tmpl"
[dotfiles.registry.fastfetch.apply.target]
linux = "~/.config/fastfetch/config.jsonc"
"""
        with open(self.toml_path, "w", encoding="utf-8") as f:
            f.write(sample_toml)

        # Copy canonical template to temp dir
        canonical_template = os.path.join(
            _ROOT, "usr", "share", "mios", "nix", "flake-template.nix"
        )
        if os.path.isfile(canonical_template):
            shutil.copyfile(canonical_template, self.template_path)
        else:
            with open(self.template_path, "w", encoding="utf-8") as f:
                f.write(nix_project.DEFAULT_FLAKE_TEMPLATE)

        self.manager = nix_project.NixProjectManager(
            toml_path=self.toml_path,
            template_path=self.template_path,
            generations_dir=self.gen_dir,
            mock=False,
        )

    def tearDown(self) -> None:
        if os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_toml_package_extraction(self) -> None:
        """Asserts accurate extraction and deduplication of declared packages from mios.toml."""
        cfg = self.manager.load_toml_config()
        packages = self.manager.extract_packages(cfg)
        expected = ["ripgrep", "fd", "bat", "jq", "python3Packages.requests", "fzf", "zoxide", "starship", "curl", "git"]
        for exp in expected:
            self.assertIn(exp, packages)

    def test_toml_shell_aliases_extraction(self) -> None:
        """Asserts extraction of shell aliases with proper naming mapping."""
        cfg = self.manager.load_toml_config()
        aliases = self.manager.extract_shell_aliases(cfg)
        self.assertEqual(aliases.get("ll"), "ls -la --color=auto")
        self.assertEqual(aliases.get("gs"), "git status")
        self.assertEqual(aliases.get("gp"), "git push origin main")

    def test_toml_dotfiles_extraction(self) -> None:
        """Asserts extraction of dotfiles target paths and source mappings."""
        cfg = self.manager.load_toml_config()
        dotfiles = self.manager.extract_dotfiles(cfg)
        self.assertIn(".config/btop/themes/mios.theme", dotfiles)
        self.assertIn(".config/fastfetch/config.jsonc", dotfiles)

    def test_pure_flake_rendering_and_syntax(self) -> None:
        """Asserts pure Nix flake rendering produces valid syntax without unclosed delimiters."""
        rendered = self.manager.render_flake()
        self.assertIn("description = \"MiOS Declarative User Environment and Dotfiles Flake\";", rendered)
        self.assertIn("homeConfigurations.mios", rendered)
        self.assertIn("ripgrep", rendered)
        self.assertIn("alias_ll", rendered.replace('ll = "ls -la --color=auto";', 'alias_ll'))
        self.assertIn(".config/btop/themes/mios.theme", rendered)

        valid, msg = self.manager.validate_flake_syntax(rendered)
        self.assertTrue(valid, f"Syntax validation failed: {msg}")

    def test_flake_syntax_validator_detects_errors(self) -> None:
        """Asserts flake syntax validator catches mismatched delimiters and unclosed strings."""
        # Unclosed brace
        bad_flake_1 = "{ description = \"test\"; inputs = {}; outputs = {}; "
        valid, msg = self.manager.validate_flake_syntax(bad_flake_1)
        self.assertFalse(valid)
        self.assertIn("Unclosed delimiter", msg)

        # Mismatched bracket
        bad_flake_2 = "{ description = \"test\"; inputs = {}; outputs = { val = [ 1 2 }; }; }"
        valid, msg = self.manager.validate_flake_syntax(bad_flake_2)
        self.assertFalse(valid)
        self.assertIn("Mismatched delimiter", msg)

        # Unclosed string
        bad_flake_3 = "{ description = \"test; inputs = {}; outputs = {}; }"
        valid, msg = self.manager.validate_flake_syntax(bad_flake_3)
        self.assertFalse(valid)

        # Missing required attribute
        bad_flake_4 = "{ some_attr = 123; }"
        valid, msg = self.manager.validate_flake_syntax(bad_flake_4)
        self.assertFalse(valid)
        self.assertIn("Missing required top-level flake attribute", msg)

    def test_atomic_generation_lifecycle_and_rollback(self) -> None:
        """Asserts atomic generation persistence, version indexing, and rollback restoration."""
        cfg = self.manager.load_toml_config()
        rendered_1 = self.manager.render_flake(cfg)

        # Generation 1
        summary_1 = self.manager.save_generation(
            output_path=self.output_flake,
            flake_content=rendered_1,
            config=cfg,
        )
        self.assertEqual(summary_1["generation"], 1)
        self.assertTrue(os.path.isfile(self.output_flake))

        # Generation 2 with modified package
        cfg_2 = dict(cfg)
        cfg_2["packages"] = {"nix": {"pkgs": ["neovim", "tmux", "helix"]}}
        rendered_2 = self.manager.render_flake(cfg_2)
        summary_2 = self.manager.save_generation(
            output_path=self.output_flake,
            flake_content=rendered_2,
            config=cfg_2,
        )
        self.assertEqual(summary_2["generation"], 2)

        # Verify active flake currently has neovim
        current_content = Path(self.output_flake).read_text(encoding="utf-8")
        self.assertIn("neovim", current_content)

        # List generations
        gens = self.manager.list_generations()
        self.assertEqual(len(gens), 2)
        self.assertTrue(gens[1]["active"])
        self.assertFalse(gens[0]["active"])

        # Rollback to generation 1
        rollback_res = self.manager.rollback(target_generation=1, output_path=self.output_flake)
        self.assertEqual(rollback_res["status"], "success")
        self.assertEqual(rollback_res["rolled_back_to"], 1)

        # Verify rolled-back content restores generation 1 packages
        restored_content = Path(self.output_flake).read_text(encoding="utf-8")
        self.assertIn("ripgrep", restored_content)

    def test_cli_execution_render_and_validate(self) -> None:
        """Asserts command-line interface execution across --render-flake, --validate-flake, and --json."""
        out_flake = os.path.join(self.temp_dir, "cli_flake.nix")
        argv = [
            "--render-flake",
            "--output", out_flake,
            "--toml", self.toml_path,
            "--template", self.template_path,
            "--generations-dir", self.gen_dir,
            "--json",
        ]

        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = nix_project.main(argv)
        self.assertEqual(ret, 0)
        res = json.loads(buf.getvalue())
        self.assertEqual(res["status"], "success")
        self.assertTrue(os.path.isfile(out_flake))

        # Test CLI validate-flake
        validate_argv = ["--validate-flake", out_flake, "--json"]
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            ret_val = nix_project.main(validate_argv)
        self.assertEqual(ret_val, 0)
        res_val = json.loads(buf2.getvalue())
        self.assertTrue(res_val["valid"])

    def test_tmpfiles_specification_conformance(self) -> None:
        """Asserts tmpfiles.d declares L+ /nix -> /var/nix and persistent store directories."""
        tmpfiles_path = os.path.join(_ROOT, "usr", "lib", "tmpfiles.d", "50-mios.conf")
        self.assertTrue(os.path.isfile(tmpfiles_path), "50-mios.conf missing")

        with open(tmpfiles_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("L+ /nix", content)
        self.assertIn("/var/nix", content)
        self.assertIn("d /var/nix/store", content)
        self.assertIn("nixbld", content)

    def test_nix_conf_vendor_defaults(self) -> None:
        """Asserts usr/share/mios/nix/nix.conf contains required multi-user and sandbox settings."""
        nix_conf_path = os.path.join(_ROOT, "usr", "share", "mios", "nix", "nix.conf")
        self.assertTrue(os.path.isfile(nix_conf_path), "nix.conf missing")

        with open(nix_conf_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("experimental-features = nix-command flakes", content)
        self.assertIn("build-users-group = nixbld", content)
        self.assertIn("sandbox = true", content)
        self.assertIn("substituters = https://cache.nixos.org/", content)

    def test_automation_59_tools_nix_integration(self) -> None:
        """Asserts automation/59-tools.sh contains proper Nix initialization commands and does not violate NO-MKDIR-IN-VAR."""
        script_path = os.path.join(_ROOT, "automation", "59-tools.sh")
        self.assertTrue(os.path.isfile(script_path), "59-tools.sh missing")

        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("set -euo pipefail", content)
        self.assertIn("/etc/nix", content)
        self.assertIn("nix-daemon", content)
        # Verify NO-MKDIR-IN-VAR invariant: should not create /var/nix directories during build script
        self.assertNotIn("mkdir -p /var/nix", content)

if __name__ == "__main__":
    unittest.main()
