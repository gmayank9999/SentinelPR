"""Rollout controller: 10% -> 25% -> 50% -> 100%, rolling back on the first SLO breach."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field

import httpx

from sentinel.canary.monitor import SLO, Snapshot, evaluate, reading
from sentinel.canary.traffic import run_traffic

log = logging.getLogger(__name__)
STEPS = (0.10, 0.25, 0.50, 1.00)
MAX_EXTENSIONS = 3  # extra step windows allowed when a step has too little canary traffic to judge


@dataclass
class StepRecord:
    weight: float
    started: float
    ended: float
    healthy: bool
    inconclusive: bool
    breaches: list[str]
    stable: dict
    canary: dict
    requests_sent: int


@dataclass
class RolloutReport:
    outcome: str  # promoted | rolled_back
    steps: list[StepRecord] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    started: float = 0.0
    ended: float = 0.0
    time_to_detection_s: float | None = None
    canary_requests: int = 0
    total_requests: int = 0
    slo: dict = field(default_factory=dict)
    labels: dict = field(default_factory=dict)

    @property
    def exposed_share(self) -> float:
        """Share of all user requests that the canary served before the decision."""
        return round(self.canary_requests / self.total_requests, 4) if self.total_requests else 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["exposed_share"] = self.exposed_share
        return data


class RolloutController:
    def __init__(self, proxy_url: str, stable_url: str, canary_url: str, *, step_seconds: float = 20.0,
                 steps: tuple[float, ...] = STEPS, slo: SLO | None = None, users: int = 8, rate_per_user: float = 12.0):
        self.proxy_url, self.stable_url, self.canary_url = proxy_url, stable_url, canary_url
        self.step_seconds, self.steps = step_seconds, steps
        self.slo = slo or SLO()
        self.users, self.rate = users, rate_per_user

    def _set_weight(self, weight: float) -> None:
        httpx.put(f"{self.proxy_url}/__canary/weight", json={"weight": weight}, timeout=5).raise_for_status()

    def run(self) -> RolloutReport:
        report = RolloutReport("promoted", started=time.time(), slo=asdict(self.slo))
        for weight in self.steps:
            self._set_weight(weight)
            report.events.append({"t": time.time(), "type": "shift", "weight": weight})
            before_s, before_c = Snapshot.fetch(self.stable_url), Snapshot.fetch(self.canary_url)
            started = time.time()
            sent = 0
            for _ in range(1 + MAX_EXTENSIONS):
                sent += run_traffic(self.proxy_url, self.step_seconds, users=self.users, rate_per_user=self.rate).sent
                after_s, after_c = Snapshot.fetch(self.stable_url), Snapshot.fetch(self.canary_url)
                stable, canary = reading(before_s, after_s), reading(before_c, after_c)
                verdict = evaluate(stable, canary, self.slo)
                if not verdict.inconclusive:
                    break  # otherwise keep the weight and gather more canary traffic before judging
            report.canary_requests += canary.requests
            report.total_requests += stable.requests + canary.requests
            report.steps.append(StepRecord(weight, started, time.time(), verdict.healthy, verdict.inconclusive, verdict.breaches,
                                           asdict(stable), asdict(canary), sent))
            log.info("step %.0f%%: stable %s | canary %s | %s", weight * 100, asdict(stable), asdict(canary),
                     "healthy" if verdict.healthy else "; ".join(verdict.breaches))
            if not verdict.healthy:
                self._set_weight(0.0)
                report.outcome = "rolled_back"
                report.time_to_detection_s = round(time.time() - report.started, 2)
                report.events.append({"t": time.time(), "type": "rollback", "weight": 0.0, "reason": verdict.breaches})
                break
        else:
            report.events.append({"t": time.time(), "type": "promote", "weight": 1.0})
        report.ended = time.time()
        return report
