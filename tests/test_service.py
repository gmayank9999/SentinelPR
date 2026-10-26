"""The product service: event log, stage topology, repositories, runs, API security."""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentinel.api import create_api
from sentinel.config import Config
from sentinel.orchestrator.observe import EDGES, PIPELINE_NODES, STAGES
from sentinel.orchestrator.pipeline import Pipeline
from sentinel.service import github
from sentinel.service.events import EventLog, redact
from sentinel.service.repos import detect_project
from sentinel.service.runs import compare_runs, reconstruct_events

ROOT = Path(__file__).resolve().parent.parent
DEMO_REPO = ROOT / ".sentinel" / "uni-erp-repo"


class FakeGitHub:
    authenticated = False

    def repo(self, owner, name):
        return {"owner": owner, "name": name, "full_name": f"{owner}/{name}", "private": False, "default_branch": "main", "description": "d",
                "language": "Python", "html_url": f"https://github.com/{owner}/{name}", "clone_url": "", "size_kb": 1, "stars": 0, "pushed_at": None}

    def languages(self, owner, name):
        return {"Python": 900, "Shell": 100}

    def pulls(self, owner, name, **_):
        return []


def make_client(tmp_path, github_cls):
    """An API whose GitHub client is a fake: no network in tests."""
    from sentinel.service import api as service_api

    cfg = Config.load(tmp_path, environ={}, overrides={"workdir": str(tmp_path / "w")})
    original = service_api.GitHubClient
    service_api.GitHubClient = github_cls
    try:
        return TestClient(create_api(cfg))
    finally:
        service_api.GitHubClient = original


@pytest.fixture()
def client(tmp_path):
    cfg = Config.load(tmp_path, environ={}, overrides={"workdir": str(tmp_path / "w")})
    app = create_api(cfg)
    return TestClient(app)


# --- events ------------------------------------------------------------------------


