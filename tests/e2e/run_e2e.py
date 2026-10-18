#!/usr/bin/env python3
"""MiOS E2E Test Suite Runner.

CLI entry point for running opaque-box end-to-end tests across Tiers 1 to 4.
Supports filtering by tier or test pattern, and outputs TAP or JSON summaries.
"""

from __future__ import annotations

import os
import sys
import json
import argparse
from typing import List, Type

# Ensure / is in sys.path so tests can be imported hermetically
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.e2e.e2e_framework import E2ERunner, E2ETestCase

# Import Tier 1 Suites
from tests.e2e.tier1_coverage.test_t1_ssot import TestTier1SSOT
from tests.e2e.tier1_coverage.test_t1_credentials import TestTier1Credentials
from tests.e2e.tier1_coverage.test_t1_display import TestTier1Display
from tests.e2e.tier1_coverage.test_t1_xbox import TestTier1Xbox
from tests.e2e.tier1_coverage.test_t1_gates import TestTier1Gates

# Import Tier 2 Suites
from tests.e2e.tier2_boundary.test_t2_boundary_ssot import TestTier2BoundarySSOT
from tests.e2e.tier2_boundary.test_t2_boundary_credentials import TestTier2BoundaryCredentials
from tests.e2e.tier2_boundary.test_t2_boundary_display import TestTier2BoundaryDisplay
from tests.e2e.tier2_boundary.test_t2_boundary_xbox import TestTier2BoundaryXbox
from tests.e2e.tier2_boundary.test_t2_boundary_gates import TestTier2BoundaryGates

# Import Tier 3 Suite
from tests.e2e.tier3_combinations.test_t3_cross_features import TestTier3CrossFeatures

# Import Tier 4 Suite
from tests.e2e.tier4_scenarios.test_t4_scenarios import TestTier4Scenarios


ALL_SUITES: List[Type[E2ETestCase]] = [
    # Tier 1
    TestTier1SSOT,
    TestTier1Credentials,
    TestTier1Display,
    TestTier1Xbox,
    TestTier1Gates,
    # Tier 2
    TestTier2BoundarySSOT,
    TestTier2BoundaryCredentials,
    TestTier2BoundaryDisplay,
    TestTier2BoundaryXbox,
    TestTier2BoundaryGates,
    # Tier 3
    TestTier3CrossFeatures,
    # Tier 4
    TestTier4Scenarios,
]


def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS E2E Test Runner")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3, 4], help="Run tests only for the given tier")
    parser.add_argument("--pattern", type=str, default=None, help="Filter tests by name substring")
    parser.add_argument("--format", choices=["tap", "json"], default="tap", help="Output format (tap or json)")
    parser.add_argument("--output", type=str, default=None, help="Save report to file")
    parser.add_argument("--list", action="store_true", help="List all discovered tests without running")

    args = parser.parse_args()

    # Filter suites
    selected_suites = ALL_SUITES
    if args.tier is not None:
        selected_suites = [s for s in ALL_SUITES if s.tier == args.tier]

    if args.list:
        print(f"Discovered test suites (Tier filter: {args.tier or 'all'}):")
        total_methods = 0
        for cls in selected_suites:
            methods = [m for m in dir(cls) if m.startswith("test_")]
            methods.sort()
            print(f"\n[{cls.suite_name}] (Tier {cls.tier}) - {len(methods)} tests:")
            for m in methods:
                if args.pattern and args.pattern not in m:
                    continue
                print(f"  - {m}")
                total_methods += 1
        print(f"\nTotal listed tests: {total_methods}")
        return 0

    runner = E2ERunner(filter_tier=args.tier, filter_pattern=args.pattern)

    for suite_cls in selected_suites:
        runner.run_suite(suite_cls)

    summary = runner.summary()
    tap_output = runner.generate_tap()

    if args.format == "tap":
        print(tap_output)
        print("\n" + "=" * 60)
        print("MiOS E2E TEST EXECUTION SUMMARY:")
        print(f"  Total:    {summary['total']}")
        print(f"  Passed:   {summary['passed']}")
        print(f"  Failed:   {summary['failed']}")
        print(f"  Errored:  {summary['errored']}")
        print(f"  Skipped:  {summary['skipped']}")
        print(f"  Duration: {summary['duration_s']}s")
        print("\nBreakdown by Tier:")
        for t, stats in sorted(summary['by_tier'].items()):
            print(f"  Tier {t}: {stats['passed']}/{stats['total']} passed ({stats['failed']} failed, {stats['errored']} errored)")
        print("=" * 60)
    elif args.format == "json":
        json_data = {
            "summary": summary,
            "results": [r.to_dict() for r in runner.all_results],
        }
        json_str = json.dumps(json_data, indent=2)
        print(json_str)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            if args.format == "json":
                f.write(json.dumps({"summary": summary, "results": [r.to_dict() for r in runner.all_results]}, indent=2))
            else:
                f.write(tap_output)

    return 0 if summary["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
