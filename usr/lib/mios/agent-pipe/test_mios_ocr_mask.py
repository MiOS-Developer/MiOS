#!/usr/bin/env python3
# AI-hint: CI agent-pipe unit test runner for mios_ocr_mask (task_484, task_2717).
# AI-doc: usr/share/doc/mios/manual/ch85-ocr-credential-masking.md
"""Unit test suite for mios_ocr_mask.py under ci.globs.agent-pipe."""

from __future__ import annotations

import os
import subprocess
import sys

cur = os.path.abspath(__file__)
while cur and cur != os.path.dirname(cur):
    if os.path.isfile(os.path.join(cur, "tests/test-ocr-mask.py")):
        break
    cur = os.path.dirname(cur)
REPO_ROOT = cur
TEST_SCRIPT = os.path.join(REPO_ROOT, "tests/test-ocr-mask.py")


def test_ocr_mask_suite() -> None:
    res = subprocess.run([sys.executable, TEST_SCRIPT], capture_output=True, text=True, check=False)
    if res.returncode != 0:
        print(res.stdout)
        print(res.stderr, file=sys.stderr)
        raise AssertionError(f"test-ocr-mask.py failed with returncode {res.returncode}")
    print(res.stdout)


if __name__ == "__main__":
    test_ocr_mask_suite()
