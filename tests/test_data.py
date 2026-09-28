import pytest

from sentinel.data.history import classify_fix, closing_issues, mine_commits, referenced_issues
from sentinel.data.store import Store
from sentinel.data.szz import defect_summary, run_szz
from sentinel.data.tracker import link_issues_to_prs

V1 = '''def price(qty, unit):
    total = qty * unit
    return total


def discount(total):
    return total
'''

# introduces the bug: discount applies at 100 instead of above 100
V2 = V1.replace("def discount(total):\n    return total", "def discount(total):\n    if total >= 100:\n        return total * 0.9\n    return total")

# fixes it by modifying the buggy line
V3 = V2.replace("if total >= 100:", "if total > 100:")

# a second fix that only inserts a guard clause
V4 = V3.replace("    total = qty * unit\n", "    if qty < 0:\n        raise ValueError(qty)\n    total = qty * unit\n")


@pytest.fixture
def shop(git_repo):
    shas = {
        "v1": git_repo.commit("Add pricing", {"src/shop.py": V1, "src/tests/test_shop.py": "def test_x():\n    pass\n"}, date="2026-01-01T10:00:00"),
        "v2": git_repo.commit("Add bulk discount", {"src/shop.py": V2}, date="2026-01-05T10:00:00"),
        "docs": git_repo.commit("Update readme", {"README.md": "hi\n"}, date="2026-01-06T10:00:00"),
        "v3": git_repo.commit("Fix discount threshold\n\nCloses #7.", {"src/shop.py": V3}, date="2026-01-09T10:00:00"),
        "v4": git_repo.commit("Reject negative quantities (fixes #9)", {"src/shop.py": V4}, date="2026-01-12T10:00:00"),
    }
    return git_repo, shas


def test_issue_reference_parsing():
    assert referenced_issues("Fix thing (#12)\n\nSee #3 and #12") == [3, 12]
    assert closing_issues("Fixes #4, closes #5 and resolved: #6") == [4, 5, 6]
    assert referenced_issues("color: #fff") == []


def test_fix_classification_prefers_labels():
    labels = {1: ["bug"], 2: ["enhancement"]}
    assert classify_fix("Something (#10)\n\nCloses #1.", labels) == (True, [1])
    assert classify_fix("Fix typo in feature\n\nCloses #2.", labels) == (False, [])
    assert classify_fix("Fix crash on empty input", None)[0]
    assert not classify_fix("Add search", None)[0]


def test_mine_commits_scoped_to_prefix(shop):
    repo, shas = shop
    commits = mine_commits(repo.path, "src/")
    assert [c["sha"] for c in commits] == [shas["v1"], shas["v2"], shas["v3"], shas["v4"]]  # README commit excluded
    first = commits[0]
    assert {f["path"] for f in first["files"]} == {"shop.py", "tests/test_shop.py"}
    assert "shop.py::discount" in commits[1]["functions"]
    assert commits[2]["is_fix"] and commits[2]["issues"] == [7]
    assert commits[3]["functions"] == ["shop.py::price"]


def test_szz_traces_modified_and_inserted_lines(shop):
    repo, shas = shop
    commits = mine_commits(repo.path, "src/")
    links = run_szz(repo.path, commits, "src/")
    by_fix = {}
    for link in links:
        by_fix.setdefault(link["fix_sha"], []).append(link)

    modified = by_fix[shas["v3"]]
    assert {l["introducing_sha"] for l in modified} == {shas["v2"]}
    assert modified[0]["function"] == "discount" and modified[0]["confidence"] == 1.0

    inserted = by_fix[shas["v4"]]  # pure insertion: blame the neighbouring lines
    assert {l["introducing_sha"] for l in inserted} == {shas["v1"]}
    assert all(l["confidence"] == 0.5 and l["function"] == "price" for l in inserted)

    summary = defect_summary(links)
    assert summary["shop.py::discount"]["introducers"] == {shas["v2"]}


def test_szz_ignores_introducers_after_the_issue_was_reported(shop):
    repo, _ = shop
    commits = mine_commits(repo.path, "src/")
    issues = {7: {"number": 7, "created_at": "2026-01-02T00:00:00+00:00"}}
    links = run_szz(repo.path, commits, "src/", issues)
    assert all(l["issue"] != 7 for l in links)


def test_store_roundtrip(tmp_path):
    store = Store(tmp_path / "s.db")
    store.replace_commits(
        [
            {
                "sha": "a" * 40, "parents": [], "message": "m", "author": "x", "email": "e", "timestamp": 1,
                "files": [{"path": "f.py", "additions": 1, "deletions": 0}], "functions": ["f.py::g"],
                "additions": 1, "deletions": 0, "is_fix": True, "issues": [3],
            }
        ]
    )
    assert store.commit("aaaa")["is_fix"]
    assert [c["sha"] for c in store.commits_touching_unit("f.py::g")] == ["a" * 40]
    store.replace_coverage([("f.py", 1, "t1"), ("f.py", 2, "t1"), ("f.py", 2, "t2")], [{"test_id": "t1", "file": "t.py"}])
    assert store.tests_covering("f.py", [2, 3]) == {"t1": {2}, "t2": {2}}
    assert store.covered_lines("f.py") == {1, 2}
    store.set_meta("k", {"v": 1})
    assert store.get_meta("k") == {"v": 1}
    store.save_run({"run_id": "r1", "decision": "PASS", "claims": [{"claim_id": "c1", "agent": "impact", "type": "test_impact", "target": "t"}]})
    assert store.runs()[0]["decision"] == "PASS"


def test_link_issues_to_prs():
    issues = [{"number": 1}, {"number": 2, "linked_prs": [5]}]
    link_issues_to_prs(issues, [{"number": 5, "linked_issues": [1, 2]}])
    assert issues[0]["linked_prs"] == [5] and issues[1]["linked_prs"] == [5]
