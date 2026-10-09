#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# AI-hint: Continuous Linux PSI (Pressure Stall Information) sampler and throttling monitor (task_429).
# AI-doc: usr/share/doc/mios/manual/ch04-the-agentic-ai-stack.md
"""Linux PSI (Pressure Stall Information) sampler and throttling monitor for agent-pipe.

Continuously samples /proc/pressure/cpu, /proc/pressure/memory, and /proc/pressure/io.
Emits WARNING events when any resource's `some avg10 > 40.0`.
Emits CRITICAL throttling events when any resource's `some avg10 > 70.0`.
Signals throttling state to inference workers and admission scheduler with safe
hysteresis margins to prevent alarm flapping.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import functools
import logging
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("mios-agent-pipe.psi")

_LIB = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
import mios_toml  # noqa: E402 -- the one layered mios.toml loader (Law 13)


@functools.lru_cache(maxsize=1)
def _psi_table() -> Dict[str, Any]:
    data = mios_toml.load_merged(mios_toml.layer_paths())
    return dict(data.get("psi") or {})


def psi_setting(key: str) -> str:
    """[psi].<key>: the unit's projected MIOS_PSI_* value, else the layered mios.toml.

    Unresolved raises: a sampler with invented thresholds is worse than none."""
    v = os.environ.get("MIOS_PSI_" + key.upper(), "")
    if v:
        return v
    v = _psi_table().get(key)
    if v is None:
        raise KeyError(f"[psi].{key} is not set in mios.toml")
    return str(v).lower() if isinstance(v, bool) else str(v)


DEFAULT_PROC_DIR = "/proc/pressure"  # kernel ABI, not a tunable
DEFAULT_SAMPLE_INTERVAL_S = float(psi_setting("sample_interval_ms")) / 1000.0
DEFAULT_WARNING_THRESHOLD = float(psi_setting("warning_threshold"))
DEFAULT_CRITICAL_THRESHOLD = float(psi_setting("critical_threshold"))
DEFAULT_HYSTERESIS_CLEAR_PCT = DEFAULT_WARNING_THRESHOLD - float(psi_setting("clear_margin_pct"))
DEFAULT_HYSTERESIS_CRIT_CLEAR_PCT = DEFAULT_CRITICAL_THRESHOLD - float(psi_setting("clear_margin_pct"))

RESOURCES = ("cpu", "memory", "io")


@dataclasses.dataclass(frozen=True)
class PsiMetric:
    """Parsed PSI metric values for one pressure type (some or full)."""
    avg10: float = 0.0
    avg60: float = 0.0
    avg300: float = 0.0
    total: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "avg10": round(self.avg10, 2),
            "avg60": round(self.avg60, 2),
            "avg300": round(self.avg300, 2),
            "total": self.total,
        }


@dataclasses.dataclass(frozen=True)
class PsiResourcePressure:
    """Pressure metrics for a specific resource (cpu, memory, or io)."""
    resource: str
    some: PsiMetric = dataclasses.field(default_factory=PsiMetric)
    full: Optional[PsiMetric] = None

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "resource": self.resource,
            "some": self.some.to_dict(),
            "full": self.full.to_dict() if self.full is not None else None,
        }
        return res


@dataclasses.dataclass(frozen=True)
class PsiTrigger:
    """Details of a threshold trigger on a resource metric."""
    resource: str
    metric: str
    value: float
    threshold: float
    level: str  # "WARNING" or "CRITICAL"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource": self.resource,
            "metric": self.metric,
            "value": round(self.value, 2),
            "threshold": round(self.threshold, 2),
            "level": self.level,
        }


@dataclasses.dataclass(frozen=True)
class PsiSample:
    """A point-in-time sample of all system pressure stall information."""
    timestamp: float
    resources: Dict[str, PsiResourcePressure]
    level: str  # "NORMAL", "WARNING", "CRITICAL"
    throttled: bool
    triggers: List[PsiTrigger]
    available: bool = True
    highest_resource: Optional[str] = None
    highest_avg10: float = 0.0

    @property
    def cpu(self) -> PsiResourcePressure:
        return self.resources.get("cpu", create_fallback_resource("cpu"))

    @property
    def memory(self) -> PsiResourcePressure:
        return self.resources.get("memory", create_fallback_resource("memory"))

    @property
    def io(self) -> PsiResourcePressure:
        return self.resources.get("io", create_fallback_resource("io"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "timestamp": self.timestamp,
            "level": self.level,
            "throttled": self.throttled,
            "highest_resource": self.highest_resource,
            "highest_avg10": round(self.highest_avg10, 2),
            "triggers": [t.to_dict() for t in self.triggers],
            "resources": {k: v.to_dict() for k, v in self.resources.items()},
            "cpu": self.cpu.to_dict(),
            "memory": self.memory.to_dict(),
            "io": self.io.to_dict(),
        }


def parse_psi_line(line: str) -> Tuple[Optional[str], Optional[PsiMetric]]:
    """Parse a single line from a /proc/pressure/* file.

    Format:
        some avg10=0.00 avg60=0.00 avg300=0.00 total=0
        full avg10=0.00 avg60=0.00 avg300=0.00 total=0

    Returns:
        (line_type, PsiMetric) or (None, None) if the line cannot be parsed.
    """
    if not line:
        return None, None
    s = line.strip()
    if not s or s.startswith("#"):
        return None, None

    parts = s.split()
    if not parts:
        return None, None

    line_type = parts[0].lower()
    if line_type not in ("some", "full"):
        return None, None

    parsed: Dict[str, Any] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip().lower()
        v = v.strip()
        try:
            if k in ("avg10", "avg60", "avg300"):
                parsed[k] = float(v)
            elif k == "total":
                parsed[k] = int(v)
        except (ValueError, TypeError):
            continue

    if "avg10" not in parsed:
        return None, None

    metric = PsiMetric(
        avg10=parsed.get("avg10", 0.0),
        avg60=parsed.get("avg60", 0.0),
        avg300=parsed.get("avg300", 0.0),
        total=parsed.get("total", 0),
    )
    return line_type, metric


def parse_psi_content(content: str, resource: str = "unknown") -> PsiResourcePressure:
    """Parse full multi-line text content of a /proc/pressure/* file.

    Returns:
        PsiResourcePressure containing parsed `some` and `full` metrics.
    """
    some_metric: PsiMetric = PsiMetric(0.0, 0.0, 0.0, 0)
    full_metric: Optional[PsiMetric] = None

    for line in content.splitlines():
        line_type, metric = parse_psi_line(line)
        if line_type is None or metric is None:
            continue
        if line_type == "some":
            some_metric = metric
        elif line_type == "full":
            full_metric = metric

    return PsiResourcePressure(
        resource=resource,
        some=some_metric,
        full=full_metric,
    )



def read_psi_file(path: str, resource: str) -> Optional[PsiResourcePressure]:
    """Read and parse a PSI file from disk if accessible."""
    try:
        if not os.path.exists(path) or os.path.isdir(path):
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return parse_psi_content(content, resource=resource)
    except Exception as e:
        log.debug("Failed reading PSI file %s: %s", path, e)
        return None


def is_psi_available(proc_dir: str = DEFAULT_PROC_DIR) -> bool:
    """Check if /proc/pressure is supported and readable on this host."""
    try:
        cpu_path = os.path.join(proc_dir, "cpu")
        mem_path = os.path.join(proc_dir, "memory")
        io_path = os.path.join(proc_dir, "io")
        return (
            (os.path.exists(cpu_path) and os.path.isfile(cpu_path))
            or (os.path.exists(mem_path) and os.path.isfile(mem_path))
            or (os.path.exists(io_path) and os.path.isfile(io_path))
        )
    except Exception:
        return False


def create_fallback_resource(resource: str) -> PsiResourcePressure:
    """Generate safe zero-pressure metrics when /proc/pressure is absent."""
    zero_metric = PsiMetric(avg10=0.0, avg60=0.0, avg300=0.0, total=0)
    return PsiResourcePressure(
        resource=resource,
        some=zero_metric,
        full=zero_metric if resource != "cpu" else None,
    )


class PsiSampler:
    """Continuous Linux PSI pressure stall sampler and load-shedding monitor.

    Monitors cpu, memory, and io pressure metrics every `sample_interval_s` seconds.
    Emits WARNING when `some avg10 > warning_threshold` (default 40.0%).
    Emits CRITICAL throttling events when `some avg10 > critical_threshold` (default 70.0%).
    Includes hysteresis to prevent alarm flapping.
    """

    @staticmethod
    def throttle_status() -> int:
        """HTTP status for requests shed under critical pressure ([psi].throttle_status)."""
        return int(psi_setting("throttle_status"))

    def __init__(
        self,
        proc_dir: str = DEFAULT_PROC_DIR,
        sample_interval_s: float = DEFAULT_SAMPLE_INTERVAL_S,
        warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
        critical_threshold: float = DEFAULT_CRITICAL_THRESHOLD,
        hysteresis_clear_pct: float = DEFAULT_HYSTERESIS_CLEAR_PCT,
        hysteresis_crit_clear_pct: float = DEFAULT_HYSTERESIS_CRIT_CLEAR_PCT,
        mock_provider: Optional[Callable[[str], Optional[PsiResourcePressure]]] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self.proc_dir = proc_dir
        self.sample_interval_s = max(0.01, float(sample_interval_s))
        self.warning_threshold = float(warning_threshold)
        self.critical_threshold = float(critical_threshold)
        self.hysteresis_clear_pct = float(hysteresis_clear_pct)
        self.hysteresis_crit_clear_pct = float(hysteresis_crit_clear_pct)
        self.mock_provider = mock_provider
        
        env_enabled = psi_setting("enable").lower() not in ("false", "0", "no", "off")
        self.enabled = enabled if enabled is not None else env_enabled

        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._listeners: List[Callable[..., Any]] = []
        self._throttle_callbacks: List[Callable[[bool, Dict[str, Any]], Any]] = []

        self._current_level: str = "NORMAL"
        self._is_throttled: bool = False
        self._consecutive_low_samples: int = 0
        self._last_sample: Optional[PsiSample] = None
        self._last_event: Optional[Dict[str, Any]] = None
        self._sample_count: int = 0
        self._history: collections.deque = collections.deque(maxlen=120)

        # In-memory mock overrides for testing or simulation
        self._mock_data: Dict[str, PsiResourcePressure] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def available(self) -> bool:
        """True if PSI is available on this host or mock data is injected."""
        if not self.enabled:
            return False
        return bool(self._mock_data or is_psi_available(self.proc_dir))

    @property
    def current_level(self) -> str:
        return self._current_level

    @property
    def latest_metrics(self) -> PsiSample:
        """Most recent sample metrics."""
        if self._last_sample is not None:
            return self._last_sample
        return self.sample_once()

    def is_throttled(self) -> bool:
        if not self.enabled:
            return False
        return self._is_throttled

    def is_critical(self) -> bool:
        """True if current pressure stall level is CRITICAL."""
        if not self.enabled:
            return False
        return self._current_level == "CRITICAL" or self._is_throttled

    def is_warning(self) -> bool:
        """True if current pressure stall level is WARNING or CRITICAL."""
        if not self.enabled:
            return False
        return self._current_level in ("WARNING", "CRITICAL")

    def get_shed_reason(self) -> Optional[str]:
        """Descriptive reason string if critical pressure threshold is breached."""
        if not self.is_critical():
            return None
        if self._last_sample and self._last_sample.triggers:
            crit_triggers = [t for t in self._last_sample.triggers if t.level == "CRITICAL"]
            if crit_triggers:
                t = crit_triggers[0]
                return f"Linux PSI critical pressure stall detected on {t.resource} ({t.metric}={t.value:.1f}% > {t.threshold:.1f}%)"
            t = self._last_sample.triggers[0]
            return f"Linux PSI critical pressure stall detected on {t.resource} ({t.metric}={t.value:.1f}% > {t.threshold:.1f}%)"
        if self._last_sample and self._last_sample.highest_resource:
            return f"Linux PSI critical pressure stall detected on {self._last_sample.highest_resource} (some avg10={self._last_sample.highest_avg10:.1f}% > {self.critical_threshold:.1f}%)"
        return "Linux PSI critical pressure stall detected"

    def get_metrics_dict(self) -> Dict[str, Any]:
        """Return dict representation of current PSI metrics."""
        sample = self._last_sample if self._last_sample is not None else self.sample_once()
        d = sample.to_dict()
        d["thresholds"] = {
            "warning": self.warning_threshold,
            "critical": self.critical_threshold,
            "hysteresis_clear": self.hysteresis_clear_pct,
            "hysteresis_crit_clear": self.hysteresis_crit_clear_pct,
            "sample_interval_s": self.sample_interval_s,
        }
        d["available"] = self.available
        d["history"] = [s.to_dict() for s in list(self._history)[-10:]]
        return d

    def add_listener(self, callback: Callable[..., Any]) -> None:
        """Register a callback for PSI transition events."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def register_throttle_callback(self, callback: Callable[[bool, Dict[str, Any]], Any]) -> None:
        """Register a callback invoked when throttling state is signaled."""
        if callback not in self._throttle_callbacks:
            self._throttle_callbacks.append(callback)

    def set_mock_pressure(
        self,
        resource: str,
        some_avg10: float,
        some_avg60: float = 0.0,
        some_avg300: float = 0.0,
        total: int = 0,
        full_avg10: Optional[float] = None,
    ) -> None:
        """Set mock pressure metrics for a resource (for unit tests / simulation)."""
        some_metric = PsiMetric(
            avg10=float(some_avg10),
            avg60=float(some_avg60),
            avg300=float(some_avg300),
            total=int(total),
        )
        full_metric = None
        if full_avg10 is not None:
            full_metric = PsiMetric(
                avg10=float(full_avg10),
                avg60=float(some_avg60),
                avg300=float(some_avg300),
                total=int(total),
            )
        self._mock_data[resource] = PsiResourcePressure(
            resource=resource,
            some=some_metric,
            full=full_metric,
        )

    def read_resource(self, resource: str) -> PsiResourcePressure:
        """Read pressure metrics for one resource, using mock, procfs, or fallback."""
        if not self.enabled:
            return create_fallback_resource(resource)

        # 1. Direct mock data override
        if resource in self._mock_data:
            return self._mock_data[resource]

        # 2. Custom mock provider
        if self.mock_provider is not None:
            try:
                res = self.mock_provider(resource)
                if res is not None:
                    return res
            except Exception as e:
                log.debug("Mock provider error for %s: %s", resource, e)

        # 3. Read from procfs
        path = os.path.join(self.proc_dir, resource)
        parsed = read_psi_file(path, resource)
        if parsed is not None:
            return parsed

        # 4. Fallback when procfs unavailable (Windows, WSL without PSI, containers)
        return create_fallback_resource(resource)

    def sample_once(self) -> PsiSample:
        """Perform a single sample across cpu, memory, and io resources."""
        now = time.time()
        resources: Dict[str, PsiResourcePressure] = {}
        triggers: List[PsiTrigger] = []

        is_avail = self.available

        max_avg10 = 0.0
        worst_resource: Optional[str] = None

        for res_name in RESOURCES:
            rp = self.read_resource(res_name)
            resources[res_name] = rp

            val = rp.some.avg10
            if val > max_avg10:
                max_avg10 = val
                worst_resource = res_name

            if val > self.critical_threshold:
                triggers.append(PsiTrigger(
                    resource=res_name,
                    metric="some.avg10",
                    value=val,
                    threshold=self.critical_threshold,
                    level="CRITICAL",
                ))
            elif val > self.warning_threshold:
                triggers.append(PsiTrigger(
                    resource=res_name,
                    metric="some.avg10",
                    value=val,
                    threshold=self.warning_threshold,
                    level="WARNING",
                ))

        # Evaluate state with hysteresis
        curr = self._current_level
        if not is_avail:
            new_level = "NORMAL"
            self._consecutive_low_samples = 0
        elif curr == "NORMAL":
            if max_avg10 > self.critical_threshold:
                new_level = "CRITICAL"
                self._consecutive_low_samples = 0
            elif max_avg10 > self.warning_threshold:
                new_level = "WARNING"
                self._consecutive_low_samples = 0
            else:
                new_level = "NORMAL"
        elif curr == "WARNING":
            if max_avg10 > self.critical_threshold:
                new_level = "CRITICAL"
                self._consecutive_low_samples = 0
            elif max_avg10 <= self.hysteresis_clear_pct:
                self._consecutive_low_samples += 1
                if self._consecutive_low_samples >= 2:
                    new_level = "NORMAL"
                    self._consecutive_low_samples = 0
                else:
                    new_level = "WARNING"
            else:
                self._consecutive_low_samples = 0
                new_level = "WARNING"
        elif curr == "CRITICAL":
            if max_avg10 <= self.hysteresis_clear_pct:
                self._consecutive_low_samples += 1
                if self._consecutive_low_samples >= 2:
                    new_level = "NORMAL"
                    self._consecutive_low_samples = 0
                else:
                    new_level = "CRITICAL"
            elif max_avg10 <= self.hysteresis_crit_clear_pct:
                self._consecutive_low_samples = 0
                new_level = "WARNING"
            else:
                self._consecutive_low_samples = 0
                new_level = "CRITICAL"
        else:
            new_level = "NORMAL"

        throttled = (new_level == "CRITICAL")

        sample = PsiSample(
            timestamp=now,
            resources=resources,
            level=new_level,
            throttled=throttled,
            triggers=triggers,
            available=is_avail,
            highest_resource=worst_resource,
            highest_avg10=max_avg10,
        )

        self._last_sample = sample
        self._sample_count += 1
        self._history.append(sample)

        prev_level = self._current_level
        prev_throttled = self._is_throttled

        self._current_level = new_level
        self._is_throttled = throttled

        # Event emission on transitions or high alerts
        state_changed = (prev_level != new_level)
        throttling_changed = (prev_throttled != throttled)

        if state_changed or throttling_changed or new_level in ("WARNING", "CRITICAL"):
            event = self._build_event(sample, prev_level, prev_throttled)
            self._last_event = event
            self._notify_listeners(event, sample, prev_level, new_level, max_avg10)

            if throttling_changed or throttled:
                self._notify_throttle_callbacks(throttled, event)

        return sample

    def sample(self) -> PsiSample:
        """Alias to sample_once()."""
        return self.sample_once()

    def _build_event(self, sample: PsiSample, prev_level: str, prev_throttled: bool) -> Dict[str, Any]:
        """Construct a standardized PSI event payload."""
        trigger_descs = [
            f"{t.resource} {t.metric}={t.value:.1f}% > {t.threshold:.1f}% ({t.level})"
            for t in sample.triggers
        ]
        if sample.level == "CRITICAL":
            msg = f"PSI CRITICAL pressure detected (throttling active): {', '.join(trigger_descs)}"
        elif sample.level == "WARNING":
            msg = f"PSI WARNING elevated pressure detected: {', '.join(trigger_descs)}"
        else:
            msg = f"PSI pressure normalized (recovered from {prev_level})"

        return {
            "type": "psi_throttling" if sample.throttled else ("psi_warning" if sample.level == "WARNING" else "psi_normal"),
            "level": sample.level,
            "throttled": sample.throttled,
            "timestamp": sample.timestamp,
            "triggers": [t.to_dict() for t in sample.triggers],
            "resources": {k: v.to_dict() for k, v in sample.resources.items()},
            "previous_level": prev_level,
            "state_changed": prev_level != sample.level,
            "throttling_changed": prev_throttled != sample.throttled,
            "message": msg,
        }

    def _notify_listeners(
        self,
        event: Dict[str, Any],
        sample: PsiSample,
        prev_level: str,
        new_level: str,
        worst_val: float,
    ) -> None:
        """Notify registered event listeners (flexible signature support)."""
        for listener in list(self._listeners):
            try:
                # Support both (event) and (old_level, new_level, worst_val, sample)
                try:
                    res = listener(event)
                except TypeError:
                    res = listener(prev_level, new_level, worst_val, sample)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
            except Exception as e:
                log.warning("PSI listener callback failed: %s", e)

    def _notify_throttle_callbacks(self, throttled: bool, event: Dict[str, Any]) -> None:
        """Notify registered throttle callbacks."""
        for cb in list(self._throttle_callbacks):
            try:
                res = cb(throttled, event)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
            except Exception as e:
                log.warning("PSI throttle callback failed: %s", e)

    async def run_loop(self) -> None:
        """Continuous background sampling loop."""
        self._running = True
        log.info(
            "Starting PSI sampler loop (interval=%.2fs, warn=%.1f%%, crit=%.1f%%, proc_dir=%s)",
            self.sample_interval_s,
            self.warning_threshold,
            self.critical_threshold,
            self.proc_dir,
        )
        try:
            while self._running:
                try:
                    self.sample_once()
                except Exception as e:
                    log.error("Unhandled error in PSI sample: %s", e, exc_info=True)
                await asyncio.sleep(self.sample_interval_s)
        except asyncio.CancelledError:
            log.info("PSI sampler loop cancelled")
            raise
        finally:
            self._running = False

    async def start(self) -> None:
        """Start the background sampling task."""
        if self._running or (self._task is not None and not self._task.done()):
            return
        self.sample_once()
        self._running = True
        self._task = asyncio.create_task(self.run_loop())

    async def stop(self) -> None:
        """Stop the background sampling task cleanly."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._task = None

    def get_status(self) -> Dict[str, Any]:
        """Return comprehensive status dictionary."""
        return {
            "running": self._running,
            "proc_dir": self.proc_dir,
            "psi_supported": is_psi_available(self.proc_dir),
            "sample_interval_s": self.sample_interval_s,
            "warning_threshold": self.warning_threshold,
            "critical_threshold": self.critical_threshold,
            "current_level": self._current_level,
            "is_throttled": self._is_throttled,
            "sample_count": self._sample_count,
            "last_sample": self._last_sample.to_dict() if self._last_sample else None,
            "last_event": self._last_event,
        }


# Global singleton instance for agent-pipe
_GLOBAL_MONITOR: Optional[PsiSampler] = None

# Aliases for cross-module and architectural compatibility
PSIMonitor = PsiSampler
PSISample = PsiSample
PSIMetric = PsiMetric
PSIMetrics = PsiSample
PSIResourceRecord = PsiResourcePressure
parse_pressure_line = parse_psi_line
parse_pressure_text = parse_psi_content


def get_psi_monitor() -> PsiSampler:
    """Get or create the global PSI monitor singleton."""
    global _GLOBAL_MONITOR
    if _GLOBAL_MONITOR is None:
        _GLOBAL_MONITOR = PsiSampler(
            proc_dir=DEFAULT_PROC_DIR,
            sample_interval_s=DEFAULT_SAMPLE_INTERVAL_S,
            warning_threshold=DEFAULT_WARNING_THRESHOLD,
            critical_threshold=DEFAULT_CRITICAL_THRESHOLD,
        )
    return _GLOBAL_MONITOR

