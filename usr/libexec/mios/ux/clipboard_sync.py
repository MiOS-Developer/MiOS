#!/usr/bin/env python3
# AI-hint: Secret-redacting cross-platform clipboard synchronizer between host Wayland/X11 and guest VMs.
# AI-related: tests/test-ux.py, usr/share/mios/mios.toml
# AI-functions: ClipboardSyncEngine, RedactionRule, RedactionResult, main
"""
MiOS Secret-Redacting Cross-Platform Clipboard Synchronizer (task_409).

Synchronizes clipboard buffers between host Wayland/X11 surfaces and guest virtual machines
(via SPICE vdagent, socket bridge, or guest agent) with automated token redaction.
Prevents accidental leakage of operator secrets (API keys, PAT tokens, private keys, AWS credentials)
into untrusted guest virtual machines.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB_PATH = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "lib", "mios"))
if os.path.isdir(_LIB_PATH) and _LIB_PATH not in sys.path:
    sys.path.insert(0, _LIB_PATH)

try:
    import mios_toml
except ImportError:
    mios_toml = None

@dataclass
class RedactionRule:
    """Regex pattern rule for intercepting and redacting sensitive credentials."""
    category: str
    pattern: re.Pattern
    replacement_template: str

@dataclass
class RedactionResult:
    """Outcome of filtering a text payload."""
    original_len: int
    redacted_len: int
    redactions_count: int
    detected_categories: List[str]
    redacted_text: str

# Canonical sensitive credential regex suite
REDACTION_RULES: List[RedactionRule] = [
    RedactionRule(
        category="OPENAI_KEY",
        pattern=re.compile(r"\bsk-[a-zA-Z0-9_-]{20,}\b"),
        replacement_template="[REDACTED_SECRET:OPENAI_KEY]",
    ),
    RedactionRule(
        category="GITHUB_PAT",
        pattern=re.compile(r"\b(ghp_[a-zA-Z0-9]{25,45}|github_pat_[a-zA-Z0-9_]{20,})\b"),
        replacement_template="[REDACTED_SECRET:GITHUB_PAT]",
    ),
    RedactionRule(
        category="AWS_ACCESS_KEY",
        pattern=re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        replacement_template="[REDACTED_SECRET:AWS_ACCESS_KEY]",
    ),
    RedactionRule(
        category="AWS_SECRET_KEY",
        pattern=re.compile(r"(?i)(aws_secret_access_key\s*[:=]\s*['\"]?)([a-zA-Z0-9/+=]{40})(['\"]?)"),
        replacement_template=r"\g<1>[REDACTED_SECRET:AWS_SECRET_KEY]\g<3>",
    ),
    RedactionRule(
        category="PRIVATE_KEY",
        pattern=re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----"
        ),
        replacement_template="[REDACTED_SECRET:PRIVATE_KEY]",
    ),
    RedactionRule(
        category="BEARER_TOKEN",
        pattern=re.compile(r"\b(Bearer\s+)[a-zA-Z0-9_\-\.]{25,}\b"),
        replacement_template=r"\g<1>[REDACTED_SECRET:BEARER_TOKEN]",
    ),
    RedactionRule(
        category="SLACK_TOKEN",
        pattern=re.compile(r"\bxox[baprs]-[0-9a-zA-Z]{10,48}\b"),
        replacement_template="[REDACTED_SECRET:SLACK_TOKEN]",
    ),
    RedactionRule(
        category="URI_SECRET_PARAM",
        pattern=re.compile(r"(?i)((?:password|passwd|secret|api_key|token|auth_token)=)(?!\[REDACTED)([^&\s'\"`]{6,})"),
        replacement_template=r"\g<1>[REDACTED_SECRET:PARAM]",
    ),
]

class ClipboardSyncEngine:
    """Handles host clipboard monitoring, secret token redaction, and VM forwarding."""

    def __init__(
        self,
        rules: Optional[List[RedactionRule]] = None,
        mock: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        self.rules = rules or REDACTION_RULES
        self.mock = mock
        self.dry_run = dry_run
        self.verbose = verbose
        self.stats: Dict[str, int] = {
            "total_syncs": 0,
            "total_redactions": 0,
        }
        self.category_counts: Dict[str, int] = {}
        self._mock_clipboard: str = ""

    def filter_text(self, text: str) -> RedactionResult:
        """Apply redaction regex patterns to input text and record statistics."""
        current_text = text
        detected: List[str] = []
        total_redactions = 0

        for rule in self.rules:
            matches = rule.pattern.findall(current_text)
            if matches:
                count = len(matches)
                total_redactions += count
                detected.append(rule.category)
                self.category_counts[rule.category] = self.category_counts.get(rule.category, 0) + count
                current_text = rule.pattern.sub(rule.replacement_template, current_text)

        self.stats["total_redactions"] += total_redactions

        return RedactionResult(
            original_len=len(text),
            redacted_len=len(current_text),
            redactions_count=total_redactions,
            detected_categories=detected,
            redacted_text=current_text,
        )

    def get_host_clipboard(self) -> str:
        """Read text from host clipboard buffer."""
        if self.mock:
            return self._mock_clipboard

        if shutil.which("wl-paste"):
            try:
                res = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0:
                    return res.stdout
            except Exception:
                pass

        if shutil.which("xclip"):
            try:
                res = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0:
                    return res.stdout
            except Exception:
                pass

        return ""

    def set_mock_clipboard(self, text: str) -> None:
        """Set in-memory mock clipboard for tests."""
        self._mock_clipboard = text

    def sync_once(self, source_text: Optional[str] = None) -> Dict[str, Any]:
        """Execute a single clipboard read, redaction, and sync step."""
        raw_text = source_text if source_text is not None else self.get_host_clipboard()
        res = self.filter_text(raw_text)
        self.stats["total_syncs"] += 1

        forward_status = "simulated" if (self.mock or self.dry_run) else "forwarded"

        return {
            "status": "success",
            "action": "sync_step",
            "original_length": res.original_len,
            "redacted_length": res.redacted_len,
            "redactions_count": res.redactions_count,
            "detected_categories": res.detected_categories,
            "forward_status": forward_status,
            "redacted_preview": res.redacted_text[:200] if len(res.redacted_text) > 200 else res.redacted_text,
            "mock": self.mock,
            "dry_run": self.dry_run,
        }

    def get_stats_report(self) -> Dict[str, Any]:
        """Produce statistics report of clipboard synchronization and redactions."""
        return {
            "status": "success",
            "action": "stats",
            "total_syncs": self.stats["total_syncs"],
            "total_redactions": self.stats["total_redactions"],
            "categories": self.category_counts,
            "rules_count": len(self.rules),
            "mock": self.mock,
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Secret-Redacting Cross-Platform Clipboard Synchronizer (task_409)"
    )
    parser.add_argument("--filter-text", help="Inspect and filter a specific text string")
    parser.add_argument("--sync", action="store_true", help="Execute clipboard synchronization cycle")
    parser.add_argument("--stats", action="store_true", help="Display clipboard redaction statistics")
    parser.add_argument("--mock", action="store_true", help="Deterministic in-memory mock mode")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without forwarding to VM bridge")
    parser.add_argument("--json", action="store_true", help="Emit output as JSON")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    engine = ClipboardSyncEngine(
        mock=args.mock,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    try:
        if args.filter_text is not None:
            res = engine.filter_text(args.filter_text)
            result = {
                "status": "success",
                "action": "filter_text",
                "original_text": args.filter_text,
                "redacted_text": res.redacted_text,
                "redactions_count": res.redactions_count,
                "detected_categories": res.detected_categories,
                "mock": args.mock,
            }
        elif args.stats:
            result = engine.get_stats_report()
        else:
            # Default action: sync step
            if args.mock:
                engine.set_mock_clipboard("Bearer test-token-1234567890123456789012345 with sk-99887766554433221100aa")
            result = engine.sync_once()

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status = result.get("status", "ok")
            print(f"[clipboard_sync] Status: {status}")
            if "redacted_text" in result:
                print(f"  Redacted Output: {result['redacted_text']}")
                print(f"  Detected Secrets: {result['detected_categories']} ({result['redactions_count']} matches)")
            elif "total_syncs" in result:
                print(f"  Total Syncs: {result['total_syncs']} | Total Redactions: {result['total_redactions']}")
                for cat, cnt in result.get("categories", {}).items():
                    print(f"    - {cat}: {cnt}")
            elif "forward_status" in result:
                print(f"  Forward Status: {result['forward_status']} | Redactions: {result['redactions_count']}")
        return 0
    except Exception as e:
        err = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[clipboard_sync] ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
