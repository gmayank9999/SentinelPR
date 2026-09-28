"""The single entry point agents use to talk to language models.

Responsibilities: route the task to a tier, walk that tier's provider chain on rate limits
or outages, cache responses, enforce the per-run token budget, validate structured output
against a Pydantic schema (with one repair attempt), and log every call.

If no provider is reachable the client reports ``available == False`` and agents run their
deterministic, evidence-only path instead. The pipeline never *requires* an LLM.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from sentinel.llm.cache import ResponseCache
from sentinel.llm.providers import ProviderError, RateLimited, build_providers
from sentinel.models import LLMCall
from sentinel.orchestrator.router import route

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

RATE_LIMIT_COOLDOWN_S = 30.0
FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str):
    """Parse JSON from a model reply, tolerating code fences and leading prose."""
    text = text.strip()
    match = FENCE.search(text)
    if match:
        text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON object found in model output")


class BudgetExceeded(RuntimeError):
    pass


class LLMClient:
    def __init__(self, cfg, providers: dict | None = None, cache: ResponseCache | None = None):
        self.cfg = cfg
        self.enabled = bool(cfg.get("llm.enabled", True))
        self.providers = providers if providers is not None else build_providers(cfg)
        self.routing = cfg.get("llm.routing", {}) or {}
        self.temperature = float(cfg.get("llm.temperature", 0.0))
        self.budget = int(cfg.get("llm.budget_tokens", 60000))
        self.low_budget = int(cfg.get("llm.low_budget_threshold", 8000))
        self.router_enabled = bool(cfg.get("llm.router", True))
        if cache is None and cfg.get("llm.cache", True):
            cache = ResponseCache(cfg.workdir / "llm-cache.sqlite")
        self.cache = cache
        self.calls: list[LLMCall] = []
        self.tokens_used = 0
        self._lock = threading.Lock()
        self._cooldown: dict[str, float] = {}

    # status -----------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.enabled and any(p.available for p in self.providers.values())

    @property
    def budget_left(self) -> int:
        return self.budget - self.tokens_used

    def reset_usage(self) -> None:
        self.calls, self.tokens_used = [], 0

    # core ---------------------------------------------------------------------
    def complete(self, task: str, system: str, user: str, *, json_mode: bool = False, max_tokens: int = 1200,
                 diff_lines: int = 0, change_units: int = 0) -> tuple[str, LLMCall]:
        if not self.enabled:
            raise ProviderError("LLM use is disabled")
        context_tokens = len(system + user) // 4
        if self.budget_left <= 0:
            raise BudgetExceeded(f"token budget of {self.budget} exhausted")
        decision = route(
            task, diff_lines=diff_lines, change_units=change_units, context_tokens=context_tokens,
            budget_left=self.budget_left, low_budget=self.low_budget, enabled=self.router_enabled,
        )
        chain = [n for n in (self.routing.get(decision.tier) or ["groq", "gemini", "ollama"]) if n in self.providers]
        params = {"t": self.temperature, "json": json_mode, "max": max_tokens}
        keys = {n: ResponseCache.key(f"{n}:{self.providers[n].model}", system, user, params) for n in chain}

        # A cached answer from any provider in the chain beats a fresh call.
        if self.cache:
            for name in chain:
                cached = self.cache.get(keys[name])
                if cached is not None:
                    call = LLMCall(task=task, provider=name, model=cached["model"], prompt_tokens=cached["prompt_tokens"],
                                   completion_tokens=cached["completion_tokens"], latency_s=0.0, cache_hit=True, tier=decision.tier)
                    self._record(call, count_tokens=False)
                    return cached["text"], call

        errors = []
        for name in chain:
            provider = self.providers[name]
            if not provider.available or self._cooling_down(name):
                continue
            try:
                completion = provider.complete(system, user, json_mode=json_mode, temperature=self.temperature, max_tokens=max_tokens)
            except RateLimited as exc:
                errors.append(str(exc))
                self._cooldown[name] = time.monotonic() + RATE_LIMIT_COOLDOWN_S
                self._record(LLMCall(task=task, provider=name, model=provider.model, tier=decision.tier, error="rate_limited"), count_tokens=False)
                continue
            except ProviderError as exc:
                errors.append(str(exc))
                continue
            call = LLMCall(task=task, provider=name, model=completion.model, prompt_tokens=completion.prompt_tokens,
                           completion_tokens=completion.completion_tokens, latency_s=round(completion.latency_s, 3), tier=decision.tier)
            self._record(call)
            if self.cache:
                self.cache.put(keys[name], {"text": completion.text, "model": completion.model,
                                            "prompt_tokens": completion.prompt_tokens, "completion_tokens": completion.completion_tokens})
            return completion.text, call
        raise ProviderError("no provider could serve the request: " + "; ".join(errors or ["none available"]))

    def structured(self, task: str, system: str, user: str, schema: type[T], **kwargs) -> tuple[T | None, list[LLMCall], list[str]]:
        """Ask for JSON matching ``schema``. One repair round on validation failure, then give up.

        Returns (parsed or None, calls made, problems) — callers log problems as schema-guard events.
        """
        calls: list[LLMCall] = []
        problems: list[str] = []
        prompt = user
        for attempt in range(2):
            text, call = self.complete(task, system, prompt, json_mode=True, **kwargs)
            calls.append(call)
            try:
                return schema.model_validate(extract_json(text)), calls, problems
            except (ValueError, ValidationError) as exc:
                problems.append(f"attempt {attempt + 1}: {str(exc)[:400]}")
                prompt = (
                    f"{user}\n\nYour previous reply was not valid for the required JSON schema:\n{str(exc)[:600]}\n"
                    "Reply again with ONLY a corrected JSON object."
                )
        return None, calls, problems

    def _cooling_down(self, name: str) -> bool:
        return time.monotonic() < self._cooldown.get(name, 0.0)

    def _record(self, call: LLMCall, count_tokens: bool = True) -> None:
        with self._lock:
            self.calls.append(call)
            if count_tokens:
                self.tokens_used += call.prompt_tokens + call.completion_tokens

    def usage(self) -> dict:
        by_provider: dict[str, int] = {}
        for c in self.calls:
            by_provider[c.provider] = by_provider.get(c.provider, 0) + 1
        return {
            "calls": len(self.calls),
            "cache_hits": sum(1 for c in self.calls if c.cache_hit),
            "tokens": self.tokens_used,
            "by_provider": by_provider,
            "by_tier": {t: sum(1 for c in self.calls if c.tier == t) for t in ("easy", "medium", "hard")},
        }
