"""Publishing to GitHub: PR comment, commit status and Check Run.

A personal access token cannot create a Check Run (GitHub: "you must use a GitHub App"), so the Check Run uses a separate
GitHub App installation token and is not even attempted without one. The comment and the commit status use the personal token.
GitHub is always an in-memory transport, so these tests can never create anything real.
"""

import json

import httpx
import pytest

from sentinel.config import Config
from sentinel.publish.diagnose import explain, token_kind
from sentinel.publish.github import GitHubPublisher, publish
from sentinel.service.runs import describe_publish

PAT = "github_pat_" + "K7m8N9p0" * 8
APP_TOKEN = "ghs_" + "AppInstall7Token" * 2
HEAD = "d" * 40
REPORT = {"run_id": "run-test", "elapsed_s": 1.0, "tokens": 0, "decision": "CANARY", "risk": 0.55, "pr": {"number": 2}, "claims": [], "verdicts": [], "changes": [], "change_units": [],
          "verification": None, "decision_reason": "risk 0.55 >= canary threshold", "explanation": "x", "contributions": [], "guard_events": [], "selection": None, "signals": {}, "features": {}}
PAT_403 = {"message": "Resource not accessible by personal access token", "documentation_url": "https://docs.github.com/rest/checks/runs#create-a-check-run", "status": "403"}


def cfg(tmp_path):
    return Config.load(tmp_path, environ={}, overrides={"workdir": str(tmp_path / "w")})


