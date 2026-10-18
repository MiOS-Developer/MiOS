#!/usr/bin/env python3
# AI-hint: Interactive Human-In-The-Loop (HITL) permission escalation and destructive tool interception engine.
# AI-related: tests/test-sec.py, usr/share/mios/mios.toml
# AI-functions: ApprovalEngine, ApprovalRequest, Status, requires_approval
"""
MiOS Interactive Human-In-The-Loop (HITL) Permission Escalation and Approval Engine.

Intercepts high-risk or destructive tool execution requests, issues cryptographically
signed escalation tokens upon operator approval, and enforces strict TTL-based expiration.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Pattern, Union

class Status(str, Enum):
    """Lifecycle status for an approval request."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    def __str__(self) -> str:
        return self.value

DEFAULT_HIGH_RISK_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r|--recursive\s+--force|--force\s+--recursive)\b.*",
    r"\brm\s+-[a-zA-Z]*r\b.*",
    r"\bmkfs(\.[a-zA-Z0-9_-]+)?\s+.*",
    r"\bfdisk\s+.*",
    r"\bgdisk\s+.*",
    r"\bparted\s+.*",
    r"\bsfdisk\s+.*",
    r"\bwipefs\s+.*",
    r"\bdd\s+.*(if=|of=/dev/).*",
    r"\bbootc\s+(switch|rollback|edit)\b.*",
    r"\bcryptsetup\s+(luksFormat|luksErase|luksKillSlot|erase)\b.*",
    r"\biptables\s+.*(-F|--flush|-X|--delete-chain).*",
    r"\bip6tables\s+.*(-F|--flush|-X|--delete-chain).*",
    r"\bnft\s+(flush|delete)\s+.*",
    r"\blvremove\s+.*",
    r"\bvgremove\s+.*",
    r"\bpvremove\s+.*",
    r"\bbtrfs\s+(device\s+delete|subvolume\s+delete|filesystem\s+defrag)\b.*",
    r"\bzpool\s+(destroy|split)\b.*",
    r"\bzfs\s+destroy\b.*",
    r"\b(reboot|shutdown|poweroff|halt)\b.*",
    r"\binit\s+[06]\b.*",
]

@dataclass
class ApprovalRequest:
    """Represents a pending or resolved tool execution approval request."""
    request_id: str
    tool_name: str
    command: str
    reason: Optional[str] = None
    status: Status = Status.PENDING
    created_at: float = field(default_factory=time.time)
    ttl_seconds: int = 120
    expires_at: float = 0.0
    operator: Optional[str] = None
    approved_at: Optional[float] = None
    rejected_at: Optional[float] = None
    rejection_reason: Optional[str] = None
    token: Optional[str] = None

    def __post_init__(self) -> None:
        if self.expires_at == 0.0:
            self.expires_at = self.created_at + max(0, self.ttl_seconds)
        if isinstance(self.status, str) and not isinstance(self.status, Status):
            try:
                self.status = Status(self.status.upper())
            except ValueError:
                self.status = Status.PENDING

    def is_expired(self, now: Optional[float] = None) -> bool:
        """Check if request TTL has expired."""
        current_time = time.time() if now is None else now
        if current_time >= self.expires_at:
            if self.status == Status.PENDING:
                self.status = Status.EXPIRED
            return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        """Convert request to JSON-serializable dictionary."""
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ApprovalRequest:
        """Construct an ApprovalRequest from a dictionary."""
        req_data = dict(data)
        if "status" in req_data:
            req_data["status"] = Status(req_data["status"])
        return cls(**req_data)

