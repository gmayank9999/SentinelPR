"""Publishing a finished analysis: what is checked, what is reported, and that nothing is claimed without GitHub's confirmation."""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import sentinel.publish.github as github_publish
from sentinel.api import create_api
from sentinel.config import Config

TOKEN = "github_pat_" + "Q1w2E3r4" * 8
HEAD = "c" * 40
RUN = "run-20260101000000-aaaaaa"
REPO = {"id": "o--r", "kind": "github", "owner": "o", "name": "r", "full_name": "o/r", "url": "https://github.com/o/r", "default_branch": "main", "description": "", "private": False,
        "language": "Python", "python_share": 1.0, "trusted": False, "status": "ready", "project": {"root": ".", "package": "r", "tests": "tests"},
        "compat": {"level": "full", "checks": []}, "added_at": 1, "last_synced_at": None}


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    for name in ("SENTINEL_ALLOW_PUBLISH", "SENTINEL_ALLOW_PUBLISH_FORKS", "SENTINEL_GITHUB_TOKEN", "GITHUB_TOKEN", "SENTINEL_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    cfg = Config.load(tmp_path, environ={}, overrides={"workdir": str(tmp_path / "w")})
    service_dir = cfg.workdir / "service"
    service_dir.mkdir(parents=True)
    (service_dir / "repos.json").write_text(json.dumps({"o--r": REPO}), encoding="utf-8")
    run_dir = service_dir / "runs" / RUN
    run_dir.mkdir(parents=True)
    meta = {"schema": "sentinelpr.run-meta/1", "run_id": RUN, "repo_id": "o--r", "repo_name": "o/r", "pr": {"ref": "2", "number": 2, "head_sha": HEAD}, "status": "completed",
            "decision": "PASS", "risk": 0.2, "created_at": time.time(), "options": {"llm": False, "publish": False}, "imported": False}
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    report = {"run_id": RUN, "decision": "PASS", "risk": 0.2, "pr": {"number": 2, "head_sha": HEAD, "is_fork": False}, "claims": [], "verdicts": [], "trace": []}
    (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return TestClient(create_api(cfg)), monkeypatch, run_dir


def enable(monkeypatch):
    monkeypatch.setenv("SENTINEL_ALLOW_PUBLISH", "1")
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)


def fake_github(monkeypatch, result, calls=None, gate=None):
    """Replace only the call to GitHub; the server's own gating, recording and reporting stay real."""
    def publish(report, cfg, repository, token, *, head_sha, **kw):
        if calls is not None:
            calls.append({"repository": repository, "head_sha": head_sha, "decision": report["decision"]})
        if gate:
            gate.wait(5)
        return result

    monkeypatch.setattr(github_publish, "publish", publish)


def codes(info):
    return {b["code"] for b in info["blockers"]}


def test_the_control_explains_what_blocks_publishing_and_how_to_fix_it(setup):
    api, mp, _ = setup
    info = api.get(f"/api/analyses/{RUN}/publish").json()
    assert info["allowed"] is False and codes(info) == {"disabled", "no_token"}
    disabled = next(b for b in info["blockers"] if b["code"] == "disabled")
    assert "SENTINEL_ALLOW_PUBLISH=1" in disabled["fix"] and ".env" in disabled["fix"] and "restart" in disabled["fix"]
    assert "GITHUB_TOKEN" in next(b for b in info["blockers"] if b["code"] == "no_token")["fix"]
    refused = api.post(f"/api/analyses/{RUN}/publish", json={})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "publish_disabled"  # the server enforces it, not just the UI


def test_the_target_is_the_runs_own_pr_and_commit_and_expectations_are_stated(setup):
    api, mp, _ = setup
    enable(mp)
    info = api.get(f"/api/analyses/{RUN}/publish").json()
    assert info["allowed"] is True and info["blockers"] == []
    assert info["target"] == {"repository": "o/r", "pr_number": 2, "head_sha": HEAD, "decision": "PASS"}
    assert "GitHub App" in info["will_post"]["check_run"] and "personal access token" in info["will_post"]["check_run"]  # the true requirement is stated up front
    assert info["targets"]["comment"]["available"] and info["targets"]["status"]["available"]
    assert info["targets"]["check_run"]["available"] is False and "No GitHub App is configured" in info["targets"]["check_run"]["problems"][0]


def test_fork_prs_are_blocked_unless_allowed(setup):
    api, mp, run_dir = setup
    enable(mp)
    report = json.loads((run_dir / "report.json").read_text())
    report["pr"]["is_fork"] = True
    (run_dir / "report.json").write_text(json.dumps(report))
    assert codes(api.get(f"/api/analyses/{RUN}/publish").json()) == {"fork"}
    mp.setenv("SENTINEL_ALLOW_PUBLISH_FORKS", "1")
    assert api.get(f"/api/analyses/{RUN}/publish").json()["allowed"] is True


def test_comment_posted_but_check_run_refused_is_reported_as_exactly_that(setup):
    api, mp, _ = setup
    enable(mp)
    calls = []
    fake_github(mp, {"comment": 987, "check_run_error": 403}, calls)
    result = api.post(f"/api/analyses/{RUN}/publish", json={}).json()
    assert calls == [{"repository": "o/r", "head_sha": HEAD, "decision": "PASS"}]  # the run's own repository, PR commit and decision
    assert result["published"] is True
    comment = result["comment"]
    assert (comment["posted"], comment["id"], comment["url"], comment["error"]) == (True, 987, "https://github.com/o/r/pull/2#issuecomment-987", None)
    assert result["check_run"]["created"] is False and result["check_run"]["id"] is None and result["check_run"]["error"] == 403
    assert result["check_run"]["attempted"] is True and "only a GitHub App can create Check Runs" in result["check_run"]["explanation"]
    assert "Checks → Read and write" not in json.dumps(result)  # no permission is suggested that a personal token cannot have
    assert result["pr_number"] == 2 and result["head_sha"] == HEAD and TOKEN not in json.dumps(result)


def test_a_check_run_is_only_claimed_when_github_returned_its_id(setup):
    api, mp, _ = setup
    enable(mp)
    fake_github(mp, {"comment": 5, "check_run": 77})
    done = api.post(f"/api/analyses/{RUN}/publish", json={}).json()
    check = done["check_run"]
    assert (check["created"], check["id"], check["error"], check["explanation"]) == (True, 77, None, None) and done["comment"]["posted"] is True


def test_when_github_refuses_everything_nothing_is_reported_as_published(setup):
    api, mp, _ = setup
    enable(mp)
    fake_github(mp, {"check_run_error": 403, "comment_error": 403})
    out = api.post(f"/api/analyses/{RUN}/publish", json={}).json()
    assert out["published"] is False and out["comment"]["error"] == 403 and out["comment"]["posted"] is False and out["check_run"]["created"] is False


def test_publishing_twice_needs_an_explicit_confirmation_and_every_publication_is_recorded(setup):
    api, mp, _ = setup
    enable(mp)
    calls = []
    fake_github(mp, {"comment": 11}, calls)
    assert api.post(f"/api/analyses/{RUN}/publish", json={}).status_code == 200
    again = api.post(f"/api/analyses/{RUN}/publish", json={})
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_published" and len(calls) == 1
    assert api.get(f"/api/analyses/{RUN}/publish").json()["already_published"]["comment"]["id"] == 11
    assert api.post(f"/api/analyses/{RUN}/publish", json={"force": True}).status_code == 200 and len(calls) == 2
    assert len(api.get(f"/api/analyses/{RUN}").json()["meta"]["publish_history"]) == 2


def test_concurrent_clicks_publish_once(setup):
    api, mp, _ = setup
    enable(mp)
    calls, gate, statuses = [], threading.Event(), []
    fake_github(mp, {"comment": 21}, calls, gate)
    first = threading.Thread(target=lambda: statuses.append(api.post(f"/api/analyses/{RUN}/publish", json={}).status_code))
    first.start()
    for _ in range(100):
        if calls:
            break
        time.sleep(0.05)
    second = api.post(f"/api/analyses/{RUN}/publish", json={})
    assert second.status_code == 409 and "in progress" in second.json()["error"]["message"]
    gate.set()
    first.join(10)
    assert statuses == [200] and len(calls) == 1


def test_a_github_exception_is_contained_redacted_and_reported(setup):
    api, mp, _ = setup
    enable(mp)

    def boom(*a, **k):
        raise RuntimeError(f"401 Bad credentials for {TOKEN}")

    mp.setattr(github_publish, "publish", boom)
    out = api.post(f"/api/analyses/{RUN}/publish", json={})
    assert out.status_code == 200 and out.json()["published"] is False and out.json()["error"]
    assert TOKEN not in out.text and "github_pat_" not in out.text


def test_an_unfinished_or_unknown_run_cannot_be_published(setup):
    api, mp, run_dir = setup
    enable(mp)
    assert api.get("/api/analyses/run-20200101000000-ffffff/publish").status_code == 404
    meta = json.loads((run_dir / "meta.json").read_text())
    meta["status"] = "running"
    (run_dir / "meta.json").write_text(json.dumps(meta))
    assert "not_completed" in codes(api.get(f"/api/analyses/{RUN}/publish").json())


# --- the GitHub App path through the server --------------------------------------------------------------------------------------------

APP_TOKEN = "ghs_" + "AppInstall7Token" * 2


def with_app(monkeypatch, *, token=APP_TOKEN, error=None, checks_write=True):
    """Make the server believe an App is configured, with GitHub's App endpoints replaced by fakes."""
    from sentinel.publish.app_auth import AppAuthError, GitHubAppAuth

    monkeypatch.setattr(GitHubAppAuth, "configured", property(lambda self: True))
    monkeypatch.setattr(GitHubAppAuth, "describe", lambda self, repo=None: {
        "configured": True, "problems": [] if checks_write else ["The App does not have Checks: Read and write."],
        "app": {"name": "SentinelPR Gate", "slug": "sentinelpr-gate", "html_url": "https://github.com/apps/sentinelpr-gate", "permissions": {"checks": "write"}},
        "installation": {"id": 7, "repository": repo}, "checks_write": checks_write})

    def mint(self, repository, permissions=None):
        if error:
            raise AppAuthError(*error)
        assert permissions == {"checks": "write"}  # least privilege: one permission
        return token

    monkeypatch.setattr(GitHubAppAuth, "token", mint)


def capture(monkeypatch, result):
    seen = []

    def publish(report, cfg, repository, token, *, head_sha, check_token=None, targets=None):
        seen.append({"token": token, "check_token": check_token, "targets": targets})
        return result

    monkeypatch.setattr(github_publish, "publish", publish)
    return seen


def test_with_an_app_the_check_run_is_offered_and_made_with_the_installation_token(setup):
    api, mp, _ = setup
    enable(mp)
    with_app(mp)
    info = api.get(f"/api/analyses/{RUN}/publish").json()
    check = info["targets"]["check_run"]
    assert check["available"] is True and check["via"] == "GitHub App" and check["app"]["slug"] == "sentinelpr-gate" and check["problems"] == []
    seen = capture(mp, {"comment": 1, "status": 2, "check_run": 3})
    record = api.post(f"/api/analyses/{RUN}/publish", json={}).json()
    assert seen == [{"token": TOKEN, "check_token": APP_TOKEN, "targets": {"comment": True, "status": True, "check_run": True}}]
    assert record["published"] and record["comment"]["posted"] and record["status"]["posted"] and record["check_run"]["created"] and record["check_run"]["id"] == 3
    assert TOKEN not in json.dumps(record) and APP_TOKEN not in json.dumps(record)


def test_an_app_that_lacks_the_checks_permission_is_not_offered(setup):
    api, mp, _ = setup
    enable(mp)
    with_app(mp, checks_write=False)
    check = api.get(f"/api/analyses/{RUN}/publish").json()["targets"]["check_run"]
    assert check["available"] is False and any("Checks: Read and write" in p for p in check["problems"])


def test_without_an_app_the_check_run_is_not_attempted_but_comment_and_status_still_go_out(setup):
    api, mp, _ = setup
    enable(mp)
    seen = capture(mp, {"comment": 1, "status": 2, "check_run_skipped": "needs_github_app"})
    record = api.post(f"/api/analyses/{RUN}/publish", json={}).json()
    assert seen[0]["check_token"] is None  # the personal token is never offered as a Check Run credential
    assert record["published"] and record["comment"]["posted"] and record["status"]["posted"]
    assert record["check_run"]["created"] is False and record["check_run"]["attempted"] is False and "needs a GitHub App" in record["check_run"]["explanation"]


def test_an_app_failure_is_reported_and_does_not_stop_the_comment_or_status(setup):
    api, mp, _ = setup
    enable(mp)
    with_app(mp, error=("not_installed", "The GitHub App is not installed on this repository.", "Install the App on it."))
    seen = capture(mp, {"comment": 1, "status": 2})
    record = api.post(f"/api/analyses/{RUN}/publish", json={}).json()
    assert seen[0]["check_token"] is None and record["comment"]["posted"] and record["status"]["posted"]
    assert record["check_run"]["created"] is False and record["check_run"]["error"] == "not_installed" and record["check_run"]["fix"] == "Install the App on it."


def test_the_caller_chooses_which_targets_are_posted(setup):
    api, mp, _ = setup
    enable(mp)
    seen = capture(mp, {"comment": 1})
    api.post(f"/api/analyses/{RUN}/publish", json={"targets": {"comment": True, "status": False, "check_run": False}})
    assert seen[0]["targets"] == {"comment": True, "status": False, "check_run": False}
    none = api.post(f"/api/analyses/{RUN}/publish", json={"force": True, "targets": {"comment": False, "status": False, "check_run": False}})
    assert none.status_code == 422 and none.json()["error"]["code"] == "nothing_selected"


def test_the_app_status_endpoint_never_returns_secrets(setup, tmp_path):
    _, mp, _ = setup
    mp.setenv("SENTINEL_GITHUB_APP_CLIENT_ID", "Iv1.abc")  # read when the server starts, like every other credential
    mp.setenv("SENTINEL_GITHUB_APP_PRIVATE_KEY_PATH", str(tmp_path / "nonexistent.pem"))
    api = TestClient(create_api(Config.load(tmp_path, environ={}, overrides={"workdir": str(tmp_path / "w2")})))
    body = api.get("/api/github/app").json()
    assert body["configured"] is True and any("does not point to an existing file" in p for p in body["problems"])
    assert "BEGIN" not in json.dumps(body) and "Iv1.abc" not in json.dumps(body)
