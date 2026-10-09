#!/usr/bin/env python3
# AI-hint: Jensen-Shannon divergence network anomaly detector and PostgreSQL threat_events vector sink (task_456).
# AI-doc: usr/share/doc/mios/manual/ch17-defense-in-depth-hardening.md
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple


def jensen_shannon_divergence(p: Dict[str, float], q: Dict[str, float]) -> float:
    """Computes Jensen-Shannon divergence (base 2) between two discrete probability distributions.

    Returns value bounded in [0.0, 1.0].
    """
    all_keys = set(p.keys()) | set(q.keys())
    if not all_keys:
        return 0.0

    # Normalize distributions
    sum_p = sum(p.values())
    sum_q = sum(q.values())
    p_norm = {k: p.get(k, 0.0) / sum_p if sum_p > 0 else 0.0 for k in all_keys}
    q_norm = {k: q.get(k, 0.0) / sum_q if sum_q > 0 else 0.0 for k in all_keys}

    # Midpoint distribution M = 0.5 * (P + Q)
    m = {k: 0.5 * (p_norm[k] + q_norm[k]) for k in all_keys}

    # KL divergences D_KL(P || M) and D_KL(Q || M)
    kl_pm = 0.0
    kl_qm = 0.0
    for k in all_keys:
        if p_norm[k] > 0.0 and m[k] > 0.0:
            kl_pm += p_norm[k] * math.log2(p_norm[k] / m[k])
        if q_norm[k] > 0.0 and m[k] > 0.0:
            kl_qm += q_norm[k] * math.log2(q_norm[k] / m[k])

    jsd = 0.5 * (kl_pm + kl_qm)
    return max(0.0, min(1.0, jsd))


class NetAnomalyDetector:
    """Detects statistical network anomalies using JSD and sinks events to threat_events."""

    def __init__(
        self,
        baseline_ports: Optional[Dict[str, float]] = None,
        divergence_threshold: float = 0.35,
        pg_dsn: Optional[str] = None,
    ) -> None:
        # Default baseline: mostly DNS (53, 5353), HTTP/HTTPS (80, 443), Registry (5000)
        self.baseline = baseline_ports or {
            "53": 0.20,
            "5353": 0.15,
            "443": 0.50,
            "5000": 0.10,
            "80": 0.05,
        }
        self.threshold = divergence_threshold
        self.pg_dsn = pg_dsn or os.environ.get("MIOS_PG_DSN", "")

    def flow_distribution(self, flows: List[Dict[str, Any]]) -> Dict[str, float]:
        """Converts flow records into a discrete port distribution by packet count."""
        dist: Dict[str, float] = {}
        for f in flows:
            port = str(f.get("dst_port", 0))
            pkts = float(f.get("packets", 1))
            dist[port] = dist.get(port, 0.0) + pkts
        return dist

    def analyze_flow_window(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        """Analyzes a 10s flow summary window for anomalous traffic distribution shifts."""
        flows = summary.get("flows", [])
        incoming_dist = self.flow_distribution(flows)
        jsd = jensen_shannon_divergence(self.baseline, incoming_dist)
        is_anomaly = jsd > self.threshold

        result: Dict[str, Any] = {
            "timestamp": time.time(),
            "divergence": round(jsd, 4),
            "threshold": self.threshold,
            "is_anomaly": is_anomaly,
            "flow_count": len(flows),
        }

        if is_anomaly:
            severity = "critical" if jsd > 0.65 else ("high" if jsd > 0.50 else "medium")
            desc = (
                f"Statistical network flow distribution anomaly detected: "
                f"Jensen-Shannon divergence {jsd:.4f} > threshold {self.threshold:.2f}"
            )

            threat_event = {
                "event_type": "network_anomaly",
                "divergence": round(jsd, 4),
                "description": desc,
                "severity": severity,
                "flow_summary": summary,
                "emb_dim": 0,
                "embedding_pending": True,
                "stored": False,
            }

            # Store the event immediately; the OpenAI embedding backfill supplies
            # its vector and model/version stamp after successful inference.
            if self.pg_dsn:
                try:
                    import psycopg
                    with psycopg.connect(self.pg_dsn) as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO threat_events (event_type, divergence, description, flow_summary, emb_version, severity)
                                VALUES (%s, %s, %s, %s, NULL, %s)
                                """,
                                (
                                    threat_event["event_type"],
                                    threat_event["divergence"],
                                    threat_event["description"],
                                    json.dumps(summary),
                                    threat_event["severity"],
                                ),
                            )
                            conn.commit()
                            threat_event["stored"] = True
                except Exception as e:
                    threat_event["storage_error"] = str(e)

            result["threat_event"] = threat_event

        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS Network Anomaly Detector (task_456)")
    parser.add_argument("--input", help="Path to netflow summary JSON file")
    parser.add_argument("--threshold", type=float, default=0.35, help="JSD threshold")
    parser.add_argument("--mock-normal", action="store_true", help="Test with normal traffic")
    parser.add_argument("--mock-anomaly", action="store_true", help="Test with anomalous traffic")
    parser.add_argument("--json", action="store_true", help="Output JSON results")

    args = parser.parse_args()
    detector = NetAnomalyDetector(divergence_threshold=args.threshold)

    if args.mock_normal:
        summary = {
            "window_sec": 10.0,
            "flows": [
                {"dst_port": 443, "packets": 500},
                {"dst_port": 53, "packets": 200},
                {"dst_port": 5353, "packets": 150},
                {"dst_port": 5000, "packets": 100},
            ],
        }
    elif args.mock_anomaly:
        summary = {
            "window_sec": 10.0,
            "flows": [
                {"dst_port": 4444, "packets": 900}, # reverse shell / unexpected beacon
                {"dst_port": 31337, "packets": 600},
                {"dst_port": 6667, "packets": 400},
            ],
        }
    elif args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            summary = json.load(f)
    else:
        sys.stderr.write("Specify --input, --mock-normal, or --mock-anomaly\n")
        return 1

    analysis = detector.analyze_flow_window(summary)
    if args.json:
        print(json.dumps(analysis, indent=2))
    else:
        status = "ANOMALY" if analysis["is_anomaly"] else "NORMAL"
        print(f"Traffic status: {status} (JSD: {analysis['divergence']}, threshold: {args.threshold})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
