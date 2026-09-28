"""Synthetic user traffic against UniERP.

A small async load generator with a realistic mix of reads (profiles, GPA, transcripts,
fees) and occasional writes. Locust users with the same mix live in ``locustfile.py`` for
heavier runs.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field

import httpx

STUDENTS = ["S2024001", "S2024002", "S2024003", "S2024004", "S2025001", "S2025002", "S2025003", "S2026001"]

# (weight, method, path template, body)
ENDPOINTS = [
    (20, "GET", "/students/{sid}", None),
    (18, "GET", "/students/{sid}/gpa", None),
    (12, "GET", "/students/{sid}/transcript", None),
    (10, "GET", "/students/{sid}/credits", None),
    (10, "GET", "/students/{sid}/fees/2026-FALL", None),
    (8, "GET", "/students/{sid}/standing", None),
    (6, "GET", "/students/{sid}/eligible-courses", None),
    (6, "GET", "/courses", None),
    (5, "POST", "/fees/late-fee", {"amount": "25000", "due_date": "2026-09-01", "paid_on": "2026-09-12"}),
    (5, "POST", "/exams/grade", {"internal": 71, "final": 64}),
]


@dataclass
class TrafficStats:
    sent: int = 0
    errors: int = 0
    by_target: dict[str, int] = field(default_factory=dict)


async def _user(client: httpx.AsyncClient, deadline: float, rate: float, stats: TrafficStats, rng: random.Random) -> None:
    weights = [e[0] for e in ENDPOINTS]
    while time.monotonic() < deadline:
        _, method, template, body = rng.choices(ENDPOINTS, weights)[0]
        path = template.format(sid=rng.choice(STUDENTS))
        try:
            response = await client.request(method, path, json=body)
            target = response.headers.get("X-Canary-Target", "direct")
            stats.by_target[target] = stats.by_target.get(target, 0) + 1
            stats.errors += response.status_code >= 500
        except httpx.HTTPError:
            stats.errors += 1
        stats.sent += 1
        await asyncio.sleep(rng.expovariate(rate))


async def generate(base_url: str, duration_s: float, *, users: int = 8, rate_per_user: float = 12.0, seed: int = 7) -> TrafficStats:
    stats = TrafficStats()
    deadline = time.monotonic() + duration_s
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        await asyncio.gather(*(_user(client, deadline, rate_per_user, stats, random.Random(seed + i)) for i in range(users)))
    return stats


def run_traffic(base_url: str, duration_s: float, **kwargs) -> TrafficStats:
    return asyncio.run(generate(base_url, duration_s, **kwargs))