class ApprovalEngine:
    """
    Engine for evaluating risk of commands, managing human-in-the-loop approval
    lifecycles, issuing cryptographically signed tokens, and validating execution authorizations.
    """

    def __init__(
        self,
        patterns: Optional[List[str]] = None,
        ttl_seconds: int = 120,
        secret_key: Optional[bytes] = None,
        state_file: Optional[str] = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.secret_key = secret_key or secrets.token_bytes(32)
        self.state_file = state_file
        self._compiled_patterns: List[Pattern[str]] = []
        self._requests: Dict[str, ApprovalRequest] = {}

        raw_patterns = patterns if patterns is not None else DEFAULT_HIGH_RISK_PATTERNS
        for pat in raw_patterns:
            self.add_pattern(pat)

        if self.state_file and os.path.exists(self.state_file):
            self._load_state()

    def add_pattern(self, pattern: str) -> None:
        """Add and compile a regex pattern for high-risk detection."""
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
            self._compiled_patterns.append(compiled)
        except re.error as e:
            sys.stderr.write(f"Warning: Invalid regex pattern '{pattern}': {e}\n")

    def requires_approval(self, command: str) -> bool:
        """Determine whether a command contains high-risk patterns requiring operator escalation."""
        if not command or not command.strip():
            return False
        cmd_clean = command.strip()
        for pat in self._compiled_patterns:
            if pat.search(cmd_clean):
                return True
        return False

    def create_request(
        self,
        tool_name: str,
        command: str,
        reason: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> ApprovalRequest:
        """Create a new approval request in PENDING status."""
        req_ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        request_id = f"req-{secrets.token_hex(8)}"
        created_time = time.time()

        req = ApprovalRequest(
            request_id=request_id,
            tool_name=tool_name,
            command=command,
            reason=reason,
            status=Status.PENDING,
            created_at=created_time,
            ttl_seconds=req_ttl,
            expires_at=created_time + max(0, req_ttl),
        )
        self._requests[request_id] = req
        self._save_state()
        return req

    def get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        """Fetch a request by ID, updating status to EXPIRED if TTL passed while PENDING."""
        req = self._requests.get(request_id)
        if req is not None:
            req.is_expired()
        return req

    def approve(self, request_id: str, operator: str = "admin") -> str:
        """
        Approve a pending request, issuing a cryptographically signed HMAC token.
        Raises KeyError if request_id does not exist, or ValueError if request cannot be approved.
        """
        req = self.get_request(request_id)
        if req is None:
            raise KeyError(f"Approval request '{request_id}' not found.")

        if req.is_expired():
            raise ValueError(f"Approval request '{request_id}' has expired and cannot be approved.")

        if req.status != Status.PENDING:
            raise ValueError(f"Cannot approve request '{request_id}' with status {req.status.value}.")

        now = time.time()
        token = self._generate_token(req.request_id, operator, now, req.command)

        req.status = Status.APPROVED
        req.operator = operator
        req.approved_at = now
        req.token = token
        self._save_state()
        return token

    def reject(self, request_id: str, reason: Optional[str] = None) -> bool:
        """
        Reject a pending request. Returns True on success, False if already resolved or not found.
        """
        req = self.get_request(request_id)
        if req is None:
            return False

        if req.is_expired():
            return False

        if req.status != Status.PENDING:
            return False

        req.status = Status.REJECTED
        req.rejected_at = time.time()
        req.rejection_reason = reason
        self._save_state()
        return True

    def validate_token(self, request_id: str, token: str) -> bool:
        """
        Verify that a given token is authentic, cryptographically valid, matches the request,
        and that the request has not expired or been revoked.
        """
        if not request_id or not token:
            return False

        req = self.get_request(request_id)
        if req is None:
            return False

        if req.is_expired():
            return False

        if req.status != Status.APPROVED:
            return False

        if not req.token or not hmac.compare_digest(req.token, token):
            return False

        return self._verify_token_crypto(request_id, token, req.command)

    def is_executable(self, request_id: str) -> bool:
        """Check if request is currently approved and within its valid TTL."""
        req = self.get_request(request_id)
        if req is None:
            return False
        if req.is_expired():
            return False
        return req.status == Status.APPROVED

    def list_requests(self, status: Optional[Union[Status, str]] = None) -> List[ApprovalRequest]:
        """List all tracked requests, optionally filtered by status."""
        for req in self._requests.values():
            req.is_expired()

        if status is None:
            return list(self._requests.values())

        target_status = status.value if isinstance(status, Status) else str(status).upper()
        return [r for r in self._requests.values() if r.status.value == target_status]

    def purge_expired(self) -> int:
        """Purge requests that have expired. Returns number of purged requests."""
        expired_keys = [
            req_id for req_id, req in self._requests.items() if req.is_expired()
        ]
        for k in expired_keys:
            del self._requests[k]
        self._save_state()
        return len(expired_keys)

    def _generate_token(self, request_id: str, operator: str, issued_at: float, command: str) -> str:
        """Generate HMAC-SHA256 authenticated token."""
        cmd_digest = hashlib.sha256(command.encode("utf-8", "replace")).hexdigest()[:16]
        payload = f"v1:{request_id}:{operator}:{issued_at:.3f}:{cmd_digest}"
        signature = hmac.new(self.secret_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        raw_token = f"{payload}:{signature}"
        return base64.urlsafe_b64encode(raw_token.encode("utf-8")).decode("ascii").rstrip("=")

    def _verify_token_crypto(self, request_id: str, token: str, command: str) -> bool:
        """Cryptographically verify signature and payload integrity."""
        try:
            padded = token + "=" * (-len(token) % 4)
            raw_token = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            parts = raw_token.split(":")
            if len(parts) != 6:
                return False
            version, token_req_id, operator, issued_at_str, token_cmd_digest, signature = parts
            if version != "v1" or token_req_id != request_id:
                return False

            cmd_digest = hashlib.sha256(command.encode("utf-8", "replace")).hexdigest()[:16]
            if not hmac.compare_digest(cmd_digest, token_cmd_digest):
                return False

            payload = f"v1:{request_id}:{operator}:{issued_at_str}:{token_cmd_digest}"
            expected_sig = hmac.new(self.secret_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
            return hmac.compare_digest(signature, expected_sig)
        except Exception:
            return False

    def _save_state(self) -> None:
        """Persist state to file if state_file is configured."""
        if not self.state_file:
            return
        try:
            parent = os.path.dirname(self.state_file)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            data = {
                "secret_key": base64.b64encode(self.secret_key).decode("ascii"),
                "requests": {req_id: req.to_dict() for req_id, req in self._requests.items()},
            }
            tmp_file = f"{self.state_file}.tmp.{os.getpid()}"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_file, self.state_file)
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to persist approval state: {e}\n")

    def _load_state(self) -> None:
        """Load state from file if available."""
        if not self.state_file or not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "secret_key" in data:
                self.secret_key = base64.b64decode(data["secret_key"].encode("ascii"))
            if "requests" in data:
                self._requests = {
                    req_id: ApprovalRequest.from_dict(req_dict)
                    for req_id, req_dict in data["requests"].items()
                }
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to load approval state: {e}\n")

def requires_approval(command: str, patterns: Optional[List[str]] = None) -> bool:
    """Helper function to quickly check if a command requires approval."""
    engine = ApprovalEngine(patterns=patterns)
    return engine.requires_approval(command)

def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS HITL Security Approval & Escalation Engine")
    parser.add_argument("--check", type=str, help="Check if a command requires operator approval.")
    parser.add_argument("--request", action="store_true", help="Create a new approval request.")
    parser.add_argument("--tool", type=str, default="bash_exec", help="Tool name for the request.")
    parser.add_argument("--command", type=str, help="Command string requiring approval.")
    parser.add_argument("--reason", type=str, default=None, help="Reason or justification.")
    parser.add_argument("--ttl", type=int, default=120, help="TTL in seconds (default 120).")
    parser.add_argument("--approve", type=str, metavar="REQ_ID", help="Approve request ID and generate token.")
    parser.add_argument("--operator", type=str, default="admin", help="Operator username for approval.")
    parser.add_argument("--reject", type=str, metavar="REQ_ID", help="Reject request ID.")
    parser.add_argument("--validate", action="store_true", help="Validate an approval token.")
    parser.add_argument("--request-id", type=str, help="Request ID for validation or status.")
    parser.add_argument("--token", type=str, help="Approval token string to validate.")
    parser.add_argument("--status", type=str, metavar="REQ_ID", help="Query status of request ID.")
    parser.add_argument("--list", action="store_true", help="List all approval requests.")
    parser.add_argument("--filter-status", type=str, default=None, help="Filter list by status.")
    parser.add_argument("--state-file", type=str, default="/var/lib/mios/sec/approval_state.json", help="State file path.")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format.")
    args = parser.parse_args()

    engine = ApprovalEngine(state_file=args.state_file)

    if args.check:
        is_risky = engine.requires_approval(args.check)
        if args.json:
            sys.stdout.write(json.dumps({"command": args.check, "requires_approval": is_risky}) + "\n")
        else:
            sys.stdout.write(f"REQUIRES_APPROVAL: {is_risky}\n")
        return 0 if is_risky else 1

    if args.request:
        if not args.command:
            sys.stderr.write("Error: --command is required when creating a request.\n")
            return 2
        req = engine.create_request(
            tool_name=args.tool,
            command=args.command,
            reason=args.reason,
            ttl_seconds=args.ttl,
        )
        if args.json:
            sys.stdout.write(json.dumps(req.to_dict(), indent=2) + "\n")
        else:
            sys.stdout.write(f"[REQUEST_CREATED] ID={req.request_id} Status={req.status.value} TTL={req.ttl_seconds}s\n")
        return 0

    if args.approve:
        try:
            token = engine.approve(args.approve, operator=args.operator)
            if args.json:
                sys.stdout.write(json.dumps({"request_id": args.approve, "status": "APPROVED", "token": token}, indent=2) + "\n")
            else:
                sys.stdout.write(f"[APPROVED] Request {args.approve} approved by {args.operator}.\nToken: {token}\n")
            return 0
        except Exception as e:
            if args.json:
                sys.stdout.write(json.dumps({"error": str(e)}, indent=2) + "\n")
            else:
                sys.stderr.write(f"Error: {e}\n")
            return 1

    if args.reject:
        ok = engine.reject(args.reject, reason=args.reason)
        if args.json:
            sys.stdout.write(json.dumps({"request_id": args.reject, "rejected": ok}, indent=2) + "\n")
        else:
            sys.stdout.write(f"[{'REJECTED' if ok else 'REJECT_FAILED'}] Request {args.reject}\n")
        return 0 if ok else 1

    if args.validate:
        if not args.request_id or not args.token:
            sys.stderr.write("Error: --request-id and --token are required for validation.\n")
            return 2
        valid = engine.validate_token(args.request_id, args.token)
        if args.json:
            sys.stdout.write(json.dumps({"request_id": args.request_id, "valid": valid}) + "\n")
        else:
            sys.stdout.write(f"VALID: {valid}\n")
        return 0 if valid else 1

    if args.status:
        req = engine.get_request(args.status)
        if req is None:
            if args.json:
                sys.stdout.write(json.dumps({"error": "not_found", "request_id": args.status}) + "\n")
            else:
                sys.stderr.write(f"Request '{args.status}' not found.\n")
            return 1
        if args.json:
            sys.stdout.write(json.dumps(req.to_dict(), indent=2) + "\n")
        else:
            sys.stdout.write(f"Request ID: {req.request_id}\nStatus: {req.status.value}\nTool: {req.tool_name}\nCommand: {req.command}\nExpires: {time.ctime(req.expires_at)}\n")
        return 0

    if args.list:
        reqs = engine.list_requests(status=args.filter_status)
        if args.json:
            sys.stdout.write(json.dumps([r.to_dict() for r in reqs], indent=2) + "\n")
        else:
            sys.stdout.write(f"Found {len(reqs)} request(s):\n")
            for r in reqs:
                sys.stdout.write(f"- {r.request_id} | {r.status.value} | {r.tool_name} | {r.command[:40]}\n")
        return 0

    parser.print_help()
    return 0

if __name__ == "__main__":
    sys.exit(main())
