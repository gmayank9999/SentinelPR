"""Request metrics exposed at ``/metrics`` (Prometheus text) and ``/metrics.json``.

The canary controller scrapes these to compare the stable and canary versions.
"""

from __future__ import annotations

import threading
import time
from collections import deque

LATENCY_BUCKETS_MS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)
RECENT_WINDOW = 2048


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.requests = 0
        self.errors = 0
        self.buckets = [0] * (len(LATENCY_BUCKETS_MS) + 1)
        self.latency_sum_ms = 0.0
        self.by_route: dict[str, list[int]] = {}
        self._recent: deque[float] = deque(maxlen=RECENT_WINDOW)

    def observe(self, route: str, status: int, latency_ms: float) -> None:
        with self._lock:
            self.requests += 1
            is_error = status >= 500
            self.errors += is_error
            self.latency_sum_ms += latency_ms
            self._recent.append(latency_ms)
            for i, bound in enumerate(LATENCY_BUCKETS_MS):
                if latency_ms <= bound:
                    self.buckets[i] += 1
                    break
            else:
                self.buckets[-1] += 1
            counts = self.by_route.setdefault(route, [0, 0])
            counts[0] += 1
            counts[1] += is_error

    def percentile(self, q: float) -> float:
        with self._lock:
            values = sorted(self._recent)
        if not values:
            return 0.0
        index = min(len(values) - 1, max(0, round(q * (len(values) - 1))))
        return round(values[index], 2)

    def snapshot(self) -> dict:
        with self._lock:
            requests, errors = self.requests, self.errors
            mean = self.latency_sum_ms / requests if requests else 0.0
            routes = {k: {"requests": v[0], "errors": v[1]} for k, v in self.by_route.items()}
        return {
            "uptime_s": round(time.time() - self.started_at, 1),
            "requests": requests,
            "errors": errors,
            "error_rate": round(errors / requests, 4) if requests else 0.0,
            "latency_ms": {"mean": round(mean, 2), "p50": self.percentile(0.5), "p95": self.percentile(0.95)},
            "routes": routes,
        }

    def prometheus(self, version: str) -> str:
        with self._lock:
            lines = [
                "# TYPE unierp_requests_total counter",
                f'unierp_requests_total{{version="{version}"}} {self.requests}',
                "# TYPE unierp_errors_total counter",
                f'unierp_errors_total{{version="{version}"}} {self.errors}',
                "# TYPE unierp_request_latency_ms histogram",
            ]
            cumulative = 0
            for bound, count in zip(LATENCY_BUCKETS_MS, self.buckets):
                cumulative += count
                lines.append(f'unierp_request_latency_ms_bucket{{le="{bound}",version="{version}"}} {cumulative}')
            cumulative += self.buckets[-1]
            lines.append(f'unierp_request_latency_ms_bucket{{le="+Inf",version="{version}"}} {cumulative}')
            lines.append(f'unierp_request_latency_ms_sum{{version="{version}"}} {self.latency_sum_ms:.2f}')
            lines.append(f'unierp_request_latency_ms_count{{version="{version}"}} {self.requests}')
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        self.__init__()
