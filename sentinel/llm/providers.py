"""LLM providers: Groq and Gemini (free tiers) and a local Ollama server.

All three are called over plain HTTP so there is no SDK to pin, and each maps its own
error vocabulary onto :class:`RateLimited` / :class:`ProviderUnavailable` so the client
can fall through the chain uniformly.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx


class ProviderError(RuntimeError):
    pass


class RateLimited(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


@dataclass
class Completion:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _raise_for(response: httpx.Response, provider: str) -> None:
    if response.status_code == 429:
        raise RateLimited(f"{provider}: rate limited")
    if response.status_code in (401, 403):
        raise ProviderUnavailable(f"{provider}: not authorised")
    if response.status_code >= 500:
        raise ProviderUnavailable(f"{provider}: server error {response.status_code}")
    if response.status_code >= 400:
        raise ProviderError(f"{provider}: HTTP {response.status_code}: {response.text[:300]}")


class GroqProvider:
    name = "groq"
    url = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, model: str, api_key: str | None = None, timeout: float = 60):
        self.model = model
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, system: str, user: str, *, json_mode: bool, temperature: float, max_tokens: int) -> Completion:
        if not self.available:
            raise ProviderUnavailable("groq: no API key")
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        started = time.time()
        try:
            response = httpx.post(self.url, json=body, headers={"Authorization": f"Bearer {self.api_key}"}, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"groq: {exc}") from exc
        _raise_for(response, self.name)
        data = response.json()
        usage = data.get("usage", {})
        text = data["choices"][0]["message"]["content"] or ""
        return Completion(
            text,
            data.get("model", self.model),
            usage.get("prompt_tokens", estimate_tokens(system + user)),
            usage.get("completion_tokens", estimate_tokens(text)),
            time.time() - started,
        )


class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str, api_key: str | None = None, timeout: float = 60):
        self.model = model
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, system: str, user: str, *, json_mode: bool, temperature: float, max_tokens: int) -> Completion:
        if not self.available:
            raise ProviderUnavailable("gemini: no API key")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        config = {"temperature": temperature, "maxOutputTokens": max_tokens}
        if json_mode:
            config["responseMimeType"] = "application/json"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }
        started = time.time()
        try:
            response = httpx.post(url, json=body, headers={"x-goog-api-key": self.api_key}, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"gemini: {exc}") from exc
        _raise_for(response, self.name)
        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise ProviderError(f"gemini: no candidates ({data.get('promptFeedback')})")
        text = "".join(p.get("text", "") for p in candidates[0].get("content", {}).get("parts", []))
        usage = data.get("usageMetadata", {})
        return Completion(
            text,
            self.model,
            usage.get("promptTokenCount", estimate_tokens(system + user)),
            usage.get("candidatesTokenCount", estimate_tokens(text)),
            time.time() - started,
        )


class OllamaProvider:
    name = "ollama"

    def __init__(self, model: str, host: str = "http://localhost:11434", timeout: float = 120):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self._available: bool | None = None

    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                response = httpx.get(f"{self.host}/api/tags", timeout=1.5)
                models = {m.get("name", "") for m in response.json().get("models", [])}
                self._available = response.status_code == 200 and any(
                    m == self.model or m.split(":")[0] == self.model.split(":")[0] for m in models
                )
            except (httpx.HTTPError, ValueError):
                self._available = False
        return self._available

    def complete(self, system: str, user: str, *, json_mode: bool, temperature: float, max_tokens: int) -> Completion:
        if not self.available:
            raise ProviderUnavailable("ollama: server or model not available")
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if json_mode:
            body["format"] = "json"
        started = time.time()
        try:
            response = httpx.post(f"{self.host}/api/chat", json=body, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"ollama: {exc}") from exc
        _raise_for(response, self.name)
        data = response.json()
        text = data.get("message", {}).get("content", "")
        return Completion(
            text,
            self.model,
            data.get("prompt_eval_count", estimate_tokens(system + user)),
            data.get("eval_count", estimate_tokens(text)),
            time.time() - started,
        )


def build_providers(cfg) -> dict:
    timeout = cfg.get("llm.timeout_s", 60)
    providers = cfg.get("llm.providers", {}) or {}
    return {
        "groq": GroqProvider(providers.get("groq", {}).get("model", "llama-3.3-70b-versatile"), timeout=timeout),
        "gemini": GeminiProvider(providers.get("gemini", {}).get("model", "gemini-2.0-flash"), timeout=timeout),
        "ollama": OllamaProvider(
            providers.get("ollama", {}).get("model", "qwen2.5-coder:1.5b"),
            providers.get("ollama", {}).get("host", "http://localhost:11434"),
            timeout=max(timeout, 120),
        ),
    }
