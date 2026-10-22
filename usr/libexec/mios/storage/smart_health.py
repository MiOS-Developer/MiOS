#!/usr/bin/env python3
# AI-hint: Predictive S.M.A.R.T. drive health monitor and automated CephFS evacuation manager (task_583, task_584).
# AI-related: usr/libexec/mios/storage/disk_health.py, usr/libexec/mios/storage/smart_health.py, tests/test-storage.py
"""Predictive S.M.A.R.T. drive health monitor proxy module for MiOS."""

from __future__ import annotations

from disk_health import (
    DEFAULT_WEAR_PERCENT_THRESHOLD,
    DEFAULT_SPARE_PERCENT_THRESHOLD,
    DEFAULT_TEMP_THRESHOLD_C,
    DriveHealth,
    SmartHealthMonitor,
    main,
)

__all__ = [
    "DEFAULT_WEAR_PERCENT_THRESHOLD",
    "DEFAULT_SPARE_PERCENT_THRESHOLD",
    "DEFAULT_TEMP_THRESHOLD_C",
    "DriveHealth",
    "SmartHealthMonitor",
    "main",
]

if __name__ == "__main__":
    main()
