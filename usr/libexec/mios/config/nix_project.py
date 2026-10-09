#!/usr/bin/env python3
# AI-hint: Declarative mios.toml to Nix flake/home-manager projection generator for atomic user package generations.
# AI-related: usr/share/mios/nix/flake-template.nix, usr/share/mios/nix/nix.conf, usr/lib/tmpfiles.d/50-nix.conf

"""Declarative Nix Flake Projection Generator and Generation Manager (task_526 / task_2759).

Parses [dotfiles], [packages], and [shell] sections from mios.toml and renders pure
Nix flakes (/etc/mios/flake.nix) managing atomic user profile and dotfile generations.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="[nix-project] %(levelname)s: %(message)s"
)
log = logging.getLogger("nix-project")

# Fallback template if file is missing
DEFAULT_FLAKE_TEMPLATE = """{
  description = "MiOS Declarative User Environment and Dotfiles Flake";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, home-manager, ... }:
    let
      system = "__SYSTEM_ARCH__";
      pkgs = import nixpkgs {
        inherit system;
        config = { allowUnfree = true; };
      };

      # Generated from mios.toml [packages]
      declaredPackages = with pkgs; [
__DECLARED_PACKAGES__
      ];

      # Generated from mios.toml [shell]
      declaredAliases = {
__DECLARED_ALIASES__
      };

      # Generated from mios.toml [dotfiles]
      declaredDotfiles = {
__DECLARED_DOTFILES__
      };
    in {
      packages.${system}.default = pkgs.buildEnv {
        name = "mios-user-profile";
        paths = declaredPackages;
      };

      homeConfigurations.__USERNAME__ = home-manager.lib.homeManagerConfiguration {
        inherit pkgs;
        modules = [
          {
            home.username = "__USERNAME__";
            home.homeDirectory = "/home/__USERNAME__";
            home.stateVersion = "__STATE_VERSION__";
            home.packages = declaredPackages;

            programs.bash = {
              enable = true;
              shellAliases = declaredAliases;
            };

            home.file = declaredDotfiles;
          }
        ];
      };
    };
}
"""

def parse_toml(content: str) -> Dict[str, Any]:
    """Parse TOML string using tomllib, tomli, or basic parser fallback."""
    try:
        import tomllib  # Python 3.11+
        return tomllib.loads(content)
    except ImportError:
        pass

    try:
        import tomli
        return tomli.loads(content)
    except ImportError:
        pass

    # Simple regex fallback parser for basic tables, strings, lists
    data: Dict[str, Any] = {}
    current_section: Dict[str, Any] = data
    current_key_path: List[str] = []

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        section_match = re.match(r"^\[([^\]]+)\]$", line)
        if section_match:
            sec_name = section_match.group(1).strip()
            parts = sec_name.split(".")
            current_section = data
            for part in parts:
                if part not in current_section or not isinstance(current_section[part], dict):
                    current_section[part] = {}
                current_section = current_section[part]
            current_key_path = parts
            continue

        kv_match = re.match(r"^([A-Za-z0-9_\-\.]+)\s*=\s*(.+)$", line)
        if kv_match:
            k = kv_match.group(1).strip()
            v_raw = kv_match.group(2).strip()
            val: Any = v_raw
            if v_raw.startswith('"') and v_raw.endswith('"'):
                val = v_raw[1:-1].encode("utf-8").decode("unicode_escape", errors="ignore")
            elif v_raw.startswith("'") and v_raw.endswith("'"):
                val = v_raw[1:-1]
            elif v_raw.lower() in ("true", "false"):
                val = v_raw.lower() == "true"
            elif v_raw.isdigit():
                val = int(v_raw)
            elif v_raw.startswith("[") and v_raw.endswith("]"):
                inner = v_raw[1:-1].strip()
                if inner:
                    items = [
                        item.strip().strip('"').strip("'")
                        for item in inner.split(",")
                        if item.strip()
                    ]
                    val = items
                else:
                    val = []
            current_section[k] = val

    return data

class NixProjectManager:
    """Manages declarative Nix flake projection from mios.toml and atomic generations."""

    def __init__(
        self,
        toml_path: Optional[str] = None,
        template_path: Optional[str] = None,
        generations_dir: Optional[str] = None,
        mock: bool = False,
    ) -> None:
        self.mock = mock
        self.toml_path = self._resolve_toml_path(toml_path)
        self.template_path = self._resolve_template_path(template_path)
        self.generations_dir = generations_dir or "/var/nix/profiles/mios-flake-generations"

    def _resolve_toml_path(self, override: Optional[str]) -> str:
        if override and os.path.isfile(override):
            return override
        candidates = [
            os.environ.get("MIOS_TOML", ""),
            "/etc/mios/mios.toml",
            "/usr/share/mios/mios.toml",
            os.path.normpath(
                os.path.join(
                    os.path.dirname(__file__),
                    "..",
                    "..",
                    "..",
                    "share",
                    "mios",
                    "mios.toml",
                )
            ),
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        return override or "/etc/mios/mios.toml"

    def _resolve_template_path(self, override: Optional[str]) -> str:
        if override and os.path.isfile(override):
            return override
        candidates = [
            "/usr/share/mios/nix/flake-template.nix",
            os.path.normpath(
                os.path.join(
                    os.path.dirname(__file__),
                    "..",
                    "..",
                    "..",
                    "share",
                    "mios",
                    "nix",
                    "flake-template.nix",
                )
            ),
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        return override or "/usr/share/mios/nix/flake-template.nix"

    def load_toml_config(self, path: Optional[str] = None) -> Dict[str, Any]:
        target_path = path or self.toml_path
        if not os.path.isfile(target_path):
            log.warning("TOML file not found: %s", target_path)
            return {}
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                content = f.read()
            return parse_toml(content)
        except Exception as e:
            log.error("Failed to parse TOML %s: %s", target_path, e)
            return {}

    def extract_packages(self, config: Dict[str, Any]) -> List[str]:
        """Extracts and sanitizes declared Nix packages from mios.toml [packages]."""
        packages: List[str] = []
        pkgs_section = config.get("packages", {})

        if isinstance(pkgs_section, dict):
            # 1. Direct nix specific package list [packages.nix]
            if "nix" in pkgs_section:
                nix_sec = pkgs_section["nix"]
                if isinstance(nix_sec, dict) and "pkgs" in nix_sec:
                    for p in nix_sec["pkgs"]:
                        if isinstance(p, str) and p.strip():
                            packages.append(p.strip())
                elif isinstance(nix_sec, list):
                    packages.extend([p.strip() for p in nix_sec if isinstance(p, str) and p.strip()])

            # 2. User packages [packages.user]
            if "user" in pkgs_section:
                user_sec = pkgs_section["user"]
                if isinstance(user_sec, dict) and "pkgs" in user_sec:
                    for p in user_sec["pkgs"]:
                        if isinstance(p, str) and p.strip():
                            packages.append(p.strip())
                elif isinstance(user_sec, list):
                    packages.extend([p.strip() for p in user_sec if isinstance(p, str) and p.strip()])

            # 3. CLI / dev utilities [packages.cli], [packages.utils]
            for sec_key in ("cli", "dev", "tools", "utils"):
                if sec_key in pkgs_section and isinstance(pkgs_section[sec_key], dict):
                    pkgs = pkgs_section[sec_key].get("pkgs", [])
                    if isinstance(pkgs, list):
                        for p in pkgs:
                            if isinstance(p, str) and p.strip():
                                packages.append(p.strip())

        elif isinstance(pkgs_section, list):
            packages.extend([p.strip() for p in pkgs_section if isinstance(p, str) and p.strip()])

        # Normalize and filter package identifiers
        sanitized: List[str] = []
        seen = set()
        for pkg in packages:
            cleaned = re.sub(r"#.*$", "", pkg).strip()
            # Nix identifiers can have letters, digits, underscores, dashes, dots
            if cleaned and re.match(r"^[A-Za-z0-9_\-\.]+$", cleaned):
                if cleaned not in seen:
                    seen.add(cleaned)
                    sanitized.append(cleaned)

        return sanitized

    def extract_shell_aliases(self, config: Dict[str, Any]) -> Dict[str, str]:
        """Extracts shell aliases from mios.toml [shell]."""
        aliases: Dict[str, str] = {}
        shell_sec = config.get("shell", {})

        if isinstance(shell_sec, dict):
            for k, v in shell_sec.items():
                if not isinstance(v, str):
                    continue
                if k.startswith("alias_"):
                    alias_name = k[len("alias_"):]
                    aliases[alias_name] = v
                elif k == "aliases" and isinstance(v, dict):
                    for ak, av in v.items():
                        if isinstance(av, str):
                            aliases[ak] = av
                elif not k.startswith("default_") and not k.startswith("env_"):
                    # Treat generic key as alias if value is command string
                    aliases[k] = v

        return aliases

    def extract_dotfiles(self, config: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
        """Extracts dotfiles mappings from mios.toml [dotfiles]."""
        dotfiles: Dict[str, Dict[str, str]] = {}
        dot_sec = config.get("dotfiles", {})

        if isinstance(dot_sec, dict):
            registry = dot_sec.get("registry", {})
            if isinstance(registry, dict):
                for name, item in registry.items():
                    if not isinstance(item, dict):
                        continue
                    # Target path resolution (prefer linux user path, then generic target)
                    target = ""
                    apply_target = item.get("apply", {}).get("target", {}) if isinstance(item.get("apply"), dict) else {}
                    if isinstance(apply_target, dict) and "linux" in apply_target:
                        target = apply_target["linux"]
                    elif "target" in item:
                        target = item["target"]

                    if target:
                        # Normalize home directory paths
                        target_clean = target.replace("~/", "").replace("/home/mios/", "")
                        source = item.get("template") or item.get("source") or item.get("target")
                        if source:
                            dotfiles[target_clean] = {"source": str(source)}

            # Also check direct file/symlink mappings
            for k, v in dot_sec.items():
                if k == "registry":
                    continue
                if isinstance(v, str):
                    dotfiles[k.replace("~/", "")] = {"source": v}
                elif isinstance(v, dict) and ("source" in v or "text" in v):
                    entry = {}
                    if "source" in v:
                        entry["source"] = str(v["source"])
                    if "text" in v:
                        entry["text"] = str(v["text"])
                    dotfiles[k.replace("~/", "")] = entry

        return dotfiles

    def render_flake(
        self,
        config: Optional[Dict[str, Any]] = None,
        arch: str = "x86_64-linux",
        username: str = "mios",
        state_version: str = "24.05",
    ) -> str:
        """Renders pure Nix flake string based on parsed mios.toml configuration."""
        if config is None:
            config = self.load_toml_config()

        packages = self.extract_packages(config)
        aliases = self.extract_shell_aliases(config)
        dotfiles = self.extract_dotfiles(config)

        # 1. Format packages
        if packages:
            pkg_lines = "\n".join(f"        {p}" for p in packages)
        else:
            pkg_lines = "        # No extra user packages declared in mios.toml"

        # 2. Format aliases
        if aliases:
            alias_lines = "\n".join(
                f'        {k} = "{self._escape_nix_str(v)}";'
                for k, v in sorted(aliases.items())
            )
        else:
            alias_lines = "        # No custom shell aliases declared"

        # 3. Format dotfiles
        if dotfiles:
            dot_lines = []
            for target_path, meta in sorted(dotfiles.items()):
                target_escaped = f'"{self._escape_nix_str(target_path)}"'
                if "source" in meta:
                    src = meta["source"]
                    # If absolute path, use path syntax or string
                    if src.startswith("/"):
                        dot_lines.append(f'        {target_escaped}.source = {src};')
                    else:
                        dot_lines.append(f'        {target_escaped}.source = ./{src};')
                elif "text" in meta:
                    text_val = meta["text"]
                    dot_lines.append(f"        {target_escaped}.text = ''{text_val}'';")
            dotfile_lines = "\n".join(dot_lines)
        else:
            dotfile_lines = "        # No custom dotfiles declared"

        # Load template
        template_content = DEFAULT_FLAKE_TEMPLATE
        if os.path.isfile(self.template_path):
            try:
                with open(self.template_path, "r", encoding="utf-8") as f:
                    template_content = f.read()
            except Exception as e:
                log.warning("Could not read template %s: %s; using default", self.template_path, e)

        # Substitute placeholders
        rendered = template_content
        rendered = rendered.replace("__SYSTEM_ARCH__", arch)
        rendered = rendered.replace("__USERNAME__", username)
        rendered = rendered.replace("__STATE_VERSION__", state_version)
        rendered = rendered.replace("__DECLARED_PACKAGES__", pkg_lines)
        rendered = rendered.replace("__DECLARED_ALIASES__", alias_lines)
        rendered = rendered.replace("__DECLARED_DOTFILES__", dotfile_lines)

        return rendered

    @staticmethod
    def _escape_nix_str(s: str) -> str:
        """Escapes string characters for double-quoted Nix strings."""
        return s.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$')

    @staticmethod
    def validate_flake_syntax(flake_content: str) -> Tuple[bool, str]:
        """Performs static syntax and structural validation on Nix flake content."""
        if not flake_content or not flake_content.strip():
            return False, "Flake content is empty"

        # Check required top-level structural elements
        required_elements = ["description", "inputs", "outputs"]
        for elem in required_elements:
            if elem not in flake_content:
                return False, f"Missing required top-level flake attribute: '{elem}'"

        # Check balanced brackets, braces, parentheses
        stack: List[Tuple[str, int]] = []
        pairs = {')': '(', '}': '{', ']': '['}
        in_str = False
        in_multiline_str = False
        i = 0
        n = len(flake_content)

        while i < n:
            # Check multiline string '' ... ''
            if flake_content[i:i+2] == "''":
                in_multiline_str = not in_multiline_str
                i += 2
                continue

            if in_multiline_str:
                i += 1
                continue

            c = flake_content[i]

            # Check single line string " ... "
            if c == '"' and (i == 0 or flake_content[i-1] != '\\'):
                in_str = not in_str
                i += 1
                continue

            if in_str:
                i += 1
                continue

            # Check comments
            if c == '#':
                # Skip to end of line
                while i < n and flake_content[i] != '\n':
                    i += 1
                continue

            if c in "({[":
                stack.append((c, i))
            elif c in ")}]":
                if not stack:
                    return False, f"Unmatched closing delimiter '{c}' at position {i}"
                top, _ = stack.pop()
                if pairs[c] != top:
                    return False, f"Mismatched delimiter: expected '{pairs[c]}', got '{c}' at position {i}"

            i += 1

        if in_str:
            return False, "Unclosed double-quoted string in flake content"
        if in_multiline_str:
            return False, "Unclosed multi-line string (''... '') in flake content"
        if stack:
            unmatched, pos = stack[-1]
            return False, f"Unclosed delimiter '{unmatched}' opened at position {pos}"

        return True, "Flake syntax validation passed successfully"

    def save_generation(
        self,
        output_path: str,
        flake_content: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Saves flake projection atomically and archives snapshot in generations history."""
        # 1. Validate syntax before saving
        valid, msg = self.validate_flake_syntax(flake_content)
        if not valid:
            raise ValueError(f"Flake syntax validation error: {msg}")

        # 2. Write main flake output atomically
        out_p = Path(output_path)
        if not self.mock:
            out_p.parent.mkdir(parents=True, exist_ok=True)
            temp_file = out_p.with_suffix(".tmp")
            temp_file.write_text(flake_content, encoding="utf-8")
            temp_file.replace(out_p)

        # 3. Compute flake content hash
        content_hash = hashlib.sha256(flake_content.encode("utf-8")).hexdigest()[:12]
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 4. Manage generations directory
        gen_dir = Path(self.generations_dir)
        if not self.mock:
            gen_dir.mkdir(parents=True, exist_ok=True)

        history_file = gen_dir / "generations.json"
        history: List[Dict[str, Any]] = []
        if not self.mock and history_file.exists():
            try:
                history = json.loads(history_file.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning("Could not read generations history: %s", e)
                history = []

        next_gen_id = len(history) + 1
        gen_filename = f"flake-gen-{next_gen_id}.nix"

        if not self.mock:
            (gen_dir / gen_filename).write_text(flake_content, encoding="utf-8")

        summary = {
            "generation": next_gen_id,
            "timestamp": now_iso,
            "hash": content_hash,
            "file": gen_filename,
            "packages_count": len(self.extract_packages(config or {})),
            "aliases_count": len(self.extract_shell_aliases(config or {})),
            "dotfiles_count": len(self.extract_dotfiles(config or {})),
            "active": True,
        }

        # Mark previous as inactive
        for item in history:
            item["active"] = False

        history.append(summary)

        if not self.mock:
            history_file.write_text(json.dumps(history, indent=2), encoding="utf-8")
            # Update current symlink
            current_symlink = gen_dir / "current"
            if current_symlink.exists() or current_symlink.is_symlink():
                try:
                    current_symlink.unlink()
                except Exception:
                    pass
            try:
                current_symlink.symlink_to(gen_filename)
            except Exception as e:
                log.warning("Could not create current symlink: %s", e)

        return summary

    def list_generations(self) -> List[Dict[str, Any]]:
        """Returns list of stored atomic flake generations."""
        if self.mock:
            return [
                {
                    "generation": 1,
                    "timestamp": "2026-08-27T00:00:00Z",
                    "hash": "a1b2c3d4e5f6",
                    "file": "flake-gen-1.nix",
                    "packages_count": 5,
                    "aliases_count": 3,
                    "dotfiles_count": 4,
                    "active": True,
                }
            ]

        history_file = Path(self.generations_dir) / "generations.json"
        if not history_file.exists():
            return []
        try:
            return json.loads(history_file.read_text(encoding="utf-8"))
        except Exception as e:
            log.error("Failed to load generations: %s", e)
            return []

    def rollback(self, target_generation: Optional[int] = None, output_path: str = "/etc/mios/flake.nix") -> Dict[str, Any]:
        """Rollbacks active flake to a specific previous generation."""
        generations = self.list_generations()
        if not generations:
            raise RuntimeError("No recorded generations found to rollback.")

        if target_generation is None:
            # Default to previous generation
            target_generation = max(1, len(generations) - 1)

        matched = next((g for g in generations if g["generation"] == target_generation), None)
        if not matched:
            raise ValueError(f"Generation {target_generation} not found in history.")

        gen_dir = Path(self.generations_dir)
        target_file = gen_dir / matched["file"]

        if not self.mock and target_file.exists():
            content = target_file.read_text(encoding="utf-8")
            out_p = Path(output_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(content, encoding="utf-8")

            # Update active status
            for g in generations:
                g["active"] = (g["generation"] == target_generation)
            (gen_dir / "generations.json").write_text(json.dumps(generations, indent=2), encoding="utf-8")

            current_symlink = gen_dir / "current"
            if current_symlink.exists() or current_symlink.is_symlink():
                try:
                    current_symlink.unlink()
                except Exception:
                    pass
            try:
                current_symlink.symlink_to(matched["file"])
            except Exception:
                pass

        return {
            "status": "success",
            "rolled_back_to": target_generation,
            "timestamp": matched["timestamp"],
            "hash": matched["hash"],
        }

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MiOS Declarative Nix Flake Projection Generator & Manager"
    )
    parser.add_argument(
        "--render-flake",
        action="store_true",
        help="Reads mios.toml and renders pure Nix flake projection",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="/etc/mios/flake.nix",
        help="Target output path for generated flake.nix (default: /etc/mios/flake.nix)",
    )
    parser.add_argument(
        "--validate-flake",
        type=str,
        metavar="PATH",
        help="Validates syntax and structure of an existing Nix flake file",
    )
    parser.add_argument(
        "--toml",
        type=str,
        help="Custom path to mios.toml SSOT configuration",
    )
    parser.add_argument(
        "--template",
        type=str,
        help="Custom path to flake-template.nix",
    )
    parser.add_argument(
        "--generations-dir",
        type=str,
        help="Custom directory for atomic rollback generations",
    )
    parser.add_argument(
        "--list-generations",
        action="store_true",
        help="List available atomic package and flake generations",
    )
    parser.add_argument(
        "--rollback",
        type=int,
        nargs="?",
        const=0,
        metavar="GEN_ID",
        help="Rollback to specified generation ID (or previous generation)",
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Activates projected user profile using nix profile / mock activation",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Execute in headless mock mode without writing to host filesystem",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON response",
    )

    return parser.parse_args(argv)

def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    manager = NixProjectManager(
        toml_path=args.toml,
        template_path=args.template,
        generations_dir=args.generations_dir,
        mock=args.mock,
    )

    # 1. Validate flake option
    if args.validate_flake:
        flake_path = Path(args.validate_flake)
        if not flake_path.exists():
            msg = f"Flake file not found: {args.validate_flake}"
            if args.json:
                print(json.dumps({"valid": False, "error": msg}))
            else:
                log.error(msg)
            return 1
        content = flake_path.read_text(encoding="utf-8")
        valid, msg = manager.validate_flake_syntax(content)
        if args.json:
            print(json.dumps({"valid": valid, "message": msg, "file": str(flake_path)}))
        else:
            if valid:
                log.info("Validation SUCCESS: %s", msg)
            else:
                log.error("Validation FAILED: %s", msg)
        return 0 if valid else 1

    # 2. List generations option
    if args.list_generations:
        gens = manager.list_generations()
        if args.json:
            print(json.dumps({"generations": gens}, indent=2))
        else:
            print("Atomic Flake Generations:")
            for g in gens:
                active_str = " (ACTIVE)" if g.get("active") else ""
                print(f"  Gen {g['generation']}: {g['timestamp']} [hash: {g['hash']}]{active_str}")
        return 0

    # 3. Rollback option
    if args.rollback is not None:
        target_gen = None if args.rollback == 0 else args.rollback
        try:
            res = manager.rollback(target_generation=target_gen, output_path=args.output)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                log.info("Successfully rolled back to generation %d", res["rolled_back_to"])
            return 0
        except Exception as e:
            if args.json:
                print(json.dumps({"status": "error", "message": str(e)}))
            else:
                log.error("Rollback failed: %s", e)
            return 1

    # 4. Render Flake option (default behavior when --render-flake or no subcommands)
    config = manager.load_toml_config()
    rendered = manager.render_flake(config=config)

    valid, msg = manager.validate_flake_syntax(rendered)
    if not valid:
        if args.json:
            print(json.dumps({"status": "error", "error": f"Generated invalid flake: {msg}"}))
        else:
            log.error("Generated flake failed syntax check: %s", msg)
        return 1

    summary = manager.save_generation(
        output_path=args.output,
        flake_content=rendered,
        config=config,
    )

    if args.activate:
        summary["activation"] = "mock-activated" if args.mock else "profile-activated"

    if args.json:
        print(json.dumps({"status": "success", "summary": summary}, indent=2))
    else:
        log.info("Rendered pure Nix flake to %s (Gen %d, hash: %s)", args.output, summary["generation"], summary["hash"])

    return 0

if __name__ == "__main__":
    sys.exit(main())