def test_event_log_numbers_persists_and_reloads(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog("run-1", path)
    a = log.emit("verify", "stage_started", "running", "go")
    b = log.emit("verify", "stage_completed", "completed", "done", {"n": 3})
    assert (a["seq"], b["seq"], b["id"]) == (1, 2, "run-1:2")
    assert [e["seq"] for e in log.since(1)] == [2]
    reloaded = EventLog.load("run-1", path)
    assert [e["message"] for e in reloaded.since(0)] == ["go", "done"] and reloaded.closed


def test_event_log_wait_wakes_on_new_event(tmp_path):
    import threading

    log = EventLog("r", tmp_path / "e.jsonl")
    threading.Timer(0.1, lambda: log.emit("x", "log", "info", "hi")).start()
    started = time.time()
    assert [e["message"] for e in log.wait(0, timeout=5)] == ["hi"]
    assert time.time() - started < 2


def test_secrets_never_reach_events(tmp_path):
    log = EventLog("r", tmp_path / "e.jsonl")
    event = log.emit("intake", "log", "info", "token ghp_" + "a" * 36, {"authorization": "Bearer abc", "nested": {"api_key": "x", "note": "AKIA" + "A" * 16}})
    blob = json.dumps(event)
    assert "ghp_" not in blob and "AKIA" not in blob and "Bearer abc" not in blob
    assert redact({"password": "hunter2"})["password"] == "[REDACTED]"
    assert "ghp_" not in (tmp_path / "e.jsonl").read_text()


# --- topology -------------------------------------------------------------------------


def test_stage_topology_matches_the_compiled_langgraph():
    pipeline = Pipeline.__new__(Pipeline)  # build() only needs the bound node methods
    graph = pipeline.build().get_graph()
    real = {(e.source, e.target) for e in graph.edges if not e.source.startswith("__") and not e.target.startswith("__")}
    declared = {(a, b) for a, b in EDGES if a in PIPELINE_NODES and b in PIPELINE_NODES}
    assert real == declared
    assert {n for n in graph.nodes if not n.startswith("__")} == PIPELINE_NODES
    assert {s["id"] for s in STAGES} >= PIPELINE_NODES


# --- repositories & input validation ---------------------------------------------------------


@pytest.mark.parametrize("text", ["", "not a url", "https://evil.example/o/r", "o/r/extra", "../../etc/passwd", "o/..", "https://github.com/o/r;rm -rf"])
def test_repository_input_is_validated(text):
    with pytest.raises(github.GitHubError):
        github.parse_repo_url(text)


@pytest.mark.parametrize("text,expected", [("https://github.com/psf/requests", ("psf", "requests")), ("github.com/psf/requests.git", ("psf", "requests")), ("psf/requests/", ("psf", "requests"))])
def test_repository_urls_are_normalised(text, expected):
    assert github.parse_repo_url(text) == expected


def test_refs_are_checked_before_reaching_git():
    assert github.valid_ref("main") and github.valid_ref("feature/x-1")
    assert not any(github.valid_ref(r) for r in ["", "-rf", "a..b", "a b", "x;y", "refs/heads/x.lock", "../x"])


def test_validation_api_rejects_bad_input_without_calling_github(client):
    response = client.post("/api/repos/validate", json={"url": "https://evil.example/x/y"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_repository"


def _docker(monkeypatch, available):
    from sentinel.verify import sandbox as sbx

    monkeypatch.setattr(sbx, "docker_status", lambda max_age=10.0: {"available": available, "version": "x" if available else None,
                                                                    "detail": "Docker engine x" if available else "Docker engine is not running."})


def test_without_docker_an_untrusted_repository_is_refused_not_run_on_the_host(tmp_path, monkeypatch):
    _docker(monkeypatch, False)
    client = make_client(tmp_path, FakeGitHub)
    repo = client.post("/api/repos", json={"url": "https://github.com/acme/widgets"}).json()
    assert repo["id"] == "acme--widgets" and repo["trusted"] is False and repo["status"] == "needs_clone"
    refused = client.post("/api/analyses", json={"repo_id": repo["id"], "ref": "7"})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_trusted"
    assert "Docker" in refused.json()["error"]["message"]
    assert client.post(f"/api/repos/{repo['id']}/trust", json={"trusted": True}).json()["trusted"] is True
    assert client.post("/api/analyses", json={"repo_id": repo["id"], "ref": "not-a-number"}).status_code == 422
    assert client.delete(f"/api/repos/{repo['id']}").status_code == 204


def test_with_docker_a_repository_is_sandboxed_without_needing_trust(tmp_path, monkeypatch):
    _docker(monkeypatch, True)
    client = make_client(tmp_path, FakeGitHub)
    repo = client.post("/api/repos", json={"url": "https://github.com/acme/widgets"}).json()
    from sentinel.service.repos import RepoRegistry

    registry = RepoRegistry(Config.load(tmp_path, environ={}, overrides={"workdir": str(tmp_path / "w")}), tmp_path / "w" / "service")
    assert registry.execution_mode(repo) == "docker"
    assert registry.execution_mode({**repo, "trusted": True}) == "docker"  # trust never downgrades a sandbox to the host


def test_host_execution_needs_both_no_docker_and_explicit_trust(tmp_path, monkeypatch):
    _docker(monkeypatch, False)
    from sentinel.service.repos import RepoError, RepoRegistry

    registry = RepoRegistry(Config.load(tmp_path, environ={}, overrides={"workdir": str(tmp_path / "w")}), tmp_path / "w" / "service")
    repo = {"kind": "github", "trusted": False}
    with pytest.raises(RepoError) as err:
        registry.execution_mode(repo)
    assert err.value.code == "not_trusted"
    assert registry.execution_mode({**repo, "trusted": True}) == "host"


def test_non_python_repository_is_unsupported(tmp_path, monkeypatch):
    class Rust(FakeGitHub):
        def languages(self, owner, name):
            return {"Rust": 1000}

    _docker(monkeypatch, True)
    client = make_client(tmp_path, Rust)
    repo = client.post("/api/repos", json={"url": "acme/crabby"}).json()
    assert repo["status"] == "unsupported"
    client.post(f"/api/repos/{repo['id']}/trust", json={"trusted": True})
    assert client.post("/api/analyses", json={"repo_id": repo["id"], "ref": "1"}).json()["error"]["code"] == "unsupported"


def test_project_detection_reports_what_it_found(tmp_path):
    (tmp_path / "src" / "widgets").mkdir(parents=True)
    (tmp_path / "src" / "widgets" / "__init__.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='widgets'\n")
    found = detect_project(tmp_path, "widgets", 0.9)
    assert found["project"] == {"root": ".", "package": "widgets", "tests": "tests"}
    assert found["compat"]["level"] == "full"
    assert any(c["id"] == "dependencies" and c["ok"] is None for c in found["compat"]["checks"])  # never claimed
    assert detect_project(tmp_path / "empty", "x", 0.2)["compat"]["level"] == "unsupported"


# --- API security ------------------------------------------------------------------------------


def test_cross_site_origin_is_rejected(client):
    assert client.get("/api/service/status", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/service/status", headers={"Origin": "http://localhost:5173"}).status_code == 200


def test_token_is_required_when_configured(client, monkeypatch):
    monkeypatch.setenv("SENTINEL_API_TOKEN", "s3cret")
    assert client.get("/api/repos").status_code == 401
    assert client.get("/api/repos", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/repos", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/api/repos?token=s3cret").status_code == 200  # EventSource cannot set headers


def test_status_reports_the_sandbox_truthfully_in_both_states(client, monkeypatch):
    _docker(monkeypatch, True)
    up = {c["id"]: c for c in client.get("/api/service/status").json()["checks"]}["sandbox"]
    assert up["ok"] is True and "no network" in up["detail"]
    _docker(monkeypatch, False)
    down = {c["id"]: c for c in client.get("/api/service/status").json()["checks"]}["sandbox"]
    assert down["ok"] is False and "never falls back to the host silently" in down["detail"]
    assert client.get("/api/service/status").json()["auth"] in ("none", "token")


def test_model_card_comes_from_the_model_file_and_results(client):
    card = client.get("/api/model").json()
    assert card["family"] in ("expert", "fitted") and card["thresholds"]["block"] > card["thresholds"]["canary"]
    assert len(card["features"]) == card["feature_count"]
    assert any("hand-set" in text for text in card["limitations"])


# --- replay & comparison ------------------------------------------------------------------------------


def _report(decision, risk, head, claims, failed=()):
    return {"run_id": "r", "decision": decision, "risk": risk, "pr": {"head_sha": head}, "claims": claims, "features": {"mutation_score": risk},
            "verification": {"tests": {"failed": [{"nodeid": n} for n in failed]}, "mutation": {"score": 1 - risk}}, "selection": {"tests": ["t1"]},
            "thresholds": {"canary": 0.3, "block": 0.6}, "risk_model": "m", "llm": {}}


def test_compare_runs_reports_differences():
    meta = lambda i: {"run_id": i, "repo_id": "x", "pr": {"ref": "1"}}  # noqa: E731
    a = _report("PASS", 0.1, "aaa", [{"type": "test_impact", "target": "t::a", "status": "VERIFIED"}])
    b = _report("BLOCK", 0.9, "bbb", [{"type": "test_impact", "target": "t::a", "status": "REFUTED"}, {"type": "module_impact", "target": "m.py", "status": "VERIFIED"}], failed=["t::a"])
    diff = compare_runs(meta("a"), a, meta("b"), b)
    assert diff["decision_changed"] and diff["same_pr"] and not diff["same_head"] and diff["risk_delta"] == 0.8
    assert diff["claims"]["status_changed"] == [{"type": "test_impact", "target": "t::a", "from": "VERIFIED", "to": "REFUTED"}]
    assert diff["claims"]["added"][0]["target"] == "m.py" and diff["tests"]["newly_failing"] == ["t::a"]


def test_events_are_reconstructed_from_a_recorded_trace():
    report = {"run_id": "run-x", "decision": "PASS", "risk": 0.1, "trace": [
        {"node": "guard_in", "start": 10.0, "end": 10.1, "status": "ok", "note": "", "tokens": 0, "retries": 0},
        {"node": "impact", "start": 10.2, "end": 10.9, "status": "ok", "note": "rules: 2 claims", "tokens": 0, "retries": 0},
        {"node": "signals", "start": 10.2, "end": 10.5, "status": "ok", "note": "intent=refactor", "tokens": 0, "retries": 0}]}
    events = reconstruct_events(report)
    assert all(e["data"]["reconstructed"] for e in events)
    kinds = [(e["stage"], e["kind"]) for e in events]
    assert kinds.index(("impact", "stage_started")) < kinds.index(("signals", "stage_completed"))  # parallel overlap preserved
    assert ("verify", "stage_skipped") in kinds and ("guard_in", "stage_skipped") not in kinds and ("canary", "stage_skipped") in kinds


# --- a real run, end to end -------------------------------------------------------------------------------


@pytest.mark.skipif(not (DEMO_REPO / ".git").exists(), reason="demo repository not seeded (python -m sentinel.data.index --seed-demo)")
def test_real_analysis_streams_events_and_persists(tmp_path):
    cfg = Config.load(ROOT, environ={}, overrides={"llm": {"enabled": False}})
    client = TestClient(create_api(cfg))
    pulls = client.get("/api/repos/unierp-demo/pulls").json()
    branch = next(p["ref"] for p in pulls if "refactor" in p["ref"])
    run = client.post("/api/analyses", json={"repo_id": "unierp-demo", "ref": branch, "llm": False}).json()
    run_id = run["run_id"]
    deadline = time.time() + 240
    while time.time() < deadline:
        meta = client.get(f"/api/analyses/{run_id}").json()["meta"]
        if meta["status"] not in ("queued", "running"):
            break
        time.sleep(1)
    assert meta["status"] == "completed", meta
    body = client.get(f"/api/analyses/{run_id}").json()
    events = client.get(f"/api/analyses/{run_id}/events").json()["events"]
    by_stage = {}
    for e in events:
        by_stage.setdefault(e["stage"], []).append(e["kind"])
    assert by_stage["verify"][0] == "stage_started" and "stage_completed" in by_stage["verify"] and "substep" in by_stage["verify"]
    assert events[-1]["kind"] == "run_completed" and events[-1]["data"]["decision"] == body["report"]["decision"] == meta["decision"]
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    # the three agents genuinely overlapped
    starts = {e["stage"]: e["ts"] for e in events if e["kind"] == "stage_started"}
    ends = {e["stage"]: e["ts"] for e in events if e["kind"] == "stage_completed"}
    assert max(starts[a] for a in ("impact", "historian", "signals")) < min(ends[a] for a in ("impact", "historian", "signals"))
    # SSE replays the same log and terminates
    with client.stream("GET", f"/api/analyses/{run_id}/stream") as stream:
        text = "".join(stream.iter_text())
    assert text.count("\ndata: {\"schema\"") + text.startswith("data:") >= len(events) - 1 and "event: end" in text
    # a second analysis of the same PR is a new run
    again = client.post("/api/analyses", json={"repo_id": "unierp-demo", "ref": branch, "llm": False}).json()
    assert again["run_id"] != run_id
    client.post(f"/api/analyses/{again['run_id']}/cancel")
