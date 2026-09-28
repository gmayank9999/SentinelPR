from pathlib import Path

from sentinel.agents.signals import change_entropy, heuristic_intent, unit_complexity
from sentinel.agents.test_selector import expand
from sentinel.models import Claim, FileChange, Mutant, MutationReport, TestOutcome, TestRun, VerdictStatus
from sentinel.verify.checkers import lexical_support, signature_incompatible
from sentinel.verify.mutation import apply, candidates_for, generate_mutants, run_mutation
from sentinel.verify.reconciler import reconcile
from sentinel.verify.runner import run_tests

SOURCE = '''def grade(score, strict=False):
    if score >= 90 and not strict:
        return "A"
    total = score * 2 + 1
    return "B" if total > 100 else "C"
'''


def test_candidates_only_on_lines_of_interest():
    ops = {c.operator for c in candidates_for(SOURCE, {2})}
    assert ops == {"cmp", "bool", "not", "const", "cond"}
    assert {c.line for c in candidates_for(SOURCE, {4})} == {4}
    assert candidates_for(SOURCE, {99}) == []


def test_mutants_compile_and_change_behaviour():
    for cand in candidates_for(SOURCE, {2, 3, 4, 5}):
        mutated = apply(SOURCE, cand)
        assert mutated is not None and mutated != SOURCE
        compile(mutated, "m", "exec")
    cmp = next(c for c in candidates_for(SOURCE, {2}) if c.operator == "cmp")
    assert "score > 90" in apply(SOURCE, cmp)


def test_mutants_respect_unicode_columns():
    source = 'def f(x):\n    return "é" if x < 1 else "ü"\n'
    cand = next(c for c in candidates_for(source, {2}) if c.operator == "cmp")
    assert apply(source, cand) == 'def f(x):\n    return "é" if (x <= 1) else "ü"\n'


def test_generate_mutants_is_capped_and_spread_across_lines():
    mutants = generate_mutants({"g.py": SOURCE}, {"g.py": {2, 4, 5}}, limit=5)
    assert len(mutants) == 5
    assert {m.line for m, _ in mutants} == {2, 4, 5}


def test_run_mutation_kills_and_survives(tmp_path):
    project = tmp_path / "proj"
    (project / "tests").mkdir(parents=True)
    (project / "calc.py").write_text("def double(x):\n    return x * 2\n\n\ndef unused(y):\n    return y + 1\n")
    (project / "tests" / "test_calc.py").write_text("from calc import double\n\n\ndef test_double():\n    assert double(3) == 6\n")
    (project / "conftest.py").write_text("")

    baseline = run_tests(project, ["tests/test_calc.py::test_double"], package="calc", coverage=True, scratch=tmp_path / "s")
    assert [o.outcome for o in baseline.run.outcomes] == ["passed"]
    assert ("calc.py", 2) in baseline.line_tests

    sources = {"calc.py": (project / "calc.py").read_text()}
    mutants = generate_mutants(sources, {"calc.py": {2, 6}})
    report = run_mutation(project, mutants, baseline.line_tests, scratch=tmp_path / "m")
    status = {(m.line, m.operator): m.status for m in report.mutants}
    assert status[(2, "arith")] == "killed"
    assert status[(6, "arith")] == "survived"  # no test executes unused()
    assert 0 < report.score < 1
    assert (project / "calc.py").read_text() == sources["calc.py"]  # restored


def test_failing_and_missing_tests_are_reported(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    (project / "test_x.py").write_text("def test_ok():\n    pass\n\n\ndef test_bad():\n    assert 1 == 2\n")
    result = run_tests(project, ["test_x.py::test_ok", "test_x.py::test_bad"], scratch=tmp_path / "s")
    outcomes = {o.nodeid: o.outcome for o in result.run.outcomes}
    assert outcomes == {"test_x.py::test_ok": "passed", "test_x.py::test_bad": "failed"}
    assert "assert 1 == 2" in result.run.failed[0].message


def test_signature_compatibility():
    assert signature_incompatible("def f(a, b)", "def f(a, b, c=1)") is None
    assert "removed" in signature_incompatible("def f(a, b)", "def f(a)")
    assert "required" in signature_incompatible("def f(a)", "def f(a, b)")
    assert "reordered" in signature_incompatible("def f(a, b)", "def f(b, a)")
    assert signature_incompatible("def m(self, a) -> int", "def m(self, a, *, flag=False) -> int") is None


def test_lexical_support():
    assert lexical_support("fees are rounded down to whole rupees", "Finance: round fee totals down to the whole rupee") > 0.6
    assert lexical_support("GPA uses banker's rounding", "late fee cap at ten percent") < 0.25


def test_reconcile_requests_one_retry():
    from sentinel.models import Verdict

    claims = [Claim(claim_id=f"imp-{i}", agent="impact", type="test_impact", target=f"t{i}", evidence_ids=["x"]) for i in range(3)]
    verdicts = [
        Verdict(claim_id="imp-0", status=VerdictStatus.REFUTED, method="e", detail="never runs"),
        Verdict(claim_id="imp-1", status=VerdictStatus.REFUTED, method="e"),
        Verdict(claim_id="imp-2", status=VerdictStatus.VERIFIED, method="e"),
    ]
    rec = reconcile(claims, verdicts)
    assert rec.should_retry_impact and rec.impact_refuted_ratio == 0.667
    assert "never runs" in rec.feedback
    assert not reconcile(claims, verdicts, retried=True).should_retry_impact


def test_signal_helpers():
    changes = [FileChange(path="a.py", added_lines=[1, 2]), FileChange(path="b.py", added_lines=[1, 2])]
    assert change_entropy(changes) == 1.0
    assert change_entropy(changes[:1]) == 0.0
    complexity = unit_complexity("class A:\n    def m(self, x):\n        if x:\n            return 1\n        return 2\n\n\ndef f():\n    pass\n")
    assert complexity == {"A": 3, "A.m": 2, "f": 1} or complexity["A.m"] == 2
    assert heuristic_intent("Fix crash when list is empty", ["app.py"]) == "bugfix"
    assert heuristic_intent("Anything", ["tests/test_a.py"]) == "test"
    assert heuristic_intent("Update", ["README.md", "docs/x.md"]) == "docs"
    assert heuristic_intent("Rename helper", ["app.py"]) == "refactor"


def test_expand_parametrised_ids():
    known = {"t.py::test_a[1]": {}, "t.py::test_a[2]": {}, "t.py::test_ab": {}}
    assert expand("t.py::test_a", known) == ["t.py::test_a[1]", "t.py::test_a[2]"]
    assert expand("t.py::test_ab", known) == ["t.py::test_ab"]


def test_facts_keep_an_explicitly_empty_change_set():
    from sentinel.models import ChangeUnit
    from sentinel.retrieval.graph import CodeGraph
    from sentinel.verify.checkers import Facts

    unit = ChangeUnit(id="a.py::f", file="a.py", symbol="f", qualname="f", kind="function", change_type="modified", changed_lines=[3])
    changed: dict = {}  # e.g. only a docstring changed: nothing executable
    facts = Facts([unit], TestRun(), {}, MutationReport(), CodeGraph(), None, changed_lines=changed)
    assert facts.changed_lines == {} and changed == {}
    assert Facts([unit], TestRun(), {}, MutationReport(), CodeGraph(), None).changed_lines == {"a.py": {3}}
