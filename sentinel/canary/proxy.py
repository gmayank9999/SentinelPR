"""Weighted reverse proxy: sends a configurable share of requests to the canary.

Routing is deterministic per request counter (a smooth weighted round-robin), so a 10%
weight means exactly one request in ten, not "about" one in ten. The weight is changed at
runtime through ``PUT /__canary/weight`` — that is how the controller shifts traffic and how a
rollback takes effect instantly.
"""

from __future__ import annotations

import threading

import httpx
from fastapi import FastAPI, Request, Response


class WeightedRouter:
    def __init__(self, weight: float = 0.0):
        self._lock = threading.Lock()
        self.weight = weight
        self._credit = 0.0
        self.sent = {"stable": 0, "canary": 0}

    def set_weight(self, weight: float) -> None:
        with self._lock:
            self.weight = max(0.0, min(1.0, weight))
            self._credit = 0.0

    def pick(self) -> str:
        with self._lock:
            self._credit += self.weight
            target = "canary" if self._credit >= 1.0 else "stable"
            if target == "canary":
                self._credit -= 1.0
            self.sent[target] += 1
            return target


HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding", "host"}


def create_proxy(stable_url: str, canary_url: str, weight: float = 0.0) -> FastAPI:
    app = FastAPI(title="SentinelPR canary proxy")
    router = WeightedRouter(weight)
    client = httpx.AsyncClient(timeout=10)
    upstream = {"stable": stable_url.rstrip("/"), "canary": canary_url.rstrip("/")}
    app.state.router = router

    @app.put("/__canary/weight")
    async def set_weight(body: dict):
        router.set_weight(float(body["weight"]))
        return {"weight": router.weight}

    @app.get("/__canary/status")
    async def status():
        return {"weight": router.weight, "sent": router.sent}

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def forward(path: str, request: Request):
        target = router.pick()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
        try:
            upstream_response = await client.request(
                request.method, f"{upstream[target]}/{path}", params=request.query_params,
                content=await request.body(), headers=headers,
            )
        except httpx.HTTPError:
            return Response(status_code=502, headers={"X-Canary-Target": target})
        out_headers = {k: v for k, v in upstream_response.headers.items() if k.lower() not in HOP_HEADERS}
        out_headers["X-Canary-Target"] = target
        return Response(upstream_response.content, status_code=upstream_response.status_code, headers=out_headers)

    return app
