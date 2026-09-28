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
