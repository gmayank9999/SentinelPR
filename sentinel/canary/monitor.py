"""SLO monitor: compare canary against stable over one rollout step.

Each version exposes cumulative counters at ``/metrics.json``. The monitor snapshots both
before and after a step and compares the *deltas*, so every step is judged only on the
traffic it actually received.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx


@dataclass
class SLO:
    max_error_rate: float = 0.01          # absolute ceiling for the canary
    max_error_rate_delta: float = 0.005   # canary may not exceed stable by more than this
    p95_ratio: float = 1.5                # canary p95 may not exceed stable p95 by this factor...
    p95_slack_ms: float = 50.0            # ...plus this much slack (protects very fast services)
    min_requests: int = 20                # below this, a step is inconclusive


@dataclass
class Snapshot:
    requests: int
    errors: int
    p95_ms: float
    mean_ms: float

    @classmethod
    def fetch(cls, url: str) -> "Snapshot":
        data = httpx.get(f"{url.rstrip('/')}/metrics.json", timeout=5).json()
        return cls(data["requests"], data["errors"], data["latency_ms"]["p95"], data["latency_ms"]["mean"])


@dataclass
class StepReading:
    requests: int
    errors: int
    error_rate: float
    p95_ms: float


@dataclass
class Verdict:
    healthy: bool
    stable: StepReading
    canary: StepReading
    breaches: list[str] = field(default_factory=list)
    inconclusive: bool = False


def reading(before: Snapshot, after: Snapshot) -> StepReading:
    requests = after.requests - before.requests
    errors = after.errors - before.errors
    return StepReading(requests, errors, round(errors / requests, 4) if requests else 0.0, after.p95_ms)


def evaluate(stable: StepReading, canary: StepReading, slo: SLO) -> Verdict:
    breaches = []
    if canary.requests < slo.min_requests:
        return Verdict(True, stable, canary, [f"only {canary.requests} canary requests"], inconclusive=True)
    if canary.error_rate > slo.max_error_rate:
        breaches.append(f"canary error rate {canary.error_rate:.2%} > {slo.max_error_rate:.2%}")
    if canary.error_rate > stable.error_rate + slo.max_error_rate_delta:
        breaches.append(f"canary error rate {canary.error_rate:.2%} vs stable {stable.error_rate:.2%}")
    limit = stable.p95_ms * slo.p95_ratio + slo.p95_slack_ms
    if canary.p95_ms > limit:
        breaches.append(f"canary p95 {canary.p95_ms:.0f}ms > {limit:.0f}ms (stable {stable.p95_ms:.0f}ms)")
    return Verdict(not breaches, stable, canary, breaches)
