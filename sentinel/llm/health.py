"""Connectivity and model-availability checks for the LLM providers.

Credentials come only from the server's environment (``GROQ_API_KEY``, ``GEMINI_API_KEY``) and are
never returned. A check calls the provider's model-listing endpoint, which verifies the key and the
configured model without spending tokens; ``probe=True`` additionally sends a one-token completion to
measure latency and token accounting.
"""

from __future__ import annotations

import time

import httpx

from sentinel.llm.providers import build_providers

GROQ_MODELS = "https://api.groq.com/openai/v1/models"
GEMINI_MODELS = "https://generativelanguage.googleapis.com/v1beta/models"
INSTRUCTIONS = {
    "groq": "Set GROQ_API_KEY on the server (free key at console.groq.com). Several comma-separated keys are used in turn when one hits its quota.",
    "gemini": "Set GEMINI_API_KEY (or GOOGLE_API_KEY) on the server (free key at aistudio.google.com).",
    "ollama": "Install Ollama, run `ollama pull <model>`, and keep it running on localhost:11434 (or set llm.providers.ollama.host).",
}


def _classify(response: httpx.Response) -> tuple[str, str]:
    code = response.status_code
    if code in (401, 403):
        return "invalid_credentials", "The provider rejected the API key."
    if code == 429:
        retry = response.headers.get("retry-after")
        return "rate_limited", f"Quota or rate limit reached{f' (retry after {retry}s)' if retry else ''}."
    if code == 404:
        return "model_not_found", "The configured model was not found."
    if code >= 500:
        return "provider_error", f"The provider returned HTTP {code}."
    return "error", f"Unexpected HTTP {code}."


def check_provider(name: str, cfg, *, probe: bool = False, http: httpx.Client | None = None) -> dict:
    providers = build_providers(cfg)
    if name not in providers:
        return {"provider": name, "state": "unknown_provider", "ok": False, "detail": f"Unknown provider {name!r}."}
    provider = providers[name]
    result = {"provider": name, "model": provider.model, "configured": bool(provider.available), "ok": False, "state": "not_configured",
              "detail": "", "latency_ms": None, "model_available": None, "instructions": INSTRUCTIONS[name]}
    if not provider.available:
        result["detail"] = "No credentials or local server found."
        return result
    client = http or httpx.Client(timeout=15)
    started = time.time()
    try:
        if name == "groq":
            response = client.get(GROQ_MODELS, headers={"Authorization": f"Bearer {provider.api_key}"})
        elif name == "gemini":
            response = client.get(f"{GEMINI_MODELS}/{provider.model}", headers={"x-goog-api-key": provider.api_key})
        else:
            response = client.get(f"{provider.host}/api/tags", timeout=3)
    except httpx.TimeoutException:
        result.update(state="timeout", detail="The provider did not answer within 15 seconds.")
        return result
    except httpx.HTTPError as exc:
        result.update(state="unreachable", detail=f"Could not reach the provider ({type(exc).__name__}).")
        return result
    result["latency_ms"] = round((time.time() - started) * 1000)
    if response.status_code != 200:
        state, detail = _classify(response)
        result.update(state=state, detail=detail)
        return result
    data = response.json()
    if name == "groq":
        ids = {m["id"] for m in data.get("data", [])}
        result["model_available"] = provider.model in ids
        result["models_offered"] = len(ids)
    elif name == "gemini":
        result["model_available"] = True  # the model endpoint answered 200 for this exact model
    else:
        have = {m.get("name", "") for m in data.get("models", [])}
        result["model_available"] = any(m == provider.model or m.split(":")[0] == provider.model.split(":")[0] for m in have)
    result["state"] = "ok" if result["model_available"] else "model_not_found"
    result["ok"] = bool(result["model_available"])
    result["detail"] = "Connected; the configured model is available." if result["ok"] else "Connected, but the configured model is not offered by this provider."
    if probe and result["ok"]:
        try:
            completion = provider.complete("Reply with one word.", "ok", json_mode=False, temperature=0.0, max_tokens=8)
            result["probe"] = {"latency_ms": round(completion.latency_s * 1000), "prompt_tokens": completion.prompt_tokens, "completion_tokens": completion.completion_tokens,
                               "model": completion.model}
        except Exception as exc:  # noqa: BLE001 - the probe reports, it never raises
            result.update(ok=False, state="probe_failed", detail=f"The model listing worked but a completion failed: {type(exc).__name__}: {str(exc)[:160]}")
    return result


def status(cfg) -> dict:
    """Which providers are configured and the routing between them. Never includes a key."""
    providers = build_providers(cfg)
    routing = cfg.get("llm.routing", {}) or {}
    return {
        "enabled": bool(cfg.get("llm.enabled", True)),
        "providers": {n: {"configured": bool(p.available), "model": p.model, "instructions": INSTRUCTIONS[n]} for n, p in providers.items()},
        "any_configured": any(p.available for p in providers.values()),
        "routing": routing,
        "mode": "llm-assisted" if any(p.available for p in providers.values()) else "evidence-only",
        "token_budget_per_run": cfg.get("llm.budget_tokens"),
        "decision_note": "Models propose claims and write explanations. The verdict comes from execution evidence, the calibrated risk model and the policy; model text cannot change it.",
    }
