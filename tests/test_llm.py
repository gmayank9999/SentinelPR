import pytest
from pydantic import BaseModel

from sentinel.agents.prompts import available, load_prompt
from sentinel.config import Config
from sentinel.llm.cache import ResponseCache
from sentinel.llm.client import BudgetExceeded, LLMClient, extract_json
from sentinel.llm.providers import Completion, ProviderError, RateLimited
from sentinel.orchestrator.router import complexity, route


class FakeProvider:
    def __init__(self, name, replies=None, error=None, available=True):
        self.name, self.model = name, f"{name}-model"
        self.replies = list(replies or [])
        self.error = error
        self._available = available
        self.calls = 0

    @property
    def available(self):
        return self._available

    def complete(self, system, user, *, json_mode, temperature, max_tokens):
        self.calls += 1
        if self.error:
            raise self.error
        return Completion(self.replies.pop(0), self.model, 100, 20, 0.01)


def client(tmp_path, providers, **llm):
    cfg = Config.load(tmp_path, environ={}, overrides={"llm": {"routing": {"easy": ["a", "b"], "medium": ["a", "b"], "hard": ["a", "b"]}, **llm}})
    return LLMClient(cfg, providers=providers, cache=ResponseCache(":memory:"))


class Answer(BaseModel):
    value: int


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure!\n```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('noise {"a": 3} trailing') == {"a": 3}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_falls_back_on_rate_limit_and_caches(tmp_path):
    a = FakeProvider("a", error=RateLimited("slow down"))
    b = FakeProvider("b", replies=["hello"])
    c = client(tmp_path, {"a": a, "b": b})
    text, call = c.complete("impact", "sys", "user")
    assert (text, call.provider) == ("hello", "b")
    assert c.tokens_used == 120
    # second identical request is served from the cache without touching the provider
    text, call = c.complete("impact", "sys", "user")
    assert call.cache_hit and b.calls == 1
    usage = c.usage()
    assert usage["cache_hits"] == 1 and usage["by_provider"] == {"a": 1, "b": 2}
    assert a.calls == 1  # the rate-limited provider was not retried


class LimitedOnce(FakeProvider):
    def complete(self, system, user, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RateLimited("slow down", retry_after=0.6)
        return Completion("later", self.model, 10, 2, 0.01)


def test_waits_for_rate_limit_when_every_provider_is_limited(tmp_path):
    a = LimitedOnce("a")
    c = client(tmp_path, {"a": a}, max_wait_s=5)
    text, _ = c.complete("impact", "sys", "user")
    assert text == "later" and a.calls == 2


def test_gives_up_when_rate_limit_outlasts_max_wait(tmp_path):
    a = FakeProvider("a", error=RateLimited("slow down", retry_after=60))
    c = client(tmp_path, {"a": a}, max_wait_s=1)
    with pytest.raises(ProviderError):
        c.complete("impact", "sys", "user")
    assert a.calls == 1


def test_unavailable_everywhere(tmp_path):
    c = client(tmp_path, {"a": FakeProvider("a", available=False)})
    assert not c.available
    with pytest.raises(ProviderError):
        c.complete("impact", "s", "u")


def test_budget_is_enforced(tmp_path):
    c = client(tmp_path, {"a": FakeProvider("a", replies=["x", "y"])}, budget_tokens=100)
    c.complete("intent", "s", "u1")
    with pytest.raises(BudgetExceeded):
        c.complete("intent", "s", "u2")


def test_structured_output_repairs_once(tmp_path):
    a = FakeProvider("a", replies=['{"value": "not a number"}', '{"value": 7}'])
    parsed, calls, problems = client(tmp_path, {"a": a}).structured("impact", "s", "u", Answer)
    assert parsed.value == 7 and len(calls) == 2 and len(problems) == 1


def test_structured_output_gives_up(tmp_path):
    a = FakeProvider("a", replies=["nope", "still nope"])
    parsed, calls, problems = client(tmp_path, {"a": a}).structured("impact", "s", "u", Answer)
    assert parsed is None and len(problems) == 2


def test_router_tiers():
    assert route("intent").tier == "easy"
    assert route("impact").tier == "medium"
    assert route("impact", diff_lines=900, change_units=15).tier == "hard"
    assert route("patch", budget_left=100, low_budget=8000).tier == "easy"
    assert complexity("impact", diff_lines=10_000) <= 1.0


def test_prompt_library():
    versions = available()
    assert versions["impact"] == [1, 2, 3]
    prompt = load_prompt("impact")
    assert prompt.id == "impact@v3"
    system, user = prompt.render(title="T", body="B", changes="C", neighbourhood="N", candidate_tests="X", retrieved="R")
    assert "<untrusted>" in user and "$" not in user.split("Reply with JSON")[0]
    assert load_prompt("impact", "v1").version == 1
    for name in ("historian", "intent", "explain", "rationale_judge", "qa", "patch"):
        assert load_prompt(name).system


def test_groq_rotates_keys_on_rate_limit(monkeypatch):
    from sentinel.llm.providers import GroqProvider

    seen = []

    def fake(self, system, user, **kwargs):
        seen.append(self.api_key)
        if self.api_key == "k1":
            raise RateLimited("quota", retry_after=600)
        return Completion("ok", self.model, 1, 1, 0.0)

    monkeypatch.setattr(GroqProvider, "_complete", fake)
    provider = GroqProvider("m", api_key="k1, k2")
    assert provider.complete("s", "u", json_mode=False, temperature=0, max_tokens=5).text == "ok"
    assert seen == ["k1", "k2"]


def test_groq_network_errors_are_retried_not_fatal(monkeypatch):
    import httpx

    from sentinel.llm.providers import GroqProvider

    def boom(*args, **kwargs):
        raise httpx.ConnectError("getaddrinfo failed")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(RateLimited):
        GroqProvider("m", api_key="k").complete("s", "u", json_mode=False, temperature=0, max_tokens=5)


# --- provider health checks and hostile model output ---------------------------------------------------

import httpx  # noqa: E402

from sentinel.llm.health import check_provider, status  # noqa: E402


def _http(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _cfg(tmp_path):
    return Config.load(tmp_path, environ={}, overrides={"workdir": str(tmp_path / "w")})


@pytest.mark.parametrize("code,state", [(401, "invalid_credentials"), (403, "invalid_credentials"), (429, "rate_limited"), (404, "model_not_found"), (503, "provider_error")])
def test_health_check_classifies_provider_failures(tmp_path, monkeypatch, code, state):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "k" * 40)
    result = check_provider("groq", _cfg(tmp_path), http=_http(lambda request: httpx.Response(code, headers={"retry-after": "7"})))
    assert result["state"] == state and result["ok"] is False
    assert "gsk_" not in str(result)  # the key never appears in a result


def test_health_check_reports_model_availability(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "k" * 40)
    cfg = _cfg(tmp_path)
    present = check_provider("groq", cfg, http=_http(lambda r: httpx.Response(200, json={"data": [{"id": "openai/gpt-oss-120b"}]})))
    assert present["ok"] and present["model_available"] is True and present["latency_ms"] is not None
    absent = check_provider("groq", cfg, http=_http(lambda r: httpx.Response(200, json={"data": [{"id": "other-model"}]})))
    assert absent["ok"] is False and absent["state"] == "model_not_found"


def test_health_check_without_credentials_is_not_configured_and_makes_no_request(tmp_path, monkeypatch):
    for name in ("GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    def forbidden(request):
        raise AssertionError("no request may be made without credentials")

    result = check_provider("gemini", _cfg(tmp_path), http=_http(forbidden))
    assert result["state"] == "not_configured" and result["configured"] is False and "GEMINI_API_KEY" in result["instructions"]


def test_timeouts_and_unreachable_providers_are_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "k" * 40)

    def timeout(request):
        raise httpx.ReadTimeout("slow")

    def down(request):
        raise httpx.ConnectError("no route")

    assert check_provider("groq", _cfg(tmp_path), http=_http(timeout))["state"] == "timeout"
    assert check_provider("groq", _cfg(tmp_path), http=_http(down))["state"] == "unreachable"


def test_status_names_the_mode_and_never_contains_a_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "s" * 40)
    on = status(_cfg(tmp_path))
    assert on["mode"] == "llm-assisted" and on["providers"]["groq"]["configured"] is True and "gsk_" not in str(on)
    monkeypatch.delenv("GROQ_API_KEY")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert status(_cfg(tmp_path))["providers"]["groq"]["configured"] is False
