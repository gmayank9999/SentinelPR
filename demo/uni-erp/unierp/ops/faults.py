"""Runtime fault injection, used to rehearse canary rollbacks.

Configured with environment variables so the same image can run healthy or faulty:

``UNIERP_FAULT``       none | slow | errors | memory | wrong
``UNIERP_FAULT_RATE``  probability that a request is affected (default 0.05)
``UNIERP_FAULT_DELAY_MS``  extra latency for ``slow`` (default 400)
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field

FAULT_KINDS = ("none", "slow", "errors", "memory", "wrong")


@dataclass
class FaultConfig:
    kind: str = "none"
    rate: float = 0.05
    delay_ms: int = 400
    _leak: list[bytes] = field(default_factory=list, repr=False)
    _rng: random.Random = field(default_factory=random.Random, repr=False)

    @classmethod
    def from_env(cls) -> "FaultConfig":
        kind = os.getenv("UNIERP_FAULT", "none").strip().lower()
        if kind not in FAULT_KINDS:
            kind = "none"
        return cls(
            kind=kind,
            rate=float(os.getenv("UNIERP_FAULT_RATE", "0.05")),
            delay_ms=int(os.getenv("UNIERP_FAULT_DELAY_MS", "400")),
        )

    @property
    def active(self) -> bool:
        return self.kind != "none"

    def triggered(self) -> bool:
        return self.active and self._rng.random() < self.rate

    def leak(self) -> None:
        # Hold on to 256 KiB per affected request to simulate a memory leak.
        self._leak.append(bytes(256 * 1024))
