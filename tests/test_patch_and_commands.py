import json

from sentinel.agents.patch_advisor import PatchAdvisor, _Plan, apply_plan, remediation_plan
from sentinel.commands import handle, parse
from sentinel.config import Config
from sentinel.retrieval.hybrid import Retrieved

BUGGY = "def discount(total):\n    if total >= 100:\n        return total * 0.9\n    return total\n"
TEST = "from shop import discount\n\n\ndef test_small():\n    assert discount(10) == 10\n"


class FakeLLM:
    available = True

    def __init__(self, plan: dict):
        self.plan = plan

    def structured(self, task, system, user, schema, **kwargs):
        assert "<untrusted>" in user and "shop.py" in user
        return schema.model_validate(self.plan), [], []


class Retriever:
    def search_code(self, query, seeds=None, top_k=8):
        return [Retrieved("chunk:shop.py#L1-4", 1.0, ["bm25"], BUGGY, "shop.py", "discount")]


def project(tmp_path):
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    (root / "shop.py").write_text(BUGGY)
    (root / "tests" / "test_shop.py").write_text(TEST)
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\npythonpath = ['.']\n")
    cfg = Config.load(tmp_path, environ={}, overrides={"project": {"root": "proj", "package": "shop", "tests": "tests"}, "workdir": str(tmp_path / "w")})
    return cfg, {"shop.py": BUGGY, "tests/test_shop.py": TEST}


GOOD_PLAN = {
    "summary": "Apply the discount only above 100",
    "edits": [{"path": "shop.py", "find": "if total >= 100:", "replace": "if total > 100:"}],
    "new_files": [{"path": "tests/test_discount_boundary.py", "content": "from shop import discount\n\n\ndef test_boundary():\n    assert discount(100) == 100\n"}],
}


def test_parse_commands():
    assert parse("/sentinel ask \"why round down?\"") == ("ask", "why round down?")
    assert parse("thanks!\n/sentinel rerun") == ("rerun", "")
    assert parse("/sentinel deploy") is None


def test_apply_plan_requires_unique_snippets():
    files, originals, problems = apply_plan(_Plan.model_validate(GOOD_PLAN), {"shop.py": BUGGY})
    assert "if total > 100:" in files["shop.py"] and originals["tests/test_discount_boundary.py"] is None and not problems
    bad = _Plan(summary="x", edits=[{"path": "shop.py", "find": "return", "replace": "yield"}, {"path": "nope.py", "find": "a", "replace": "b"}])
    _, _, problems = apply_plan(bad, {"shop.py": BUGGY})
    assert len(problems) == 2


def test_patch_advisor_validates_with_tests(tmp_path):
    cfg, sources = project(tmp_path)
    advisor = PatchAdvisor(cfg, Retriever(), FakeLLM(GOOD_PLAN), sources)
    proposal = advisor.propose("Discount applied at exactly 100")
    advisor.validate(proposal, ["tests/test_shop.py"], tmp_path / "scratch")
    assert proposal.valid, (proposal.problems, proposal.tests)
    assert len(proposal.tests.passed) == 2


def test_patch_advisor_rejects_unsafe_or_failing_patches(tmp_path):
    cfg, sources = project(tmp_path)
    unsafe = dict(GOOD_PLAN, edits=[{"path": "shop.py", "find": "if total >= 100:", "replace": "if eval('total > 100'):"}])
    advisor = PatchAdvisor(cfg, Retriever(), FakeLLM(unsafe), sources)
    proposal = advisor.validate(advisor.propose("x"), [], tmp_path / "s1")
    assert not proposal.valid and not proposal.review.allowed
    wrong = dict(GOOD_PLAN, edits=[{"path": "shop.py", "find": "return total * 0.9", "replace": "return total * 0.5"}])
    advisor = PatchAdvisor(cfg, Retriever(), FakeLLM(wrong), sources)
    proposal = advisor.validate(advisor.propose("x"), ["tests/test_shop.py"], tmp_path / "s2")
    assert not proposal.valid and proposal.tests.failed


def test_remediation_plan_lists_concrete_gaps():
    report = {"verification": {"tests": {"failed": []}, "changed_lines": {"uncovered": ["a.py#L3"]},
                               "mutation": {"mutants": [{"status": "survived", "file": "a.py", "line": 4, "original": "x > 1", "mutated": "x >= 1"}]}}}
    text = remediation_plan(report)
    assert "a.py#L3" in text and "`x > 1` to `x >= 1`" in text


def test_handle_permissions_and_usage(tmp_path):
    cfg = Config.load(tmp_path, environ={})
    event = {"comment": {"body": "/sentinel ask", "author_association": "NONE"}, "issue": {"number": 3}}
    assert "members only" in handle(event, cfg, None)
    event["comment"]["author_association"] = "OWNER"
    assert handle(event, cfg, None).startswith("Usage")
    event["comment"]["body"] = "/sentinel explain"
    assert "pull requests" in handle(event, cfg, None)
    assert handle({"comment": {"body": "nice work"}, "issue": {"number": 3}}, cfg, None) is None
    json.dumps(event)