class FakeGitHub:
    """Records every request (with the credential that made it) and answers from a script."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def client_for(self, token):
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append((token, request))
            for (method, suffix), answer in self.routes.items():
                if request.method == method and request.url.path.endswith(suffix):
                    out = answer(request) if callable(answer) else answer
                    if isinstance(out, Exception):
                        raise out
                    return out
            return httpx.Response(404, json={"message": "Not Found"})

        return httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))

    def paths(self, token=None):
        return [(t, r.method, r.url.path) for t, r in self.requests if token is None or t == token]


def run_publish(tmp_path, monkeypatch, routes, **kw):
    gh = FakeGitHub(routes)
    real = GitHubPublisher.__init__

    def init(self, repository, token, client=None):
        real(self, repository, token, client=gh.client_for(token))

    monkeypatch.setattr(GitHubPublisher, "__init__", init)
    return publish(REPORT, cfg(tmp_path), "o/r", PAT, head_sha=HEAD, **kw), gh


COMMENT = {("GET", "/issues/2/comments"): httpx.Response(200, json=[]), ("POST", "/issues/2/comments"): httpx.Response(201, json={"id": 555})}
STATUS = {("POST", f"/statuses/{HEAD}"): httpx.Response(201, json={"id": 31})}
CHECK = {("POST", "/repos/o/r/check-runs"): httpx.Response(201, json={"id": 4242})}


# --- the Check Run needs a GitHub App --------------------------------------------------------------------------------------------


def test_without_an_app_the_check_run_is_not_attempted_at_all_and_nothing_else_is_disturbed(tmp_path, monkeypatch):
    result, gh = run_publish(tmp_path, monkeypatch, {**CHECK, **STATUS, **COMMENT}, check_token=None)
    assert result == {"check_run_skipped": "needs_github_app", "status": 31, "comment": 555}
    assert not [p for _, _, p in gh.paths() if p.endswith("/check-runs")], "a doomed call to the Check Runs endpoint must not even be sent"


def test_with_an_app_token_the_check_run_uses_that_token_and_the_others_use_the_personal_token(tmp_path, monkeypatch):
    result, gh = run_publish(tmp_path, monkeypatch, {**CHECK, **STATUS, **COMMENT}, check_token=APP_TOKEN)
    assert result == {"check_run": 4242, "status": 31, "comment": 555}
    for token, method, path in gh.paths():
        assert token == (APP_TOKEN if path.endswith("/check-runs") else PAT), f"{method} {path} used the wrong credential"
    sent = next(r for _, r in gh.requests if r.url.path.endswith("/check-runs"))
    body = json.loads(sent.content)
    assert body["head_sha"] == HEAD and body["name"] == "SentinelPR Gate" and body["status"] == "completed" and body["conclusion"] == "neutral"
    assert len(body["output"]["annotations"]) <= 50


def test_in_github_actions_the_workflow_token_does_both_as_before(tmp_path, monkeypatch):
    result, gh = run_publish(tmp_path, monkeypatch, {**CHECK, **STATUS, **COMMENT})  # check_token defaults to the same token
    assert result["check_run"] == 4242 and {t for t, _, _ in gh.paths()} == {PAT}


def test_a_check_run_is_counted_only_for_a_2xx_response_that_carries_an_id(tmp_path, monkeypatch):
    for bad in (httpx.Response(201, json={"unexpected": True}), httpx.Response(200, content=b"<html>no</html>"), httpx.Response(201, json={"id": "abc"})):
        result, _ = run_publish(tmp_path, monkeypatch, {("POST", "/repos/o/r/check-runs"): bad, **STATUS, **COMMENT}, check_token=APP_TOKEN)
        assert "check_run" not in result and "check_run_error" in result and result["comment"] == 555


def test_an_app_token_without_the_permission_is_explained_as_an_app_problem(tmp_path, monkeypatch):
    refused = httpx.Response(403, json={"message": "Resource not accessible by integration"})
    result, _ = run_publish(tmp_path, monkeypatch, {("POST", "/repos/o/r/check-runs"): refused, **STATUS, **COMMENT}, check_token=APP_TOKEN)
    check = describe_publish(result, "o/r", 2, HEAD)["check_run"]
    assert check["created"] is False and check["attempted"] is True and "installation lacks the permission" in check["explanation"] and "accept the updated permissions" in check["fix"]


def test_a_personal_token_used_for_the_check_run_gets_the_true_explanation_not_a_permission_to_grant(monkeypatch):
    explained = explain("check_run", {"status": 403, "message": PAT_403["message"], "accepted_permissions": "checks=write"}, "fine-grained")
    assert "only a GitHub App can create Check Runs" in explained["explanation"] and "no Checks permission to grant" in explained["explanation"]
    assert "docs/github-app.md" in explained["fix"] and "commit status" in explained["fix"]
    assert "Checks → Read and write" not in explained["fix"]  # the earlier, wrong advice is gone


# --- the commit status ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("decision,state", [("PASS", "success"), ("CANARY", "success"), ("BLOCK", "failure")])
def test_the_commit_status_maps_the_decision_and_stays_within_githubs_limits(tmp_path, monkeypatch, decision, state):
    gh = FakeGitHub(STATUS)
    publisher = GitHubPublisher("o/r", PAT, gh.client_for(PAT))
    report = {**REPORT, "decision": decision, "decision_reason": "x" * 400}
    assert publisher.commit_status(HEAD, report, "SentinelPR / gate", "https://dash.example/run") == 31
    body = json.loads(gh.requests[0][1].content)
    assert body["state"] == state and body["context"] == "SentinelPR / gate" and body["target_url"] == "https://dash.example/run"
    assert len(body["description"]) <= 140 and body["description"].startswith(decision)


def test_a_status_refusal_names_the_permission_and_leaves_the_comment_alone(tmp_path, monkeypatch):
    refused = httpx.Response(403, json=PAT_403, headers={"x-accepted-github-permissions": "statuses=write"})
    result, _ = run_publish(tmp_path, monkeypatch, {("POST", f"/statuses/{HEAD}"): refused, **COMMENT}, check_token=None)
    record = describe_publish(result, "o/r", 2, HEAD)
    assert record["status"]["posted"] is False and "Commit statuses → Read and write" in record["status"]["fix"] and record["comment"]["posted"] is True
    assert record["status"]["accepted_permissions"] == "statuses=write"


# --- choosing what to post -------------------------------------------------------------------------------------------------------------


def test_only_the_selected_targets_are_sent(tmp_path, monkeypatch):
    result, gh = run_publish(tmp_path, monkeypatch, {**CHECK, **STATUS, **COMMENT}, check_token=APP_TOKEN, targets={"comment": True, "status": False, "check_run": False})
    assert list(result) == ["comment"] and not [p for _, _, p in gh.paths() if "statuses" in p or "check-runs" in p]


# --- reporting: not attempted is not the same as refused --------------------------------------------------------------------------------


def test_the_record_separates_posted_refused_and_not_attempted(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", PAT)
    record = describe_publish({"comment": 555, "status": 31, "check_run_skipped": "needs_github_app"}, "o/r", 2, HEAD)
    assert record["published"] is True and record["comment"]["posted"] and record["status"]["posted"]
    check = record["check_run"]
    assert check["created"] is False and check["attempted"] is False and "needs a GitHub App" in check["explanation"] and "docs/github-app.md" in check["fix"]
    refused = describe_publish({"comment": 555, "check_run_error": 403, "check_run_detail": {"status": 403, "message": PAT_403["message"]}}, "o/r", 2, HEAD)
    assert refused["check_run"]["attempted"] is True and refused["check_run"]["error"] == 403
    unselected = describe_publish({"comment": 555}, "o/r", 2, HEAD, {"comment": True, "status": False, "check_run": False})
    assert unselected["check_run"]["explanation"] == "Not selected." and unselected["status"]["attempted"] is False


def test_an_app_failure_before_any_request_is_reported_as_such(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", PAT)
    app_error = {"code": "not_installed", "message": "The GitHub App is not installed on this repository.", "fix": "Install it."}
    check = describe_publish({"comment": 1, "check_run_app_error": app_error}, "o/r", 2, HEAD)["check_run"]
    assert check["created"] is False and check["attempted"] is False and check["error"] == "not_installed" and check["fix"] == "Install it."


def test_the_network_failing_on_one_call_does_not_stop_the_others(tmp_path, monkeypatch):
    result, _ = run_publish(tmp_path, monkeypatch, {("POST", "/repos/o/r/check-runs"): httpx.ConnectTimeout("slow"), **STATUS, **COMMENT}, check_token=APP_TOKEN)
    assert result["comment"] == 555 and result["status"] == 31 and result["check_run_error"] == 0
    assert "Could not reach GitHub" in describe_publish(result, "o/r", 2, HEAD)["check_run"]["explanation"]


def test_no_secret_reaches_any_result(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", PAT)
    result, _ = run_publish(tmp_path, monkeypatch, {("POST", "/repos/o/r/check-runs"): httpx.Response(403, json=PAT_403), **STATUS, **COMMENT}, check_token=APP_TOKEN)
    blob = json.dumps(describe_publish(result, "o/r", 2, HEAD))
    assert PAT not in blob and APP_TOKEN not in blob
    assert token_kind(PAT) == "fine-grained" and token_kind("ghp_x") == "classic"
