#!/usr/bin/env python3
# AI-hint: Ephemeral HMAC Macaroon minter and attenuated caveat verifier in agent-pipe (task_667, task_668).
# AI-related: usr/lib/mios/agent-pipe/macaroon_auth.py, tests/test-macaroon-auth.py, usr/lib/mios/agent-pipe/server.py
"""Ephemeral HMAC Macaroon minter and attenuated caveat verifier for MiOS agent-pipe.

Mints 60s single-use Macaroons with first-party caveats (repo=<id>, op=pull, exp=<epoch+60s>, nonce=<uuid>),
enforces strict caveat attenuation, and burns nonces on execution to prevent replay attacks.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mios-macaroon-auth")

@dataclass
class MintedMacaroon:
    token_id: str
    nonce: str
    caveats: Dict[str, str]
    expiration_epoch: float
    signature_hex: str

class MacaroonAuthManager:
    """Manages HMAC token minting, caveat checking, and single-use nonce burning."""

    def __init__(self, secret_key: bytes = b"MIOS_ROOT_MACAROON_SECRET_KEY", dry_run: bool = False) -> None:
        self.secret_key = secret_key
        self.dry_run = dry_run
        self.burned_nonces: set[str] = set()

    def mint_macaroon(self, repo_id: str, operation: str, lifetime_sec: float = 60.0) -> MintedMacaroon:
        """Mints a time-bound Macaroon with strict attenuated caveats."""
        nonce = str(uuid.uuid4())
        exp = time.time() + lifetime_sec
        caveats = {
            "repo": repo_id,
            "op": operation,
            "exp": str(exp),
            "nonce": nonce,
        }

        # Calculate HMAC signature
        h = hmac.new(self.secret_key, json.dumps(caveats, sort_keys=True).encode(), hashlib.sha256)
        sig = h.hexdigest()

        token = MintedMacaroon(
            token_id=f"mac_{nonce[:8]}",
            nonce=nonce,
            caveats=caveats,
            expiration_epoch=exp,
            signature_hex=sig,
        )
        logger.info(f"Minted Macaroon for repo '{repo_id}' op '{operation}' (exp in {lifetime_sec}s).")
        return token

    def verify_and_burn_macaroon(self, token: MintedMacaroon, requested_repo: str, requested_op: str) -> bool:
        """Validates signature, checks caveats and expiration, and burns nonce atomically."""
        # 1. Check replay / already burned
        if token.nonce in self.burned_nonces:
            logger.warning(f"Replay attack blocked: nonce {token.nonce} already burned!")
            return False

        # 2. Check expiration
        if time.time() > token.expiration_epoch:
            logger.warning("Token expired!")
            return False

        # 3. Check caveats
        if token.caveats.get("repo") != requested_repo or token.caveats.get("op") != requested_op:
            logger.warning("Caveat mismatch: operation not permitted!")
            return False

        # 4. Verify cryptographic signature
        h = hmac.new(self.secret_key, json.dumps(token.caveats, sort_keys=True).encode(), hashlib.sha256)
        if not hmac.compare_digest(h.hexdigest(), token.signature_hex):
            logger.error("Invalid HMAC signature!")
            return False

        # Burn nonce
        self.burned_nonces.add(token.nonce)
        logger.info(f"Macaroon {token.token_id} successfully verified and nonce burned.")
        return True

def main():
    mgr = MacaroonAuthManager(dry_run=True)
    tok = mgr.mint_macaroon("repo-main", "pull", 60.0)
    ok = mgr.verify_and_burn_macaroon(tok, "repo-main", "pull")
    replay = mgr.verify_and_burn_macaroon(tok, "repo-main", "pull")
    print(f"Verified: {ok}, Replay blocked: {not replay}")

if __name__ == "__main__":
    main()
